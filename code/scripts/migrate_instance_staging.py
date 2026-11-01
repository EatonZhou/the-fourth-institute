"""Split an old-layout instance's staging root into its areas (one time).

Old layouts kept application data, uploads, caches, Provider state, locks and
sockets under one staging root (``migration.staging_root`` in the manifest) and
deployment files under ``data-roots/instances`` beside it. The protocol keeps
them by kind: application data in the features area, uploads in the uploads
area, Provider state in the provider-state area, rebuildable files in
``cache/`` and run state in ``runtime/``. This command moves each known
directory to the place the code now reads it from (``RULES`` names the code
that reads each target). Everything is a rename on one filesystem; a target that
already exists is merged entry by entry and a colliding entry stays where it
was, listed as a conflict.

Directories no code reads are listed with a suggested place
(``needs_alignment``) and move only with ``--include-suggested``. Loose files
and other top-level entries are listed with the Resident whose files mention
them, when one is found, and are never moved or deleted here: their owners
decide.

Planning is the default. ``--apply`` checks, in the same command, that the
instance services are stopped (PostgreSQL may run; it is needed to read the
account registrations when Kimi sessions follow their moved state), that the
protected processes are alive, and that no other process uses the old
directories; then it moves, makes Provider session records follow, and removes
``migration`` from ``instance.json``. The report's ``moves`` and ``aliases``
feed ``migrate_instance_database_paths``.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from management_common.instance_manifest import AREAS, InstanceManifest, validate
from scripts.instance_storage_migration import (
    Move, PrefixMap, Report, apply_moves, check_move, lexists, processes_using, remove_empty_directories,
    require_offline, require_protected, rewrite_text_files, tree_size, walk_files, write_report,
)


@dataclass(frozen=True)
class Rule:
    """``source`` (relative to the old directory) belongs at ``base:target``."""

    source: str
    base: str  # an area, or the fixed directory "cache" or "runtime"
    target: str
    basis: str  # the code that reads the target
    needs_alignment: bool = False


# Old staging root.
RULES = (
    Rule("shared/projection_applications", "features", "projection_applications",
         "management_portal/workbench_projections/application_build.py:905"),
    Rule("shared/portal_paper_workspace", "features", "paper_workspace", "management_portal/paper_store.py:36"),
    Rule("shared/knowledge_archive_jobs", "features", "knowledge_archive_jobs",
         "management_portal/knowledge_archive_projection.py:11"),
    Rule("shared/portal_lineages", "features", "lineage_data",
         "management_portal/society_service/lineage_aggregate.py:27, invariant_locks.py:18"),
    Rule("shared/portal_explorations", "features", "explorations",
         "management_portal/society_service/lineage_handoff_preparation.py:16"),
    Rule("shared/effect_artifacts", "features", "effect_artifacts",
         "management_portal/society_service/effect_artifacts.py:24"),
    Rule("shared/codex_limit_usage", "features", "codex_limit_usage", "scripts/codex_daily_limit_snapshot.py:22"),
    Rule("shared/codex_weekly_usage", "features", "codex_weekly_usage", "scripts/codex_weekly_quota_usage.py:26"),
    Rule("portal_execution", "features", "execution", "management_portal/execution/config.py:21"),
    Rule("shared/portal_execution", "features", "execution", "management_portal/execution/config.py:21"),
    Rule("shared/exploration_runs", "features", "exploration_runs", "existing registered Workspace locations"),
    Rule("shared/portal_uploads", "uploads", "",
         "management_portal/web_accounts.py:48, society_service/workspace_contracts.py:11-12, "
         "knowledge_item_chat_backend.py:18"),
    Rule("shared/portal_cache/article_builds", "cache", "article_builds", "management_portal/article_build_results.py:16"),
    Rule("shared/portal_cache/devices", "cache", "devices", "management_portal/device_identity_backend.py:30"),
    Rule("shared/portal_cache/agent_status", "cache", "agent_status", "management_portal/status_cache_store.py:15"),
    Rule("shared/portal_data/ui-association-locks", "runtime", "locks/ui-association",
         "management_portal/portal_ui_state.py:20"),
    Rule("shared/portal_data/wall-locks", "runtime", "locks/wall", "management_portal/wall_backend.py:36"),
    Rule("shared/portal_runtime/resource-auto-registration.last-run", "runtime", "resource-auto-registration.last-run",
         "management_portal/resource_auto_registration.py:24"),
    Rule("shared/kimi-code-portal", "provider-state", "kimi-code", "management_portal/kimi_code_server.py:23"),
    Rule("shared/opencode", "provider-state", "opencode", "management_portal/opencode_client.py:29"),
    Rule("shared/dsh", "provider-state", "dsh", "management_portal/dsh_client.py:29"),
    Rule("shared/agent-config", "provider-state", "agent-config", "management_portal/execution/native_options.py:138"),
    Rule("shared/script_runs", "runtime", "script_runs", "management_portal/society_service/script_runs/runtime.py:11"),
    Rule("portal_locks/codex-app-server-threads", "runtime", "codex-app-server-threads",
         "management_portal/codex_app_server_threads.py:37"),
    Rule("portal_locks/dsh", "runtime", "dsh", "management_portal/dsh_client.py:30"),
    Rule("portal_locks/opencode", "runtime", "opencode", "management_portal/opencode_client.py:30"),
    Rule("portal_locks/kimi-tools", "runtime", "kimi-tools", "management_portal/kimi_portal_tools.py:54"),
)
# Other entries of portal_locks/ are run state: runtime/<name> (Plan step 6).
LOCKS_DIRECTORY = "portal_locks"

# Old deployment directory beside the staging root (data-roots/instances).
INSTANCE_RULES = (
    Rule("uploads", "uploads", "", "management_portal/resources/isa.py:402"),
    Rule("login", "runtime", "login", "management_portal/execution/account_login.py:293"),
    Rule("settings", "runtime", "settings",
         "management_common/embedding_feature.py:13, distribution/initialization.py:125"),
    Rule("dev/test-results", "cache", "test-results", "management_portal/page_bootstrap.py:30"),
    Rule("cache/presentation-preview", "runtime", "cache/presentation-preview",
         "management_portal/presentation_preview.py:72 (reads runtime/cache, not the instance cache/)", True),
    Rule("tools/libreoffice", "runtime", "tools/libreoffice",
         "management_portal/presentation_preview.py:33, scripts/install_portal_libreoffice.py:68 "
         "(a program kept in runtime/)", True),
)
# Files of the old deployment layout that nothing reads any more; deleted by
# migrate_instance_old_directories once the moves are done.
OBSOLETE_INSTANCE_ENTRIES = {
    "deployments.json": "deployments are in instance.json",
    "user-router-source.json": "the old user router is replaced by the gateway service",
    "sockets": "sockets are in runtime/s",
    "systemd": "units are generated from instance.json",
    "dev/portal-services.lock": "the old service lock",
    "release/portal-services.lock": "the old service lock",
    "dev/resource-rpc-signing.key": "the signing key is runtime/s/resource-rpc-signing.key",
    "release/resource-rpc-signing.key": "the signing key is runtime/s/resource-rpc-signing.key",
    "dev/uploads": "link to the old uploads",
    "release/uploads": "link to the old uploads",
}
# Old-layout links inside the staging root.
OBSOLETE_STAGING_LINKS = {"portal_instances": "link to data-roots/instances"}

# Directories under shared/ that no current code reads: a suggestion only.
SUGGESTIONS = {
    "shared/portal_chat_workspaces": ("features", "chat_workspaces", "listed in the backup configuration"),
    "shared/portal_knowledge_item_chats": ("features", "knowledge_item_chats",
                                           "listed in the backup configuration; static/portal_file_links.js:216"),
    "shared/portal_deliveries": ("features", "deliveries", "old delivery records"),
    "shared/codex_token_usage": ("features", "codex_token_usage", "old usage reports"),
    "shared/codex-app-server-build": ("cache", "codex-app-server-build", "a build tree"),
    "shared/agent_status": ("cache", "agent_status", "an older status cache"),
    "shared/locks": ("runtime", "locks/llm-provider", "stale Provider locks"),
    "shared/wall": ("runtime", "locks/wall-index", "stale WALL index locks"),
    "shared/portal_ui_state.json.association-locks": ("runtime", "locks/ui-association-legacy", "stale locks"),
    "shared/portal_locks": ("runtime", "locks/portal-legacy", "a stale lock"),
}
# Partly mapped containers: what is left inside them is listed entry by entry.
OWNER_SHARED = ("shared/portal_data", "shared/portal_runtime", "shared/portal_cache")
# A Resident is named as the owner only when most mentions are its own.
OWNER_SHARE = 0.6
PROVIDER_STATE_RULES = {"shared/kimi-code-portal", "shared/opencode", "shared/dsh", "shared/agent-config"}
KIMI_RULE = "shared/kimi-code-portal"
RESIDENT_ID = re.compile(r"resident_[0-9a-f]{12}")


# -- the instance -------------------------------------------------------------------


def read_instance(root: Path, staging_root: Path | None = None) -> tuple[InstanceManifest, dict, Path | None]:
    """The manifest without its pending ``migration`` and the staging root it named."""
    root = Path(root).resolve()
    source = root / "instance.json"
    if source.is_symlink():
        raise ValueError("Instance manifest must not be a symbolic link")
    document = json.loads(source.read_text(encoding="utf-8"))
    pending = (document.get("migration") or {}).get("staging_root")
    selected = Path(pending) if pending else (Path(staging_root) if staging_root else None)
    if pending and staging_root and Path(staging_root) != Path(pending):
        raise ValueError("--staging-root differs from the staging root the manifest names")
    current = {key: value for key, value in document.items() if key != "migration"}
    return validate(root, current), document, selected


def destination(instance: InstanceManifest, base: str, relative: str) -> Path:
    if base in AREAS:
        root = instance.area(base)
    elif base in ("cache", "runtime"):
        root = instance.directory(base)
    else:
        raise ValueError(f"Unknown destination {base}")
    return root / relative if relative else root


def reference(base: str, relative: str) -> str:
    return f"{base}:{relative}" if base in AREAS else f"{base}/{relative}".rstrip("/")


# -- planning -----------------------------------------------------------------------


class Planner:
    """Renames planned so far; a planned target counts as existing for later ones."""

    def __init__(self) -> None:
        self.moves: list[Move] = []
        self.conflicts: list[dict] = []
        self.aliases: list[Move] = []
        self._planned: dict[str, str] = {}

    def _existing(self, target: Path) -> Path | None:
        """What will be at ``target`` before this rename: a real path, or a planned source."""
        if lexists(target):
            return target
        for parent in (target, *target.parents):
            source = self._planned.get(str(parent))
            if source is not None:
                candidate = Path(source) / target.relative_to(parent)
                return candidate if lexists(candidate) else None
        return None

    def expand(self, source: Path, target: Path, kind: str, note: str, *, into: bool = False) -> None:
        """A rename, or into an existing directory a rename per entry that does not collide."""
        existing = self._existing(target)
        if existing is None and not into:
            self.moves.append(Move(str(source), str(target), kind, note))
            self._planned[str(target)] = str(source)
            return
        directory = source.is_dir() and not source.is_symlink()
        if directory and (existing is None or (existing.is_dir() and not existing.is_symlink())):
            self.aliases.append(Move(str(source), str(target), "merged-directory", note))
            for child in sorted(source.iterdir()):
                self.expand(child, target / child.name, kind, note)
            return
        self.conflicts.append({"source": str(source), "target": str(target), "kind": kind})


def _covered(relative: str, rules: list[Rule]) -> bool:
    return any(relative == rule.source or relative.startswith(rule.source + "/") for rule in rules)


def _parents(relative: str) -> list[str]:
    parts = relative.split("/")
    return ["/".join(parts[:index]) for index in range(1, len(parts))]


def staging_rules(staging: Path, *, include_suggested: bool) -> list[Rule]:
    rules = list(RULES)
    locks = staging / LOCKS_DIRECTORY
    if locks.is_dir() and not locks.is_symlink():
        for entry in sorted(locks.iterdir()):
            relative = f"{LOCKS_DIRECTORY}/{entry.name}"
            if not _covered(relative, rules):
                rules.append(Rule(relative, "runtime", entry.name, "Plan step 6: portal_locks/ goes to runtime/"))
    if include_suggested:
        rules += [Rule(source, base, target, "suggestion: " + why, True)
                  for source, (base, target, why) in SUGGESTIONS.items()]
    return rules


def _entry(path: Path, *, size: bool) -> dict:
    kind = "link" if path.is_symlink() else "directory" if path.is_dir() else "file"
    result = {"name": path.name, "path": str(path), "kind": kind}
    if size:
        result["bytes"] = tree_size(path)
    return result


def owner_hints(staging: Path, names: list[str], search_roots: list[Path]) -> dict[str, dict]:
    """Residents whose Lineage files mention ``staging/<name>``; a name with a Resident ID names it."""
    hints: dict[str, dict] = {}
    for name in names:
        found = RESIDENT_ID.findall(name)
        if found:
            hints[name] = {"residents": {found[0]: 1}, "evidence": "name"}
    program = shutil.which("rg")
    roots = [str(root) for root in search_roots if root.is_dir()]
    if not program or not roots or not names:
        return hints
    alternatives = "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))
    expression = r"staging/(" + alternatives + r")(?:[^\w.\-]|$)"
    completed = subprocess.run(
        [program, "--no-messages", "-o", "--with-filename", "--max-filesize", "2M", "-g", "!*.jsonl",
         "-g", "!node_modules", "-g", "!.git", "-e", expression, *roots],
        capture_output=True, text=True, timeout=1800, check=False)
    pattern = re.compile(expression)
    for line in completed.stdout.splitlines():
        path, _separator, match = line.rpartition(":staging/")
        found = pattern.match("staging/" + match)
        resident = re.search(r"/residents/(resident_[0-9a-f]{12})/", path)
        if not found or not resident:
            continue
        item = hints.setdefault(found.group(1), {"residents": {}, "evidence": "mentioned in Resident files"})
        item["residents"][resident.group(1)] = item["residents"].get(resident.group(1), 0) + 1
        item.setdefault("workspaces", {})[resident.group(1)] = path.split("/residents/")[0] + \
            f"/residents/{resident.group(1)}/workspace"
    return hints


def lineage_search_roots(instance: InstanceManifest) -> list[Path]:
    roots = []
    projects = instance.area("projects")
    if projects.is_dir():
        for project in sorted(projects.iterdir()):
            for folder in (project / "knowledge_base" / "agents", project / "agents"):
                if folder.is_dir() and not folder.is_symlink():
                    roots.append(folder)
    lineages = instance.area("lineages")
    if lineages.is_dir():
        roots += [entry for entry in sorted(lineages.glob("lineage_*")) if entry.is_dir() and not entry.is_symlink()]
    return roots


def plan(instance: InstanceManifest, staging: Path | None, *, include_suggested: bool = False,
         historical_roots: tuple[Path, ...] = (), sizes: bool = True, owners: bool = True) -> dict:
    planner = Planner()
    moves, conflicts, aliases = planner.moves, planner.conflicts, planner.aliases
    rule_pairs: list[tuple[str, str, str]] = []
    mapping: list[dict] = []
    unmapped: list[dict] = []
    scattered: list[dict] = []
    links: list[dict] = []
    deployment_directory = staging.parent / "instances" if staging is not None else None
    for label, directory, rules in (
            ("staging", staging, staging_rules(staging, include_suggested=include_suggested) if staging else []),
            ("instances", deployment_directory, list(INSTANCE_RULES))):
        if directory is None or not directory.is_dir():
            continue
        for rule in rules:
            source = directory / rule.source
            target = destination(instance, rule.base, rule.target)
            row = {"from": f"{label}:{rule.source}", "to": reference(rule.base, rule.target), "basis": rule.basis,
                   "needs_alignment": rule.needs_alignment, "present": lexists(source)}
            if row["present"]:
                if sizes:
                    row["bytes"] = tree_size(source)
                before = len(moves)
                # An area root is filled entry by entry, so several sources can share it.
                planner.expand(source, target, "staging-split", row["to"], into=not rule.target)
                row["renames"] = len(moves) - before
                rule_pairs.append((str(source), str(target), row["to"]))
            mapping.append(row)
    if staging is not None and staging.is_dir():
        rules = staging_rules(staging, include_suggested=include_suggested)
        shared = staging / "shared"
        for entry in sorted(shared.iterdir()) if shared.is_dir() else ():
            relative = f"shared/{entry.name}"
            if _covered(relative, rules):
                continue
            if any(relative == source for source in OWNER_SHARED) and entry.is_dir():
                # Partly mapped containers list what is left inside them.
                for child in sorted(entry.iterdir()):
                    child_relative = f"{relative}/{child.name}"
                    if not _covered(child_relative, rules):
                        unmapped.append({**_entry(child, size=sizes), "relative": child_relative,
                                         "suggestion": "owner decides (old records or personal data)",
                                         "needs_alignment": True})
                continue
            suggestion = SUGGESTIONS.get(relative)
            unmapped.append({**_entry(entry, size=sizes), "relative": relative,
                             "suggestion": reference(*suggestion[:2]) + " (" + suggestion[2] + ")" if suggestion
                             else "owner decides", "needs_alignment": True})
        for entry in sorted(staging.iterdir()):
            if entry.name in ("shared", LOCKS_DIRECTORY) or _covered(entry.name, rules):
                continue
            if entry.name in OBSOLETE_STAGING_LINKS and entry.is_symlink():
                links.append({"path": str(entry), "target": os.readlink(entry),
                              "reason": OBSOLETE_STAGING_LINKS[entry.name]})
                continue
            scattered.append(_entry(entry, size=sizes))
        if owners:
            hints = owner_hints(staging, [item["name"] for item in scattered], lineage_search_roots(instance))
            for item in scattered:
                hint = hints.get(item["name"])
                if hint:
                    residents = sorted(hint["residents"].items(), key=lambda pair: -pair[1])
                    share = residents[0][1] / sum(count for _resident, count in residents)
                    if share < OWNER_SHARE:
                        item["owner"] = {"ambiguous": True, "mentions": dict(residents[:8]),
                                         "evidence": hint["evidence"]}
                        continue
                    item["owner"] = {"resident_id": residents[0][0], "mentions": dict(residents[:8]),
                                     "evidence": hint["evidence"],
                                     "return_to": (hint.get("workspaces") or {}).get(residents[0][0])}
                else:
                    item["owner"] = "owner_unknown"
    # Paths written through the old link and the old roots name the same files;
    # one alias per mapped directory covers everything under it.
    if staging is not None and deployment_directory is not None:
        link = staging / "portal_instances"
        for source, target, note in rule_pairs:
            if Path(source).is_relative_to(deployment_directory):
                relative = Path(source).relative_to(deployment_directory)
                aliases.append(Move(str(link / relative), target, "old-link", note))
                if relative.parts[:1] == ("uploads",):
                    for deployment in ("dev", "release"):
                        aliases.append(Move(str(deployment_directory / deployment / relative), target,
                                            "old-link", note))
    for old_root in historical_roots:
        for source, target, note in rule_pairs:
            if staging is not None and Path(source).is_relative_to(staging):
                relative = Path(source).relative_to(staging)
                if not lexists(old_root / relative):
                    aliases.append(Move(str(old_root / relative), target, "historical-root", note))
        for item in scattered:
            if not lexists(old_root / item["name"]):
                aliases.append(Move(str(old_root / item["name"]), item["path"], "historical-root",
                                    "left in the staging root for its owner"))
    return {
        "staging_root": str(staging) if staging else None,
        "deployment_directory": str(deployment_directory) if deployment_directory else None,
        "mapping": mapping,
        "moves": moves,
        "conflicts": conflicts,
        "aliases": aliases,
        "unmapped_shared": unmapped,
        "scattered": scattered,
        "obsolete_links": links,
    }


def manifest_change(document: dict) -> dict | None:
    if "migration" not in document:
        return None
    return {"remove": {"migration": document["migration"]}}


def provider_state_targets(instance: InstanceManifest, result: dict) -> list[Path]:
    targets = []
    for rule in RULES:
        if rule.source in PROVIDER_STATE_RULES:
            target = destination(instance, rule.base, rule.target)
            targets.append(target)
    return targets


def provider_state_rewrites(paths: list[Path], mapping: PrefixMap, *, apply: bool) -> dict:
    files = [path for root in paths if root.is_dir() for path in walk_files(root)]
    return rewrite_text_files(files, mapping, apply=apply)


# -- apply --------------------------------------------------------------------------


def apply(instance: InstanceManifest, document: dict, staging: Path | None, result: dict, *,
          protected: list[int], include_suggested: bool, historical_roots: tuple[Path, ...]) -> dict:
    from management_common.configuration_files import atomic_json

    require_protected(protected)
    require_offline(instance, running=("postgres",))
    watched = [path for path in (staging, Path(result["deployment_directory"] or "")) if path and path.is_dir()]
    users = processes_using(watched)
    blocking = [user for user in users if user["pid"] not in set(protected)]
    if blocking:
        raise RuntimeError("Processes still use the old directories: " + json.dumps(blocking[:20]))
    # The plan is made again here, against the state just checked.
    current = plan(instance, staging, include_suggested=include_suggested, historical_roots=historical_roots,
                   sizes=False, owners=False)
    done = apply_moves(current["moves"])
    outcome = {"renamed": len(done), "conflicts": current["conflicts"], "protected_users": users}
    mapping = PrefixMap.from_moves([*current["moves"], *current["aliases"]])
    kimi_old = staging / KIMI_RULE if staging else None
    kimi_new = destination(instance, "provider-state", "kimi-code")
    if kimi_old is not None and any(Path(move.source).is_relative_to(kimi_old) for move in done):
        from management_portal.provider_session_relocation import relocate_provider_sessions
        outcome["provider_sessions"] = relocate_provider_sessions(kimi_old, kimi_new, apply=True)
    outcome["provider_state_rewrites"] = provider_state_rewrites(
        provider_state_targets(instance, current), mapping, apply=True)
    parents = {parent for move in done for parent in Path(move.source).parents
               if staging is not None and parent.is_relative_to(staging) and parent != staging}
    outcome["removed_empty_directories"] = remove_empty_directories(parents)
    if staging is not None and staging.is_dir():
        if not any(staging.iterdir()):
            staging.rmdir()
            outcome["staging_root"] = "removed"
        else:
            outcome["staging_root"] = "kept: entries remain for their owners"
    if "migration" in document:
        updated = {key: value for key, value in document.items() if key != "migration"}
        validate(instance.root, updated)
        atomic_json(instance.root / "instance.json", updated)
        outcome["manifest"] = "migration removed"
    return outcome


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, required=True, help="instance root")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True, help="plan only (default)")
    mode.add_argument("--apply", action="store_true", help="check the live state and move")
    parser.add_argument("--report", type=Path, help="write the JSON report here")
    parser.add_argument("--protected-pid", type=int, action="append", default=[])
    parser.add_argument("--staging-root", type=Path,
                        help="the old staging root, when the manifest no longer names it (a rerun)")
    parser.add_argument("--historical-root", type=Path, action="append", default=[],
                        help="an older location of the staging root that database records may still name")
    parser.add_argument("--include-suggested", action="store_true",
                        help="also move the directories listed with a suggested place")
    args = parser.parse_args(argv)
    instance, document, staging = read_instance(args.root, args.staging_root)
    historical = tuple(Path(path) for path in args.historical_root)
    result = plan(instance, staging, include_suggested=args.include_suggested, historical_roots=historical)
    report = Report("migrate_instance_staging", str(instance.root), bool(args.apply))
    report.add_moves(result.pop("moves"))
    aliases = result.pop("aliases")
    report.sections.update(result)
    report.sections["aliases"] = [{"source": a.source, "target": a.target, "kind": a.kind, "note": a.note}
                                  for a in aliases]
    report.sections["manifest_change"] = manifest_change(document)
    # Who uses the old directories now; apply refuses unless only protected processes remain.
    watched = [Path(path) for path in (result["staging_root"], result["deployment_directory"]) if path]
    report.sections["processes_now"] = processes_using([path for path in watched if path.is_dir()])
    report.sections["blocked_moves"] = [
        {"source": move["source"], "target": move["target"], "problem": problem}
        for move in report.moves if (problem := check_move(Move(**move)))]
    if staging is not None:
        kimi = staging / KIMI_RULE
        mapping = PrefixMap.from_moves([*(Move(**move) for move in report.moves), *aliases])
        sources = [kimi, *(staging / rule for rule in PROVIDER_STATE_RULES if rule != KIMI_RULE)]
        report.sections["provider_state_rewrites"] = provider_state_rewrites(
            [path for path in sources if path.is_dir()], mapping, apply=False)
    report.sections["summary"] = {
        "renames": len(report.moves),
        "bytes_moved": sum(row.get("bytes", 0) for row in result["mapping"] if row.get("present")),
        "conflicts": len(result["conflicts"]),
        "unmapped_shared": len(result["unmapped_shared"]),
        "scattered": len(result["scattered"]),
        "scattered_with_owner": sum(1 for item in result["scattered"]
                                    if isinstance(item.get("owner"), dict) and item["owner"].get("resident_id")),
        "scattered_ambiguous_owner": sum(1 for item in result["scattered"]
                                         if isinstance(item.get("owner"), dict) and item["owner"].get("ambiguous")),
        "needs_alignment": [row["from"] + " -> " + row["to"] for row in result["mapping"] if row["needs_alignment"]],
    }
    if args.apply:
        report.sections["apply"] = apply(instance, document, staging, result, protected=args.protected_pid,
                                         include_suggested=args.include_suggested, historical_roots=historical)
    document_out = write_report(args.report, report)
    print(json.dumps({"mode": document_out["mode"], **report.sections["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
