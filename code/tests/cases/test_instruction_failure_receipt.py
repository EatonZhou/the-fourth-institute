"""Business failure envelopes retain the ISA receipt without inventing a commit.

:module: isa.instructions
:importance: P1
:aspects: functional, behavior
:design: decision_898b8911a2ff
"""
from copy import deepcopy

import pytest

from management_common.operation_errors import failure_payload
from management_portal.hatg import routing
from management_portal.hatg.contracts import InstructionContext, InstructionError, InstructionResult


@pytest.mark.parametrize("receipt_id,code,details", [
    ("inst_entry_lost", "instruction.owner_unavailable", {"owner": "lineage"}),
    ("inst_lin_failed", "instruction.failed", {"owner": "lineage"}),
    ("inst_lin_retry", "instruction.in_progress", {"instruction_id": "inst_lin_original"}),
])
@pytest.mark.parametrize("replayed", [False, True])
def test_business_error_retains_receipt_and_original_execution(monkeypatch, receipt_id, code, details, replayed):
    """业务错误保留指令回执与原执行编号

    ISA 异常进入业务错误回执时保留指令名和外层编号，原在途执行编号及 unknown 语义不变，不自动重发。

    :design-point:
    """
    original = {"code": code, "message": "No result received", "details": details,
                "retryable": True, "commit_state": "unknown"}
    snapshot = deepcopy(original)
    result = InstructionResult(receipt_id, "CreateComponent", "failed", error=original, replayed=replayed)
    calls = []

    def execute(call, context):
        calls.append((call, context.caller))
        return result

    monkeypatch.setattr(routing, "execute", execute)
    with pytest.raises(InstructionError) as raised:
        routing.execute_or_raise({"instruction": "CreateComponent", "arguments": {}},
                                 InstructionContext(caller="resident_test", entry="web"))
    receipt = failure_payload(raised.value)
    assert len(calls) == 1
    assert receipt["code"] == code
    assert receipt["commit_state"] == "unknown"
    assert receipt["retryable"] is True
    assert receipt["details"] == {**details, "instruction_result": {
        "instruction": "CreateComponent", "instruction_id": receipt_id, "replayed": replayed,
    }}
    assert original == snapshot
    if receipt_id.startswith("inst_entry_"):
        assert "instruction_id" not in receipt["details"]
