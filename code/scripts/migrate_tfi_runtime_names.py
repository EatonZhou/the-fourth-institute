#!/usr/bin/env python3
"""One-time runtime migration using explicit private names, never runtime aliases.

Default is inspection. Apply keeps exclusive backups before changes. Historical
messages and business bodies are not renamed. Database selection is explicit.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from management_common.file_lock import try_lock_descriptor
from management_portal.service_invocation_contract import digest, plan_fingerprint, PLAN_SCHEMA
from management_portal.society_service.seed_state_marker import MARKER_NAME, read_marker


def load_names(path):
    """Only this one-time tool consumes legacy spellings supplied out of band."""
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Private names manifest must be an object")
    aliases = value.get("environment_aliases", {})
    if not isinstance(aliases, dict):
        raise ValueError("environment_aliases must be an object")
    seen = set()
    for canonical, names in aliases.items():
        if not re.fullmatch(r"PORTAL_[A-Z0-9_]+", canonical):
            raise ValueError("Expected canonical environment destination")
        if not isinstance(names, list) or not names:
            raise ValueError("Expected explicit environment source names")
        for name in names:
            if (not isinstance(name, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", name)
                    or name.startswith("PORTAL_") or name in seen):
                raise ValueError("Invalid or ambiguous environment source name")
            seen.add(name)
    for key, canonical in (("plan_schemas", PLAN_SCHEMA), ("seed_markers", MARKER_NAME)):
        names = value.get(key, [])
        if (not isinstance(names, list) or any(not isinstance(n, str) or not n for n in names)
                or canonical in names):
            raise ValueError("Expected explicit retired " + key)
    for source, target in value.get("state_file_names", {}).items():
        if any(Path(n).name != n or n in {".", "..", ""} for n in (source, target)):
            raise ValueError("State filename mapping must use basenames")
    return value


def transform(value, fingerprints=None, *, environment_aliases=None):
    """Update owned execution fields, excluding message/prompt payloads."""
    names = {old: current for current, aliases in (environment_aliases or {}).items()
             for old in aliases}
    fingerprints = fingerprints or {}
    def visit(item):
        if isinstance(item, list):
            return [visit(child) for child in item]
        if not isinstance(item, dict):
            return item
        result = {}
        for key, child in item.items():
            if key in {"prompt", "content", "content_parts", "messages", "payload"}:
                result[key] = deepcopy(child)
            elif key == "resident_environment" and isinstance(child, dict):
                converted = {}
                for old, val in child.items():
                    current = names.get(old, old)
                    if current in converted and converted[current] != val:
                        raise ValueError("Conflicting old and canonical environment fields")
                    converted[current] = val
                result[key] = converted
            elif key == "plan_fingerprint" and isinstance(child, str):
                result[key] = fingerprints.get(child, child)
            else:
                result[key] = visit(child)
        return result
    return visit(value)


def plan_digests(row, schemas):
    job = {"created_by": row["actor"], "surface": row["surface"],
           "search_id": row["search_id"], "run_generation": row["generation"]}
    manifest = [{key: batch[key] for key in ("batch_id", "input_fingerprint")}
                for batch in row["batches_json"]]
    current = plan_fingerprint(job, manifest)
    return current, {digest({"schema": schema, **job, "batches": manifest}): current
                     for schema in schemas}


def migrated_plan(row, schemas=()):
    current, old = plan_digests(row, schemas)
    if row["plan_fingerprint"] not in {current, *old}:
        raise ValueError("Stored plan digest fails original and canonical validation")
    return current


def migrate_seed_text(text, markers=(), *, dismissed=False):
    """Change/remove only a configured leading comment, never its business body."""
    for name in markers:
        match = re.match(r"\A(?:\ufeff)?\s*<!--\s*(?P<name>" + re.escape(name)
                         + r")\s+(?P<payload>[^\r\n]*?)\s*-->", text, re.IGNORECASE)
        if not match:
            continue
        try:
            valid = isinstance(json.loads(match["payload"]), dict)
        except ValueError:
            valid = False
        if valid:
            start, end = match.span("name")
            return text[:start] + MARKER_NAME + text[end:]
        remainder = text[match.end():]
        return (('<!-- ' + MARKER_NAME + ' {"status":"dismissed"} -->') + remainder
                if dismissed else remainder)
    if dismissed and read_marker(text) is None:
        return '<!-- ' + MARKER_NAME + ' {"status":"dismissed"} -->\n' + text
    return text


class Backup:
    def __init__(self, root):
        self.root = Path(root) / ("attempt-" + uuid.uuid4().hex)
        self.root.mkdir(parents=True, mode=0o700)

    def json(self, name, value):
        with (self.root / name).open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, default=str, indent=2)
        (self.root / name).chmod(0o600)

    def file(self, path, data):
        name = hashlib.sha256(str(path).encode()).hexdigest() + ".before"
        with (self.root / name).open("xb") as stream:
            stream.write(data)
        (self.root / name).chmod(0o600)
        self.json(name + ".json", {"path": str(path), "sha256": hashlib.sha256(data).hexdigest()})


def replace_file(path, before, after):
    if path.is_symlink() or path.read_bytes() != before:
        raise ValueError("File changed since inspection: " + str(path))
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    try:
        with temporary.open("xb") as stream:
            stream.write(after)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(path.stat().st_mode & 0o777)
        if path.read_bytes() != before:
            raise ValueError("File changed during migration: " + str(path))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def migrate_deployment_registry(path, *, source_schema, expected_sha256,
                                backup_dir=None, apply=False):
    """Rename only a pinned, validated deployment registry format marker."""
    from management_portal.portal_deployment_registry import load_portal_deployments

    canonical = "tfi.portal-deployments.v1"
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Deployment registry must be a regular non-symlink file")
    before = path.read_bytes()
    before_hash = hashlib.sha256(before).hexdigest()
    if before_hash != expected_sha256:
        raise ValueError("Deployment registry changed since inspection")
    if (not isinstance(source_schema, str) or source_schema == canonical
            or not source_schema.endswith(".portal-deployments.v1")):
        raise ValueError("An explicit same-version retired registry schema is required")
    value = json.loads(before)
    if not isinstance(value, dict) or value.get("schema") not in {source_schema, canonical}:
        raise ValueError("Deployment registry schema does not match the explicit plan")
    old_token, new_token = json.dumps(source_schema).encode(), json.dumps(canonical).encode()
    if value["schema"] == canonical:
        after = before
    else:
        if before.count(old_token) != 1:
            raise ValueError("Ambiguous deployment registry schema token")
        after = before.replace(old_token, new_token, 1)
    if json.loads(after) != {**value, "schema": canonical}:
        raise ValueError("Deployment registry content changed outside its marker")
    # Use the actual strict reader; never widen runtime acceptance for migration.
    with tempfile.TemporaryDirectory(prefix="tfi-registry-check-") as directory:
        candidate = Path(directory) / "deployments.json"
        candidate.write_bytes(after)
        load_portal_deployments(candidate)
    report = {"path": str(path), "before_sha256": before_hash,
              "after_sha256": hashlib.sha256(after).hexdigest(),
              "status": "canonical" if after == before else "would_migrate"}
    if apply and after != before:
        if backup_dir is None:
            raise ValueError("Apply requires a private backup directory")
        backup = Backup(backup_dir)
        backup.file(path, before)
        replace_file(path, before, after)
        if path.read_bytes() != after:
            raise ValueError("Deployment registry changed before readback")
        load_portal_deployments(path)
        report.update(status="migrated", backup=str(backup.root))
        backup.json("result.json", report)
    return report


def state_process_references(*roots):
    """Fail closed when live use cannot be inspected; never inspect process env."""
    if not sys.platform.startswith("linux"):
        return {"references": [], "unverified": ["process inspection unavailable"]}
    roots = [str(Path(root).resolve()) for root in roots]
    references, unverified = [], []
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            links = [process / "cwd", *list((process / "fd").iterdir())]
            for link in links:
                try:
                    target = os.readlink(link).removesuffix(" (deleted)")
                except FileNotFoundError:
                    continue
                if any(target == root or target.startswith(root + os.sep) for root in roots):
                    references.append({"pid": int(process.name), "reference": str(link), "path": target})
        except FileNotFoundError:
            continue  # Process exited while inspected.
        except PermissionError:
            unverified.append(int(process.name))
    return {"references": references, "unverified": unverified}


def migrate_state_directory(source, target, *, names=None, backup_dir=None, apply=False):
    """Merge selected state trees; preserve both trees when a lock is held."""
    source, target = Path(source).absolute(), Path(target).absolute()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("State source and destination must be disjoint")
    if source.is_symlink() or target.is_symlink():
        raise ValueError("State roots must not be symlinks")
    if backup_dir is not None and any(Path(backup_dir).resolve().is_relative_to(root.resolve())
                                      for root in (source, target)):
        raise ValueError("Private backup must be outside both state trees")
    report = {"moved": False, "metadata": 0, "symlinks": 0, "status": "absent"}
    if not source.exists():
        return report
    if apply and backup_dir is None:
        raise ValueError("Apply requires a private backup directory")
    renames = (names or {}).get("state_file_names", {})
    def destination(path):
        relative = path.relative_to(source)
        return target / relative.parent / renames.get(relative.name, relative.name)
    entries = sorted(source.rglob("*"))
    destinations = set()
    for previous in entries:
        current = destination(previous)
        if current in destinations:
            raise ValueError("Multiple sources map to the same destination")
        destinations.add(current)
        if any(parent.is_symlink() for parent in current.parents):
            raise ValueError("Destination parent is a symlink")
        if not current.exists() and not current.is_symlink():
            continue
        if previous.is_symlink() or current.is_symlink():
            equal = previous.is_symlink() and current.is_symlink() and os.readlink(previous) == os.readlink(current)
        elif previous.is_dir() and current.is_dir():
            continue
        else:
            equal = previous.is_file() and current.is_file() and previous.read_bytes() == current.read_bytes()
        if not equal:
            raise ValueError("Existing destination conflicts with state migration: " + str(current))
    # Validate metadata before moving anything.
    metadata_names = {"runs.json", "tau-residents.json"}
    for root in (source, target):
        for file in root.rglob("*.json"):
            if not file.is_symlink() and (file.name in metadata_names or file.parent.name == "runs"):
                json.loads(file.read_bytes())
    usage = state_process_references(source, target)
    if usage["references"]:
        return {**report, "status": "retained_in_use", **usage}
    if usage["unverified"]:
        return {**report, "status": "retained_unverified", **usage}
    with ExitStack() as stack:
        for lock in sorted({*source.rglob("*.lock"), *target.rglob("*.lock")}):
            if lock.is_symlink():
                raise ValueError("Lock must not be a symlink")
            stream = stack.enter_context(lock.open("r+b"))
            if not try_lock_descriptor(stream.fileno()):
                return {**report, "status": "retained_in_use", "lock": str(lock)}
        if not apply:
            return {**report, "status": "would_migrate"}
        backup = Backup(backup_dir)
        shutil.copytree(source, backup.root / "source", symlinks=True)
        if target.exists():
            shutil.copytree(target, backup.root / "target", symlinks=True)
        backup.json("intent.json", {"source": str(source), "target": str(target)})
        for previous in entries:
            current = destination(previous)
            if previous.is_dir() and not previous.is_symlink():
                current.mkdir(parents=True, exist_ok=True)
            else:
                current.parent.mkdir(parents=True, exist_ok=True)
                if current.exists() or current.is_symlink():
                    previous.unlink()
                else:
                    previous.rename(current)
        for directory in sorted((p for p in source.rglob("*") if p.is_dir() and not p.is_symlink()),
                                key=lambda p: len(p.parts), reverse=True):
            directory.rmdir()
        source.rmdir()
        def paths(value):
            if isinstance(value, dict):
                return {key: item if key in {"output", "prompt", "content", "content_parts", "messages", "payload"}
                        else paths(item) for key, item in value.items()}
            if isinstance(value, list):
                return [paths(item) for item in value]
            if isinstance(value, str) and (value == str(source) or value.startswith(str(source) + os.sep)):
                return str(target) if value == str(source) else str(destination(Path(value)))
            return value
        metadata = [*target.glob("*/runs.json"), *target.glob("*/tau-residents.json"), *target.glob("*/runs/*.json")]
        for file in metadata:
            if file.is_symlink():
                continue
            before = file.read_bytes()
            original = json.loads(before)
            updated = paths(original)
            if original != updated:
                replace_file(file, before, json.dumps(updated, ensure_ascii=False).encode())
                report["metadata"] += 1
        for link in target.rglob("*"):
            if link.is_symlink():
                old = os.readlink(link)
                if paths(old) != old:
                    temporary = link.with_name("." + link.name + "." + uuid.uuid4().hex)
                    temporary.symlink_to(paths(old))
                    temporary.replace(link)
                    report["symlinks"] += 1
        report.update(moved=True, status="migrated", backup=str(backup.root))
        backup.json("result.json", report)
        return report


def migrate(connection, *, names, backup_dir=None, seed_root=None, apply=False):
    from psycopg import sql

    if apply and backup_dir is None:
        raise ValueError("Apply requires a private backup directory")
    backup = Backup(backup_dir) if apply else None
    report = {"plans": [], "execution_rows": [], "seed_files": [], "backup": str(backup.root) if backup else None}
    seed_changes = []
    with connection.transaction():
        connection.execute("SET LOCAL lock_timeout='3s'")
        connection.execute("SET LOCAL statement_timeout='360s'")
        if not apply:
            connection.execute("SET TRANSACTION READ ONLY")
        plans = connection.execute("SELECT * FROM service_invocations.service_invocations" + (" FOR UPDATE" if apply else "")).fetchall()
        fingerprints = {}
        for index, row in enumerate(plans):
            key = [row[k] for k in ("actor", "surface", "search_id", "generation")]
            try:
                current = migrated_plan(row, names.get("plan_schemas", ()))
                _, mapping = plan_digests(row, names.get("plan_schemas", ()))
            except (ValueError, KeyError, TypeError) as exc:
                report["plans"].append({"key": key, "status": "retained_unverified", "reason": str(exc)})
                continue
            fingerprints.update(mapping)
            status = "canonical" if current == row["plan_fingerprint"] else "migrated" if apply else "would_migrate"
            if status == "migrated":
                backup.json(f"plan-{index}.json", row)
                connection.execute("UPDATE service_invocations.service_invocations SET plan_fingerprint=%s WHERE actor=%s AND surface=%s AND search_id=%s AND generation=%s", (current, *key))
            report["plans"].append({"key": key, "status": status})
        patterns = ["%" + name + "%" for aliases in names.get("environment_aliases", {}).values() for name in aliases]
        patterns += ["%" + old + "%" for old in fingerprints]
        rows = connection.execute("SELECT execution_id,status,request_json,checkpoint_json FROM execution_runtime.executions WHERE request_json LIKE ANY(%s) OR checkpoint_json LIKE ANY(%s)" + (" FOR UPDATE" if apply else ""), (patterns, patterns)).fetchall()
        for index, row in enumerate(rows):
            changes = {}
            for column in ("request_json", "checkpoint_json"):
                if not row[column]:
                    continue
                original = json.loads(row[column])
                value = transform(original, fingerprints, environment_aliases=names.get("environment_aliases"))
                if value != original:
                    changes[column] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            status = "historical_text_only" if not changes else "would_migrate"
            if changes and row["status"] not in {"completed", "failed", "cancelled"}:
                status = "retained_in_use"
            elif changes and apply:
                backup.json(f"execution-{index}.json", row)
                connection.execute(sql.SQL("UPDATE execution_runtime.executions SET {} WHERE execution_id=%s").format(sql.SQL(",").join(sql.SQL("{}=%s").format(sql.Identifier(key)) for key in changes)), (*changes.values(), row["execution_id"]))
                status = "migrated"
            report["execution_rows"].append({"execution_id": row["execution_id"], "status": status})
        if seed_root:
            seeds = connection.execute("SELECT n.node_id,n.lineage_id,c.config_json FROM lineage.lineage_nodes n JOIN lineage.lineage_components c USING(node_id) WHERE n.node_type='seed' AND n.deleted_at IS NULL").fetchall()
            for row in seeds:
                config = json.loads(row["config_json"]) if isinstance(row["config_json"], str) else row["config_json"]
                relative = config.get("artifact_path")
                if not relative:
                    continue
                root = (Path(seed_root) / row["lineage_id"]).resolve()
                path = root / relative
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    raise ValueError("Seed artifact escapes its registered Lineage folder")
                item = {"node_id": row["node_id"], "path": str(path), "status": "missing"}
                if path.is_file():
                    before = path.read_bytes()
                    after = migrate_seed_text(before.decode("utf-8"), names.get("seed_markers", ()),
                                              dismissed=config.get("status") == "dismissed").encode("utf-8")
                    item["status"] = "canonical_or_unmarked"
                    if before != after:
                        item["status"] = "would_migrate"
                        if apply:
                            backup.file(path, before)
                            seed_changes.append((row["node_id"], config, path, before, after, item))
                    item["sha256"] = hashlib.sha256(before).hexdigest()
                report["seed_files"].append(item)
    # Database changes are committed before independent file work. Recheck the
    # authoritative Seed status under a row lock and report each file outcome.
    if backup:
        backup.json("database-result-seed-intent.json", report)
    for node_id, config, path, before, after, item in seed_changes:
        with connection.transaction():
            connection.execute("SET LOCAL lock_timeout='3s'")
            current = connection.execute("SELECT c.config_json FROM lineage.lineage_nodes n JOIN lineage.lineage_components c USING(node_id) WHERE n.node_id=%s AND n.deleted_at IS NULL FOR UPDATE OF n,c", (node_id,)).fetchone()
            current_config = current["config_json"] if current else None
            if isinstance(current_config, str):
                current_config = json.loads(current_config)
            if current_config != config:
                item.update(status="retained_changed", reason="Seed definition changed since inspection")
                continue
            try:
                replace_file(path, before, after)
            except (OSError, ValueError) as exc:
                item.update(status="retained_changed", reason=str(exc))
            else:
                item.update(status="migrated", sha256=hashlib.sha256(after).hexdigest())
    if backup:
        backup.json("result.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--names-file", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--seed-root", type=Path)
    parser.add_argument("--state-source", type=Path)
    parser.add_argument("--state-target", type=Path)
    parser.add_argument("--deployment-registry", type=Path,
                        help="Only migrate a pinned deployment registry; no database operations")
    args = parser.parse_args()
    os.umask(0o077)
    names = load_names(args.names_file)
    if args.deployment_registry:
        if args.state_source or args.state_target or args.seed_root:
            parser.error("Deployment registry migration cannot combine with other targets")
        selection = names.get("deployment_registry", {})
        report = migrate_deployment_registry(
            args.deployment_registry, source_schema=selection.get("source_schema"),
            expected_sha256=selection.get("sha256"), backup_dir=args.backup_dir, apply=args.apply)
    elif args.state_source or args.state_target:
        if not (args.state_source and args.state_target):
            parser.error("State migration requires both paths")
        report = migrate_state_directory(args.state_source, args.state_target, names=names,
                                         backup_dir=args.backup_dir, apply=args.apply)
    else:
        import psycopg
        from psycopg.rows import dict_row
        dsn = os.environ.get("PORTAL_DATABASE_URL")
        if not dsn:
            parser.error("PORTAL_DATABASE_URL is required; no database is guessed")
        with psycopg.connect(dsn, row_factory=dict_row) as connection:
            report = migrate(connection, names=names, backup_dir=args.backup_dir,
                             seed_root=args.seed_root, apply=args.apply)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
