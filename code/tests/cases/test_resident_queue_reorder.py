"""ReorderMailbox changes only the waiting order (decision_b5d349190743, decision_2a3ff353165c).

:module: resident.session
:design: decision_b5d349190743, decision_2a3ff353165c
"""

from __future__ import annotations

import uuid

import pytest

from management_portal.society_service import (
    resident_intake_domain,
    resident_execution_work,
    resident_lifecycle_domain,
    resident_record_domain,
)
from tests.cases.test_cross_owner_storage_integration import ACTOR, owners  # noqa: F401 - fixture
from tests.cases.test_wall04_residual_20260919 import instruction, team  # noqa: F401 - fixture


def _queue(team, count: int = 4) -> tuple[str, list[dict]]:
    resident_id = team.target["resident_id"]
    entries = []
    for index in range(count):
        message_id = "msg_" + uuid.uuid4().hex
        message = resident_intake_domain.build_message("user", message_id, ACTOR, message_id=message_id)
        if index == 0:
            resident_lifecycle_domain.append_conversation_message(resident_id, message)
        else:
            resident_lifecycle_domain.accept_resident_user_message(resident_id, message)
        entries.append({"user_message_id": message_id, "input_message": message,
                        "message": message_id, "actor": ACTOR})
    record = resident_record_domain.find_resident(resident_id)
    work = {**entries[0], "execution_id": "execution-" + uuid.uuid4().hex[:24], "queue": entries[1:]}
    resident_execution_work.write(record, work)
    entries[0]["execution_id"] = work["execution_id"]
    return resident_id, entries


def _waiting(resident_id: str) -> list[str]:
    record = resident_record_domain.find_resident(resident_id)
    return [item["user_message_id"] for item in resident_execution_work.read(record)["queue"]]


def test_move_before_after_and_full_order_keep_the_running_head_and_scheduler_in_step(team):
    """调序支持前后移动与完整顺序，运行中的项和调度优先级同步

    ReorderMailbox 按 before、after 或完整顺序调整等待项；正在执行的项不动，调度优先级随顺序更新。
    """
    resident_id, entries = _queue(team, 5)
    head, a, b, c, d = (item["user_message_id"] for item in entries)

    moved = instruction(team, "ReorderMailbox", {"resident_id": resident_id, "message_id": d, "before_message_id": b})
    assert moved.status == "committed" and moved.result["reordered"] is True
    assert _waiting(resident_id) == [a, d, b, c]

    instruction(team, "ReorderMailbox", {"resident_id": resident_id, "message_id": a, "after_message_id": c})
    assert _waiting(resident_id) == [d, b, c, a]

    full = instruction(team, "ReorderMailbox", {"resident_id": resident_id, "order": [c, a, d, b]})
    assert _waiting(resident_id) == [c, a, d, b]
    assert full.result["revision"].startswith("queue-")
    record = resident_record_domain.find_resident(resident_id)
    assert resident_execution_work.read(record)["user_message_id"] == head
    work = resident_execution_work.read(record)
    assert work["execution_id"] == entries[0]["execution_id"]
    assert all("execution_id" not in item for item in work["queue"])



@pytest.mark.parametrize("arguments, code", [
    ({"order": ["x"]}, "instruction.invalid_arguments"),
    ({"message_id": "first", "before_message_id": "first"}, "instruction.invalid_arguments"),
    ({"message_id": "missing", "before_message_id": "first"}, "instruction.conflict"),
    ({"message_id": "first"}, "instruction.invalid_arguments"),
])
def test_bad_moves_are_refused_and_change_nothing(team, arguments, code):
    """无效调序被拒绝且队列不变

    引用不存在的消息、自身或过期版本的调序请求被拒绝，队列保持原样。
    """
    resident_id, entries = _queue(team, 3)
    names = {"first": entries[1]["user_message_id"], "second": entries[2]["user_message_id"]}
    resolved = {key: names.get(value, value) if isinstance(value, str) else value for key, value in arguments.items()}
    before = _waiting(resident_id)

    refused = instruction(team, "ReorderMailbox", {"resident_id": resident_id, **resolved})

    assert refused.status == "failed" and refused.error["code"] == code
    assert _waiting(resident_id) == before


def test_reordered_ids_rejects_duplicates_and_partial_permutations():
    """完整顺序不能重复或缺项

    完整顺序必须恰好覆盖当前全部等待项。
    """
    with pytest.raises(ValueError):
        resident_intake_domain._reordered_ids(
            ["a", "b"], message_id="", before_message_id="", after_message_id="", order=["a", "a"],
        )
    assert resident_intake_domain._reordered_ids(
        ["a", "b", "c"], message_id="c", before_message_id="a", after_message_id="", order=None,
    ) == ["c", "a", "b"]
