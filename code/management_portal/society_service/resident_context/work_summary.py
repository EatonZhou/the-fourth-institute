"""Resident-authored work facts, mechanical coverage checks and facade rollups.

Search, routing and work views read the same summary. Family members write
their own; a facade's steward writes its summary; the global writer maintains
standalone Residents. No model runs here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from management_common.message_identity import derived_message_identity

from .evidence import fingerprint, validate_summary
from .service import resident_context

REMINDER_REF = "duty/work-summary-reminder"
ROLLUP_REF = "duty/family-work-summary"
MAINTENANCE_REFS = (REMINDER_REF, ROLLUP_REF, "duty/family-progress")
REMINDER = (
    "你有已完成轮次尚未登记工作摘要。请用 GetResidentSummaries（resident_ids 只含自己，include_maintenance=true）"
    "读取 layers.recent 和 missing_message_ids；用 UpdateResidentWorkSummary 替换近期事实，勿复制阶段与长期层。"
    "当前输入 ID 可用 ListResidentStates（resident_ids 只含自己）返回的 states[0].state.active_message_id 获取。"
    "提交 resident_id、message_ids（覆盖的旧轮次及当前输入）和 summary；"
    "内容确实无新增时可省略 summary，只登记已检查的轮次。以后每轮完成前同样维护自己的摘要。"
    "保留未决承诺、阻塞、约束和项目记录入口，遵守 consolidation.policy.total_chars 的各层各字段合计字数上限（默认 5000）。不写测试数量。无需回复提醒。"
)


def _record(resident_id: str) -> dict:
    from .. import resident_record_domain
    record = resident_record_domain.find_resident(resident_id)
    if not record:
        raise KeyError("Resident 不存在")
    return record


def _visible(actor: str, resident_id: str) -> dict:
    context = resident_context()
    if resident_id not in context.source.authorized_resident_ids(actor, [resident_id]):
        raise PermissionError("无权读取这个 Resident")
    return _record(resident_id)


def _family(record: dict) -> tuple[dict, dict]:
    from ..state_store import get_lineage_data_store
    from .. import family_storage
    fid = (record.get("family") or {}).get("family_id")
    if not fid:
        raise ValueError("Resident 不属于 Family")
    store = get_lineage_data_store()
    with store.database.read_current() as connection:
        row = connection.execute(f"SELECT {','.join(family_storage.COLUMNS['families'])} FROM families WHERE family_id=%s AND deleted_at IS NULL", (fid,)).fetchone()
        roles = family_storage.roles_of(connection, fid)
    if not row:
        raise ValueError("Family 已解除")
    return dict(row), roles


def maintenance_view(actor: str, resident_id: str) -> dict:
    """The written summary with what its maintainer needs: revision, sources, uncovered rounds and writer."""
    return maintenance_views(actor, [resident_id], strict=True)[resident_id]


def maintenance_views(actor: str, resident_ids: list[str], *, visible: set[str] | frozenset[str] = frozenset(),
                      strict: bool = False) -> dict[str, dict]:
    """``maintenance_view`` of each Resident the actor may read: one authorization and one read for the batch.

    ``visible`` are IDs the caller already authorized for ``actor``. An ID that is not visible or has no record
    is left out, or raises PermissionError/KeyError when ``strict``.
    """
    context = resident_context()
    ids = list(dict.fromkeys(resident_ids))
    unchecked = [rid for rid in ids if rid not in visible]
    allowed = set(visible) | (context.source.authorized_resident_ids(actor, unchecked) if unchecked else set())
    records: dict[str, dict] = {}
    for resident_id in ids:
        if resident_id not in allowed:
            if strict:
                raise PermissionError("无权读取这个 Resident")
            continue
        try:
            records[resident_id] = _record(resident_id)
        except KeyError:
            if strict:
                raise
    items = context.store.work_summaries(list(records))
    views: dict[str, dict] = {}
    for resident_id, record in records.items():
        item = items[resident_id]
        if item["missing_message_ids"] and _tracks_work_rounds(record):
            reminder = _work_summary_reminder(record, resident_id, item["missing_message_ids"])
            if reminder is not None:
                item["reminder"] = reminder
        views[resident_id] = {**item, "title": str(record.get("title") or ""), "lineage_id": str(record.get("lineage_id") or "")}
    return views


def _tracks_work_rounds(record: dict) -> bool:
    from .. import family_facade
    return (not record.get("deleted_at") and (record.get("family") or {}).get("role") == "member"
            and not family_facade.is_facade(record))


def record_completed_work_round(resident_id: str, message_id: str) -> None:
    """Register a completed business input for coverage before terminal observers read it."""
    from .. import resident_conversation_stream, resident_held_infos
    key = (resident_id, message_id)
    repo = resident_conversation_stream.get_repository()
    if repo.input_statuses_by_id([key]).get(key, {}).get("state") != "completed":
        return
    try:
        record = _record(resident_id)
    except KeyError:
        return
    if not _tracks_work_rounds(record) or resident_held_infos.is_info(record, message_id):
        return
    if repo.message_business_ref(resident_id, message_id).startswith(MAINTENANCE_REFS):
        return
    resident_context().store.missing_work_round(resident_id, message_id)


def _work_summary_reminder(record: dict, resident_id: str, missing_ids: list[str]) -> dict | None:
    from .. import resident_conversation_stream, resident_held_infos
    repo = resident_conversation_stream.get_repository()
    if any(repo.message_business_ref(resident_id, entry["message_id"]) == REMINDER_REF
           for entry in resident_held_infos.peek(record)):
        return None
    states = repo.input_statuses_by_id([(resident_id, mid) for mid in missing_ids])
    completed = [
        (datetime.fromisoformat(state["updated_at"]).astimezone(timezone.utc), mid)
        for (_, mid), state in states.items()
        if state.get("state") == "completed" and state.get("updated_at")
    ]
    if not completed:
        return None
    _, oldest_id = min(completed)
    return {
        "message_id": derived_message_identity(REMINDER_REF, resident_id, oldest_id),
        "content_parts": [{"type": "text", "text": REMINDER}],
    }


def update_resident_work_summary(actor: str, resident_id: str,
                                 summary: Any = None, message_ids: list[str] | None = None) -> dict:
    from .. import resident_conversation_stream, family_facade
    record = _visible(actor, resident_id)
    if actor != resident_id or family_facade.is_facade(record):
        raise PermissionError("工作摘要只能由 Resident 本人写入")
    ids = list(dict.fromkeys(message_ids or []))
    if not ids:
        raise ValueError("必须提供本次摘要覆盖的 message_ids")
    if len(ids) > 128 or any(not isinstance(mid, str) or not mid for mid in ids):
        raise ValueError("message_ids 最多 128 个有效输入 ID")
    repository = resident_conversation_stream.get_repository()
    for mid in ids:
        state = repository.input_state(resident_id, mid)
        if not state or state.get("state") not in {"running", "completed"}:
            raise ValueError("摘要只能登记本人正在运行或已经完成的输入")
    clean = validate_summary(summary) if summary is not None else None
    counted = [mid for mid in ids if not repository.message_business_ref(resident_id, mid).startswith(MAINTENANCE_REFS)]
    return resident_context().store.write_work_summary(
        resident_id, actor, clean, ids, {}, temporal=True, counted_message_ids=counted,
    )


def update_resident_summary_consolidation(actor: str, resident_id: str, consolidation: dict) -> dict:
    from .. import family_facade
    record = _visible(actor, resident_id)
    if family_facade.is_facade(record):
        raise ValueError("门面由本 Family Steward 整体汇总，不归纳门面时间层")
    _, roles = _family(record)
    if actor not in roles["steward"] or resident_id not in roles["member"]:
        raise PermissionError("只有本 Family Steward 可以归纳业务成员摘要")
    if not isinstance(consolidation, dict):
        raise ValueError("consolidation 必须是 JSON object")
    return resident_context().store.write_work_summary(
        resident_id, actor, None, [], {}, temporal=True, consolidation=consolidation,
    )


def _facade_family(record: dict) -> tuple[dict, dict]:
    if (record.get("family") or {}).get("role") != "facade":
        return {}, {"facade": [], "steward": [], "member": []}
    return _family(record)


def update_facade_summary(actor: str, resident_id: str, summary: Any, source_versions: dict) -> dict:
    from .. import family_facade
    facade = _visible(actor, resident_id)
    _, roles = _facade_family(facade)
    if resident_id not in roles["facade"] or actor not in roles["steward"] or not family_facade.is_facade(facade):
        raise PermissionError("门面工作摘要只能由本 Family 的 Steward 写入")
    if not isinstance(source_versions, dict) or not source_versions:
        raise ValueError("门面汇总必须保存 GetFacadeSummaryInputs 返回的 source_versions")
    return resident_context().store.write_work_summary(
        resident_id, actor, validate_summary(summary), [], source_versions,
    )


def get_facade_summary_inputs(actor: str, resident_id: str, *, overview_only: bool = False) -> dict:
    from management_portal import permission_system
    from .. import family_facade
    facade = _visible(actor, resident_id)
    family, roles = _facade_family(facade)
    owns_family_summary = resident_id in roles["facade"] and actor in roles["facade"] + roles["steward"]
    if actor != resident_id and not owns_family_summary and not permission_system.allows_roles(
        actor, [("@admin", resident_id)],
    ):
        raise PermissionError("需要门面本人、本 Family Steward 或门面管理权限")
    relay_targets = family_facade.relay_target_ids(facade)
    if not relay_targets or facade.get("deleted_at"):
        raise ValueError("Resident 未登记为门面")
    context = resident_context()
    family_members = set(roles["member"]) if owns_family_summary else set()
    operating_roles = set(roles["steward"] + roles.get("router", []))
    member_ids = [rid for rid in relay_targets if rid not in operating_roles]
    other_members = [rid for rid in member_ids if rid not in family_members]
    if other_members:
        visible = set(context.source.authorized_resident_ids(actor, other_members))
        member_ids = [rid for rid in member_ids if rid in family_members or rid in visible]
    title = family.get("title") or str(facade.get("title") or resident_id)
    goal = family.get("goal", "")
    members = []
    pending = []
    pending_inputs = {}
    versions: dict[str, Any] = {"roster": fingerprint({"members": member_ids, "title": title, "goal": goal})}
    for rid in member_ids:
        work = context.store.work_summary(rid)
        record = _record(rid)
        summary = work["summary"]
        versions[rid] = fingerprint({"title": record.get("title"), "summary": summary,
                                     "missing": bool(work["missing_message_ids"])})
        members.append({"resident_id": rid, "title": record.get("title", rid), "summary": summary,
                        "summary_stale": bool(work["missing_message_ids"]) or not bool(summary)})
        # A nested facade belongs to its own Steward's summary workflow.
        if rid in family_members and not family_facade.is_facade(record) and work["consolidation"]["due"]:
            pending.append({"resident_id": rid, "revision": work["revision"],
                            "level": work["consolidation"]["level"], "reason": work["consolidation"]["reason"]})
            pending_inputs[rid] = work["pending_message_ids"]
    # Result reports and notices the facade kept reach members through the facade summary.
    notices = [{**notice, "text": notice["text"][:1500]} for notice in family_facade.kept_notices(facade)]
    if notices:
        versions["notices"] = fingerprint([[notice["message_id"], notice.get("kept_at")] for notice in notices])
    previous = context.store.work_summary(resident_id)
    changed = not family.get("personal_ama_id") and previous["source_versions"] != versions
    if overview_only:
        # The ID depends only on the material: a summary task that was
        # completed, failed or stopped is not sent again until it changes.
        material = {"versions": versions, "due": pending}
        if pending_inputs:
            material["pending_inputs"] = pending_inputs
        return {"resident_id": resident_id, "needs_update": changed or bool(pending),
                "facade_needs_update": changed, "consolidation_due": pending,
                "notification": {
                    "message_id": derived_message_identity(ROLLUP_REF, resident_id, fingerprint(material)),
                    "content_parts": [{"type": "text", "text":
                        f"门面 {resident_id} 有摘要维护待办。先用 GetFacadeSummaryInputs overview_only=true 查看；"
                        "对 consolidation_due 按需读取 GetResidentSummaries include_maintenance=true，"
                        "用 UpdateResidentSummaryConsolidation 归纳阶段与长期层，"
                        "在 consolidated_message_ids 中列出已纳入的 pending_message_ids，保留未决事项和项目记录入口。"
                        "汇总由本 Family Steward 调用 UpdateFacadeSummary，原样提交 source_versions。"
                        "遵守各层合计字数上限，归纳而非截断，不读取成员历史。"}]}}
    return {"resident_id": resident_id, "title": title, "goal": goal,
            "members": members, "notices": notices, "source_versions": versions, "previous_summary": previous["summary"],
            "needs_update": changed or bool(pending),
            "facade_needs_update": changed, "consolidation_due": pending}
