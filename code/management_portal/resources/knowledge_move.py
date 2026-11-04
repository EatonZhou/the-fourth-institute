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
    from .providers.knowledge import MUTABLE_RESOURCE_TYPES
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
                or rid.partition("_")[0] not in MUTABLE_RESOURCE_TYPES):
            raise ValueError("Only Knowledge and Skill resources can be moved")
        expected = item["expected_version"]
        if rid.startswith("skill_"):
            if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise ValueError("Skill expected_version must be the SHA256 returned by ReadResource")
            normalized.append({"resource_id": rid, "expected_version": expected})
            continue
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


def _project_text(before, target, actor, *, skill=False):
    from knowledge_board_lan.knowledge_source import _FRONT_MATTER_PATTERN
    from knowledge_board_lan.knowledge_versions import stamp_knowledge_version
    match = _FRONT_MATTER_PATTERN.match(before)
    if match is None:
        raise ValueError("Knowledge migration requires YAML front matter")
    # YAML marks select the real scalar, including quoted keys and multiline
    # values. Every unrelated metadata byte and the body remain intact.
    front = match.group(1)
    root = yaml.compose(front)
    if not isinstance(root, yaml.MappingNode):
        raise ValueError("Knowledge front matter must be a mapping")
    pairs = [(key, value) for key, value in root.value if key.value == "project"]
    if not pairs and skill:
        end = match.end(1)
        text = before[:end] + "\nproject: " + json.dumps(target, ensure_ascii=False) + before[end:]
        return text
    if len(pairs) != 1:
        raise ValueError("Knowledge must declare exactly one Project")
    value = pairs[0][1]
    start = match.start(1) + value.start_mark.index
    end = match.start(1) + value.end_mark.index
    text = before[:start] + json.dumps(target, ensure_ascii=False) + before[end:]
    return text if skill else stamp_knowledge_version(text, actor=actor, previous_text=before)


def _paths(document, rid):
    entries = locations("knowledge", document)
    paths = [_safe(Path(located_path(item["path"]))) for item in entries]
    if rid.startswith("skill_"):
        from skill_board_lan import skill_backend as skills
        if len(paths) != 1:
            raise ValueError("Skill registration must identify its file or package root")
        root = paths[0]
        primary = skills._choose_primary_file(root, skills._candidate_markdown_files(root))
        if primary is None:
            raise ValueError("Skill has no primary Markdown")
        return _safe(primary), root if root.is_dir() else None
    if len(paths) == 1 and paths[0].is_dir():
        from knowledge_board_lan import knowledge_source as knowledge
        root = paths[0]
        files = [path for path in root.iterdir() if path.is_file() and path.suffix.lower() == ".md"
                 and not path.name.startswith(".") and not knowledge._is_index_file(path)]
        primary = knowledge._choose_primary_file(root, files)
        if primary is None:
            raise ValueError("Knowledge package has no primary Markdown")
        return _safe(primary), root
    mains = [path for path in paths if path.suffix.lower() == ".md" and path.is_file()]
    if len(mains) != 1:
        raise ValueError("Knowledge registration must identify one primary Markdown")
    others = [path for path in paths if path != mains[0]]
    return mains[0], others[0] if others else None


def _owned(plan):
    return plan["owned"]


def _version(text, rid):
    from knowledge_board_lan.knowledge_source import knowledge_version_record
    return (hashlib.sha256(text.encode("utf-8")).hexdigest() if rid.startswith("skill_")
            else knowledge_version_record(text))


