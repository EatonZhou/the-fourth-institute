"""Remove an old-layout instance's emptied directories and point its backup at the new layout (one time).

After the staging split, the Lineage moves and the HOME merge, the old layout's
directories under the instance root hold only leftovers. This command:

* deletes ``data-roots/`` once what is left in it is empty directories or
  files of the old layout that nothing reads (``OBSOLETE_INSTANCE_ENTRIES``);
  anything else is listed and the directory stays;
* deletes the old empty ``provider-state/`` beside ``data/``;
* lists, never deletes, ``maintenance/`` and ``migration/`` (their owner
  decides), and reports the state of ``config/``, ``installation.json``,
  ``accounts/`` and ``home/``, which other steps remove;
* rewrites the backup program's configuration so it names the new locations:
  the database environment file in ``secrets/``, the manifest, site settings,
  ``secrets/`` and each area; old staging and deployment paths go to where they
  moved; nothing under ``cache/`` or ``runtime/``. References that the backup
  program resolved through the retired ``~/.config/tfi/portal_paths.json`` are
  written as absolute paths.

Planning is the default; ``--apply`` checks in the same command that the
instance services are stopped (PostgreSQL may run).
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import sys
import tempfile

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from management_common.instance_manifest import AREAS, InstanceManifest
from scripts.instance_storage_migration import (
    Move, PrefixMap, Report, lexists, read_moves, remove_empty_directories, require_offline, require_protected,
    tree_size, write_report,
)
from scripts.migrate_instance_staging import OBSOLETE_INSTANCE_ENTRIES, OBSOLETE_STAGING_LINKS, read_instance

PLACEHOLDER = re.compile(r"\$\{(deployment(?:\.[A-Za-z_][A-Za-z_0-9]*)+|user_home)\}")
OWNER_DIRECTORIES = ("maintenance", "migration")
STATUS_ONLY = {
    "config": "removed when step 1 is closed",
    "installation.json": "removed when step 1 is closed",
    "home": "removed by migrate_instance_home",
    "accounts": "account-dir bridges, removed by the account-dir cleanup",
}


def _listing(path: Path, *, sizes: bool = False) -> list[dict]:
    if not path.is_dir() or path.is_symlink():
        return []
    result = []
    for entry in sorted(path.iterdir()):
        item = {"name": entry.name, "kind": "link" if entry.is_symlink() else "directory" if entry.is_dir() else "file"}
        if sizes:
            item["bytes"] = tree_size(entry)
        result.append(item)
    return result


# -- data-roots -------------------------------------------------------------------


def _obsolete(root: Path) -> dict[Path, str]:
    """Old-layout files known to be unread, with the reason; the signing keys only when replaced."""
    data_roots = root / "data-roots"
    instances, staging = data_roots / "instances", data_roots / "staging"
    result = {}
    replaced_key = (root / "runtime" / "s" / "resource-rpc-signing.key").is_file()
    for relative, reason in OBSOLETE_INSTANCE_ENTRIES.items():
        path = instances / relative
        if not lexists(path) or (relative.endswith("resource-rpc-signing.key") and not replaced_key):
            continue
        if relative.endswith("/uploads") and not path.is_symlink():
            continue
        result[path] = reason
    for name, reason in OBSOLETE_STAGING_LINKS.items():
        if (staging / name).is_symlink():
            result[staging / name] = reason
    return result


def plan_data_roots(root: Path, moved: PrefixMap | None = None) -> dict:
    """What would be left; entries under ``moved`` sources count as gone (a plan made before the moves)."""
    data_roots = root / "data-roots"
    if not data_roots.is_dir():
        return {"path": str(data_roots), "state": "absent", "obsolete": [], "remaining": []}
    obsolete = _obsolete(root)
    moved = moved or PrefixMap([])
    remaining = []
    for directory, folders, files in os.walk(data_roots, followlinks=False):
        here = Path(directory)
        if here in obsolete or moved.match(str(here)):
            folders[:] = []
            continue
        kept = []
        for name in folders:
            path = here / name
            if path in obsolete or moved.match(str(path)):
                continue
            if path.is_symlink():
                remaining.append(str(path))
            else:
                kept.append(name)
        folders[:] = kept
        for name in files:
            path = here / name
            if path not in obsolete and not moved.match(str(path)):
                remaining.append(str(path))
    return {
        "path": str(data_roots),
        "state": "removable" if not remaining else "blocked",
        "obsolete": [{"path": str(path), "reason": reason} for path, reason in sorted(obsolete.items())],
        "remaining_count": len(remaining),
        "remaining": remaining[:500],
        "remaining_top": sorted({str(Path(item).relative_to(data_roots)).split("/")[0] + "/" +
                                 (str(Path(item).relative_to(data_roots)).split("/") + [""])[1] for item in remaining}),
    }


def remove_data_roots(root: Path) -> dict:
    data_roots = root / "data-roots"
    plan = plan_data_roots(root)
    if plan["state"] == "absent":
        return {"state": "absent"}
    removed = []
    for item in plan["obsolete"]:
        path = Path(item["path"])
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            # Only directories of the old layout listed as obsolete, emptied or not.
            shutil.rmtree(path)
        removed.append(str(path))
    directories = [Path(directory) for directory, _folders, _files in os.walk(data_roots, followlinks=False)]
    removed += remove_empty_directories(directories)
    return {"state": "removed" if not data_roots.exists() else "kept: entries remain",
            "removed": removed, "remaining": plan_data_roots(root).get("remaining", [])[:200]}


# -- backup configuration ------------------------------------------------------------


def _locator(environment=os.environ) -> Path:
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    return Path(environment.get("PORTAL_PATHS_CONFIG") or home / ".config/tfi/portal_paths.json")


def resolve_placeholders(value: str, deployment: dict | None, home: Path) -> str:
    def replace(match):
        key = match.group(1)
        if key == "user_home":
            return str(home)
        if deployment is None:
            raise ValueError("The backup configuration names deployment paths but the old deployment file is gone")
        resolved = deployment
        for part in key.split(".")[1:]:
            resolved = resolved[part]
        return str(resolved).rstrip("/")
    return PLACEHOLDER.sub(replace, value)


class BackupPaths:
    """Where an old backup path is now, by the migration's moves and the old layout's own rules."""

    def __init__(self, instance: InstanceManifest, staging: Path | None, moves: list[Move], home: Path):
        root = instance.root
        self.instance, self.root, self.home = instance, root, home
        self.staging = staging
        self.instances = staging.parent / "instances" if staging else root / "data-roots" / "instances"
        fixed = [
            (root / "data-roots" / "secrets", root / "secrets"),
            (root / "config" / "postgres.env", root / "secrets" / "postgres.env"),
            (root / "home" / ".dirtybase" / "portal" / "session_signing.key", root / "secrets" / "session_signing.key"),
            (root / "home" / ".dirtybase" / "portal" / "two_factor.key", root / "secrets" / "two_factor.key"),
        ]
        self.map = PrefixMap([*((m.source, m.target) for m in moves), *((str(a), str(b)) for a, b in fixed)])
        protocol = [root / "instance.json", root / "site.json", root / "secrets"]
        self.protocol = protocol
        self.areas = [instance.area(area) for area in AREAS]
        self.moves = moves

    def split(self, path: Path) -> list[Path] | None:
        """Old containers whose content went to several places; None when not a container."""
        root = self.root
        if path in (root / "config", root / "installation.json"):
            return [root / "instance.json", root / "site.json"]
        if path == root / "provider-state" or path == root / "home" / ".codex":
            return []
        if path == root / "home" / ".ssh":
            return [self.home / ".ssh"]
        if path == self.instances or (self.staging is not None and path in (self.staging, self.staging / "shared")):
            targets = []
            for move in self.moves:
                if Path(move.source).is_relative_to(path):
                    target = Path(move.target)
                    area = next((a for a in self.areas if target.is_relative_to(a)), None)
                    if area is not None and area not in targets:
                        targets.append(area)
            if path != self.instances and path.exists():
                # Entries left in the staging root for their owners are still backed up;
                # what is left of the deployment directory is deleted with data-roots/.
                targets.append(path)
            return targets
        return None

    def locate(self, path: Path) -> list[Path]:
        parts = self.split(path)
        if parts is not None:
            return parts
        mapped = self.map.map(str(path))
        return [Path(mapped)] if mapped else [path]

    def old_layout(self, path: Path) -> bool:
        roots = [self.root, self.instances, *([self.staging] if self.staging is not None else [])]
        return any(path == root or path.is_relative_to(root) for root in roots) or self.map.match(str(path)) is not None

    def unbacked(self, path: Path) -> bool:
        return any(path == d or path.is_relative_to(d) for d in (self.root / "cache", self.root / "runtime"))


def _source_list(items: list, paths: BackupPaths, deployment, changes: list, label: str) -> list:
    result: list[dict] = []
    seen: dict[str, dict] = {}
    for index, item in enumerate(items):
        original = item["path"]
        resolved = Path(resolve_placeholders(original, deployment, paths.home))
        located = paths.locate(resolved)
        new_items = []
        for path in located:
            if paths.unbacked(path):
                changes.append({"at": f"{label}[{index}]", "old": original, "new": None, "why": "cache/ and runtime/ are not backed up"})
                continue
            if item.get("required") is not True and not lexists(path) and not lexists(resolved):
                changes.append({"at": f"{label}[{index}]", "old": original, "new": None, "why": "optional source no longer exists"})
                continue
            new_items.append({**item, "path": str(path)})
        if not located:
            changes.append({"at": f"{label}[{index}]", "old": original, "new": None, "why": "old-layout location with no successor"})
        for new in new_items:
            if new["path"] in seen:
                seen[new["path"]]["required"] = bool(seen[new["path"]].get("required")) or bool(new.get("required"))
                if new["path"] != original:
                    changes.append({"at": f"{label}[{index}]", "old": original, "new": new["path"], "why": "merged with an existing source"})
                continue
            seen[new["path"]] = new
            result.append(new)
            if new["path"] != original:
                changes.append({"at": f"{label}[{index}]", "old": original, "new": new["path"], "why": "new location"})
    return result


def _covered(path: str, sources: list[dict]) -> bool:
    return any(path == s["path"] or path.startswith(s["path"].rstrip("/") + "/") for s in sources)


def rewrite_backup_config(config: dict, paths: BackupPaths, deployment: dict | None) -> tuple[dict, list[dict]]:
    updated = copy.deepcopy(config)
    changes: list[dict] = []
    for name, profile in (updated.get("profiles") or {}).items():
        label = f"profiles.{name}"
        sources = _source_list(profile.get("sources") or [], paths, deployment, changes, label + ".sources")
        required = list(paths.protocol)
        if name == "content":
            required += paths.areas
        for path in required:
            if lexists(path) or path in paths.protocol or path.parent == paths.root / "data":
                if not _covered(str(path), sources):
                    sources.append({"path": str(path), "required": True})
                    changes.append({"at": label + ".sources", "old": None, "new": str(path), "why": "instance content the protocol backs up"})
        profile["sources"] = sources
        excludes = []
        for index, pattern in enumerate(profile.get("excludes") or []):
            if not PLACEHOLDER.match(pattern) and not pattern.startswith("/"):
                excludes.append(pattern)
                continue
            resolved = resolve_placeholders(pattern, deployment, paths.home)
            prefix, star, rest = resolved.partition("*")
            base = prefix.rstrip("/")
            located = paths.locate(Path(base)) if base else []
            if base and not paths.old_layout(Path(base)):
                # A guard on a host location stays, whatever the sources are now.
                kept = [resolved]
            else:
                kept = [str(p) + resolved[len(base):] for p in located
                        if not paths.unbacked(p) and _covered(str(p), sources)]
            if not kept:
                changes.append({"at": f"{label}.excludes[{index}]", "old": pattern, "new": None, "why": "excluded location is no longer backed up"})
            for value in kept:
                if value not in excludes:
                    excludes.append(value)
                if value != pattern:
                    changes.append({"at": f"{label}.excludes[{index}]", "old": pattern, "new": value, "why": "new location"})
        profile["excludes"] = excludes
    postgres = paths.instance.document["dependencies"]["postgres"]
    for index, entry in enumerate(updated.get("live_postgresql") or []):
        wanted = {
            "environment_file": str(paths.root / "secrets" / "postgres.env"),
            "dump_binary": str(Path(postgres["bin"]) / "pg_dump"),
            "restore_binary": str(Path(postgres["bin"]) / "pg_restore"),
            "library_path": postgres["lib"],
        }
        for key, value in wanted.items():
            if key in entry and entry[key] != value:
                changes.append({"at": f"live_postgresql[{index}].{key}", "old": entry[key], "new": value, "why": "manifest and secrets/"})
                entry[key] = value
    # Whatever still names the retired deployment file is written out.
    def resolve_all(value, at):
        if isinstance(value, dict):
            return {k: resolve_all(v, f"{at}.{k}") for k, v in value.items()}
        if isinstance(value, list):
            return [resolve_all(v, f"{at}[{i}]") for i, v in enumerate(value)]
        if isinstance(value, str) and PLACEHOLDER.search(value):
            new = resolve_placeholders(value, deployment, paths.home)
            changes.append({"at": at.lstrip("."), "old": value, "new": new, "why": "absolute path; the deployment file is retired"})
            return new
        return value
    updated = resolve_all(updated, "")
    return updated, changes


def backup_config_path(instance: InstanceManifest) -> Path | None:
    program = instance.document["dependencies"].get("backup")
    if not program:
        return None
    return Path(program).resolve().parents[1] / "config" / "config.yaml"


def write_yaml(path: Path, value: dict) -> None:
    import yaml
    from management_common.configuration_files import preserve_config_permissions

    descriptor, name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            preserve_config_permissions(temporary, path)
            yaml.safe_dump(value, stream, sort_keys=False, allow_unicode=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def plan_backup(instance: InstanceManifest, staging: Path | None, moves: list[Move], config_path: Path | None) -> dict:
    import yaml

    result: dict = {"program": instance.document["dependencies"].get("backup")}
    result["program_exists"] = bool(result["program"]) and Path(result["program"]).is_file()
    if config_path is None or not config_path.is_file():
        result["config"] = None
        return result
    text = config_path.read_text(encoding="utf-8")
    config = yaml.safe_load(text)
    deployment = None
    if PLACEHOLDER.search(text) and re.search(r"\$\{deployment\.", text):
        locator = _locator()
        deployment = json.loads(locator.read_text(encoding="utf-8")) if locator.is_file() else None
        result["deployment_file"] = str(locator) if deployment is not None else None
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    paths = BackupPaths(instance, staging, moves, home)
    updated, changes = rewrite_backup_config(config, paths, deployment)
    result.update({"config": str(config_path), "changes": changes, "changed": updated != config, "_updated": updated})
    return result


# -- the command ----------------------------------------------------------------------


def plan(instance: InstanceManifest, staging: Path | None, moves: list[Move], *, owner_decisions: Path | None,
         backup_config: Path | None) -> dict:
    root = instance.root
    owners = {}
    recorded = json.loads(owner_decisions.read_text(encoding="utf-8")) if owner_decisions and owner_decisions.is_file() else {}
    for name in OWNER_DIRECTORIES:
        path = root / name
        listing = _listing(path)
        owners[name] = {"path": str(path), "entries": len(listing), "listing": listing,
                        "recorded_in_owner_decisions": len(recorded.get(str(path), [])), "action": "list only; owner decides"}
    status = {name: {"exists": lexists(root / name), "note": note} for name, note in STATUS_ONLY.items()}
    old_provider_state = root / "provider-state"
    status["provider-state"] = {"exists": lexists(old_provider_state), "entries": len(_listing(old_provider_state)),
                                "note": "old empty directory; removed when empty (data/provider-state is the area)"}
    accounts = root / "accounts"
    if accounts.is_dir():
        entries = []
        for directory, folders, files in os.walk(accounts, followlinks=False):
            for name in (*folders, *files):
                path = Path(directory) / name
                entry = {"path": str(path), "kind": "link" if path.is_symlink() else "directory" if path.is_dir() else "file"}
                if path.is_symlink():
                    entry["target"] = os.readlink(path)
                try:
                    if path.lstat().st_uid != os.getuid():
                        entry["owner_uid"] = path.lstat().st_uid
                        entry["note"] = "owned by another user; removal needs that user"
                except OSError:
                    pass
                entries.append(entry)
        status["accounts"]["entries"] = entries
    stray = [entry for entry in _listing(root) if entry["name"].startswith(".") and entry["kind"] == "file"]
    status["root_dot_files"] = {"entries": stray, "note": ".configuration.lock is used by instance_services; others are listed"}
    backup = plan_backup(instance, staging, moves, backup_config if backup_config else backup_config_path(instance))
    pending = PrefixMap((move.source, move.target) for move in moves
                        if lexists(move.source) and Path(move.source).is_relative_to(root / "data-roots"))
    return {"data_roots": plan_data_roots(root, pending), "owner_directories": owners, "status": status, "backup": backup}


def apply(instance: InstanceManifest, result: dict, *, protected: list[int]) -> dict:
    require_protected(protected)
    require_offline(instance, running=("postgres",))
    outcome = {}
    backup = result["backup"]
    if backup.get("changed"):
        write_yaml(Path(backup["config"]), backup["_updated"])
        outcome["backup_config"] = "rewritten"
    old_provider_state = instance.root / "provider-state"
    if old_provider_state.is_dir() and not old_provider_state.is_symlink():
        outcome["provider-state"] = "removed" if remove_empty_directories([old_provider_state]) else "kept: not empty"
    outcome["data_roots"] = remove_data_roots(instance.root)
    return outcome


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True)
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--protected-pid", type=int, action="append", default=[])
    parser.add_argument("--staging-root", type=Path, help="the old staging root, when the manifest no longer names it")
    parser.add_argument("--moves", type=Path, action="append", default=[],
                        help="reports of the other migration commands (their moves and aliases)")
    parser.add_argument("--owner-decisions", type=Path, help="the recorded owner-decision listing")
    parser.add_argument("--backup-config", type=Path, help="the backup program's configuration file")
    args = parser.parse_args(argv)
    instance, _document, staging = read_instance(args.root, args.staging_root)
    moves = read_moves(args.moves)
    result = plan(instance, staging, moves, owner_decisions=args.owner_decisions, backup_config=args.backup_config)
    report = Report("migrate_instance_old_directories", str(instance.root), bool(args.apply))
    report.sections.update({key: value for key, value in result.items() if key != "backup"})
    report.sections["backup"] = {key: value for key, value in result["backup"].items() if key != "_updated"}
    report.sections["summary"] = {
        "data_roots": result["data_roots"]["state"],
        "data_roots_remaining": result["data_roots"].get("remaining_count", 0),
        "obsolete_entries": len(result["data_roots"]["obsolete"]),
        "backup_changes": len(result["backup"].get("changes", [])),
        "owner_entries": {name: item["entries"] for name, item in result["owner_directories"].items()},
    }
    if args.apply:
        report.sections["apply"] = apply(instance, result, protected=args.protected_pid)
    write_report(args.report, report)
    print(json.dumps({"mode": "apply" if args.apply else "dry-run", **report.sections["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
