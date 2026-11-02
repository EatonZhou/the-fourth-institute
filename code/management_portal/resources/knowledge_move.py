"""Knowledge relocation owns paths, project membership and resumable commits.

The registry remains the path authority. A move prepares another location while
the registered source stays readable, then switches registration and permission
facts in one transaction. Old locations are removed only after live projection.
The operation document is a receipt and recovery journal, never a second index.
"""
from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
from uuid import uuid4

import yaml

from management_common.directory_cleanup_store import get_directory_cleanup_store
from management_common.directory_paths import (
    assert_missing_location, cleanup_location, cleanup_path,
    lexical_path, rename_directory_no_replace, remove_path,
)
from management_common.instance_manifest import current_instance
from management_common.instance_locations import located_path, stored_location
from management_portal import permission_system
from .directory_registrations import directory_writer, is_deleted, locations
from .errors import ResourceVersionConflict
from .locking import mutation_projection_scope, resource_mutation_lock
from .knowledge_move_locks import knowledge_identity_lock
from .registry_store import get_registry_store


JOURNAL = "knowledge_moves"


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def _safe(path):
    path = lexical_path(path)
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError("Knowledge move path contains a symbolic link: " + str(parent))
    return path


def _identity(path):
    info = _safe(path).stat()
    return [info.st_dev, info.st_ino]


def _read_text(path):
    with open(_safe(path), "r", encoding="utf-8", newline="") as stream:
        return stream.read()


def _assert_identity(path, expected):
    if not expected or _identity(path) != expected:
        raise FileExistsError("Knowledge move path identity changed: " + str(path))


def _manifest(path):
    """Stat only this resource's companion tree; never walk other resources."""
    path = _safe(path)
    if not path.exists():
        return None
    result = []
    def inspect(entry):
        info = entry.lstat()
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise ValueError("Knowledge companions must be regular files/directories: " + str(entry))
        result.append([entry.relative_to(path).as_posix(), info.st_dev, info.st_ino,
                       info.st_size, info.st_mtime_ns, info.st_mode])
    inspect(path)
    if path.is_dir():
        for root, dirs, files in os.walk(path, followlinks=False):
            for name in sorted(dirs + files):
                inspect(Path(root) / name)
    return result


def _request(target, payload, actor):
    from .providers.knowledge import MARKDOWN_REGISTERABLE_RESOURCE_TYPES
    if not isinstance(target, str) or not target.strip() or not actor:
        raise ValueError("Knowledge move requires target Project and actor")
    if not isinstance(payload, dict) or set(payload) - {"items", "operation_id"}:
        raise ValueError("Unknown Knowledge move fields")
    items = payload.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise ValueError("Knowledge move requires 1–100 items")
    normalized = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {"resource_id", "expected_version"}:
            raise ValueError("Each move item requires resource_id and expected_version")
        rid = item["resource_id"]
        if (not isinstance(rid, str) or not re.fullmatch(r"[a-z][a-z0-9]*_[0-9a-f]{12}", rid)
                or rid.partition("_")[0] not in MARKDOWN_REGISTERABLE_RESOURCE_TYPES):
            raise ValueError("Only Markdown Knowledge resources can be moved")
        expected = item["expected_version"]
        number = expected.get("number") if isinstance(expected, dict) else expected
        if isinstance(number, bool) or not str(number).isdigit() or int(number) < 1:
            raise ValueError("A positive expected_version is required")
        normalized.append({"resource_id": rid, "expected_version": int(number)})
    if len({v["resource_id"] for v in normalized}) != len(normalized):
        raise ValueError("Knowledge move IDs must be distinct")
    request = {"actor": actor, "target": target, "items": normalized}
    operation_id = payload.get("operation_id", "")
    if not isinstance(operation_id, str) or len(operation_id) > 200:
        raise ValueError("operation_id must be a string of at most 200 characters")
    operation_id = operation_id or "knowledge-move:" + _digest(request)
    return request, operation_id, _digest([actor, operation_id])


