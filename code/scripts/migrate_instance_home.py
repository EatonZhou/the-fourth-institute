"""Merge an old-layout instance's service HOME into the OS user's home (one time).

Old layouts gave the instance's services their own HOME at ``<root>/home``. The
protocol runs services with the OS user's own home, so the old HOME is merged
into it (Plan step 5), on one filesystem, by renaming:

* an entry only the old HOME has moves to the same place in the home;
* in download caches both have, files merge one by one and a same-named file
  keeps the home's copy (the old copy is discarded);
* elsewhere an identical file is discarded; a different one is a conflict and
  stays in the old HOME, listed with its differences (never overwriting);
* ``.ssh``: identical keys are discarded, keys only the old HOME has move,
  differing keys and ``authorized_keys`` differences are listed for the host
  administrator;
* ``.codex`` is an unregistered account copy with credentials: never merged,
  deleted once no process uses it;
* instance data left in the old HOME goes to the instance: copies of
  ``secrets/`` files are discarded when identical, legacy avatar files are
  deleted once every record is in the database, obsolete Portal leftovers are
  deleted; anything else is listed for its owner;
* text files in Lineage folders and the host's unit and shell files that name
  an old HOME path that still resolves after the merge are rewritten to the home.

Planning is the default. ``--apply`` checks, in the same command, that the
instance services are stopped (PostgreSQL may run), that the protected processes
are alive, and that no other process uses the old HOME. The database is not
changed here: the ``aliases`` entry of the report maps the old HOME to the home
for ``migrate_instance_database_paths``.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import sys
from dataclasses import dataclass, field

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from management_common.instance_manifest import InstanceManifest
from scripts.instance_storage_migration import (
    Move, Report, apply_moves, check_move, instance_for, parse_common, processes_using,
    remove_empty_directories, require_offline, require_protected, tree_size, walk_files, write_report,
)

# Download caches: a same-named file keeps the home's copy.
CACHE_PREFIXES = (".cache", ".npm", ".conda/pkgs", ".mamba/pkgs", ".nv", ".triton", ".local/share/uv")
# Moving a file here would install it for the host's service manager; list only.
REPORT_ONLY_PREFIXES = (".config/systemd",)
# Handled by their own rules below, never by the generic merge.
SPECIAL = (".ssh", ".codex", ".dirtybase", ".tfi", ".config/tfi", ".local/state/tfi-portal")
# Instance data the old Portal kept in its HOME, and what replaced it.
OBSOLETE = {
    ".config/tfi/portal_paths.json": "replaced by the instance manifest",
    ".tfi/secrets/script_runs": "RunScript leases now live in the instance runtime directory",
    ".dirtybase/runtime": "empty legacy runtime directory",
    ".dirtybase/portal/session_signing.key.lock": "the signing key lives in secrets/",
}
AVATAR_DIRECTORIES = (".dirtybase/portal/user_avatars", ".dirtybase/portal/resident_avatars",
                      ".dirtybase/portal/scope_avatars", ".tfi/portal/user_avatars", ".tfi/portal/resident_avatars")
# Keys that belong in the instance secrets/ and must not stay in the OS home.
HOME_SECRET_COPIES = (".dirtybase/portal/session_signing.key", ".dirtybase/portal/two_factor.key")
TEXT_DIFF_FILES = re.compile(r"^(\.ssh/config|\.ssh/[^/]+_config|\.gitconfig|\.bashrc|\.profile|\.bash_profile"
                             r"|\.conda/environments\.txt|\.config/systemd/user/[^/]+)$")
SECRET_LINE = re.compile(r"(?i)(pass(word)?|secret|token|api[_-]?key|authorization|bearer|credential)")
HASH_LIMIT = 64 * 1024 * 1024
# Transcripts and logs (.jsonl, .log) record history and are never rewritten.
TEXT_SUFFIXES = {".md", ".json", ".toml", ".yaml", ".yml", ".sh", ".py", ".env", ".txt", ".cfg", ".ini"}
TEXT_LIMIT = 4 * 1024 * 1024


def home_directory() -> Path:
    """The OS user's home from the password database, never ``$HOME``."""
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def old_home(instance: InstanceManifest) -> Path:
    return instance.root / "home"


