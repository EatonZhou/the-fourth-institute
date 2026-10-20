"""WALL application workflows, backed by Markdown and existing Resident jobs."""
from __future__ import annotations
from management_common.portal_paths import PORTAL_ROOT as SOURCE_ROOT, path as portal_path

import copy
import json
import logging
import os
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

from . import business_tasks, resident_handoff
from .society_service.errors import SocietyServiceBusy, SocietyServiceUnavailable
from .portal_access_control import get_portal_access_store
from .shared_state import atomic_replace_text, exclusive_file_lock
from .wall_store import WallState
from .wall_documents import (
    KINDS, POINT_FORMAT_HINT, RESOURCE_ID, Source, apply_importance, digest, importance, index_sources, inside, parse_points, source_diagnostics,
    index_file, frontmatter, point_markdown, public_point, read_source, read_resource_source_with_version, replace_point, write_source, metadata_comment, source_version,
    document_markdown, remove_index_reference, update_frontmatter, has_point_annotations,
)
from management_common.knowledge_metadata import validate_metaness
from management_common.operation_errors import OperationError, failure_payload

LOG = logging.getLogger(__name__)
PORTAL_ROOT = Path(os.getenv("WALL_PORTAL_ROOT", str(SOURCE_ROOT)))
WALL_LOCK_ROOT = (portal_path("staging_root") / "shared" / "portal_data" / "wall-locks")
SCAN_TTL = 60
ACTIVE = frozenset({"queued", "routing", "preparing", "running"})
PROJECT_ACTIONS = {"analyse": "分析", "verify": "全面回归与修复", "publish-all": "全量发布"}
_CACHE: dict[tuple[str, str], dict[str, Any]] = {}
_CACHE_LOCK = threading.RLock()
_RUNTIME_LOCK = threading.Lock()
_RUNTIME_STARTED = False
_RUNTIME_STOP: threading.Event | None = None
_RUNTIME_THREAD: threading.Thread | None = None


def _default_project() -> dict[str, Any]:
    return {"id": "portal", "title": "TFI Portal", "root": str(PORTAL_ROOT),
            "project_id": "AI员工", "automation": {"enabled": False, "interval_minutes": 60, "auto_fix": True}}


def _state_store() -> WallState:
    return WallState()


def _read_state() -> dict[str, Any]:
    data = _state_store().read({"projects": {}, "jobs": {}, "discussions": {}})
    if not isinstance(data, dict):
        raise RuntimeError("WALL 状态文件格式错误")
    data.setdefault("projects", {}).setdefault("portal", _default_project())
    data.setdefault("jobs", {})
    data.setdefault("discussions", {})
    data.setdefault("reads", {})
    data.setdefault("reviews", {})
    data.setdefault("resident_cleanup", {})
    return data


def scheduling_status() -> dict[str, bool]:
    """Global admission for new WALL scheduled jobs; manual jobs remain available."""
    return {"enabled": _read_state()["scheduling_enabled"]}


def set_scheduling_enabled(enabled: bool) -> dict[str, bool]:
    if type(enabled) is not bool:
        raise ValueError("WALL scheduled jobs enabled must be a boolean")
    _state_store().set_scheduling_enabled(enabled)
    return {"enabled": enabled}


def _read_projects() -> dict[str, dict[str, Any]]:
    projects = _state_store().read_projects()
    projects.setdefault("portal", _default_project())
    return projects


def _change(fn: Callable[[dict[str, Any]], Any]) -> Any:
    with _state_store().transaction():
        data = _read_state()
        result = fn(data)
        _state_store().replace(data)
        return copy.deepcopy(result)


def project(key: str, actor: str, *, write: bool = False) -> dict[str, Any]:
    if not actor or not get_portal_access_store().is_active(actor):
        raise PermissionError("需要有效登录身份")
    record = _read_projects().get(key)
    if not record:
        raise KeyError("工程未登记")
    if write and record.get('deleting'):
        raise ValueError('工程正在取消登记，请完成清理后再操作')
    return copy.deepcopy(record)


def projects(actor: str) -> dict[str, Any]:
    active = bool(actor) and get_portal_access_store().is_active(actor)
    return {"projects": list(_read_projects().values()) if active else [],
            "capabilities": {"register": active}}


def _require_project_change(actor: str, project_id: str) -> None:
    """Registering an engineering root under a Portal Project, or removing it, is a change to that Project."""
    from management_portal import permission_system

    if not permission_system.allows(actor, "UpdateProject", {"project_id": project_id}):
        raise PermissionError(f"需要能修改 Project {project_id}（UpdateProject）")


def register(payload: dict[str, Any], actor: str) -> dict[str, Any]:
    root = Path(str(payload.get("root") or "")).expanduser().resolve()
    if not root.is_dir() or root == Path("/"):
        raise ValueError("请输入存在的软件工程目录")
    if not any(index_file(root, name).is_file() for name in ("SPEC.md", "IMPROVE.md")):
        raise ValueError("工程内尚未找到 SPEC.md 或 IMPROVE.md")
    access_project = str(payload.get("project_id") or "").strip()
    if access_project not in get_portal_access_store().project_ids():
        raise ValueError("请选择已登记的 Portal Project")
    _require_project_change(actor, access_project)
    item = {"id": "portal" if root == PORTAL_ROOT.resolve() else "project-" + digest(str(root)),
            "title": str(payload.get("title") or root.name).strip(), "root": str(root),
            "project_id": access_project, "automation": {"enabled": False, "interval_minutes": 60, "auto_fix": True}}
    def put(data):
        if item["id"] in data["projects"]:
            return data["projects"][item["id"]]
        data["projects"][item["id"]] = item
        return item
    return {"project": _change(put)}


def invalidate(key: str, *, removed_id: str = "") -> None:
    with _CACHE_LOCK:
        for (project_key, _), entry in _CACHE.items():
            if project_key == key:
                entry["checked_at"] = 0
                entry["generation"] = entry.get("generation", 0) + 1
                if removed_id:
                    entry["points"] = [p for p in entry["points"] if p["id"] != removed_id]
                    entry["signature"] = digest(json.dumps(entry["points"], ensure_ascii=False, sort_keys=True))


def _cache_point(key: str, point: dict[str, Any], actor: str) -> None:
    with _CACHE_LOCK:
        invalidate(key)
        for (project_key, cache_actor), entry in _CACHE.items():
            if project_key != key:
                continue
            known = any(p["id"] == point["id"] for p in entry["points"])
            if known or cache_actor == actor or point["source_id"].startswith(".wall/"):
                entry["points"] = [p for p in entry["points"] if p["id"] != point["id"]] + [point]
                entry["signature"] = digest(json.dumps(entry["points"], ensure_ascii=False, sort_keys=True))


def unregister(payload: dict[str, Any], actor: str) -> dict[str, Any]:
    key = str(payload.get('project') or '')
    with exclusive_file_lock(WALL_LOCK_ROOT / f'project-{digest(key)}.lock'):
        return _unregister(payload, actor)


def _unregister(payload: dict[str, Any], actor: str) -> dict[str, Any]:
    key = str(payload.get("project") or "")
    if key == "portal":
        raise ValueError("Portal 是 WALL 的初始工程")
    record = project(key, actor)
    _require_project_change(actor, str(record.get("project_id") or ""))
    def prepare(data):
        if any(j["project"] == key and j["status"] in ACTIVE for j in data["jobs"].values()):
            raise ValueError("请先等待或停止这个工程的工作")
        data['projects'][key]['deleting'] = True
    _change(prepare)
    from .wall_residents import release
    def remove(data):
        data["projects"].pop(key, None)
        data["jobs"] = {k: j for k, j in data["jobs"].items() if j["project"] != key}
        data['discussions'] = {k: v for k, v in data['discussions'].items()
                               if v.get('project') != key and v.get('resident_id') not in deleted}
        data['resident_cleanup'] = {k: v for k, v in data['resident_cleanup'].items() if v['project'] != key}
        for name in ('reads', 'reviews'):
            for values in data[name].values():
                values.pop(key, None)
    try:
        deleted = release(record, actor, _read_state(), blocks=None)
        _change(remove)
    except Exception as exc:
        # Preparing deletion is already durable, and some identities may have
        # been released. The same project can resume through release_roles.
        cause = failure_payload(exc)
        raise OperationError(
            "工程清理尚未完成，登记与待清理关系已保留；可再次取消登记继续清理",
            code="wall.unregister_pending", http_status=503, retryable=True,
            commit_state="unknown", details={"project": key, "cleanup": "pending",
                "retry": "same_project", "cause_code": cause["code"]},
        ) from exc
    with _CACHE_LOCK:
        for cache_key in list(_CACHE):
            if cache_key[0] == key:
                del _CACHE[cache_key]
    return {"removed": True}