def _assert_no_nested_owners(plan, connection):
    """Reject independently owned children using bounded path predicates.

    Their own move protocols also own bindings, retention and active writers;
    a Knowledge move must not silently relocate those domains' files.
    """
    from management_common.portal_database import portal_database
    rid = plan["resource_id"]
    paths = [cleanup_path(entry["source"]) for entry in _owned(plan)]
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
    from knowledge_board_lan.knowledge_source import _yaml_front_matter, knowledge_metadata_id
    rid = item["resource_id"]
    document = get_registry_store().get("knowledge", rid)
    if document is None or is_deleted(rid):
        raise KeyError(rid)
    source, companion = _paths(document, rid)
    registered = [_safe(located_path(e["path"])) for e in locations("knowledge", document)]
    if len(set(registered)) != len(registered) or any(
            a != b and a.is_relative_to(b) for a in registered for b in registered):
        raise ValueError("Resource registration contains overlapping paths")
    before = _read_text(source)
    meta = _yaml_front_matter(before)
    kind = rid.partition("_")[0]
    skill = kind == "skill"
    metadata = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
    identity = (meta.get("id") or metadata.get("id") or meta.get("kb_id")) if skill else knowledge_metadata_id(meta)
    if identity != rid or (not skill and meta.get("type") != kind):
        raise ValueError("Registered Knowledge metadata does not match its identity")
    version = _version(before, rid)
    if item["expected_version"] != (version if skill else version["number"]):
        raise ResourceVersionConflict(expected_version=item["expected_version"], current_version=version)
    source_project = ""
    source_root = None
    type_folder = folder_map.get(kind, kind)
    for project_id, project in projects.items():
        root = _safe(Path(project["path"]))
        base = root / "knowledge_base" / type_folder
        if source.is_relative_to(base):
            source_project, source_root, source_base = project_id, root, base
            break
    if source_root is None and skill:
        from skill_board_lan import skill_backend as skills
        if source.is_relative_to(skills.CORE_SKILLS_ROOT.resolve()):
            raise PermissionError("Core Skill is part of the installed code")
        shared = _safe(skills.SHARED_SKILLS_ROOT)
        if source.is_relative_to(shared):
            source_root = shared
            source_base = companion.parent if companion is not None else source.parent
    if source_root is None:
        raise ValueError("Knowledge source is outside a registered Project or Skill source")
    if not skill and meta.get("project") != source_project:
        raise ValueError("Knowledge Project metadata disagrees with its registered path")
    target_root = _safe(Path(projects[target]["path"]))
    target_base = target_root / "knowledge_base" / type_folder
    target_path = target_base / source.relative_to(source_base)
    owned = []
    for path in registered:
        relative = path.relative_to(source_base)
        destination = _safe(target_base / relative)
        manifest = _manifest(path)
        owned.append({"source": cleanup_location(path), "target": cleanup_location(destination),
                      "manifest": manifest, "kind": "directory" if path.is_dir() or manifest is None else "file"})
        if source_project != target:
            assert_missing_location(destination)
    after = before if source_project == target else _project_text(before, target, actor, skill=skill)
    parent = target_path.parent
    while not parent.exists():
        parent = parent.parent
    device = source.stat().st_dev
    if (parent.stat().st_dev != device or
            any(entry[1] != device for owned_path in owned for entry in owned_path["manifest"] or [])):
        raise ValueError("Knowledge move requires one filesystem; cross-filesystem content needs an owner-coordinated copy")
    return {**item, "source_project": source_project, "source": cleanup_location(source),
            "target": cleanup_location(target_path), "before": before, "after": after,
            "owned": owned, "source_identity": _identity(source),
            "source_root": cleanup_location(source_root), "source_root_identity": _identity(source_root),
            "target_root": cleanup_location(target_root), "target_root_identity": _identity(target_root),
            "document": document, "phase": "pending", "artifacts": [],
            "result": {"resource_id": rid, "resource_type": kind, "path": str(target_path),
                       "project": target, "project_ids": [target],
                       "content_root": str(target_base / companion.relative_to(source_base))
                           if companion and source.is_relative_to(companion) else str(target_path.parent),
                       **({"single_file": companion is None} if skill else {}),
                       "version": _version(after, rid)}}


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
    for entry in _owned(plan):
        if _manifest(cleanup_path(entry["source"])) != entry["manifest"]:
            raise ResourceVersionConflict("Knowledge contents changed while relocation was being prepared")


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
    owned = _owned(plan)
    targets = [cleanup_path(entry["target"]) for entry in owned]
    with directory_writer(JOURNAL) as writer:
        _assert_no_nested_owners(plan, writer.connection)
        for path in [*(cleanup_path(entry["source"]) for entry in owned), *targets]:
            queue.reserve(path, operation_id=reservation, purpose="knowledge_move", connection=writer.connection)
        _save(journal, writer=writer)
    if not plan["artifacts"]:
        plan["artifacts"] = [{"target": entry["target"], "kind": entry["kind"]} for entry in owned]
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
            if artifact["kind"] == "file":
                stage.touch(exist_ok=False)
            else:
                stage.mkdir()
            artifact["identity"] = _identity(stage)
            _save(journal)
            source = cleanup_path(owned[index]["source"])
            if artifact["kind"] == "file" and source == cleanup_path(plan["source"]):
                with open(stage, "w", encoding="utf-8", newline="") as stream:
                    stream.write(plan["after"])
                shutil.copymode(source, stage)
                _sync(stage)
            elif artifact["kind"] == "file":
                # Additional individually registered files are moved verbatim.
                stage.unlink()
                os.link(source, stage)
                artifact["identity"] = _identity(stage)
                _save(journal)
                _sync(stage)
            else:
                if source.exists():
                    # Same-filesystem hard links preserve payload inodes and
                    # any already open descriptors; only directory entries
                    # are duplicated during the short registration switch.
                    shutil.copytree(source, stage, dirs_exist_ok=True, symlinks=True,
                                    copy_function=os.link)
                    primary = cleanup_path(plan["source"])
                    if primary.is_relative_to(source):
                        # Never write through the hard link to the live source.
                        from knowledge_board_lan.knowledge_backend import _write_text_atomic
                        prepared_primary = stage / primary.relative_to(source)
                        _write_text_atomic(prepared_primary, plan["after"])
                        shutil.copymode(primary, prepared_primary)
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
            destinations = {entry["source"]: entry["target"] for entry in _owned(plan)}
            for entry in locations("knowledge", document):
                old = Path(located_path(entry["path"]))
                new = destinations[cleanup_location(old)]
                entry["path"] = stored_location(cleanup_path(new))
            writer.put("knowledge", rid, document)
            permission_system.sync_resource(plan["result"], resource_type=plan["result"]["resource_type"],
                                            connection=writer.connection)
            # New paths are authoritative now. Release their preparation
            # leases in the same commit so live read/projection can use them.
            queue = get_directory_cleanup_store()
            queue.release(plan["reservation"], connection=writer.connection)
            for entry in _owned(plan):
                queue.reserve(cleanup_path(entry["source"]), operation_id=plan["reservation"],
                              purpose="knowledge_move_cleanup", connection=writer.connection)
            committed = deepcopy(journal)
            next(p for p in committed["plans"] if p["resource_id"] == rid)["phase"] = "committed"
            committed["projected"] = False
            _save(committed, writer=writer)
        plan["phase"] = "committed"
        journal["projected"] = False
        record_text_change(rid, plan["result"]["path"], plan["before"], plan["after"],
                           before_version=_version(plan["before"], rid),
                           after_version=plan["result"]["version"])