def _permissions(actor, ids, projects):
    allowed = permission_system.permitted_ids(actor, "UpdateResource", "resource_id", ids)
    if set(ids) - allowed:
        raise PermissionError("Knowledge move requires edit permission on every source resource")
    allowed = permission_system.permitted_ids(actor, "RunResourceAction", "resource_id", sorted(projects))
    if set(projects) - allowed:
        raise PermissionError("Knowledge move requires contributor permission on source and target Projects")


def _project_text(before, target, actor):
    from knowledge_board_lan.knowledge_source import _FRONT_MATTER_PATTERN
    from knowledge_board_lan.knowledge_versions import stamp_knowledge_version
    match = _FRONT_MATTER_PATTERN.match(before)
    if match is None:
        raise ValueError("Knowledge migration requires YAML front matter")
    # YAML marks select the real scalar, including quoted keys and multiline
    # values. Every unrelated metadata byte and the body remain intact.
    front = match.group(1)
    root = yaml.compose(front)
    pairs = [(key, value) for key, value in root.value if key.value == "project"]
    if len(pairs) != 1:
        raise ValueError("Knowledge must declare exactly one Project")
    value = pairs[0][1]
    start = match.start(1) + value.start_mark.index
    end = match.start(1) + value.end_mark.index
    text = before[:start] + json.dumps(target, ensure_ascii=False) + before[end:]
    return stamp_knowledge_version(text, actor=actor, previous_text=before)


def _paths(document, rid):
    entries = locations("knowledge", document)
    paths = [_safe(Path(located_path(item["path"]))) for item in entries]
    mains = [path for path in paths if path.suffix.lower() == ".md"]
    if len(mains) != 1 or set(paths) != {mains[0], mains[0].parent / rid}:
        raise ValueError("Knowledge move requires one registered Markdown and its companion directory")
    return mains[0], mains[0].parent / rid


def _assert_no_nested_owners(plan, connection):
    """Reject independently owned children using bounded path predicates.

    Their own move protocols also own bindings, retention and active writers;
    a Knowledge move must not silently relocate those domains' files.
    """
    from management_common.portal_database import portal_database
    rid = plan["resource_id"]
    paths = [cleanup_path(plan[name]) for name in ("source", "companion")]
    forms = sorted({value for path in paths for value in (str(path), stored_location(path))})
    registry = get_registry_store()
    nested = connection.execute(f'''
        SELECT d.document_id FROM "{registry.database.schema}".registry_documents d,
        LATERAL jsonb_array_elements(COALESCE(d.document_json::jsonb->'directories','[]'::jsonb)
            || COALESCE(d.document_json::jsonb->'checkouts','[]'::jsonb)
            || COALESCE(d.document_json::jsonb->'roots','[]'::jsonb)) e
        WHERE d.collection=ANY(%s) AND d.document_id<>%s
          AND EXISTS(SELECT 1 FROM unnest(%s::text[]) p
                     WHERE e->>'path'=p OR starts_with(e->>'path',p||'/')) LIMIT 1
        ''', (["knowledge", "repo", "article", "paper"], rid, forms)).fetchone()
    if nested:
        raise ValueError("Knowledge has an independently registered child: " + nested[0])
    for schema, table, location, extra in (
        ("workspace", "workspaces", "path", "AND deleted_at IS NULL"),
        ("directory_retention", "artifacts", "location", ""),
    ):
        qualified = f'"{portal_database(schema=schema).schema}".{table}'
        if not connection.execute('SELECT to_regclass(%s)', (qualified,)).fetchone()[0]:
            continue
        nested = connection.execute(f'''
            SELECT {location} FROM {qualified} WHERE EXISTS(
                SELECT 1 FROM unnest(%s::text[]) p
                WHERE {location}=p OR starts_with({location},p||'/')) {extra} LIMIT 1
            ''', (forms,)).fetchone()
        if nested:
            raise ValueError("Knowledge contains an independent Workspace or retained artifact; use its owner migration")


