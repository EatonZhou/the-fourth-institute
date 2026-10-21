"""Portal Web code completes a pending projection with the same idempotency key (decision_898b8911a2ff).

:module: isa.instructions
:design: decision_898b8911a2ff
"""
from __future__ import annotations

import pytest

from management_portal import isa_client
from management_portal.hatg.contracts import InstructionError


def _pending():
    return InstructionError("instruction.projection_pending", "领域修改已经提交，页面投影或资源索引尚未确认",
                            commit_state="committed")


@pytest.fixture
def calls(monkeypatch):
    recorded = []
    monkeypatch.setattr(isa_client.time, "sleep", lambda _seconds: None)

    def install(*outcomes):
        queue = list(outcomes)

        def execute(payload, context):
            recorded.append(dict(payload))
            outcome = queue.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        monkeypatch.setattr(isa_client.routing, "execute_or_raise", execute)
        return recorded

    return install


def test_a_keyed_call_repeats_a_pending_projection_with_the_same_key(calls):
    """Portal 服务端带幂等键的指令遇到 projection_pending 时以同一键重试补齐投影"""
    recorded = calls(_pending(), _pending(), {"component_id": "event_source_a"})

    result = isa_client.call("alice", "CreateComponent", {"lineage_id": "lineage_aaaaaaaaaaaa"}, key="install:a")

    assert result == {"component_id": "event_source_a"}
    assert [call["idempotency_key"] for call in recorded] == ["install:a"] * 3


def test_an_unkeyed_call_is_never_repeated(calls):
    """没有幂等键的指令遇到 projection_pending 不重发"""
    recorded = calls(_pending())

    with pytest.raises(InstructionError) as error:
        isa_client.call("alice", "CreateComponent", {"lineage_id": "lineage_aaaaaaaaaaaa"})

    assert error.value.code == "instruction.projection_pending"
    assert len(recorded) == 1


def test_other_errors_and_running_calls_are_returned_to_the_caller(calls):
    """in_progress 与其他错误直接交给调用方处理"""
    # The caller decides how to wait for a call that is still running (it may be cancelled meanwhile).
    for code in ("instruction.in_progress", "instruction.conflict"):
        recorded = calls(InstructionError(code, code))
        with pytest.raises(InstructionError) as error:
            isa_client.call("alice", "CreateComponent", {}, key="install:b")
        assert error.value.code == code
        assert len(recorded) == 1
        recorded.clear()


def test_a_projection_that_stays_pending_is_reported_after_the_bounded_retries(calls):
    """投影持续未确认时在有限次重试后报告 projection_pending"""
    recorded = calls(*([_pending()] * (len(isa_client.PROJECTION_RETRY_DELAYS) + 1)))

    with pytest.raises(InstructionError) as error:
        isa_client.call("alice", "CreateComponent", {}, key="install:c")

    assert error.value.code == "instruction.projection_pending"
    assert len(recorded) == len(isa_client.PROJECTION_RETRY_DELAYS) + 1