def _scan(record: dict[str, Any], actor: str) -> None:
    cache_key = (record["id"], actor)
    with _CACHE_LOCK:
        generation = _CACHE.get(cache_key, {}).get("generation", 0)
        previous_documents = _CACHE.get(cache_key, {}).get("documents", {})
        previous_importance = _CACHE.get(cache_key, {}).get("importance_document", {})
    try:
        root = Path(record["root"])
        sources = index_sources(root)
        points: list[dict[str, Any]] = []
        errors = []
        warnings = []
        documents = {}
        def read(source):
            previous = previous_documents.get(source.address, {})
            same_source = previous.get("source") == source
            if not previous and RESOURCE_ID.fullmatch(source.address):
                version, text = read_resource_source_with_version(source.address, actor)
            else:
                version = source_version(root, source.address, actor)
                text = None
            if same_source and previous.get("version") == version:
                return previous
            if text is None:
                text = read_source(root, source.address, actor)
            content_hash = digest(text)
            if same_source and previous.get("content_hash") == content_hash:
                return {**previous, "version": version}
            parsed = parse_points(source, text)
            return {"source": source, "version": version, "content_hash": content_hash,
                    "points": parsed, "warnings": source_diagnostics(source, text, parsed)}
        # Bounded I/O only: this does not launch models or scan provider sessions.
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="wall-doc") as pool:
            futures = {pool.submit(read, source): source for source in sources}
            for future in as_completed(futures):
                source = futures[future]
                try:
                    document = future.result()
                    documents[source.address] = document
                    # Scores are overlaid on a copy so removing suggestions restores defaults.
                    points.extend(copy.deepcopy(document["points"]))
                    warnings.extend(document["warnings"])
                except PermissionError:
                    continue
                except Exception as exc:
                    errors.append({"source_id": source.address, "error": str(exc)})
        suggestions = inside(root, ".wall/importance.md")
        importance_document = {}
        if suggestions.is_file():
            version = source_version(root, ".wall/importance.md", actor)
            importance_document = previous_importance if previous_importance.get("version") == version else {
                "version": version, "text": read_source(root, ".wall/importance.md", actor),
            }
            apply_importance(points, importance_document["text"])
        visible_paths = {tuple(point["module_path"]) for point in points if point["module_path"]}
        modules = list(dict.fromkeys(source.module_path for source in sources if source.module_path in visible_paths))
        points.sort(key=lambda p: (p["domain"], p["source_id"], p["_start"]))
        with _CACHE_LOCK:
            previous = _CACHE.get(cache_key, {})
            if previous.get("generation", 0) != generation:
                previous.update(loading=False, checked_at=0)
                return
            old = {p["id"]: p for p in previous.get("points", [])}
            current_ids = {p["id"] for p in points}
            # Previous labels are comparison evidence for the maintenance
            # Resident, not an automatic guess about semantic equivalence.
            removed = [public_point(p) for key, p in old.items() if key not in current_ids]
            added = [public_point(p) for p in points if p["id"] not in old] if old else []
            reconciliation = previous.get("reconciliation", [])
            if removed and added:
                reconciliation = (reconciliation + [{"previous": removed, "current": added}])[-5:]
            _CACHE[cache_key] = {"points": points, "documents": documents, "importance_document": importance_document, "errors": errors, "warnings": sorted(warnings, key=lambda item: item["source_id"]), "loading": False, "generation": generation,
                                 "checked_at": time.monotonic(), "updated_at": time.time(),
                                 "source_count": len(sources), "reconciliation": reconciliation, "modules": modules,
                                 "signature": digest(json.dumps(points, ensure_ascii=False, sort_keys=True))}
    except Exception as exc:
        with _CACHE_LOCK:
            entry = _CACHE.setdefault(cache_key, {"points": []})
            if entry.get("generation", 0) != generation:
                entry.update(loading=False, checked_at=0)
                return
            entry.update(loading=False, errors=[{"error": str(exc)}], checked_at=time.monotonic())


def _cache(record: dict[str, Any], actor: str, refresh: bool = False) -> dict[str, Any]:
    cache_key = (record["id"], actor)
    with _CACHE_LOCK:
        entry = _CACHE.setdefault(cache_key, {"points": [], "errors": [], "loading": False, "checked_at": 0})
        if not entry["loading"] and (refresh or time.monotonic() - entry["checked_at"] >= SCAN_TTL):
            entry["loading"] = True
            if not business_tasks.start(f"wall-scan:{record['id']}:{actor}", _scan, record, actor):
                entry["loading"] = False
        return dict(entry)


def _add_resident_work_states(points: list[dict[str, Any]], actor: str) -> list[dict[str, str]]:
    from .society_service import get_society_service

    resident_ids = list(dict.fromkeys(point["resident_id"] for point in points if point.get("resident_id")))
    for point in points:
        point["resident_work_state"] = ""
    if not resident_ids:
        return []
    try:
        payload = get_society_service().resident.get_resident_states(resident_ids, actor)
    except Exception as error:
        LOG.warning("WALL Resident state projection unavailable: %s", type(error).__name__)
        return [{"code": "resident_state_unavailable", "message": "对话运行状态暂不可用，稍后自动重试。"}]
    states = {item["resident_id"]: str((item.get("state") or {}).get("work_state") or "")
              for item in payload.get("items", [])}
    for point in points:
        point["resident_work_state"] = states.get(point.get("resident_id"), "")
        if point.get("resident_id"):
            point["resident_available"] = point["resident_id"] in states
    return []


def board(key: str, actor: str, refresh: bool = False, known_signature: str = "") -> dict[str, Any]:
    record = project(key, actor)
    entry = _cache(record, actor, refresh)
    if entry["loading"] and not entry.get("signature"):
        # The first scan has no cards to project yet. Keep scan-status polls
        # off the jobs, discussions, and reading-state tables.
        return {"project": record, "blocks": [], "jobs": [], "recent_completed": [],
                "modules": [], "loading": True, "errors": [], "warnings": [],
                "source_count": 0, "format_hint": POINT_FORMAT_HINT, "signature": "",
                "capabilities": {"write": not record.get("deleting"), "register": True},
                "codex_accounts": []}
    state = _read_state()
    reads = state["reads"].get(actor, {}).get(key)
    if reads is None and entry.get("signature") and not entry["loading"] and not entry["errors"]:
        baseline = {point["id"]: _read_revision(point) for point in entry["points"]}
        reads = _change(lambda data: data["reads"].setdefault(actor, {}).setdefault(key, baseline))
        state["reads"].setdefault(actor, {})[key] = reads
    loading = entry["loading"] and not entry.get("signature")
    jobs = [job for job in state["jobs"].values() if job["project"] == key and job["actor"] == actor]
    jobs.sort(key=lambda job: (job.get("created_at", 0), job["id"]))
    can_write = not record.get("deleting")
    access = get_portal_access_store()
    capabilities = {"write": can_write, "register": access.is_active(actor)}
    from .execution.account_adapters import CodexRoutes
    accounts = [route.account_id for route in CodexRoutes().routes()] if can_write else []
    latest = {job.get("block", ""): job for job in sorted(jobs, key=lambda j: j["created_at"])}
    points = []
    for item in entry["points"]:
        point = public_point(item)
        discussion = state["discussions"].get(_discussion_key(key, point["id"], actor), {})
        job = latest.get(point["id"], {})
        point.update(job=job, discussion=_chat_identity(discussion), resident_id=discussion.get("resident_id") or job.get("resident_id") or "",
                     lineage_id=discussion.get("lineage_id") or job.get("lineage_id") or "")
        point.update(_point_reading_fields(point, key, actor, state))
        points.append(point)
    visible_targets = [*points, *(point["job"] for point in points if point["job"].get("resident_id")), *jobs[-80:]]
    warnings = [*entry.get("warnings", []), *_add_resident_work_states(visible_targets, actor)]
    discussions = {point["id"]: {name: point[name] for name in ("resident_id", "lineage_id", "resident_work_state", "discussion")}
                   for point in points if point["resident_id"]}
    signature = digest(json.dumps({"documents": entry.get("signature"), "jobs": jobs,
                                  "modules": entry.get("modules", []),
                                  "discussions": discussions, "loading": loading, "reads": reads,
                                  "reviews": state["reviews"].get(actor, {}).get(key, {}),
                                  "errors": entry["errors"], "warnings": warnings,
                                  "project": record, "capabilities": capabilities, "codex_accounts": accounts}, sort_keys=True, ensure_ascii=False))
    if known_signature and known_signature == signature and not refresh:
        return {"unchanged": True, "signature": signature}
    return {"project": record, "blocks": points, "jobs": jobs[-80:], "loading": loading,
            "recent_completed": _recent_completed(jobs, points),
            "modules": entry.get("modules", []),
            "errors": entry["errors"], "source_count": entry.get("source_count", 0),
            "warnings": warnings, "format_hint": POINT_FORMAT_HINT,
            "updated_at": entry.get("updated_at", 0), "signature": signature,
            "capabilities": capabilities, "codex_accounts": accounts}