def _plan(item, target, actor, projects, folder_map):
    from knowledge_board_lan.knowledge_source import _yaml_front_matter, knowledge_version_record
    rid = item["resource_id"]
    document = get_registry_store().get("knowledge", rid)
    if document is None or is_deleted(rid):
        raise KeyError(rid)
    if any(entry.get("origin") != "created" for entry in locations("knowledge", document)):
        raise ValueError("Knowledge move only relocates created directories; adopted content stays at its registered location")
    source, companion = _paths(document, rid)
    before = _read_text(source)
    meta = _yaml_front_matter(before)
    kind = rid.partition("_")[0]
    if meta.get("kb_id") != rid or meta.get("type") != kind:
        raise ValueError("Registered Knowledge metadata does not match its identity")
    version = knowledge_version_record(before)
    if item["expected_version"] != version["number"]:
        raise ResourceVersionConflict(expected_version=item["expected_version"], current_version=version)
    source_project = meta.get("project")
    if not isinstance(source_project, str) or source_project not in projects:
        raise ValueError("Knowledge source Project is not registered")
    source_root = _safe(Path(projects[source_project]["path"]))
    target_root = _safe(Path(projects[target]["path"]))
    relative = source.relative_to(source_root / "knowledge_base")
    if len(relative.parts) != 2 or relative.parts[0] != folder_map.get(kind, kind):
        raise ValueError("Knowledge source is outside its Project's type directory")
    target_path = target_root / "knowledge_base" / folder_map.get(kind, kind) / source.name
    target_companion = target_path.parent / rid
    for path in (target_path, target_companion):
        _safe(path)
        if source_project != target:
            assert_missing_location(path)
    after = before if source_project == target else _project_text(before, target, actor)
    companion_manifest = _manifest(companion)
    parent = target_path.parent
    while not parent.exists():
        parent = parent.parent
    device = source.stat().st_dev
    if (parent.stat().st_dev != device or
            any(entry[1] != device for entry in companion_manifest or [])):
        raise ValueError("Knowledge move requires one filesystem; cross-filesystem content needs an owner-coordinated copy")
    return {**item, "source_project": source_project, "source": cleanup_location(source),
            "companion": cleanup_location(companion), "target": cleanup_location(target_path),
            "target_companion": cleanup_location(target_companion), "before": before, "after": after,
            "source_identity": _identity(source), "companion_manifest": companion_manifest,
            "source_root": cleanup_location(source_root), "source_root_identity": _identity(source_root),
            "target_root": cleanup_location(target_root), "target_root_identity": _identity(target_root),
            "document": document, "phase": "pending", "artifacts": [],
            "result": {"resource_id": rid, "resource_type": kind, "path": str(target_path),
                       "project": target, "project_ids": [target], "content_root": str(target_path.parent),
                       "version": knowledge_version_record(after)}}


def _save(journal, *, writer=None):
    if writer is not None:
        writer.put(JOURNAL, journal["key"], journal)
    else:
        with get_registry_store().writer(JOURNAL) as writer:
            writer.put(JOURNAL, journal["key"], journal)


def _verify_source(plan):
    _assert_identity(cleanup_path(plan["source_root"]), plan["source_root_identity"])
    _assert_identity(cleanup_path(plan["target_root"]), plan["target_root_identity"])
    source = cleanup_path(plan["source"])
    _assert_identity(source, plan["source_identity"])
    if _read_text(source) != plan["before"]:
        raise ResourceVersionConflict("Knowledge changed while its relocation was being prepared")
    if _manifest(cleanup_path(plan["companion"])) != plan["companion_manifest"]:
        raise ResourceVersionConflict("Knowledge companions changed while relocation was being prepared")


def _sync(path):
    with open(path, "rb") as handle:
        os.fsync(handle.fileno())


