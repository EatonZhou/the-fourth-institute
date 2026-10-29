"""Count distinct facade inputs and publish bounded, retryable Family reports.

The Family ledger owns report batches. Steward supplies the prose and decides
which members need a separate, targeted update; publishing never broadcasts it.
"""
from __future__ import annotations

import copy
import json

from management_common.message_identity import derived_message_identity

from . import family_facade, resident_conversation_stream, resident_held_infos
from .resident_context import work_summary
from .resident_context.evidence import validate_summary
from .state_store import get_lineage_data_store
from . import lineage_aggregate

REF = "duty/family-progress"
MAX_CHARS = 500
DEFAULT_INTERVAL = 20


def _family(lineage_id, family_id, actor, *, publishing=False):
    from .family_familize import read_family
    lineage, block = read_family(lineage_id, family_id, actor)
    family = block["family"]
    if publishing:
        if actor != family["steward"]:
            raise PermissionError("Family 简报只能由本 Family 的 Steward 发布")
    elif actor not in {family["facade"], family["steward"]}:
        lineage_aggregate.ensure_manager(lineage, actor)
    return block, family


def _ledger(relay):
    return relay.setdefault("progress", {"seen": [], "count": 0, "reported_through": 0, "batches": {}})


def _input(facade, event):
    payload = (event or {}).get("payload") or {}
    if (event or {}).get("type") != "resident.message_received" or payload.get("resident_id") != facade:
        return ""
    mid = str(payload.get("message_id") or "")
    repository = resident_conversation_stream.get_repository()
    try:
        state = repository.input_state(facade, mid)
    except KeyError:
        return ""
    message = repository.message(facade, mid) or {}
    # Infos also have an input state, but never constitute a working turn.
    # Recovery occurrences retain the original input ID and count only once.
    return mid if (state and message.get("role") == "user"
                   and not resident_held_infos.is_info({"resident_id": facade}, mid)) else ""


def check(lineage_id, family_id, actor, *, event=None, every_turns=DEFAULT_INTERVAL):
    if isinstance(every_turns, bool) or not isinstance(every_turns, int) or every_turns < 1:
        raise ValueError("简报轮次间隔必须是正整数")
    block, family = _family(lineage_id, family_id, actor)
    facade = family["facade"]
    mid = _input(facade, event)
    repository = get_lineage_data_store().family_relays
    with repository.transaction(lineage_id, facade) as relay:
        ledger = _ledger(relay)
        if mid and mid not in ledger["seen"]:
            ledger["seen"].append(mid)
            ledger["count"] += 1
        if ledger["count"] - ledger["reported_through"] >= every_turns:
            through = ledger["count"]
            batch_id = str(through)
            text = f"第 {ledger['reported_through'] + 1}–{through} 轮进展简报。"
            ledger["batches"][batch_id] = {
                "from_turn": ledger["reported_through"] + 1, "through_turn": through,
                "request_id": derived_message_identity(REF, facade, batch_id),
                "request_text": text, "requested": False, "status": "pending",
            }
            ledger["reported_through"] = through
        pending = [(key, copy.deepcopy(batch)) for key, batch in ledger["batches"].items()
                   if not batch["requested"] and batch["status"] == "pending"]
        count = ledger["count"]
    for batch_id, batch in pending:
        family_facade.tell_steward(facade, family["steward"], actor, batch["request_text"],
                                  message_id=batch["request_id"], business_ref=REF, task_trigger="progress",
                                  task_facts={"lineage_id": lineage_id, "family_id": block["id"],
                                              "batch_id": batch_id, "through_turn": batch["through_turn"]})
        with repository.transaction(lineage_id, facade) as relay:
            relay["progress"]["batches"][batch_id]["requested"] = True
    return {"family_id": block["id"], "count": count, "requested_batch_ids": [key for key, _ in pending]}


def _bounded_summary(summary):
    if not isinstance(summary, dict):
        raise ValueError("summary 必须是九字段工作摘要")
    # Validate before normalization: its generic field limits must not silently
    # truncate an oversized report into an apparently valid submission.
    from .resident_context.evidence import SUMMARY_FIELDS
    text = []
    for key in SUMMARY_FIELDS:
        value = summary.get(key, "")
        text.extend(str(item) for item in value) if isinstance(value, list) else text.append(str(value))
    if sum(map(len, text)) > MAX_CHARS:
        raise ValueError("门面摘要的文字合计不能超过 500 字")
    return validate_summary(summary)


def publish(lineage_id, family_id, actor, *, batch_id, report, summary, expected_revision, source_versions):
    block, family = _family(lineage_id, family_id, actor, publishing=True)
    if not isinstance(report, str) or not report.strip() or len(report) > MAX_CHARS:
        raise ValueError("简报正文必须是 1–500 字，包含标点、空格和换行")
    clean = _bounded_summary(summary)
    facade = family["facade"]
    repository = get_lineage_data_store().family_relays
    content = {"report": report, "summary": clean, "source_versions": source_versions}
    with repository.transaction(lineage_id, facade) as relay:
        ledger = _ledger(relay)
        batch = ledger["batches"].get(str(batch_id))
        if not batch:
            raise ValueError("没有这个待发布的 Family 简报批次")
        if batch.get("content") and batch["content"] != content:
            raise ValueError("已经开始投递的简报内容不能修改，请用原内容重试")
        if batch["status"] == "published":
            return {"family_id": block["id"], "batch_id": str(batch_id), "status": "published"}
        if any(int(key) < int(batch_id) and item["status"] != "published" for key, item in ledger["batches"].items()):
            raise ValueError("请先发布更早的 Family 简报批次")
        if not batch.get("content"):
            previous = work_summary.maintenance_view(actor, facade)
            # Recover a crash after the summary write but before outbox commit.
            same = previous["summary"] == clean and previous["source_versions"] == source_versions
            if not same:
                work_summary.update_resident_work_summary(actor, facade, expected_revision,
                    summary=clean, source_versions=source_versions)
            batch["content"] = copy.deepcopy(content)
        # Previously publishing batches may retain delivery receipts. Keep those
        # facts, but never resume their former member outbox on a retry.
        batch["status"] = "published"
        batch.pop("request_text", None)
    return {"family_id": block["id"], "batch_id": str(batch_id), "status": "published"}
