"""Generated summary maintenance keeps its Family identity after an external turn.

:module: lineage.family
:design: decision_89b9df038a95, decision_a64b3ea2c3c5
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from management_portal import permission_system
from management_portal.hatg.contracts import InstructionContext
from management_portal.society_service import (
    family_familize, lineage_effect_script, resident_conversation_stream, resident_intake_domain,
)
from management_portal.society_service.lineage_program_effect_execution import _family_note_context
from management_portal.society_service.resident_intake_domain import deliver_resident_message_content
from tests.cases.isa_test_support import run
from tests.cases.permission_test_support import Fact, enable
from tests.cases.test_family_tasks import (  # noqa: F401
    charters, configured_family, future_creation_store, lineage,
)

EXTERNAL = "resident_ffffffffffff"
FACADE = "resident_111111111111"
MEMBER = "resident_222222222222"
EFFECT = "effect_333333333333"
LINEAGE = "lineage_444444444444"
FAMILY = "fbi_555555555555"
NOTICE = "msg_" + "6" * 32


@pytest.fixture
def maintenance(monkeypatch):
    record = {"resident_id": FACADE, "lineage_id": LINEAGE,
              "family": {"role": "facade", "family_id": FAMILY}}
    monkeypatch.setattr(family_familize, "find_record", lambda rid: record if rid == FACADE else {})
    return SimpleNamespace(
        record=record, context=InstructionContext(caller=EXTERNAL, entry="lineage"),
        args={"resident_id": FACADE, "trigger": "summary", "message_id": NOTICE},
        view={"function_blocks": [{"id": FAMILY, "elements": [
            {"id": EFFECT, "kind": "component", "key": "summary_check"}],
            "family": {"facade": FACADE, "members": [MEMBER]}}]},
        event=SimpleNamespace(type="resident.work_finished", subject_resident_id=MEMBER, payload={}),
    )


def bind(m, **overrides):
    values = dict(lineage=m.view, effect_id=EFFECT, instruction="DispatchFamilyTask",
                  arguments=m.args, context=m.context, lineage_id=LINEAGE, event=m.event)
    values.update(overrides)
    return _family_note_context(**values)


@pytest.mark.parametrize("key", ["summary_check", "summary_rollup", "memory_rollup"])
def test_only_generated_summary_maintenance_uses_own_facade(maintenance, key):
    m = maintenance
    m.view["function_blocks"][0]["elements"][0]["key"] = key
    if key != "summary_check":
        m.event.type = "time.reached"
    assert bind(m).caller == FACADE
    assert m.context.caller == EXTERNAL
    assert bind(m, instruction="UpdateResident").caller == EXTERNAL


@pytest.mark.parametrize("change", [
    "workflow", "router", "other-effect", "other-family", "other-lineage", "other-target",
    "other-trigger", "facts", "source-message", "missing-id", "invalid-id", "no-event",
    "arrival", "other-member", "role-subject", "timer-on-check", "completion-on-rollup",
    "web", "deleted", "not-facade", "moved-facade", "relay",
])
def test_summary_capability_does_not_bypass_other_authorization(maintenance, change):
    m = maintenance
    block = m.view["function_blocks"][0]
    overrides = {}
    if change in {"workflow", "router"}:
        block["elements"][0]["key"] = "workflow:summary_check" if change == "workflow" else "router"
    elif change == "other-effect":
        overrides["effect_id"] = "effect_999999999999"
    elif change == "other-family":
        block["id"] = "fbi_999999999999"
    elif change == "other-lineage":
        overrides["lineage_id"] = "lineage_999999999999"
    elif change == "other-target":
        m.args["resident_id"] = MEMBER
    elif change == "other-trigger":
        m.args["trigger"] = "recovery"
    elif change in {"facts", "source-message"}:
        m.args["facts" if change == "facts" else "source_message_id"] = {"text": "untrusted"}
    elif change == "missing-id":
        del m.args["message_id"]
    elif change == "invalid-id":
        m.args["message_id"] = "arbitrary"
    elif change == "no-event":
        overrides["event"] = None
    elif change == "arrival":
        m.event.type = "resident.message_received"
    elif change in {"other-member", "role-subject"}:
        m.event.subject_resident_id = EXTERNAL if change == "other-member" else FACADE
    elif change == "timer-on-check":
        m.event.type = "time.reached"
    elif change == "completion-on-rollup":
        block["elements"][0]["key"] = "summary_rollup"
    elif change == "web":
        overrides["context"] = InstructionContext(caller=EXTERNAL, entry="web")
    elif change == "deleted":
        m.record["deleted_at"] = "2026-11-02"
    elif change == "not-facade":
        m.record["family"]["role"] = "member"
    elif change == "moved-facade":
        m.record["lineage_id"] = "lineage_999999999999"
    elif change == "relay":
        overrides["instruction"] = "RelayFamilyMessage"
    assert bind(m, **overrides).caller == EXTERNAL


@pytest.fixture
def authorized_family(configured_family, monkeypatch):
    # Enable real PAR only after the isolated Family has been created.
    p = enable(monkeypatch)
    body, block = configured_family
    facade = block["family"]["facade"]
    p.place(Fact("resident", facade))
    p.deny(EXTERNAL, facade, "@contributor")
    p.grant(facade, facade, "@contributor")
    monkeypatch.setattr(resident_intake_domain, "deliver_resident_message_content", deliver_resident_message_content)
    yield body, block
    permission_system.forget_implementation()


@pytest.mark.parametrize("key", ["summary_check", "summary_rollup"])
def test_generated_script_dispatches_real_isa_once_without_lending_initiator_permissions(authorized_family, key):
    body, block = authorized_family
    family = block["family"]
    effect = next(e["id"] for e in block["elements"] if e["key"] == key)
    script = next(c["config"]["script"] for c in body["program"]["components"] if c["id"] == effect)
    args = {"resident_id": family["facade"], "trigger": "summary", "message_id": NOTICE}
    assert run("DispatchFamilyTask", args, actor=EXTERNAL, entry="lineage").error["code"] == "instruction.forbidden"
    context = InstructionContext(caller=EXTERNAL, entry="lineage")
    event = SimpleNamespace(type="resident.work_finished" if key == "summary_check" else "time.reached",
                            subject_resident_id=family["members"][0], payload={"message_id": NOTICE})
    calls = []

    def execute(request):
        scoped = _family_note_context(body, effect, request.instruction, request.arguments, context,
                                     lineage_id=body["lineage_id"], event=event)
        calls.append((request.instruction, scoped.caller))
        if request.instruction == "DispatchFamilyTask":
            result = run(request.instruction, request.arguments, actor=scoped.caller, entry=scoped.entry)
            return lineage_effect_script.CallResult(result.status == "committed", result.result or {}, result.error)
        # Controlled read facts isolate the identity handoff; dispatch and admission are real.
        facts = {
            "CheckResidentWorkSummary": {"status": "covered", "consolidation_due": True},
            "ListResidentStates": {"states": [{"state": {"work_state": "idle", "queued_count": 0}}]},
            "GetFamilySummaryInputs": {"needs_update": True, "notification": {"message_id": NOTICE}},
        }
        return lineage_effect_script.CallResult(True, facts[request.instruction])

    inputs = {"arguments": {"facade": family["facade"], "steward": family["steward"]},
              "event": {"subject_resident_id": event.subject_resident_id, "payload": event.payload}}
    def interpret():
        return lineage_effect_script.Interpreter(lineage_effect_script.parse(script), inputs, execute).run()

    first = interpret()
    assert first.status == "succeeded", first
    assert first.outputs["status"] == "requested"
    assert all(caller == family["facade"] for name, caller in calls if name != "ListResidentStates")
    repo = resident_conversation_stream.get_repository()
    message = deepcopy(repo.message(family["steward"], NOTICE))
    assert message["author_id"] == family["facade"]
    assert interpret().status == "succeeded"
    assert repo.message(family["steward"], NOTICE) == message
    assert len(repo.state_store.mailbox_entries([family["steward"]], message_ids=[NOTICE])) == 1
    repo.set_input_state(family["steward"], NOTICE, "cancelled")
    assert interpret().status == "succeeded"
    assert repo.input_state(family["steward"], NOTICE)["state"] == "cancelled"
    assert context.caller == EXTERNAL
    assert run("DispatchFamilyTask", args, actor=EXTERNAL).error["code"] == "instruction.forbidden"