def _cleanup(journal, plan):
    if plan["source"] != plan["target"]:
        source = cleanup_path(plan["source"])
        if source.exists():
            if _read_text(source) != plan["before"]:
                raise ResourceVersionConflict("Old Knowledge content changed before relocation cleanup")
        for entry in _owned(plan):
            path = cleanup_path(entry["source"])
            if path.exists():
                original = entry["manifest"]
                if _manifest(path) != original:
                    raise ResourceVersionConflict("Old Knowledge contents changed before relocation cleanup")
                remove_path(path, identity=original[0][1:3])
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
        if journal:
            # Upgrade persisted in-flight receipts to the path-list format.
            # This is journal schema recovery, independent of resource provenance.
            for plan in journal["plans"]:
                if "owned" not in plan:
                    plan["owned"] = [
                        {"source": plan["source"], "target": plan["target"], "kind": "file",
                         "manifest": _manifest(cleanup_path(plan["source"]))},
                        {"source": plan["companion"], "target": plan["target_companion"],
                         "kind": "directory", "manifest": plan["companion_manifest"]},
                    ]
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
            _permissions(actor, ids, {target_project_id, *(p["source_project"] for p in plans if p["source_project"])})
            journal = {"key": key, "operation_id": operation_id, "request": request,
                       "plans": plans, "projected": False}
            # Full-batch preflight precedes the first journal or file write.
            with directory_writer(JOURNAL) as writer:
                queue = get_directory_cleanup_store()
                for plan in plans:
                    _assert_no_nested_owners(plan, writer.connection)
                    for entry in _owned(plan):
                        for field in ("source", "target"):
                            queue.assert_available(cleanup_path(entry[field]), connection=writer.connection)
                _save(journal, writer=writer)
        else:
            _permissions(actor, ids, {target_project_id, *(p["source_project"] for p in journal["plans"] if p["source_project"])})
            if all(p["phase"] == "cleaned" for p in journal["plans"]) and journal.get("projected"):
                return _result(journal)
            for plan in sorted(journal["plans"], key=lambda p: p["resource_id"]):
                stack.enter_context(resource_mutation_lock(cleanup_path(plan["source"]).parent, plan["resource_id"]))
        for plan in sorted(journal["plans"], key=lambda p: p["resource_id"]):
            # A package's primary may be nested. Lock outside the owned tree:
            # resource_mutation_lock creates its directory, which must not
            # pre-create the relocation's reserved destination package.
            target_parent = Path(os.path.commonpath([str(cleanup_path(e["target"]).parent) for e in _owned(plan)]))
            if cleanup_path(plan["source"]).parent != target_parent:
                stack.enter_context(resource_mutation_lock(target_parent, plan["resource_id"]))
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