def _under(relative: str, prefixes) -> bool:
    return any(relative == prefix or relative.startswith(prefix + "/") for prefix in prefixes)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def same_file(first: Path, second: Path) -> bool:
    """Same contents; files over the hash limit compare by size and modification time."""
    a, b = first.lstat(), second.lstat()
    if a.st_size != b.st_size:
        return False
    if a.st_size > HASH_LIMIT:
        return int(a.st_mtime) == int(b.st_mtime)
    return _digest(first) == _digest(second)


def _is_text(path: Path) -> bool:
    try:
        if path.stat().st_size > 1024 * 1024:
            return False
        return b"\0" not in path.read_bytes()[:8192]
    except OSError:
        return False


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def config_difference(source: Path, target: Path, relative: str) -> dict:
    """How two same-named files differ, without showing secret-looking text."""
    entry = {"relative": relative, "source": str(source), "target": str(target)}
    if not (_is_text(source) and _is_text(target)):
        entry.update({"text": False, "source_bytes": source.lstat().st_size, "target_bytes": target.lstat().st_size})
        return entry
    old, new = _lines(source), _lines(target)
    diff = list(difflib.unified_diff(new, old, f"home/{relative}", f"old-home/{relative}", lineterm="", n=1))
    entry.update({"text": True, "lines_only_in_old_home": len(set(old) - set(new)),
                  "lines_only_in_home": len(set(new) - set(old))})
    if TEXT_DIFF_FILES.match(relative) and not relative.startswith(".ssh/authorized_keys"):
        entry["diff"] = ["[redacted]" if SECRET_LINE.search(line) and line[:1] in "+-" and not line.startswith(("+++", "---"))
                         else line for line in diff[:400]]
    return entry


@dataclass
class HomePlan:
    old: Path
    home: Path
    moves: list[Move] = field(default_factory=list)
    discards: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    configs: list[dict] = field(default_factory=list)
    report_only: list[dict] = field(default_factory=list)
    ssh: dict = field(default_factory=dict)
    codex: dict = field(default_factory=dict)
    instance_data: list[dict] = field(default_factory=list)
    avatars: dict = field(default_factory=dict)
    hardcoded: dict = field(default_factory=dict)
    cache_merge: dict = field(default_factory=dict)
    aliases: list[dict] = field(default_factory=list)

    def discard(self, path: Path, reason: str, **details) -> None:
        self.discards.append({"path": str(path), "reason": reason, "bytes": tree_size(path), **details})

    def move(self, source: Path, target: Path, kind: str, note: str = "") -> None:
        self.moves.append(Move(str(source), str(target), kind, note))


def _cache_root(relative: str) -> str:
    return next(prefix for prefix in CACHE_PREFIXES if relative == prefix or relative.startswith(prefix + "/"))