def _recent_completed(jobs: list[dict[str, Any]], points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {point["id"]: point for point in points}
    completed = sorted((job for job in jobs if job.get("status") == "completed"),
                       key=lambda job: job.get("completed_at") or job.get("updated_at") or job.get("created_at", 0),
                       reverse=True)[:20]
    records = []
    for job in completed:
        result_ids = job.get("result_block_ids") or []
        results = []
        for block_id in dict.fromkeys(result_ids):
            point = by_id.get(block_id, {})
            results.append({"id": block_id, "title": point.get("title") or job.get("title") or "工作成果",
                            "kind": point.get("kind", "improvement"), "status": point.get("status", "dev")})
        records.append({"id": job["id"], "title": job.get("title") or job.get("action", "工作"),
                        "action": job.get("action", ""), "summary": job.get("summary", ""),
                        "completed_at": job.get("completed_at") or job.get("updated_at") or job.get("created_at", 0),
                        "resident_id": job.get("resident_id", ""), "tau_run_id": job.get("tau_run_id", ""),
                        "test_run_ids": job.get("test_run_ids", []), "publication_commit": job.get("publication_commit", ""),
                        "result_blocks": results})
    return records


def _point(key: str, block_id: str, actor: str) -> tuple[dict[str, Any], dict[str, Any], str, Source]:
    record = project(key, actor)
    address, sep, _ = block_id.rpartition("#")
    if not sep:
        raise ValueError("Block 地址无效")
    source = next((s for s in index_sources(Path(record["root"])) if s.address == address), None)
    if not source:
        raise KeyError("该来源已不在工程索引中")
    text = read_source(Path(record["root"]), address, actor)
    item = next((p for p in parse_points(source, text) if p["id"] == block_id), None)
    if not item:
        raise KeyError("这个点已被修改或移除，请刷新墙面")
    if inside(Path(record["root"]), ".wall/importance.md").is_file():
        apply_importance([item], read_source(Path(record["root"]), ".wall/importance.md", actor))
    version = frontmatter(text)[0].get("version")
    item["_source_version"] = version.get("number") if isinstance(version, dict) else version
    return record, item, text, source


def block(key: str, block_id: str, actor: str) -> dict[str, Any]:
    _, item, _, _ = _point(key, block_id, actor)
    point = public_point(item)
    state = _read_state()
    jobs = [job for job in state["jobs"].values()
            if job["project"] == key and job["actor"] == actor and job.get("block") == block_id]
    job = max(jobs, key=lambda job: job["created_at"], default={})
    identity = state["discussions"].get(_discussion_key(key, block_id, actor), {})
    # A document's author or worker is not automatically this reader's conversation.
    if not identity.get("resident_id") and point.get("resident_id"):
        from .society_service import get_society_service
        try:
            identity = get_society_service().resident.get_resident(point["resident_id"], actor)
        except (PermissionError, KeyError, SocietyServiceUnavailable):
            identity = {}
    display_identity = identity if identity.get("resident_id") else job
    point.update(job=job, discussion=_chat_identity(identity), resident_id=display_identity.get("resident_id") or "",
                 lineage_id=display_identity.get("lineage_id") or "")
    point.update(_point_reading_fields(point, key, actor, state))
    warnings = _add_resident_work_states([point], actor)
    point["test_binding"] = point_test_binding(block_id) if point["kind"] == "design" else None
    return {"block": point, "warnings": warnings}


def point_test_binding(point_ref: str) -> dict[str, Any]:
    from management_portal.test_framework import api as test_api
    from management_portal.test_framework.models import CatalogError

    try:
        case = test_api.case_for_design_point(point_ref)
    except CatalogError as exc:
        return {"status": "conflict", "reason": str(exc), "ref": point_ref}
    if not case:
        return {"status": "unbound", "ref": point_ref}
    return {"status": "bound", "ref": point_ref, "case_id": case["id"], "title": case["title"],
            "lifecycle": case["lifecycle"], "url": "/wall?" + urlencode({"case": case["id"]})}


def resolve_design_point(point_ref: str, actor: str) -> dict[str, Any]:
    from management_portal.test_framework.models import CatalogError, design_point_ref

    if not actor:
        raise PermissionError("需要登录")
    ref = design_point_ref(point_ref)
    if not ref:
        raise CatalogError("请选择一个完整 WALL 设计点 ID")
    address = ref.partition("#")[0]
    matches = []
    invalid_reason = ""
    for key in _read_state()["projects"]:
        try:
            record = project(key, actor)
        except PermissionError:
            continue
        try:
            sources = index_sources(Path(record["root"]))
        except (FileNotFoundError, NotADirectoryError):
            # A removed registered workspace must not block other projects.
            continue
        source = next((source for source in sources if source.address == address), None)
        if source is None:
            continue
        try:
            _, point, _, _ = _point(key, ref, actor)
        except PermissionError:
            continue
        except (KeyError, FileNotFoundError) as exc:
            invalid_reason = str(exc)
            continue
        if point["kind"] != "design":
            invalid_reason = "绑定目标必须是 WALL 设计点"
            continue
        matches.append({"status": "valid", "ref": ref, "title": point["title"], "project": key,
                        "url": "/wall?" + urlencode({"project": key, "block": ref})})
    if len(matches) > 1:
        return {"status": "conflict", "ref": ref, "reason": "此完整点 ID 出现在多个 WALL 工程，请使用唯一的资源来源"}
    if matches:
        return matches[0]
    if invalid_reason:
        return {"status": "invalid", "ref": ref, "reason": invalid_reason}
    return {"status": "unavailable", "ref": ref, "reason": "设计来源未登记到可访问的 WALL 工程，或当前账号无权读取"}


def test_binding(query: dict[str, list[str]], actor: str) -> dict[str, Any]:
    from management_portal.test_framework import api as test_api

    if not actor:
        raise PermissionError("需要登录")
    case_id = str((query.get("case_id") or [""])[0])
    if case_id:
        case = test_api.case_definition(case_id)
        ref = case.get("design_point_ref")
        if not ref:
            return {"status": "unbound", "case_id": case_id, "ref": None}
        return {**resolve_design_point(ref, actor), "case_id": case_id}
    ref = str((query.get("block") or [""])[0])
    resolution = resolve_design_point(ref, actor)
    return {**resolution, "test_binding": point_test_binding(ref)} if resolution["status"] == "valid" else resolution


def _read_revision(point: dict[str, Any]) -> str:
    return digest(json.dumps([point.get(field) for field in ("id", "kind", "status", "title", "content")], ensure_ascii=False))


def _point_reading_fields(point: dict[str, Any], key: str, actor: str, state: dict[str, Any]) -> dict[str, Any]:
    revision = _read_revision(point)
    reads = state["reads"].get(actor, {}).get(key)
    reviews = state["reviews"].get(actor, {}).get(key, {})
    return {"read_revision": revision, "unread": reads is not None and reads.get(point["id"]) != revision,
            "reviewed": reviews.get(point["id"]) == revision}


def set_reviewed(payload: dict[str, Any], actor: str) -> dict[str, Any]:
    reviewed = payload.get("reviewed")
    if not isinstance(reviewed, bool):
        raise ValueError("reviewed 应为布尔值")
    key = str(payload.get("project") or "portal")
    block_id = str(payload.get("block") or "")
    _, point, _, _ = _point(key, block_id, actor)
    revision = _read_revision(point)
    if payload.get("read_revision") != revision:
        invalidate(key)
        raise ValueError("内容已变化，请查看最新内容后重新设置审核状态")
    def save(data):
        marks = data["reviews"].setdefault(actor, {}).setdefault(key, {})
        if reviewed:
            marks[block_id] = revision
        else:
            marks.pop(block_id, None)
        return {"block": block_id, "reviewed": reviewed, "read_revision": revision}
    return _change(save)


def mark_read(payload: dict[str, Any], actor: str) -> dict[str, Any]:
    key = str(payload.get("project") or "portal")
    block_id = str(payload.get("block") or "")
    _, point, _, _ = _point(key, block_id, actor)
    revision = _read_revision(point)
    if payload.get("read_revision") != revision:
        return {"read": False, "block": block_id}
    def save(data):
        data["reads"].setdefault(actor, {}).setdefault(key, {})[block_id] = revision
        return {"read": True, "block": block_id, "read_revision": revision}
    return _change(save)


def create(payload: dict[str, Any], actor: str) -> dict[str, Any]:
    key = str(payload.get("project") or "portal")
    record = project(key, actor, write=True)
    kind = str(payload.get("kind") or "improvement")
    if kind not in KINDS:
        raise ValueError("未知的点类型")
    degree = validate_metaness(payload["metaness"]) if "metaness" in payload else None
    point_key = secrets.token_hex(8)
    name = {"design": "design.md", "improvement": f"plans/{point_key}.md", "inconsistency": "inconsistencies.md", "direction": "directions.md"}[kind]
    address = ".wall/" + name
    path = inside(Path(record["root"]), address)
    meta = {"key": point_key, "kind": kind, "importance": importance(payload.get("importance")), "related": _related(payload.get("related", []))}
    formatter = document_markdown if kind == "improvement" else point_markdown
    content = formatter(str(payload.get("title") or ""), str(payload.get("content") or ""), meta)
    block_id = f"{address}#plan" if kind == "improvement" else f"{address}#{point_key}"
    with exclusive_file_lock(WALL_LOCK_ROOT / f"markdown-{digest(str(path))}.lock"):
        text = path.read_text(encoding="utf-8") if path.exists() else f"# {record['title']} · WALL\n\n## 设计点与工作\n\n"
        updated = content if kind == "improvement" else text.rstrip() + "\n\n" + content
        properties = {"importance": meta["importance"]} if kind == "improvement" else {}
        if degree is not None:
            properties["metaness"] = degree
        if properties:
            updated = update_frontmatter(updated, properties)
        point = next((p for p in parse_points(Source(address, record["title"], "WALL", kind), updated) if p["id"] == block_id), None)
        if point is None:
            raise ValueError("请输入可识别的标题和正文")
        atomic_replace_text(path, updated)
        _ensure_local_index(Path(record["root"]), address, kind)
        indexed = next(source for source in index_sources(Path(record["root"])) if source.address == address)
        point.update(domain=indexed.domain, module_path=list(indexed.module_path))
    if degree is not None and kind != "improvement":
        invalidate(key)
        _scan(record, actor)
    else:
        _cache_point(key, point, actor)
    return {"block": {**public_point(point), **_point_reading_fields(point, key, actor, _read_state())}}


def _ensure_local_index(root: Path, address: str, kind: str) -> None:
    filename = "SPEC.md" if kind == "design" else "IMPROVE.md" if kind == "improvement" else ".wall/WALL.md"
    path = index_file(root, filename)
    with exclusive_file_lock(WALL_LOCK_ROOT / f"index-{digest(str(path))}.lock"):
        text = path.read_text(encoding="utf-8") if path.exists() else "# WALL 文档索引\n"
        link = Path(address).name if filename.startswith(".wall/") else address
        if f"]({link})" not in text:
            atomic_replace_text(path, text.rstrip() + f"\n\n- [WALL {kind}]({link})\n")


def _related(value: object) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("关联点应为列表")
    return list(dict.fromkeys(str(v) for v in value if str(v).strip()))


def _markdown_lock(record: dict[str, Any], address: str):
    source = address if RESOURCE_ID.fullmatch(address) else str(inside(Path(record["root"]), address))
    return exclusive_file_lock(WALL_LOCK_ROOT / f"markdown-{digest(source)}.lock")


def edit(payload: dict[str, Any], actor: str) -> dict[str, Any]:
    key, block_id = str(payload.get("project") or "portal"), str(payload.get("block") or "")
    record = project(key, actor, write=True)
    with _markdown_lock(record, block_id.rpartition("#")[0]):
        return _edit_point(key, block_id, payload, actor)


def _edit_point(key: str, block_id: str, payload: dict[str, Any], actor: str) -> dict[str, Any]:
    record, point, text, source = _point(key, block_id, actor)
    meta = dict(point["_meta"])
    meta.update(key=point["id"].rpartition("#")[2], kind=point["kind"])
    if "importance" in payload:
        meta["importance"] = importance(payload["importance"])
    if "related" in payload:
        meta["related"] = _related(payload["related"])
    title = str(payload.get("title", point["title"]))
    meta["title"] = title
    if point["_syntax"] == "document":
        replacement = document_markdown(title, str(payload.get("content", point["content"])), meta)
    elif point["_syntax"] != "list":
        # The source's table/subsection shape survives a full editor form too.
        raw = str(payload.get("content", point["content"]))
        from .wall_documents import WALL_COMMENT
        annotation = metadata_comment(meta)
        clean = WALL_COMMENT.sub("", raw).strip()
        if point["_syntax"] == "table":
            if "\n" in clean or not clean.startswith("|"):
                raise ValueError("表格中的点请保留一行 Markdown 表格格式")
            replacement = clean.replace("|", "| " + annotation, 1) + "\n"
        else:
            if not re.match(r"^#{2,6}\s", clean):
                raise ValueError("分节中的点请保留 Markdown 标题")
            if title != point["title"]:
                clean = re.sub(r"^(#{2,6})\s[^\n]+", lambda match: match[1] + " " + title, clean, count=1)
            replacement = annotation + "\n" + clean + "\n\n"
    else:
        content = str(payload.get("content", point["content"]))
        if title != point["title"]:
            content = content.replace(f"**{point['title']}**", f"**{title}**", 1)
        replacement = point_markdown(title, content, meta)
    updated = replace_point(text, point, replacement)
    properties = {}
    if "metaness" in payload:
        properties["metaness"] = validate_metaness(payload["metaness"])
    if point["_syntax"] == "document" and "importance" in payload:
        properties["importance"] = importance(payload["importance"])
    if properties:
        updated = update_frontmatter(updated, properties)
    edited = next((p for p in parse_points(source, updated) if p["id"] == block_id), None)
    if edited is None:
        raise ValueError("修改后的 Markdown 无法识别这个点")
    write_source(Path(record["root"]), point["source_id"], updated, actor, expected_version=point.get("_source_version"))
    if "metaness" in payload:
        invalidate(key)
        _scan(record, actor)
    else:
        _cache_point(key, edited, actor)
    return {"updated": True, "block": {**public_point(edited), **_point_reading_fields(edited, key, actor, _read_state())}}


def _remove_point(record: dict[str, Any], block_id: str, actor: str) -> None:
    cleanup_key = digest(f"{record['id']}\0{block_id}")
    previous = _read_state()['resident_cleanup'].get(cleanup_key, {})
    try:
        _, point, _, _ = _point(record['id'], block_id, actor)
        resident_ids = [point['resident_id']] if point.get('resident_id') else []
    except KeyError:
        resident_ids = previous.get('resident_ids', [])
    _change(lambda data: data['resident_cleanup'].__setitem__(cleanup_key, {
        'project': record['id'], 'block': block_id, 'actor': actor, 'resident_ids': resident_ids,
    }))
    _remove_point_document(record, block_id, actor)
    _change(lambda data: data['resident_cleanup'][cleanup_key].update(removed=True))


def _remove_point_document(record: dict[str, Any], block_id: str, actor: str) -> None:
    with _markdown_lock(record, block_id.rpartition("#")[0]):
        try:
            _, point, text, _ = _point(record["id"], block_id, actor)
        except KeyError:
            invalidate(record["id"], removed_id=block_id)
            return  # A Resident may already have removed it while updating the Plan.
        root = Path(record["root"])
        if point["_syntax"] == "document":
            write_source(root, point["source_id"], update_frontmatter(text, {"status": "obsolete"}), actor, expected_version=point.get("_source_version"))
            index = index_file(root, "IMPROVE.md")
            if index.is_file():
                with exclusive_file_lock(WALL_LOCK_ROOT / f"index-{digest(str(index))}.lock"):
                    original = index.read_text(encoding="utf-8")
                    updated = remove_index_reference(original, point["source_id"])
                    if updated != original:
                        atomic_replace_text(index, updated)
        else:
            write_source(root, point["source_id"], replace_point(text, point, ""), actor, expected_version=point.get("_source_version"))
        invalidate(record["id"], removed_id=block_id)


def _discussion_key(key: str, block_id: str, actor: str) -> str:
    return digest(f"{key}\0{block_id}\0{actor}")


def _chat_identity(identity: dict[str, Any]) -> dict[str, str]:
    return {name: str(identity.get(name) or "") for name in ("resident_id", "lineage_id")}


def _codex_account(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Codex 账号必须是已登记的账号 ID")
    account = value.strip().lower()
    if account:
        from .execution.account_adapters import CodexRoutes
        return CodexRoutes().get(account).account_id
    return ""


def discuss(key: str, block_id: str, actor: str, *, backend_route_id: str = "") -> dict[str, Any]:
    with exclusive_file_lock(WALL_LOCK_ROOT / f'project-{digest(key)}.lock'):
        return _discuss(key, block_id, actor, backend_route_id=backend_route_id)


def _discuss(key: str, block_id: str, actor: str, *, backend_route_id: str = "") -> dict[str, Any]:
    record = project(key, actor, write=True)
    _, point, _, _ = _point(key, block_id, actor)
    scope = f"wall-discussion:{key}:{digest(block_id)}:{actor}"
    from .discussion_residents import resolve
    identity = resolve(surface="wall",
        actor=actor, resident_scope=scope, lineage_scope=f"wall:{key}", create=True,
        backend_route_id=backend_route_id,
        title=f"WALL · {point['title'][:65]}", cwd=record["root"],
        metadata={"surface": "wall", "block_id": block_id},
    )
    _change(lambda data: data["discussions"].__setitem__(_discussion_key(key, block_id, actor),
                                                      {**identity, 'project': key, 'block': block_id}))
    return {**identity, "title": point["title"]}


def _patch_job(job_id: str, **changes: Any) -> dict[str, Any]:
    def patch(data):
        job = data["jobs"][job_id]
        job.update(changes, updated_at=time.time())
        return job
    return _change(patch)


def action(payload: dict[str, Any], actor: str) -> dict[str, Any]:
    key = str(payload.get('project') or 'portal')
    with exclusive_file_lock(WALL_LOCK_ROOT / f'project-{digest(key)}.lock'):
        return _action(payload, actor)


def _action(payload: dict[str, Any], actor: str) -> dict[str, Any]:
    key, name, block_id = str(payload.get("project") or "portal"), str(payload.get("action") or ""), str(payload.get("block") or "")
    record = project(key, actor, write=True)
    if name == "discuss":
        return _discuss(key, block_id, actor, backend_route_id=_codex_account(payload.get("backend_route_id", "")))
    if name == "stop":
        job_id = str(payload.get("job_id") or "")
        jobs = [j for j in _read_state()["jobs"].values() if j["project"] == key
                and (j["id"] == job_id if job_id else j.get("block") == block_id)
                and j["actor"] == actor and j["status"] in ACTIVE]
        for job in jobs:
            _patch_job(job["id"], cancel_requested=True)
            if job.get("tau_run_id"):
                from management_portal.test_framework import api as test_api
                test_api.cancel_run(job["tau_run_id"], actor=actor)
            if job.get("resident_id") and job.get("message_id"):
                resident_handoff.cancel_resident_invocation(job["resident_id"], actor, job["message_id"])
        return {"accepted": True}
    allowed = {"implement": {"improvement"}, "reject": {"improvement", "inconsistency", "direction"},
               "retire": {"design"}, "align-design": {"inconsistency"}, "align-code": {"inconsistency"},
               "approve-direction": {"direction"}, "publish": {"dev"}}
    if name not in {*allowed, *PROJECT_ACTIONS}:
        raise ValueError("未知 WALL 操作")
    if name in PROJECT_ACTIONS and block_id:
        raise ValueError("工程级操作不能指定卡片")
    point = None
    if name == 'reject':
        cleanup_key = digest(f'{key}\0{block_id}')
        if cleanup_key in _read_state()['resident_cleanup']:
            _remove_point(record, block_id, actor)
            cleanup = _try_resident_cleanup(key, actor)
            return {'accepted': True, 'removed': True, 'cleanup': cleanup}
        if any(j['project'] == key and j.get('block') == block_id and j['status'] in ACTIVE
               for j in _read_state()['jobs'].values()):
            raise ValueError('请先等待或停止这个条目的工作')
    if name in allowed:
        _, point, _, _ = _point(key, block_id, actor)
        if point["status"] not in allowed[name]:
            raise ValueError("当前点的状态不支持这项操作")
    if name == "reject":
        _remove_point(record, block_id, actor)
        cleanup = _try_resident_cleanup(key, actor)
        return {"accepted": True, "removed": True, "cleanup": cleanup}
    account = _codex_account(payload.get("backend_route_id", ""))
    job = {"id": "wall-" + secrets.token_hex(8), "project": key, "block": block_id,
           "actor": actor, "action": name, "backend_route_id": account, "status": "queued", "created_at": time.time(),
           "message": str(payload.get("message") or ""), "title": point["title"] if point else PROJECT_ACTIONS[name],
           "source_id": point["source_id"] if point else "", "cancel_requested": False}
    if name == "verify" and payload.get("test_case_ids"):
        job["test_case_ids"] = _related(payload["test_case_ids"])
    def enqueue(data):
        # One click is one task. Distinct blocks remain independently runnable.
        existing = next((j for j in data["jobs"].values() if j["project"] == key and j["actor"] == actor and j.get("block") == block_id and j["action"] == name and j["status"] in ACTIVE), None)
        if existing:
            return existing
        data["jobs"][job["id"]] = job
        return job
    accepted = _change(enqueue)
    _start_job(accepted["id"])
    return {"accepted": True, "job": accepted}


def _task_prompt(record: dict[str, Any], job: dict[str, Any], point: dict[str, Any] | None) -> str:
    actions = {
        "implement": "整份Plan对应一个显著改进。先完整阅读背景、问题证据、目标体验、方案边界和验收，再实现并真实验证，按需更新相关Design。保留Plan来源供结果追溯；完成后WALL将整份Plan退出IMPROVE索引，不逐条删除正文或把细节拆成新改进。",
        "align-code": "修改当前实现使其符合这个不一致点所引用的设计，并真实验证。",
        "align-design": "以当前实现为依据更新对应设计，解释清楚当前行为；不修改无关功能。",
        "retire": "用户决定抛弃这个设计点。删除相关功能实现，清理真正无用的路径，更新规范和定向测试。",
        "approve-direction": "用户认可这个演化方向。自主读取其关联内容、拆解、实现和真实验证；通过HATG创建或组织协作。仅在目标冲突、重大代价或超出授权时请求判断。",
        "analyse": "维护此工程的WALL。阅读SPEC.md、IMPROVE.md及实现，只保留值得独立审查、能显著改善用户任务或系统能力的改进，一份Plan只解释一个核心变化并链接IMPROVE.md。完整说明背景、现状后果、目标体验、方案与检验；细节归入该Plan正文。没有重要发现就不创建Plan，不按Design配额凑数。metaness表示文档的meta层次，与importance分开评价；未评价可省略。实现/规范不一致与演化方向按各自类型记录。已授权的显著日常bug可以直接修复并验证。分析的成果是WALL文档：发现的不一致或缺陷如实记录后即为完成（completed），未修复的问题写入summary，不因被分析的工程本身未通过验证而返回blocked；只有分析本身未能完成时才返回blocked。",
        "verify": "你是本工程固定的回归测试与修复 Resident。先读取工程测试规范与目录，通过现有测试运行入口组织全面回归，覆盖适用的后端、浏览器及TAU真实使用测试；如本次输入指定用例，只验证指定范围并明确记录范围。你负责调用测试、观察真实终态、分析失败、修复并复测。区分产品代码bug、错误或过时的测试断言、环境问题；以有效Design和真实业务语义为依据修正代码或测试，不能靠删掉用例、跳过失败或弱化正确断言制造通过。保存实际命令、测试报告、失败原因和修复证据；已通过且相关代码未变的测试不重复运行，修复优先重跑失败与受影响用例。全面回归独立于全量发布，不自动调用全量发布、不等待发布完成；本次实际修复通过定向验证后，依开发规则发布相关改动。结果附真实test_run_ids（如使用测试框架），未解决的问题如实返回blocked。",
        "publish-all": "将此工程当前Dev源码完整同步到Release，补齐漏发的新增、修改和删除。全量同步与全面回归独立，不要求全量测试通过、工作区干净、其他功能完成或人工版本号。保留并行编辑，不清理或覆盖开发工作树。使用工程已有发布入口，工程没有全量发布能力时返回blocked，不借用其他工程的工具。发布后检查实际健康、命令输出和Release分支的commit；不要删除、完成或重写任何WALL功能卡片。" + (
            " Portal执行 ./scripts/publish_portal_release --all --message 'WALL全量同步Dev到Release'"
            + " --origin wall --wall-job " + job.get("id", "")
            + "。成功后在WALL_RESULT附完整publication_commit和实际验证结果；不传--block。WALL校验该commit已在Release分支历史中。"
            if record["id"] == "portal" else ""
        ),
        "publish": "将当前成果及必要依赖以小批量同步到 Release。先查明实际文件范围，保留同文件中已经混合的修改及其他人的工作；不要求工作区干净、全功能完成或全量测试；发布工具会把发布 commit 自动推送到全部远端，不需要另外推送。只做本次变化所需的定向验证。使用该工程的更新入口，工程没有发布入口时返回blocked；不调用其他工程的工具。不要自行删除或标记其他待发布成果。" + (
            " Portal 执行 ./scripts/publish_portal_release --message '<说明>' --path '<文件或目录>'（可重复）"
            + " --origin wall --wall-job " + job.get("id", "") + " --block '" + job.get("block", "") + "'。"
            + "发布工具返回ok后，在WALL_RESULT附完整publication_commit和实际验证结果；WALL校验该commit为Release分支HEAD或祖先后，只更新当前任务对应的成果。"
            if record["id"] == "portal" else ""
        ),
    }
    source = public_point(point) if point else {"indexes": ["SPEC.md", "IMPROVE.md", ".wall/WALL.md"]}
    with _CACHE_LOCK:
        reconciliation = copy.deepcopy(_CACHE.get((record["id"], job["actor"]), {}).get("reconciliation", []))
    if job["action"] == "analyse" and reconciliation:
        source["semantic_comparison"] = reconciliation
    return (
        f"你正在为工程 {record['title']} 执行WALL工作。源码工作目录：{record['root']}。\n"
        + actions[job["action"]]
        + "\n工作直接在当前代码工作树进行，与其他Resident自主协调。保留其他人的无关修改。不创建每任务分支、锁或逐功能构建记录。"
        + ("\n自动日常修复未获授权，本次只发现并记录建议。" if job["action"] == "analyse" and not record.get("automation", {}).get("auto_fix") else "")
        + "\nMarkdown是设计/改进/方向的事实来源。不要每个点新建代码文件。改进可对应零个、一个或多个设计点，也可只更新正文。"
        + "\nSPEC.md、IMPROVE.md 和 .wall/WALL.md 只作导航索引，正文中的 wall 点注释不会成为卡片。实际不一致点写入 .wall/inconsistencies.md，再从索引链接；关联使用实际工作文档的来源#局部key。完成前逐一核对本次声明的成果及关联都能投影到墙面。"
        + ("\n分析没有发现值得登记的改进、不一致或方向时，在结果摘要说明即可，不为凑数创建卡片或点来源文件。补充验证报告可以从实际点的正文引用，不把只有‘未发现问题’说明的报告链接到WALL点来源索引。" if job["action"] == "analyse" else "")
        + "\n文字改写、拆分、合并后由你根据语义判断点是否延续。必要时在点前加 <!-- wall: {\"key\":\"原局部key\",\"importance\":8,\"related\":[\"source#key\"]} --> 保留关联；不要把文字hash作为语义判断。"
        + "\n重要度按具体点的影响、范围和用途评估，不整篇文档统一打分。可在.wall/importance.md中维护Markdown表格「点 | 重要度 | 理由」，点使用真实来源#局部key，分数1-10，附具体理由。原点明确设置的importance优先；增改点时按语义更新建议，保留其他已评估点。"
        + "\nPlan优先使用知识库plan资源并在IMPROVE.md索引；普通工程也可在.wall/plans/中创建一改进一文件，再由IMPROVE.md链接。Plan使用一级标题和完整叙述，Block地址为来源#plan，细节不拆卡片。Design、方向、不一致及Dev结果仍可分别在.wall/design.md、directions.md、inconsistencies.md、dev.md中使用一级列表，一项一个完整点；wall注释可附kind、importance、related、recommendation，蓝色结果附status:dev。metaness写在来源文档YAML头部，缺省合法，显式值为1..10整数，不按重要度猜测。"
        + "\n本次任务的蓝色成果默认由WALL收到完成结果后写入，你负责代码、Design和Plan的更新。如你已经撰写或更新了对应的蓝色成果，在最后的WALL_RESULT中给出dev_block_id（该成果的来源#局部key），WALL会复用它。"
        + "\n知识库资源仍通过当前Resident受认证Resource工具读取/更新，不自报其他身份。方向文档和关联也都是Markdown。"
        + "\n授权中不含擅自扩大权限或操作无关工程。每完成一个获授权的功能或修复并通过定向验证，就发布对应改动及必要依赖；工程没有发布入口时只在结果中说明未发布，已完成并验证的工作仍返回completed，不因缺少发布入口返回blocked；用户明确暂缓发布时遵从用户要求。WALL不是唯一发布通道。只运行测试或只讨论不隐式触发发布。"
        + "\n完成后最后一行返回 WALL_RESULT: {\"status\":\"completed\",\"summary\":\"实际完成与验证结果\"}。尚未完成、验证不通过或需要用户决策时返回status:blocked和具体原因，不能误报完成。"
        + "如成果关联仍存在的设计点或方向，可在结果中附related地址列表；没有关联时留空，不指向已删除的Plan条目。"
        + "\n工作输入：\n" + json.dumps(source, ensure_ascii=False)
        + ("\n本次指定测试用例：" + json.dumps(job["test_case_ids"], ensure_ascii=False) if job.get("test_case_ids") else "")
        + "\n用户补充：\n" + job.get("message", "")
    )


def _result(text: str) -> dict[str, Any]:
    for line in reversed(text.splitlines()):
        if "WALL_RESULT:" not in line:
            continue
        raw = line.split("WALL_RESULT:", 1)[1].strip().rstrip("`")
        try:
            result = json.loads(raw)
            if isinstance(result, dict) and result.get("status") in {"completed", "blocked"}:
                return result
        except ValueError:
            pass
    raise ValueError("Resident 尚未返回明确的 WALL 工作结果，请在对话中查看进展")


def _unrecognized_outputs(record: dict[str, Any], job: dict[str, Any]) -> list[str]:
    """WALL sources written during this job that yield no point, when no source written during it yields one.

    A supporting report linked next to the analysed points (for example a
    verification note) shows its own no-point warning but does not void the
    points the analysis left.
    """
    root = Path(record["root"])
    started = float(job.get("created_at") or 0)
    found = []
    misplaced = []
    indexes = [index_file(root, name) for name in ("SPEC.md", "IMPROVE.md")]
    indexes.append(root / ".wall/WALL.md")
    for path in indexes:
        if path.is_file() and path.stat().st_mtime >= started and has_point_annotations(path.read_text(encoding="utf-8")):
            misplaced.append(str(path.relative_to(root)))
    has_points = False
    for source in index_sources(root):
        if RESOURCE_ID.fullmatch(source.address):
            continue
        path = inside(root, source.address)
        if not path.is_file() or path.stat().st_mtime < started:
            continue
        text = path.read_text(encoding="utf-8")
        if any(item.get("code") == "no_points" for item in source_diagnostics(source, text, parse_points(source, text))):
            found.append(source.address)
        else:
            has_points = True
    return sorted(misplaced or ([] if has_points else found))


def _finish(record: dict[str, Any], job: dict[str, Any], result: dict[str, Any]) -> None:
    with exclusive_file_lock(WALL_LOCK_ROOT / f"project-{digest(record['id'])}.lock"):
        _finish_locked(record, job, result)


def _finish_locked(record: dict[str, Any], job: dict[str, Any], result: dict[str, Any]) -> None:
    if job["action"] == "analyse" and result["status"] == "completed":
        # A completed analysis must leave WALL-readable points, not only a report.
        unrecognized = _unrecognized_outputs(record, job)
        if unrecognized:
            _patch_job(job["id"], status="blocked", error=(
                "分析已结束，但 WALL 没有从这些文档识别出点：" + "、".join(unrecognized)
                + "。SPEC.md、IMPROVE.md、.wall/WALL.md 只作导航索引。设计写入 SPEC 链接的设计文档，Plan 为一改进一文件并由 IMPROVE.md 链接，"
                "不一致写入 .wall/inconsistencies.md、方向写入 .wall/directions.md，一项一个一级列表点；修正后重新分析。"))
            invalidate(record["id"])
            return
    if job["action"] == "verify":
        run_ids = result.get("test_run_ids", [])
        if not isinstance(run_ids, list) or any(not isinstance(item, str) or not re.fullmatch(r"testrun_[0-9a-f]{16}", item) for item in run_ids):
            raise ValueError("测试结果中的 test_run_ids 格式错误")
        _patch_job(job["id"], test_run_ids=list(dict.fromkeys(run_ids)))
    if result["status"] != "completed":
        _patch_job(job["id"], status="blocked", error=str(result.get("summary") or "需要处理"))
        return
    name = job["action"]
    creates_dev = name in {"implement", "align-code", "retire", "approve-direction"}
    existing_dev = str(result.get("dev_block_id") or "").strip()
    if creates_dev and existing_dev:
        _, point, _, _ = _point(record["id"], existing_dev, job["actor"])
        if point["status"] != "dev":
            raise ValueError("Resident 指定的成果不是蓝色 Dev 条目")
    if creates_dev and not existing_dev:
        path = inside(Path(record["root"]), ".wall/dev.md")
        meta = {"key": job["id"], "kind": "improvement", "status": "dev", "importance": 7,
                "resident_id": job.get("resident_id", ""),
                "related": _related(result.get("related", [job["block"]] if name == "approve-direction" and job["block"] else []))}
        with exclusive_file_lock(WALL_LOCK_ROOT / f"markdown-{digest(str(path))}.lock"):
            text = path.read_text(encoding="utf-8") if path.exists() else "# Dev 中的工作成果\n\n## 待发布\n\n"
            existing = parse_points(Source(".wall/dev.md", "Dev", "WALL", "improvement"), text)
            if not any(p["id"] == f'.wall/dev.md#{job["id"]}' for p in existing):
                atomic_replace_text(path, text.rstrip() + "\n\n" + point_markdown(job["title"], str(result.get("summary") or ""), meta))
            _ensure_local_index(Path(record["root"]), ".wall/dev.md", "direction")
        existing_dev = f'.wall/dev.md#{job["id"]}'
    if name in {"publish", "publish-all"} and record["id"] == "portal":
        from scripts.portal_release import require_released_commit
        commit = require_released_commit(result.get("publication_commit"))
        _patch_job(job["id"], publication_commit=commit)
        if name == "publish" and job["block"]:
            # The job owns the affected card; the Resident supplies only the commit.
            try:
                _remove_point(record, job["block"], job["actor"])
            except KeyError:
                pass  # Recovery after the removal committed is idempotent.
    if (name in {"implement", "align-code", "align-design", "retire"}
            or (name == "publish" and record["id"] != "portal")) and job["block"]:
        _remove_point(record, job["block"], job["actor"])
    _patch_job(job["id"], status="completed", summary=str(result.get("summary") or ""), error="",
               completed_at=time.time(), result_block_ids=[existing_dev] if creates_dev else [])
    invalidate(record["id"])


def _run_job(job_id: str) -> None:
    try:
        job = _read_state()["jobs"][job_id]
        record = project(job["project"], job["actor"], write=True)
        if job.get("cancel_requested"):
            _patch_job(job_id, status="cancelled")
            return
        if not job.get("message_id"):
            point = _point(job["project"], job["block"], job["actor"])[1] if job["block"] else None
            scope = job.get("resident_scope") or (
                f"wall-work:{record['id']}:regression" if job["action"] == "verify" else
                f"wall-work:{record['id']}:{digest(job['block'] or job['action'])}"
            )
            _patch_job(job_id, status="routing", resident_scope=scope)
            prompt = _task_prompt(record, job, point)
            if job["action"] in {"implement", "align-code", "retire", "approve-direction"}:
                try:
                    from .resident_recommendation_backend import recommend_residents
                    recommendation = recommend_residents(
                        actor=job["actor"], limit=1, allow_fallback=True,
                        query=f"工程：{record['title']}。工作目录：{record['root']}。任务：{job['title']}。\n{(point or {}).get('content', '')[:3000]}",
                    )
                    suggestions = [item for item in recommendation.get("recommendations", [])
                                   if item.get("confidence") in {"high", "medium"}]
                    if suggestions:
                        prompt += "\nWhich Resident 建议的领域协作者，可参考其已有知识并按HATG协作：\n" + json.dumps(suggestions, ensure_ascii=False)
                        _patch_job(job_id, recommendation=suggestions)
                except Exception as exc:
                    _patch_job(job_id, routing_note=str(exc))
            if _read_state()["jobs"][job_id].get("cancel_requested"):
                _patch_job(job_id, status="cancelled")
                return
            def assigned(identity):
                latest = _patch_job(job_id, status="preparing", **identity)
                if latest.get("cancel_requested"):
                    raise RuntimeError("工作已停止，任务尚未投递")
            from .workbench_projections.native import submit
            from .wall_workflows import work_flow
            handle = submit("wall",
                workflow=work_flow(),
                actor=job["actor"], message=prompt, resident_scope=scope, create_role=True,
                lineage_scope=f"wall:{record['id']}", title=f"WALL · {job['title'][:65]}",
                cwd=record["root"], business_ref=job_id,
                backend_route_id=str(job.get("backend_route_id") or ""),
                metadata={"surface": "wall", "block_id": job["block"]},
                on_assigned=assigned,
            )
            job = _patch_job(job_id, status="running", **handle)
        response = resident_handoff.resume_and_wait(
            actor=job["actor"], resident_id=job["resident_id"], message_id=job["message_id"],
            lineage_id=job.get("lineage_id", ""),
            cancellation_requested=lambda: bool(_read_state()["jobs"][job_id].get("cancel_requested")),
        )
        if _read_state()["jobs"][job_id].get("cancel_requested"):
            _patch_job(job_id, status="cancelled")
            return
        result = _result(str(response.get("message") or ""))
        _finish(record, job, result)
    except Exception as exc:
        latest = _read_state()["jobs"].get(job_id, {})
        receipt = failure_payload(exc)
        service_recoverable = isinstance(exc, (SocietyServiceBusy, SocietyServiceUnavailable)) or (
            receipt["code"] == "instruction.owner_unavailable"
            and receipt["details"].get("owner") == "lineage"
            and receipt["retryable"] and receipt["commit_state"] == "not_committed"
        )
        if service_recoverable:
            # Resume the durable job and its original input after service recovery.
            # Unknown or committed instruction receipts need explicit resolution.
            _patch_job(job_id, status="cancelled" if latest.get("cancel_requested") else "queued",
                       error="", routing_note=str(exc), summary="等待 Lineage 服务恢复后继续原任务")
            return
        error = str(exc)
        if latest.get("resident_id") and not latest.get("message_id") and not latest.get("cancel_requested"):
            error = f"工作准备失败，任务尚未投递。请打开 Resident 查看工作区准备状态，确认就绪后重试；重试沿用当前卡片的 Resident 关联。原因：{error}"
        unresolved = isinstance(exc, OperationError) and receipt["commit_state"] in {"committed", "unknown"}
        creation = receipt["details"].get("resident_creation") or {}
        identity = {key: creation[key] for key in ("resident_id", "lineage_id") if creation.get(key)}
        _patch_job(job_id, status="cancelled" if latest.get("cancel_requested") else "blocked" if unresolved else "failed",
                   error=error, operation_error=receipt, **identity)
        LOG.warning("WALL task %s: %s", job_id, json.dumps(receipt, ensure_ascii=False))


def _start_job(job_id: str) -> None:
    if os.getenv("PORTAL_INSTANCE", "dev") == "release":
        return
    business_tasks.start(f"wall-job:{job_id}", _run_job, job_id)


def automation(payload: dict[str, Any], actor: str) -> dict[str, Any]:
    key = str(payload.get("project") or "portal")
    project(key, actor, write=True)
    interval = int(payload.get("interval_minutes") or 60)
    if not 10 <= interval <= 10080:
        raise ValueError("自动分析间隔为 10 至 10080 分钟")
    if not isinstance(payload.get("enabled"), bool) or not isinstance(payload.get("auto_fix", True), bool):
        raise ValueError("自动推进和自动修复使用开关值")
    value = {"enabled": bool(payload.get("enabled")), "interval_minutes": interval,
             "auto_fix": bool(payload.get("auto_fix", True)), "actor": actor}
    value["backend_route_id"] = _codex_account(payload.get("backend_route_id", ""))
    schedules = payload.get("schedules", {})
    if not isinstance(schedules, dict) or set(schedules) - {"verify", "publish-all"}:
        raise ValueError("独立计划只支持全面回归和全量发布")
    configured = {}
    for name, config in schedules.items():
        if not isinstance(config, dict) or not isinstance(config.get("enabled"), bool):
            raise ValueError("计划启用状态使用开关值")
        minutes = int(config.get("interval_minutes", 1440))
        if not 10 <= minutes <= 10080:
            raise ValueError("计划间隔为 10 至 10080 分钟")
        configured[name] = {"enabled": config["enabled"], "interval_minutes": minutes, "actor": actor,
                            "backend_route_id": value["backend_route_id"]}
    def save(data):
        record = data["projects"][key]
        record["automation"] = {**value, "last_run": record.get("automation", {}).get("last_run", 0)}
        for name, config in configured.items():
            previous = record.setdefault("schedules", {}).get(name, {})
            record["schedules"][name] = {**config, "last_run": previous.get("last_run", 0)}
        return record
    saved = _change(save)
    start_runtime()
    return {"automation": saved["automation"], "schedules": saved.get("schedules", {})}


def _drain_resident_cleanup(key: str, actor: str) -> None:
    from .wall_residents import release
    data = _read_state()
    pending = {k: v for k, v in data['resident_cleanup'].items() if v['project'] == key}
    if not pending:
        return
    record = project(key, actor)
    for entry in pending.values():
        if not entry.get('removed'):
            _remove_point(record, entry['block'], actor)
    data = _read_state()
    blocks = {v['block'] for v in pending.values()}
    deleted = release(record, actor, data, blocks=blocks)
    def finish(current):
        for token in pending:
            current['resident_cleanup'].pop(token, None)
        current['discussions'] = {k: v for k, v in current['discussions'].items()
                                  if v.get('resident_id') not in deleted}
    _change(finish)


def _try_resident_cleanup(key: str, actor: str) -> str:
    try:
        _drain_resident_cleanup(key, actor)
    except Exception as exc:
        def defer(data):
            for entry in data['resident_cleanup'].values():
                if entry['project'] == key:
                    attempt = int(entry.get('attempt', 0)) + 1
                    entry.update(attempt=attempt, retry_at=time.time() + min(300, 5 * 2 ** min(attempt, 6)))
        _change(defer)
        LOG.warning('WALL Resident cleanup pending for %s: %s', key, exc)
        return 'pending'
    return 'completed'


def release_test_fixtures(root: Path, manifest: dict[str, Any], actor: str, *, timeout: float) -> tuple[dict[str, Any], list[str]]:
    """Unregister the WALL projects a test run created under ``root`` and reject its recorded cards.

    The test framework's ``test.fixtures`` hook: ``manifest['wall_items']``
    keeps the cards still to reject; returns ``({projects, items}, errors)``.
    """
    projects_found = [p for p in projects(actor)['projects']
                      if p['id'] != 'portal' and Path(p['root']).resolve().is_relative_to(root)
                      and Path(p['root']).resolve() != root]
    errors, removed_projects, removed_items = [], [], []
    # No title search and no test-only Resident deletion: ordinary WALL owns it.
    for found in projects_found:
        try:
            key = found['id']
            state = _read_state()
            jobs = [j for j in state['jobs'].values() if j['project'] == key and j['status'] in ACTIVE]
            for job in jobs:
                if job['actor'] != actor:
                    raise ValueError('测试工程仍有其他调用者的工作')
                action({'project': key, 'action': 'stop', 'job_id': job['id']}, actor)
            deadline = time.monotonic() + timeout
            while any(j['project'] == key and j['status'] in ACTIVE for j in _read_state()['jobs'].values()):
                if time.monotonic() >= deadline:
                    raise RuntimeError('测试工程的 WALL 工作尚未停止')
                time.sleep(0.1)
            while True:
                try:
                    unregister({'project': key}, actor)
                    break
                except Exception as exc:
                    if getattr(exc, 'code', '') != 'projection.resident_busy' or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.1)
            removed_projects.append(key)
        except Exception as exc:
            errors.append(f"{found['id']}: {exc}")
    # For the few cases that specifically test cards in an existing project,
    # the agent must record exact newly-created card addresses before cleanup.
    for item in manifest.get('wall_items', []):
        try:
            key, block_id = item['project'], item['block']
            if key not in removed_projects:
                try:
                    action({'project': key, 'block': block_id, 'action': 'reject'}, actor)
                except KeyError:
                    # It may already be removed by the UI; finish its cleanup.
                    with exclusive_file_lock(WALL_LOCK_ROOT / f'project-{digest(key)}.lock'):
                        _drain_resident_cleanup(key, actor)
            removed_items.append(item)
        except Exception as exc:
            errors.append(f'{item}: {exc}')
    manifest['wall_items'] = [i for i in manifest.get('wall_items', []) if i not in removed_items]
    return {'projects': removed_projects, 'items': len(removed_items)}, errors


def _tick() -> None:
    data = _read_state()
    for key, actor in {(v['project'], v['actor']) for v in data['resident_cleanup'].values()
                       if float(v.get('retry_at', 0)) <= time.time()}:
        with exclusive_file_lock(WALL_LOCK_ROOT / f'project-{digest(key)}.lock'):
            _try_resident_cleanup(key, actor)
    for job in data["jobs"].values():
        if job["status"] in ACTIVE:
            _start_job(job["id"])
    if not data["scheduling_enabled"]:
        return
    for key, record in data["projects"].items():
        if record.get('deleting'):
            continue
        plans = {"analyse": record.get("automation", {}), **record.get("schedules", {})}
        for name, auto in plans.items():
            if not auto.get("enabled") or not auto.get("actor"):
                continue
            if time.time() - auto.get("last_run", 0) < auto.get("interval_minutes", 60) * 60:
                continue
            def claim(current):
                if not current["scheduling_enabled"]:
                    return False
                record = current["projects"][key]
                config = record["automation"] if name == "analyse" else record["schedules"][name]
                if not config.get("enabled") or time.time() - config.get("last_run", 0) < config.get("interval_minutes", 60) * 60:
                    return False
                config["last_run"] = time.time()
                return True
            if _change(claim):
                try:
                    action({"project": key, "action": name, "backend_route_id": auto.get("backend_route_id", "")}, auto["actor"])
                except Exception as exc:
                    LOG.warning("WALL scheduled %s %s: %s", name, key, exc)


def start_runtime() -> None:
    global _RUNTIME_STARTED, _RUNTIME_STOP, _RUNTIME_THREAD
    # Release serves the same documents; only Dev owns automatic job dispatch.
    if os.getenv("PORTAL_INSTANCE", "dev") == "release":
        return
    with _RUNTIME_LOCK:
        if _RUNTIME_STARTED:
            return
        _RUNTIME_STARTED = True
        stop = threading.Event()
        _RUNTIME_STOP = stop
        def loop():
            while not stop.is_set():
                try:
                    _tick()
                except Exception:
                    LOG.exception("WALL maintenance failed")
                stop.wait(5)
        _RUNTIME_THREAD = threading.Thread(target=loop, daemon=True, name="wall-maintenance")
        _RUNTIME_THREAD.start()


def stop_runtime() -> None:
    global _RUNTIME_STARTED, _RUNTIME_STOP, _RUNTIME_THREAD
    with _RUNTIME_LOCK:
        if _RUNTIME_STOP:
            _RUNTIME_STOP.set()
        thread = _RUNTIME_THREAD
        _RUNTIME_STARTED, _RUNTIME_STOP, _RUNTIME_THREAD = False, None, None
    if thread:
        thread.join(timeout=2)


def web_lifespan():
    """Run WALL maintenance while Web runs (``web.lifespan``)."""
    start_runtime()
    return stop_runtime


def source_document(key: str, address: str, actor: str) -> dict[str, Any]:
    record = project(key, actor)
    root = Path(record["root"])
    source = next((item for item in index_sources(root) if item.address == address), None)
    if source is None:
        raise KeyError("该来源已不在工程索引中")
    return {"source_id": address, "title": source.title, "content": read_source(root, address, actor)}


def handle_get(path: str, query: dict[str, list[str]], actor: str) -> dict[str, Any]:
    key = str((query.get("project") or ["portal"])[0])
    if path == "/api/wall/test-binding":
        return test_binding(query, actor)
    if path == "/api/wall/semantic-searches":
        from management_portal import semantic_search_jobs

        return semantic_search_jobs.list_searches("wall", actor, project=key)
    if path == "/api/wall/projects":
        return projects(actor)
    if path == "/api/wall/board":
        return board(key, actor, (query.get("refresh") or [""])[0] == "1", str((query.get("signature") or [""])[0]))
    if path == "/api/wall/block":
        return block(key, str((query.get("block") or [""])[0]), actor)
    if path == "/api/wall/source":
        return source_document(key, str((query.get("source") or [""])[0]), actor)
    raise KeyError("WALL API 不存在")


def documentation_preview(payload: dict[str, Any], actor: str) -> dict[str, Any]:
    from .wall_release_documents import preview

    record = project(str(payload.get("project") or "portal"), actor)
    return preview(record, actor)


def handle_post(path: str, payload: dict[str, Any], actor: str) -> dict[str, Any]:
    if path == "/api/wall/documentation/preview":
        return documentation_preview(payload, actor)
    if path.startswith("/api/wall/semantic-search/"):
        from management_portal import semantic_search_jobs

        actions = {
            "start": semantic_search_jobs.start_search,
            "refresh": semantic_search_jobs.refresh_search,
            "delete": semantic_search_jobs.delete_search,
        }
        search_action = actions.get(path.removeprefix("/api/wall/semantic-search/"))
        if not search_action:
            raise KeyError("WALL API 不存在")
        return search_action("wall", payload, actor)
    handlers = {"register": register, "unregister": unregister, "create": create, "edit": edit, "action": action, "automation": automation, "read": mark_read, "review": set_reviewed}
    name = path.removeprefix("/api/wall/")
    if name not in handlers:
        raise KeyError("WALL API 不存在")
    return handlers[name](payload, actor)
