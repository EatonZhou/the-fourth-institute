"""Deleted history has a separate, exact management permission.

:module: security.identity-access
:design: decision_600ae27b0538
"""
import pytest

from management_portal import permission_system
from management_portal.hatg import routing
from management_portal.hatg.catalog import instruction_catalog
from management_portal.hatg.contracts import InstructionContext, InstructionError
from management_portal.permission_system import personal_ama, temporary
from management_portal.permission_system.scheme.compiler import compile_scheme
from management_portal.permission_system.scheme.repository import builtin_documents
from tests.cases.permission_test_support import Fact, permissions  # noqa: F401


ACTION = "GetDeletedResidentHistory"
TARGET = "resident_111111111111"
OTHER = "resident_222222222222"
CALLER = "resident_333333333333"
LINEAGE = "lineage_111111111111"


def test_all_builtin_templates_explicitly_classify_deleted_history():
    for document in builtin_documents().values():
        assert document["instructions"][ACTION] == {"a": "@admin"}
        assert compile_scheme(document).unmapped() == []


def test_formal_schema_uses_target_management_without_changing_live_reads(permissions):
    for name, level in ((ACTION, "@admin"), ("GetConversation", "@viewer")):
        result = routing.execute(
            {"instruction": "GetInstructionSchema", "arguments": {"instruction": name}},
            InstructionContext(caller=CALLER, entry="web"),
        )
        assert result.status == "committed", result.error
        assert result.result["schema"]["permission"] == {
            "required": True, "requirements": [
                {"element": name, "argument": "resident_id", "a": level, "types": ["resident"]}
            ],
        }
        assert result.result["schema"]["mutates"] is False


def test_target_admin_required_and_explicit_deny_wins(permissions):
    permissions.place(Fact("lineage", LINEAGE), *[
        Fact("resident", resident, {"lineage": LINEAGE}) for resident in (TARGET, OTHER, CALLER)
    ])
    declaration = instruction_catalog()[ACTION]
    context = InstructionContext(caller=CALLER, entry="web")

    def authorize(target=TARGET):
        return permission_system.authorize_instruction(
            declaration, {"resident_id": target, "section": "messages", "limit": 1}, context,
        )

    def refused(target=TARGET):
        with pytest.raises(InstructionError) as error:
            authorize(target)
        # Read denial hides even the existence of the historical target.
        assert error.value.code == "instruction.not_found"

    permissions.grant(CALLER, TARGET, "@viewer")
    refused()
    permissions.grant(CALLER, TARGET, "@contributor")
    refused()
    permissions.grant(CALLER, TARGET, "@admin")
    assert authorize() is None
    refused(OTHER)
    permissions.deny(CALLER, TARGET, ACTION)
    refused()


@pytest.fixture
def private_world(permissions, monkeypatch):
    monkeypatch.setattr(personal_ama, "owner_of", lambda _principal: "")
    monkeypatch.setattr(personal_ama, "owners_of", lambda _principals: {})
    permissions.users("history-owner", category="intern")
    permissions.place(
        Fact("lineage", LINEAGE, {"personal_space": "personal_space:history-owner", "user": "history-owner"}),
        Fact("resident", TARGET, {"lineage": LINEAGE}),
        Fact("resident", OTHER, {"lineage": LINEAGE}),
        Fact("resident", CALLER),
    )
    permissions.grant("history-owner", LINEAGE, "@admin")
    return permissions


def test_shared_admin_and_read_loan_do_not_open_private_history(private_world):
    world = private_world
    world.administrator(CALLER)
    assert not personal_ama.reaches(CALLER, "history-owner", TARGET, action=ACTION)
    scheme, engine, _instance = world.implementation.current()
    temporary.issue(engine, scheme, issuer="history-owner", recipient=CALLER,
                    a="@viewer", resource=TARGET, seconds=60)
    # A viewer loan cannot piggyback on an unrelated shared admin grant.
    assert not personal_ama.reaches(CALLER, "history-owner", TARGET, action=ACTION)
    assert not personal_ama.reaches(CALLER, "history-owner", OTHER, action=ACTION)


@pytest.mark.parametrize("end", ["expiry", "revoke", "input_end"])
def test_exact_history_loan_ends_without_reviving_live_access(private_world, end):
    world = private_world
    scheme, engine, _instance = world.implementation.current()
    clock = [engine.clock()]
    engine.clock = lambda: clock[0]
    kwargs = ({"message_id": "msg_history_recovery", "input_open": lambda *_: True}
              if end == "input_end" else {"seconds": 60})
    loan = temporary.issue(engine, scheme, issuer="history-owner", recipient=CALLER,
                           a=ACTION, resource=TARGET, **kwargs)

    def permitted():
        return (permission_system.allows(CALLER, ACTION, {"resident_id": TARGET})
                and personal_ama.reaches(CALLER, "history-owner", TARGET, action=ACTION))

    assert permitted()
    assert not permission_system.allows(CALLER, ACTION, {"resident_id": OTHER})
    assert not personal_ama.reaches(CALLER, "history-owner", OTHER, action=ACTION)
    for live in ("GetConversation", "GetResidentInitialization", "DeliverMessage:resident_id"):
        assert not personal_ama.reaches(CALLER, "history-owner", TARGET, action=live)
    if end == "expiry":
        clock[0] = loan["expires_at"]
    elif end == "revoke":
        temporary.revoke(engine.store, loan["credential_id"], actor="history-owner")
    else:
        with engine.store.transaction() as connection:
            permission_system.end_task_credentials(connection, CALLER, "msg_history_recovery")
    assert not permitted()
