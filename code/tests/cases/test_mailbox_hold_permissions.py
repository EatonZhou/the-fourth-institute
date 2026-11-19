"""Mailbox queue holds use the current caller's permission on the target.

:module: security.identity-access
:design: decision_600ae27b0538, decision_c54e68a82d7a
"""
import pytest

from management_portal import permission_system
from management_portal.hatg.catalog import instruction_catalog
from management_portal.hatg.contracts import InstructionContext, InstructionError
from management_portal.hatg import routing
from tests.cases.permission_test_support import Fact, permissions  # noqa: F401


LINEAGE = "lineage_111111111111"
TARGET = "resident_111111111111"
OTHER = "resident_222222222222"
CALLER = "resident_333333333333"


def test_hold_schema_matches_adjacent_queue_controls(permissions):
    """Formal schema classifies every queue organizer action on its Resident."""
    for name in ("SetMailboxItemHold", "ReorderMailbox", "MergeMailboxItems",
                 "SplitMailboxItem", "InjectMailboxItem", "RequeueMessage"):
        result = routing.execute({"instruction": "GetInstructionSchema",
                                  "arguments": {"instruction": name}},
                                 InstructionContext(caller=CALLER, entry="web"))
        assert result.status == "committed", result.error
        schema = result.result["schema"]
        assert schema["mutates"] is True
        assert schema["permission"] == {"required": True, "requirements": [
            {"element": name, "argument": "resident_id", "a": "@contributor", "types": ["resident"]}]}


@pytest.mark.parametrize("held", [True, False])
def test_hold_and_resume_require_current_target_contributor(permissions, held):
    """Viewing, messaging and same-Lineage membership cannot organize a queue.

    Target-scoped delegation allows both operations, explicit denial still wins,
    and the real caller cannot substitute an author or borrow another target.
    """
    permissions.place(Fact("lineage", LINEAGE), *[
        Fact("resident", resident, {"lineage": LINEAGE}) for resident in (TARGET, OTHER, CALLER)])
    permissions.grant(CALLER, TARGET, "@message")
    declaration = instruction_catalog()["SetMailboxItemHold"]
    context = InstructionContext(caller=CALLER, entry="web")

    def authorize(target=TARGET):
        return permission_system.authorize_instruction(
            declaration, {"resident_id": target, "message_id": "msg_fixture", "held": held}, context)

    def refused(target=TARGET):
        with pytest.raises(InstructionError) as error:
            authorize(target)
        assert error.value.code == "instruction.forbidden"

    assert permissions.holds(CALLER, "@viewer", TARGET)
    refused()
    permissions.grant(CALLER, TARGET, "@contributor")
    assert authorize() is None
    refused(OTHER)
    permissions.deny(CALLER, TARGET, "SetMailboxItemHold")
    refused()
