"""Include the existing maintenance reminder in the public summary read."""

from types import SimpleNamespace

from management_portal.hatg.catalog import declaration
from management_portal.hatg.contracts import InstructionCall, InstructionContext
from management_portal.hatg.kernel import Invocation
from management_portal.society_service.isa import residents
from management_portal.society_service.resident_context import work_summary as work
from tests.cases.test_family_work_summaries import A, C, context, setup_work  # noqa: F401


def test_summary_read_returns_only_the_eligible_maintenance_reminder(setup_work, monkeypatch):
    _, _, _, _, refs, waiting, _ = setup_work
    monkeypatch.setattr(residents, "_discovery", lambda: SimpleNamespace(contexts=lambda **_: {
        "residents": [{"resident_id": A, "summary": {"current_focus": "Current work"}}],
    }))
    name = "GetResidentSummaries"

    def read(include_maintenance):
        args = {"resident_ids": [A], "fields": ["current_focus"], "include_maintenance": include_maintenance}
        invocation = Invocation(declaration(name), InstructionCall(name, args),
                                InstructionContext(caller=C, entry="lineage"), "")
        return residents.get_resident_summaries(invocation)["summaries"][0]

    work.record_completed_work_round(A, "one")
    expected = {"message_id": work.derived_message_identity(work.REMINDER_REF, A, "one"),
                "content_parts": [{"type": "text", "text": work.REMINDER}]}
    assert read(True)["reminder"] == expected
    assert read(True)["missing_message_ids"] == ["one"]
    assert "reminder" not in read(False)
    waiting.append({"message_id": expected["message_id"]})
    refs[expected["message_id"]] = work.REMINDER_REF
    assert "reminder" not in read(True)
    waiting.clear()
    work.update_resident_work_summary(A, A, summary={"current_focus": "Current work"}, message_ids=["one"])
    assert "reminder" not in read(True)


def test_terminal_runtime_records_the_gap_before_effects_read_it(setup_work, monkeypatch):
    from management_portal.society_service.runtime import backend_adapter
    from management_portal.society_service.runtime.contracts import ResidentTerminalFact

    observed = []

    def handle_terminal(resident_id, actor, message_id, **_options):
        assert (resident_id, actor, message_id) == (A, C, "one")
        observed.append(work.maintenance_view(C, A)["missing_message_ids"])

    adapter = backend_adapter.LineageBackendRuntimeAdapter()
    monkeypatch.setattr(adapter, "_backend", lambda: SimpleNamespace(
        handle_resident_message_terminal=handle_terminal,
        dispatch_resident_terminal_observers=lambda *_args, **_opts: [],
    ))
    monkeypatch.setattr(backend_adapter, "_chatroom_terminal", lambda _fact: {})
    monkeypatch.setattr(backend_adapter, "_mailbox_terminal", lambda _fact: [])
    fact = ResidentTerminalFact(resident_id=A, actor=C, message_id="one")

    adapter.consume_terminal(fact)
    adapter.consume_terminal(fact)
    assert observed == [["one"], ["one"]]