def _sync_directory(path):
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _prepare_files(journal, plan):
    queue = get_directory_cleanup_store()
    rid = plan["resource_id"]
    reservation = "knowledge-move:" + journal["key"] + ":" + rid
    plan["reservation"] = reservation
    targets = [cleanup_path(plan["target"]), cleanup_path(plan["target_companion"])]
    with directory_writer(JOURNAL) as writer:
        _assert_no_nested_owners(plan, writer.connection)
        for path in [cleanup_path(plan["source"]), cleanup_path(plan["companion"]), *targets]:
            queue.reserve(path, operation_id=reservation, purpose="knowledge_move", connection=writer.connection)
        _save(journal, writer=writer)
    if not plan["artifacts"]:
        plan["artifacts"] = [{"target": cleanup_location(path), "kind": kind}
                             for path, kind in zip(targets, ("file", "directory"))]
    for index, artifact in enumerate(plan["artifacts"]):
        target = _safe(cleanup_path(artifact["target"]))
        if target.exists():
            _assert_identity(target, artifact.get("identity"))
            if not artifact.get("ready"):
                raise FileExistsError("Unprepared Knowledge move target")
            continue
        stage = cleanup_path(artifact["stage"]) if artifact.get("stage") else None
        if not artifact.get("ready"):
            if stage and artifact.get("identity"):
                remove_path(stage, identity=artifact["identity"])
            elif stage:
                # The process may have stopped between mkdir and saving its
                # inode. Preserve and report that unproven path, never adopt it.
                journal.setdefault("unproven_stages", []).append(artifact["stage"])
            target.parent.mkdir(parents=True, exist_ok=True)
            _safe(target.parent)
            stage = target.parent / (".knowledge-move-" + uuid4().hex)
            artifact.update(stage=cleanup_location(stage), identity=None, ready=False)
            with directory_writer(JOURNAL) as writer:
                queue.reserve(stage, operation_id=reservation, purpose="knowledge_move", connection=writer.connection)
                _save(journal, writer=writer)
            if index == 0:
                stage.touch(exist_ok=False)
            else:
                stage.mkdir()
            artifact["identity"] = _identity(stage)
            _save(journal)
            if index == 0:
                with open(stage, "w", encoding="utf-8", newline="") as stream:
                    stream.write(plan["after"])
                shutil.copymode(cleanup_path(plan["source"]), stage)
                _sync(stage)
            else:
                source = cleanup_path(plan["companion"])
                if source.exists():
                    # Same-filesystem hard links preserve payload inodes and
                    # any already open descriptors; only directory entries
                    # are duplicated during the short registration switch.
                    shutil.copytree(source, stage, dirs_exist_ok=True, symlinks=True,
                                    copy_function=os.link)
                    _manifest(stage)  # refuses links, sockets and device files
                    for root, _dirs, files in os.walk(stage):
                        for filename in files:
                            _sync(Path(root) / filename)
                        _sync_directory(Path(root))
            _verify_source(plan)
            artifact["ready"] = True
            _save(journal)
        _assert_identity(stage, artifact["identity"])
        _safe(target)
        rename_directory_no_replace(stage, target)
        _sync_directory(target.parent)
    plan["phase"] = "prepared"
    _save(journal)


def _commit(journal, plan):
    from .content_changes import capture_content_changes, record_text_change
    from knowledge_board_lan.knowledge_source import knowledge_version_record
    rid = plan["resource_id"]
    with capture_content_changes(actor=journal["request"]["actor"], resource_id=rid):
        with directory_writer("knowledge", JOURNAL) as writer:
            if writer.get("knowledge", rid) != plan["document"] or is_deleted(rid, connection=writer.connection):
                raise ResourceVersionConflict("Knowledge registration changed during relocation")
            _verify_source(plan)
            _assert_no_nested_owners(plan, writer.connection)
            for artifact in plan["artifacts"]:
                _assert_identity(cleanup_path(artifact["target"]), artifact["identity"])
            document = deepcopy(plan["document"])
            for entry in locations("knowledge", document):
                old = Path(located_path(entry["path"]))
                new = plan["target"] if old == cleanup_path(plan["source"]) else plan["target_companion"]
                entry["path"] = stored_location(cleanup_path(new))
            writer.put("knowledge", rid, document)
            permission_system.sync_resource(plan["result"], resource_type=plan["result"]["resource_type"],
                                            connection=writer.connection)
            # New paths are authoritative now. Release their preparation
            # leases in the same commit so live read/projection can use them.
            queue = get_directory_cleanup_store()
            queue.release(plan["reservation"], connection=writer.connection)
            for field in ("source", "companion"):
                queue.reserve(cleanup_path(plan[field]), operation_id=plan["reservation"],
                              purpose="knowledge_move_cleanup", connection=writer.connection)
            committed = deepcopy(journal)
            next(p for p in committed["plans"] if p["resource_id"] == rid)["phase"] = "committed"
            committed["projected"] = False
            _save(committed, writer=writer)
        plan["phase"] = "committed"
        journal["projected"] = False
        record_text_change(rid, plan["result"]["path"], plan["before"], plan["after"],
                           before_version=knowledge_version_record(plan["before"]),
                           after_version=plan["result"]["version"])


