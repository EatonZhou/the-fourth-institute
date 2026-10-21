"""Loop component rounds, completion, pause and reset (decision_4adcc1ff2f2c "Packet、算子与循环")."""

from __future__ import annotations

import pytest

from management_portal.society_service import lineage_loop_runtime as loops
from management_portal.society_service import lineage_program_runtime
from tests.cases.test_lineage_program_runtime_claims import LINEAGE_ID as LINEAGE
from tests.cases.test_lineage_program_runtime_claims import lineage_id  # noqa: F401 - fixture

LOOP = "loop_a1b2c3d4e5f6"


@pytest.fixture(autouse=True)
def lineage(lineage_id):  # noqa: F811 - pytest fixture
    return lineage_id


def advance(port: str, *, maximum: int = 2, completion: str = "complete") -> tuple[str, dict]:
    return loops._advance(LINEAGE, LOOP, {"max_rounds": maximum, "completion": completion}, port)


def state() -> dict:
    return loops._state(lineage_program_runtime.read_operator_state(LINEAGE, LOOP))


def test_rounds_continue_until_the_limit_then_stop_and_stay_stopped():
    """循环到达轮次上限后停止且保持停止"""
    assert advance("invoke")[0] == "continue"
    assert advance("invoke")[0] == "continue"
    output, result = advance("invoke")
    assert (output, result["status"]) == ("stop", "max_rounds")
    assert advance("invoke") == ("", {**state(), "status": "stopped"})


def test_rearm_counts_again_on_the_next_trigger_after_stopping():
    """循环停止后以 rearm 模式从下一次触发重新计数"""
    advance("invoke", maximum=1, completion="rearm")
    assert advance("done", maximum=1, completion="rearm")[0] == "stop"
    output, result = advance("invoke", maximum=1, completion="rearm")
    assert output == "continue" and result["round"] == 1


def test_pause_blocks_new_rounds_but_the_current_round_can_finish():
    """暂停循环阻止新轮次且允许当前轮结束"""
    advance("invoke")
    loops.set_paused(LINEAGE, LOOP, True)
    assert advance("invoke")[1]["status"] == "paused"
    assert state()["round"] == 1
    output, result = advance("done")
    assert (output, result["status"]) == ("stop", "done")
    loops.set_paused(LINEAGE, LOOP, False)
    assert state()["paused"] is False


def test_reset_clears_rounds_and_keeps_the_pause():
    """重置循环清空轮次并保留暂停状态"""
    advance("invoke")
    loops.set_paused(LINEAGE, LOOP, True)
    generation = loops.reset(LINEAGE, LOOP)
    assert state() == {**state(), "round": 0, "paused": True, "generation": generation}
    assert advance("invoke")[1]["status"] == "paused"
