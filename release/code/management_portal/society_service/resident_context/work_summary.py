"""Resident-authored work facts, mechanical coverage checks and Family rollups.

Search, routing and work views read the same summary. Family members write
their own; a facade's steward writes its summary; the global writer maintains
standalone Residents. No model runs here.
"""
from __future__ import annotations

from typing import Any
import time

from management_common.message_identity import derived_message_identity

from .evidence import fingerprint, validate_summary
from .service import resident_context

REMINDER_REF = "duty/work-summary-reminder"
ROLLUP_REF = "duty/family-work-summary"
REMINDER = (
    "你有已完成轮次尚未登记工作摘要。请用 GetResidentSummaries（resident_ids 只含自己，include_maintenance=true）"
    "读取 layers.recent、revision 和 missing_message_ids；用 UpdateResidentWorkSummary 替换近期事实，勿复制阶段与长期层。"
    "当前输入 ID 可用 ListResidentStates（resident_ids 只含自己）返回的 states[0].state.active_message_id 获取。"
    "提交 resident_id、message_ids（覆盖的旧轮次及当前输入）、expected_revision 和 summary；"
    "内容确实无新增时可省略 summary，只登记已检查的轮次。以后每轮完成前同样维护自己的摘要。"
    "保留未决承诺、阻塞、约束和项目记录入口，遵守 consolidation.policy 总预算；不写测试数量。无需回复提醒。"
)


def _record(resident_id: str) -> dict:
    from ..resident_workspace_domain import _find_resident
    record = _find_resident(resident_id)
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
    record = _visible(actor, resident_id)
    item = resident_context().store.work_summary(resident_id)
    return {**item, "title": str(record.get("title") or ""), "lineage_id": str(record.get("lineage_id") or "")}


def update_resident_work_summary(actor: str, resident_id: str, expected_revision: int,
                                summary: Any = None, message_ids: list[str] | None = None,
                                source_versions: dict | None = None, consolidation: dict | None = None) -> dict:
    from .. import resident_conversation_stream, family_facade
    record = _visible(actor, resident_id)
    facade = family_facade.is_facade(record)
    if consolidation is not None:
        if facade:
            raise ValueError("门面由本 Family Steward 整体汇总，不归纳其他 Family 的门面时间层")
        _, roles = _family(record)
        if actor not in roles["steward"] or resident_id not in roles["member"]:
            raise PermissionError("只有本 Family Steward 可以归纳业务成员摘要")
        if summary is not None or message_ids or source_versions:
            raise ValueError("归纳只提交 consolidation 与 expected_revision，不登记成员轮次")
    elif facade:
        _, roles = _family(record)
        if actor not in roles["steward"]:
            raise PermissionError("门面工作摘要只能由本 Family 的 Steward 写入")
        if not isinstance(source_versions, dict) or not source_versions:
            raise ValueError("门面汇总必须保存 GetFamilySummaryInputs 返回的 source_versions")
        if message_ids:
            raise ValueError("门面汇总不登记成员轮次")
    elif actor != resident_id:
        raise PermissionError("工作摘要只能由 Resident 本人写入")
    ids = list(dict.fromkeys(message_ids or []))
    if not facade and consolidation is None and not ids:
        raise ValueError("必须提供本次摘要覆盖的 message_ids")
    if len(ids) > 128 or any(not isinstance(mid, str) or not mid for mid in ids):
        raise ValueError("message_ids 最多 128 个有效输入 ID")
    repository = resident_conversation_stream.get_repository()
    for mid in ids:
        state = repository.input_state(resident_id, mid)
        if not state or state.get("state") not in {"running", "completed"}:
            raise ValueError("摘要只能登记本人正在运行或已经完成的输入")
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
        raise ValueError("expected_revision 必须是工作摘要的非负整数版本")
    clean = validate_summary(summary) if summary is not None else None
    counted = [mid for mid in ids if not repository.message_business_ref(resident_id, mid).startswith(
        (REMINDER_REF, ROLLUP_REF, "duty/family-progress"))]
    return resident_context().store.write_work_summary(resident_id, actor, clean, ids,
                                                      expected_revision, source_versions or {},
                                                      temporal=not facade, consolidation=consolidation,
                                                      counted_message_ids=counted)