def merge(plan: HomePlan, relative: str) -> None:
    """Merge the old HOME entry at ``relative`` into the same place in the home."""
    source, target = plan.old / relative, plan.home / relative
    if _under(relative, SPECIAL):
        return
    if relative in OBSOLETE:
        plan.discard(source, OBSOLETE[relative])
        return
    if source.is_symlink() and not source.exists():
        plan.discard(source, "dangling link", link=os.readlink(source))
        return
    cache = _under(relative, CACHE_PREFIXES)
    if _under(relative, REPORT_ONLY_PREFIXES):
        if source.is_dir() and not source.is_symlink() and target.is_dir() and not target.is_symlink():
            for child in sorted(os.listdir(source)):
                merge(plan, f"{relative}/{child}")
        elif (target.is_file() and source.is_file() and not source.is_symlink() and not target.is_symlink()
              and same_file(source, target)):
            plan.discard(source, "identical to the home's copy")
        elif source.is_dir() and not source.is_symlink():
            plan.report_only.append({"relative": relative, "source": str(source), "target_exists": os.path.lexists(target),
                                     "reason": "never installed into the host's unit directory by moving"})
        else:
            entry = config_difference(source, target, relative) if os.path.lexists(target) else {
                "relative": relative, "source": str(source), "target_exists": False}
            plan.report_only.append({**entry, "reason": "never installed into the host's unit directory by moving"})
        return
    if not os.path.lexists(target):
        plan.move(source, target, "cache" if cache else "only-old-home")
        if cache:
            stats = plan.cache_merge.setdefault(_cache_root(relative), {"moved": 0, "discarded": 0, "discarded_bytes": 0})
            stats["moved"] += 1
        return
    source_dir = source.is_dir() and not source.is_symlink()
    target_dir = target.is_dir() and not target.is_symlink()
    if source_dir and target_dir:
        for child in sorted(os.listdir(source)):
            merge(plan, f"{relative}/{child}")
        return
    if source_dir or target_dir or source.is_symlink() or target.is_symlink():
        if source.is_symlink() and target.is_symlink() and os.readlink(source) == os.readlink(target):
            plan.discard(source, "same link as the home's")
            return
        plan.conflicts.append({"relative": relative, "source": str(source), "target": str(target),
                               "reason": "different kinds of entry"})
        return
    if cache:
        stats = plan.cache_merge.setdefault(_cache_root(relative), {"moved": 0, "discarded": 0, "discarded_bytes": 0})
        size = tree_size(source)
        stats["discarded"] += 1
        stats["discarded_bytes"] += size
        plan.discards.append({"path": str(source), "reason": "cache file kept from the home", "bytes": size})
        return
    if same_file(source, target):
        plan.discard(source, "identical to the home's copy")
        return
    entry = config_difference(source, target, relative)
    plan.configs.append(entry)
    plan.conflicts.append({"relative": relative, "source": str(source), "target": str(target),
                           "reason": "differs from the home's copy; the home's version is kept"})


def _key_lines(path: Path) -> dict[str, int]:
    """SHA-256 of each effective line; never the line itself."""
    if not path.is_file():
        return {}
    result = {}
    for line in path.read_bytes().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith(b"#"):
            result[hashlib.sha256(stripped).hexdigest()] = 1
    return result


def plan_ssh(plan: HomePlan) -> None:
    source_dir, target_dir = plan.old / ".ssh", plan.home / ".ssh"
    report = {"moved": [], "discarded_identical": [], "conflicts": [], "config_differences": []}
    plan.ssh = report
    if not source_dir.is_dir():
        return
    if not target_dir.exists():
        plan.move(source_dir, target_dir, "ssh")
        report["moved"].append(".ssh")
        return
    old_keys, home_keys = _key_lines(source_dir / "authorized_keys"), _key_lines(target_dir / "authorized_keys")
    report["authorized_keys"] = {
        "old_home_lines": len(old_keys), "home_lines": len(home_keys),
        "only_in_old_home_sha256": sorted(old_keys.keys() - home_keys.keys()),
        "only_in_home_sha256": sorted(home_keys.keys() - old_keys.keys()),
        "decision": "listed for the host administrator; never merged or deleted here",
    }
    for child in sorted(os.listdir(source_dir)):
        source, target = source_dir / child, target_dir / child
        relative = ".ssh/" + child
        if child == "authorized_keys":
            continue
        if not os.path.lexists(target):
            plan.move(source, target, "ssh")
            report["moved"].append(relative)
        elif source.is_file() and target.is_file() and same_file(source, target):
            plan.discard(source, "identical to the home's copy")
            report["discarded_identical"].append(relative)
        elif child == "config" or child.endswith("_config") or child.startswith("known_hosts"):
            entry = config_difference(source, target, relative)
            if child.startswith("known_hosts"):
                entry.pop("diff", None)
            report["config_differences"].append(entry)
            plan.conflicts.append({"relative": relative, "source": str(source), "target": str(target),
                                   "reason": "SSH configuration differs; merge by hand, the home's version is kept"})
        else:
            # A different key under the same name: never shown, never overwritten.
            report["conflicts"].append(relative)
            plan.conflicts.append({"relative": relative, "source": str(source), "target": str(target),
                                   "reason": "different SSH key or file under the same name"})