def _cleanup(journal, plan):
    if plan["source"] != plan["target"]:
        source = cleanup_path(plan["source"])
        companion = cleanup_path(plan["companion"])
        if source.exists():
            if _read_text(source) != plan["before"]:
                raise ResourceVersionConflict("Old Knowledge content changed before relocation cleanup")
            remove_path(source, identity=plan["source_identity"])
        if companion.exists():
            original = plan["companion_manifest"]
            if _manifest(companion) != original:
                raise ResourceVersionConflict("Old Knowledge companions changed before relocation cleanup")
            remove_path(companion, identity=original[0][1:3])
    with directory_writer(JOURNAL) as writer:
        if plan.get("reservation"):
            get_directory_cleanup_store().release(plan["reservation"], connection=writer.connection)
        plan["phase"] = "cleaned"
        _save(journal, writer=writer)


def _abort_uncommitted(journal, reason):
    """A changed source requires a new CAS request, not a stranded reservation."""
    for plan in journal["plans"]:
        if plan["phase"] in {"committed", "cleaned", "aborted"}:
            continue
        for artifact in plan["artifacts"]:
            for field in ("stage", "target"):
                if artifact.get(field):
                    path = cleanup_path(artifact[field])
                    if path.exists() or path.is_symlink():
                        # Unproven paths are left reserved for explicit repair.
                        _assert_identity(path, artifact.get("identity"))
                        remove_path(path, identity=artifact["identity"])
        with directory_writer(JOURNAL) as writer:
            if plan.get("reservation"):
                get_directory_cleanup_store().release(plan["reservation"], connection=writer.connection)
            plan.update(phase="aborted", abort_reason=reason)
            _save(journal, writer=writer)


def _result(journal, *, error=""):
    committed = [p["result"] for p in journal["plans"] if p["phase"] in {"committed", "cleaned"}]
    pending = [p["resource_id"] for p in journal["plans"] if p["phase"] not in {"committed", "cleaned"}]
    complete = not pending and not error and journal.get("projected", False)
    result = {"operation_id": journal["operation_id"], "status": "completed" if complete else "partial",
              "committed": committed, "pending": pending, "projected": journal.get("projected", False)}
    if error:
        result["error"] = error
    if journal.get("unproven_stages"):
        result["preserved_staging_paths"] = journal["unproven_stages"]
    return result