def check_resident_work_summary(actor: str, resident_id: str, message_id: str) -> dict:
    from .. import invariant_locks, resident_conversation_stream, resident_held_infos, family_facade
    from ..resident_execution_domain import deliver_resident_message_content
    record = _visible(actor, resident_id)
    if family_facade.is_facade(record):
        return {"status": "facade"}
    if (record.get("family") or {}).get("role") in {"router", "steward"}:
        return {"status": "skipped"}
    _, roles = _family(record)
    if actor not in roles["facade"] + roles["steward"] + [resident_id]:
        raise PermissionError("摘要检查只允许本 Family 或 Resident 本人执行")
    repo = resident_conversation_stream.get_repository()
    state = repo.input_state(resident_id, message_id)
    ref = repo.message_business_ref(resident_id, message_id)
    if not state or state.get("state") != "completed" or ref.startswith((REMINDER_REF, ROLLUP_REF, "duty/family-progress")):
        return {"status": "skipped"}
    store = resident_context().store
    if not store.missing_work_round(resident_id, message_id):
        return {"status": "covered", "consolidation_due": store.work_summary(resident_id)["consolidation"]["due"]}
    # The held Mailbox is authoritative: one fixed reminder covers every gap.
    # Only this look-then-attach step is serialized, so two checks for
    # different rounds cannot both attach one.
    with invariant_locks.resident_lock(resident_id, operation="work-summary-check"):
        if any(repo.message_business_ref(resident_id, item["message_id"]) == REMINDER_REF for item in resident_held_infos.peek(record)):
            return {"status": "reminder_pending"}
        reminder_id = derived_message_identity(REMINDER_REF, resident_id, message_id)
        deliver_resident_message_content(resident_id, roles["facade"][0], REMINDER,
                                        activation_policy="attach_to_next", business_ref=REMINDER_REF,
                                        message_id=reminder_id)
    return {"status": "reminder_attached"}


def get_family_summary_inputs(actor: str, resident_id: str, *, overview_only: bool = False) -> dict:
    facade = _visible(actor, resident_id)
    family, roles = _family(facade)
    if resident_id not in roles["facade"] or actor not in roles["facade"] + roles["steward"]:
        raise PermissionError("仅本 Family 的门面与 Steward 可以读取汇总材料")
    context = resident_context()
    members = []
    versions: dict[str, Any] = {"roster": fingerprint({"members": roles["member"], "title": family["title"], "goal": family["goal"]})}
    for rid in roles["member"]:
        work = context.store.work_summary(rid)
        record = _record(rid)
        summary = work["summary"]
        versions[rid] = fingerprint({"title": record.get("title"), "summary": summary,
                                     "missing": bool(work["missing_message_ids"])})
        members.append({"resident_id": rid, "title": record.get("title", rid), "summary": summary,
                        "summary_stale": bool(work["missing_message_ids"]) or not bool(summary)})
    previous = context.store.work_summary(resident_id)
    pending = []
    for rid in dict.fromkeys(roles["member"]):
        # A nested facade belongs to its own Steward's summary workflow.
        from .. import family_facade
        if family_facade.is_facade(_record(rid)):
            continue
        work = context.store.work_summary(rid)
        if work["consolidation"]["due"]:
            pending.append({"resident_id": rid, "revision": work["revision"],
                            "level": work["consolidation"]["level"], "reason": work["consolidation"]["reason"]})
    changed = not family.get("personal_ama_id") and previous["source_versions"] != versions
    if overview_only:
        return {"resident_id": resident_id, "needs_update": changed or bool(pending),
                "facade_needs_update": changed, "consolidation_due": pending,
                "notification": {
                    "message_id": derived_message_identity(ROLLUP_REF, resident_id, fingerprint(
                        {"versions": versions, "due": pending, "hour": int(time.time() // 3600)})),
                    "content_parts": [{"type": "text", "text":
                        f"Family {resident_id} 有摘要维护待办。先用 GetFamilySummaryInputs overview_only=true 查看；"
                        "对 consolidation_due 按需读取 GetResidentSummaries include_maintenance=true，"
                        "用 UpdateResidentWorkSummary 的 consolidation 归纳阶段/长期层，保留未决事项和项目记录入口。"
                        "随后按需读取 Family 材料并更新门面；遵守返回的总预算，不读取成员历史。"}]}}
    return {"resident_id": resident_id, "title": family["title"], "goal": family["goal"],
            "members": members, "source_versions": versions, "previous_summary": previous["summary"],
            "expected_revision": previous["revision"], "needs_update": changed or bool(pending),
            "facade_needs_update": changed, "consolidation_due": pending}