def plan_codex(plan: HomePlan, users: list[dict]) -> None:
    path = plan.old / ".codex"
    if not path.exists():
        plan.codex = {"present": False}
        return
    using = [user for user in users if user["path"] == str(path) or user["path"].startswith(str(path) + "/")]
    plan.codex = {
        "present": True, "path": str(path), "bytes": tree_size(path),
        "has_credentials_file": (path / "auth.json").exists(),
        "processes": using,
        "decision": ("unregistered account copy: not merged; deleted on apply when no process uses it" if not using
                     else "in use: kept until no process, protected or not, uses it"),
    }


def _same_tree_files(source: Path, reference: Path) -> tuple[list[str], list[str], list[str]]:
    """Files under ``source`` identical to / different from / missing in ``reference``."""
    identical, different, missing = [], [], []
    files = [source] if source.is_file() else sorted(walk_files(source, skip_dirs=()))
    for path in files:
        relative = "" if path == source else path.relative_to(source).as_posix()
        other = reference / relative if relative else reference
        name = str(path)
        if not other.is_file():
            missing.append(name)
        elif same_file(path, other):
            identical.append(name)
        else:
            different.append(name)
    return identical, different, missing


def plan_instance_data(plan: HomePlan, instance: InstanceManifest) -> None:
    """Portal's own data left in the old HOME and its legacy copy in the home."""
    secrets = instance.directory("secrets")
    for relative in sorted(OBSOLETE):
        if os.path.lexists(plan.old / relative):
            plan.discard(plan.old / relative, OBSOLETE[relative])
            plan.instance_data.append({"path": str(plan.old / relative), "decision": "delete", "reason": OBSOLETE[relative]})
    legacy = plan.old / ".dirtybase/secrets"
    if legacy.is_dir():
        identical, different, missing = _same_tree_files(legacy, secrets)
        for name in identical:
            plan.discard(Path(name), "identical copy of the instance secrets/ file")
        for name in missing:
            source = Path(name)
            plan.move(source, secrets / source.relative_to(legacy), 'instance-secret')
        plan.instance_data.append({
            "path": str(legacy), "decision": "identical copies deleted; the rest is listed for its owner",
            "identical_to_secrets": len(identical), "different_from_secrets": different, "not_in_secrets": missing})
    for name in (".dirtybase/README.md", ".dirtybase/properties"):
        source = plan.old / name
        if not os.path.lexists(source):
            continue
        identical, different, missing = _same_tree_files(source, plan.home / name)
        for item in identical:
            plan.discard(Path(item), "identical to the home's legacy copy")
        plan.instance_data.append({"path": str(source), "decision": "identical copies deleted; the rest is listed",
                                   "identical": len(identical), "different": different, "not_in_home": missing})
    for relative in HOME_SECRET_COPIES:
        copy = plan.home / relative
        if not copy.is_file():
            continue
        reference = secrets / Path(relative).name
        if reference.is_file() and same_file(copy, reference):
            plan.discard(copy, "copy of the instance secrets/ key in the OS home")
            plan.instance_data.append({"path": str(copy), "decision": "delete", "reason": "identical to " + str(reference)})
        else:
            plan.instance_data.append({"path": str(copy), "decision": "listed for weihao",
                                       "reason": "differs from or missing in secrets/; key must not stay in the OS home"})
    known = {*OBSOLETE, *AVATAR_DIRECTORIES, ".dirtybase/secrets", ".dirtybase/README.md", ".dirtybase/properties"}
    for top in (".dirtybase", ".tfi", ".config/tfi"):
        base = plan.old / top
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            relative = path.relative_to(plan.old).as_posix()
            if any(relative == item or relative.startswith(item + "/") or item.startswith(relative + "/") for item in known):
                continue
            if path.is_dir() and any(path.iterdir()):
                continue
            plan.instance_data.append({"path": str(path), "decision": "listed for weihao", "reason": "unknown Portal leftover"})