def move_knowledge(target_project_id, payload, *, actor, project_batch):
    from knowledge_board_lan import knowledge_backend as kb
    from .service_mutations import require_mutation_actor
    require_mutation_actor(actor)
    request, operation_id, key = _request(target_project_id, payload, actor)
    ids = sorted(item["resource_id"] for item in request["items"])
    # Authorization precedes metadata/version reads, including error paths.
    _permissions(actor, ids, {target_project_id})
    root = current_instance().root / "runtime" / "knowledge-moves"
    with ExitStack() as stack:
        for rid in ids:
            stack.enter_context(mutation_projection_scope(rid))
        stack.enter_context(resource_mutation_lock(root, "operation:" + key))
        for rid in ids:
            stack.enter_context(knowledge_identity_lock(rid))
        journal = get_registry_store().get(JOURNAL, key)
        if journal and journal["request"] != request:
            raise ValueError("Knowledge move operation_id was already used with different arguments")
        projects = {p["id"]: p for p in kb._parse_projects_yaml()}
        if target_project_id not in projects:
            raise KeyError("Target Project not found: " + target_project_id)
        if journal is None:
            # Existing content writers lock the source parent. Hold their
            # exact lock plus the destination lock through live projection.
            for rid in ids:
                document = get_registry_store().get("knowledge", rid)
                if document is None:
                    raise KeyError(rid)
                source, _ = _paths(document, rid)
                stack.enter_context(resource_mutation_lock(source.parent, rid))
            _, folders, _, _ = kb._get_tag_config()
            plans = [_plan(item, target_project_id, actor, projects, folders) for item in request["items"]]
            _permissions(actor, ids, {target_project_id, *(p["source_project"] for p in plans)})
            journal = {"key": key, "operation_id": operation_id, "request": request,
                       "plans": plans, "projected": False}
            # Full-batch preflight precedes the first journal or file write.
            with directory_writer(JOURNAL) as writer:
                queue = get_directory_cleanup_store()
                for plan in plans:
                    _assert_no_nested_owners(plan, writer.connection)
                    for field in ("source", "companion", "target", "target_companion"):
                        queue.assert_available(cleanup_path(plan[field]), connection=writer.connection)
                _save(journal, writer=writer)
        else:
            _permissions(actor, ids, {target_project_id, *(p["source_project"] for p in journal["plans"])})
            if all(p["phase"] == "cleaned" for p in journal["plans"]) and journal.get("projected"):
                return _result(journal)
            for plan in sorted(journal["plans"], key=lambda p: p["resource_id"]):
                stack.enter_context(resource_mutation_lock(cleanup_path(plan["source"]).parent, plan["resource_id"]))
        for plan in sorted(journal["plans"], key=lambda p: p["resource_id"]):
            if cleanup_path(plan["source"]).parent != cleanup_path(plan["target"]).parent:
                stack.enter_context(resource_mutation_lock(cleanup_path(plan["target"]).parent, plan["resource_id"]))
        if all(p["phase"] == "cleaned" for p in journal["plans"]) and journal.get("projected"):
            return _result(journal)
        error = ""
        try:
            for plan in journal["plans"]:
                if plan["phase"] in {"committed", "cleaned"}:
                    continue
                if plan["phase"] == "aborted":
                    raise ResourceVersionConflict(plan["abort_reason"])
                _verify_source(plan)
                if plan["source"] != plan["target"]:
                    _prepare_files(journal, plan)
                    _commit(journal, plan)
                else:
                    plan["phase"] = "committed"
                    journal["projected"] = False
                    _save(journal)
        except Exception as exc:
            # Read the durable receipt rather than guess whether an uncertain
            # database commit succeeded. Unknown storage errors propagate.
            journal = get_registry_store().get(JOURNAL, key)
            if journal is None:
                raise
            error = f"{type(exc).__name__}: {exc}"
            if isinstance(exc, ResourceVersionConflict):
                try:
                    _abort_uncommitted(journal, error)
                except Exception as cleanup_error:
                    error += f"; recovery retained paths: {cleanup_error}"
        committed = [p for p in journal["plans"] if p["phase"] in {"committed", "cleaned"}]
        if committed:
            journal["projected"] = False
            try:
                # A later move/update must never be overwritten by replay of
                # an older receipt. Project the registry's current location.
                records = []
                for plan in committed:
                    document = get_registry_store().get("knowledge", plan["resource_id"])
                    if document is None or is_deleted(plan["resource_id"]):
                        raise KeyError(plan["resource_id"])
                    current, _ = _paths(document, plan["resource_id"])
                    if current != cleanup_path(plan["target"]):
                        raise ResourceVersionConflict("Knowledge moved again before its earlier projection completed")
                    records.append(plan["result"])
                project_batch(records)
                journal["projected"] = True
                _save(journal)
                for plan in committed:
                    if plan["phase"] != "cleaned":
                        _cleanup(journal, plan)
            except Exception as exc:
                error = (error + "; " if error else "") + f"{type(exc).__name__}: {exc}"
        return _result(journal, error=error)