def avatar_records(directory: Path) -> list[tuple[str, str]]:
    """The (subject kind, subject) of each legacy avatar record file."""
    records = []
    for path in sorted(directory.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            records.append(("unreadable", str(path)))
            continue
        if isinstance(value, dict) and value.get("username"):
            records.append(("user", str(value["username"])))
        elif isinstance(value, dict) and value.get("resident_id"):
            records.append(("resident", str(value["resident_id"])))
        elif isinstance(value, dict) and value.get("subject"):
            records.append(("scope", str(value["subject"])))
        else:
            records.append(("unreadable", str(path)))
    return records


def database_rows(instance: InstanceManifest, queries: dict[str, tuple[str, tuple]]) -> dict | None:
    """Read-only answers to ``queries``; None when the database cannot be reached."""
    try:
        import psycopg
        from management_common.secret_environment import secret_environment
        endpoint = secret_environment(instance, "postgres.env")["PORTAL_DATABASE_URL"]
        with psycopg.connect(endpoint, connect_timeout=10) as connection:
            connection.read_only = True
            connection.execute("SET statement_timeout='120s'")
            return {name: connection.execute(sql, args).fetchall() for name, (sql, args) in queries.items()}
    except Exception as exc:  # noqa: BLE001 - reported; never the endpoint
        return {"error": type(exc).__name__}


def plan_avatars(plan: HomePlan, instance: InstanceManifest) -> None:
    directories = [base / relative for base in (plan.old, plan.home) for relative in AVATAR_DIRECTORIES]
    directories = [directory for directory in directories if directory.is_dir()]
    records = {str(directory): avatar_records(directory) for directory in directories}
    rows = database_rows(instance, {"avatars": ("SELECT subject_kind, subject FROM portal_avatars.avatars", ())})
    if rows is None or "error" in rows:
        plan.avatars = {"verified": False, "error": (rows or {}).get("error"), "directories": list(records),
                        "decision": "kept: the database could not be checked"}
        return
    stored = {tuple(row) for row in rows["avatars"]}
    missing = sorted({record for items in records.values() for record in items if record not in stored})
    verified = not missing
    plan.avatars = {
        "verified": verified,
        "directories": {name: len(items) for name, items in records.items()},
        "records_missing_in_database": [list(item) for item in missing],
        "decision": "delete the legacy avatar directories" if verified else "kept until every record is in the database",
    }
    if verified:
        for directory in directories:
            plan.discard(directory, "legacy avatar import source; every record is in the database")


_TAIL = r"""(?:/[^\s"'`<>|;,()\[\]{}\\]*)?"""
_TRAILING = ".:：。，、"


def _path_pattern(old: Path) -> re.Pattern:
    return re.compile(r"(?<![\w.~-])" + re.escape(str(old)) + r"(?![\w@+-]|\.\w)" + _TAIL)


def _rewritable(match: str, plan: HomePlan) -> bool:
    """An old HOME path that still names something after the merge."""
    path = match.rstrip(_TRAILING)
    relative = path[len(str(plan.old)):].lstrip("/")
    if _under(relative, SPECIAL) and not relative.startswith(".ssh"):
        return False
    return os.path.lexists(plan.old / relative) or os.path.lexists(plan.home / relative)


def hardcoded_candidates(instance: InstanceManifest, home: Path) -> list[Path]:
    roots = [instance.area("lineages")]
    projects = instance.area("projects")
    if projects.is_dir():
        for project in sorted(projects.iterdir()):
            roots += [project / "knowledge_base/agents", project / "agents"]
    files = []
    for root in roots:
        if root.is_dir() and not root.is_symlink():
            files += [path for path in walk_files(root, skip_dirs=(".git", "node_modules", "__pycache__", ".venv",
                                                                     "venv", "site-packages", ".cache"))
                      if path.suffix in TEXT_SUFFIXES or (not path.suffix and path.name[:1] != ".")]
    unit_dir = home / ".config/systemd/user"
    if unit_dir.is_dir():
        files += sorted(path for path in unit_dir.iterdir() if path.is_file())
    files += [home / name for name in (".bashrc", ".profile", ".bash_profile", ".gitconfig") if (home / name).is_file()]
    return files


def plan_hardcoded(plan: HomePlan, instance: InstanceManifest, files: list[Path] | None = None) -> None:
    pattern = _path_pattern(plan.old)
    needle = str(plan.old).encode()
    rewrite, historical = [], []
    for path in (files if files is not None else hardcoded_candidates(instance, plan.home)):
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > TEXT_LIMIT:
                continue
            data = path.read_bytes()
            if needle not in data or b"\0" in data[:8192]:
                continue
            text = data.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        good = [m.group(0) for m in pattern.finditer(text) if _rewritable(m.group(0), plan)]
        bad = [m.group(0) for m in pattern.finditer(text) if not _rewritable(m.group(0), plan)]
        if good:
            rewrite.append({"path": str(path), "replacements": len(good), "paths": sorted(set(good))})
        if bad:
            historical.append({"path": str(path), "unresolved": sorted(set(bad))[:20],
                               "reason": "names something that no longer exists; left as written"})
    plan.hardcoded["files_to_rewrite"] = rewrite
    plan.hardcoded["unresolved_references"] = historical
    like = "%" + str(plan.old).replace("_", "\\_") + "%"
    rows = database_rows(instance, {
        "workspace.workspaces.path": ("SELECT count(*) FROM workspace.workspaces WHERE path LIKE %s", (like,)),
        "resource_registry.registry_documents": (
            "SELECT collection, count(*) FROM resource_registry.registry_documents WHERE document_json LIKE %s "
            "GROUP BY 1", (like,)),
        "lineage.resident_execution_configuration": (
            "SELECT count(*) FROM lineage.resident_execution_configuration WHERE execution_settings_json LIKE %s "
            "OR harness_config_json LIKE %s", (like, like)),
        "lineage.lineage_components.config_json": (
            "SELECT count(*) FROM lineage.lineage_components WHERE config_json LIKE %s", (like,)),
        "script_runs.runs.cwd": ("SELECT count(*) FROM script_runs.runs WHERE cwd LIKE %s", (like,)),
    })
    plan.hardcoded["database"] = {
        "counts": {name: [list(row) for row in value] for name, value in rows.items()} if rows and "error" not in rows
        else rows,
        "decision": "converted by migrate_instance_database_paths through the home alias",
    }


def build_plan(instance: InstanceManifest, *, home: Path | None = None, protected=(), scan_files: bool = True,
               files: list[Path] | None = None) -> tuple[HomePlan, list[dict]]:
    plan = HomePlan(old_home(instance), home or home_directory())
    if plan.home == plan.old or plan.home.is_relative_to(plan.old) or plan.old.is_relative_to(plan.home):
        raise ValueError("The old HOME and the OS user's home must be separate directories")
    users = processes_using([plan.old]) if plan.old.exists() else []
    if plan.old.is_dir():
        from scripts.migrate_instance_staging import Planner
        framework = plan.old / '.local/state/tfi-portal'
        planner = Planner()
        for name in ('test-framework', 'test-slots'):
            source = framework / name
            if source.exists():
                target = instance.directory('runtime') / name
                if name == 'test-slots':
                    plan.discard(source, 'obsolete test slot locks')
                else:
                    planner.expand(source, target, 'test-framework', 'instance runtime state')
                plan.aliases.append({'source': str(source), 'target': str(target), 'kind': 'test-framework'})
        plan.moves.extend(planner.moves)
        plan.conflicts.extend(planner.conflicts)
        for child in sorted(os.listdir(plan.old)):
            merge(plan, child)
        plan_ssh(plan)
        plan_codex(plan, users)
        plan_instance_data(plan, instance)
        plan_avatars(plan, instance)
    if scan_files or files is not None:
        plan_hardcoded(plan, instance, files)
    return plan, users


def report_for(plan: HomePlan, users: list[dict], protected, *, apply: bool, root: Path) -> Report:
    report = Report("migrate_instance_home", str(root), apply)
    report.add_moves(plan.moves)
    protected = set(protected)
    move_bytes = sum(tree_size(Path(move.source)) for move in plan.moves) if not apply else None
    report.sections.update({
        "old_home": str(plan.old), "home": str(plan.home),
        "aliases": [{"source": str(plan.old), "target": str(plan.home), "kind": "home"}, *plan.aliases],
        "summary": {
            "moves": len(plan.moves), "move_bytes": move_bytes,
            "discards": len(plan.discards), "discard_bytes": sum(item["bytes"] for item in plan.discards),
            "conflicts": len(plan.conflicts), "report_only": len(plan.report_only),
            "old_home_bytes": tree_size(plan.old) if plan.old.exists() and not apply else None,
            "files_to_rewrite": len(plan.hardcoded.get("files_to_rewrite", [])),
            "blocking_processes": len([u for u in users if u["pid"] not in protected]),
        },
        "cache_merge": plan.cache_merge,
        "discards": plan.discards,
        "conflicts": plan.conflicts,
        "config_differences": plan.configs,
        "report_only": plan.report_only,
        "ssh": plan.ssh,
        "codex": plan.codex,
        "instance_data": plan.instance_data,
        "avatars": plan.avatars,
        "hardcoded_old_home_paths": plan.hardcoded,
        "processes": {
            "blocking": [u for u in users if u["pid"] not in protected],
            "protected": [u for u in users if u["pid"] in protected],
        },
    })
    return report


def _delete(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif os.path.lexists(path):
        path.unlink()


def apply(instance: InstanceManifest, *, home: Path | None = None, protected=(), check_services: bool = True) -> Report:
    """Check the live state, then merge. Renames are reversed if one fails; deletions follow them."""
    if check_services:
        try:
            require_offline(instance)
        except RuntimeError:
            require_offline(instance, running=("postgres",))
    require_protected(protected)
    plan, users = build_plan(instance, home=home, protected=protected)
    blocking = [user for user in users if user["pid"] not in set(protected)]
    if blocking:
        raise RuntimeError("Processes still use the old HOME: " + json.dumps(blocking[:20], ensure_ascii=False))
    for move in plan.moves:
        problem = check_move(move)
        if problem:
            raise RuntimeError(f"{problem}: {move.source} -> {move.target}")
    apply_moves(plan.moves)
    # Every rename succeeded; now the copies that are redundant or obsolete go.
    for item in plan.discards:
        _delete(Path(item["path"]))
    codex_deleted = False
    if plan.codex.get("present"):
        # Re-checked here, immediately before deleting credentials; protected processes count too.
        codex = Path(plan.codex["path"])
        if not processes_using([codex]):
            _delete(codex)
            codex_deleted = True
    rewritten = []
    for entry in plan.hardcoded.get("files_to_rewrite", []):
        path = Path(entry["path"])
        # The rewrite decision uses the planning-time state, before the moves.
        rewritten.append({"path": str(path), "replacements": _rewrite_planned(path, plan)})
    directories = [Path(directory) for directory, _folders, _files in os.walk(plan.old)] if plan.old.exists() else []
    removed = remove_empty_directories(directories)
    report = report_for(plan, users, protected, apply=True, root=instance.root)
    remaining = sorted(str(path.relative_to(plan.old)) for path in plan.old.rglob("*")) if plan.old.exists() else []
    report.sections.update({"rewritten": rewritten, "removed_empty_directories": len(removed),
                            "codex_deleted": codex_deleted,
                            "old_home_removed": not plan.old.exists(), "remaining_in_old_home": remaining[:500],
                            "remaining_count": len(remaining)})
    return report


def _rewrite_planned(path: Path, plan: HomePlan) -> int:
    planned = {item for entry in plan.hardcoded["files_to_rewrite"] if entry["path"] == str(path)
               for item in entry["paths"]}
    pattern = _path_pattern(plan.old)
    text = path.read_text(encoding="utf-8")
    count = 0

    def substitute(match):
        nonlocal count
        if match.group(0) not in planned:
            return match.group(0)
        count += 1
        return str(plan.home) + match.group(0)[len(str(plan.old)):]

    after = pattern.sub(substitute, text)
    if count:
        with path.open("r+b") as stream:
            stream.write(after.encode("utf-8"))
            stream.truncate()
    return count


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parse_common(parser)
    args = parser.parse_args(argv)
    instance = instance_for(args)
    if args.apply:
        report = apply(instance, protected=args.protected_pid)
    else:
        plan, users = build_plan(instance, protected=args.protected_pid)
        report = report_for(plan, users, args.protected_pid, apply=False, root=instance.root)
    document = write_report(args.report, report)
    print(json.dumps({"mode": document["mode"], **document["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
