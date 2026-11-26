"""Research map reads feed an ordinary, caller-authorized daily script.

:module: web.organization
:design: decision_9d15ae2053c4
"""
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest

from management_portal import research_map_store as store
from management_portal import research_map_web_isa as web


def test_read_history_supplies_full_notes_and_revision_direction_details(monkeypatch):
    project = "MapScriptHistory"
    first = store.apply(project, 0, [{"op": "add_direction", "title": "Speed", "key": "a",
                                     "proposal_reason": "reduce latency"}], actor="alice", resident=False)
    direction = first["created"]["a"]
    store.apply(project, 1, [{"op": "add_note", "direction_id": direction, "kind": "progress",
                             "text": "measured", "refs": ["experiment_000000000001"]},
                            {"op": "update_direction", "direction_id": direction,
                             "fields": {"conclusion": "confirmed"}}], actor="alice", resident=False)
    monkeypatch.setattr(web, "_project", lambda *_: {})
    monkeypatch.setattr(web, "_outputs", lambda *_: {direction: [{"resource_id": "experiment_000000000001",
                                                              "modified_at": "2026-11-26T00:00:00Z"}]})
    result = web.list_research_map_changes(SimpleNamespace(caller="alice", argument={"project_id": project}.get))
    note = result["changes"][1]["changes"][0]
    assert (note["text"], note["refs"]) == ("measured", ["experiment_000000000001"])
    assert result["changes"][1]["directions"][0]["conclusion"]["text"] == "confirmed"
    assert result["output_changes"][direction][0]["resource_id"] == "experiment_000000000001"


def test_digest_is_not_an_instruction():
    from management_portal.research_map_instructions import DECLARATIONS

    assert "GetResearchMapDigest" not in web.HANDLERS
    assert "GetResearchMapDigest" not in {item.name for item in DECLARATIONS}


def test_template_migration_exact_idempotent_and_deployment_owned():
    from management_portal import research_map_automation as automation
    from management_portal.society_service.lineage_program_diagnostics import effect_script_analysis

    target = "/installed portal/code/scripts/research_map_digest.py"
    migrated = automation.migrate_digest_script(automation.LEGACY_SCRIPT, script_path=target)
    assert target in migrated and "GetResearchMapDigest" not in migrated
    assert automation.migrate_digest_script(migrated) == migrated
    for custom in ("# custom\n" + automation.LEGACY_SCRIPT, automation.LEGACY_SCRIPT + "\n", "output status=ok"):
        assert automation.migrate_digest_script(custom) == custom
    analysis = effect_script_analysis({"script": migrated, "endpoint_names": ["target"],
                                       "endpoints": {"target": "resident_00000000000a"}})
    assert analysis.diagnostics == []
    assert 'exclusive_key="research-map:digest"' in migrated


def test_effect_interpreter_passes_endpoint_and_lineage_to_runscript():
    from management_portal import research_map_automation as automation
    from management_portal.society_service.lineage_effect_script import CallResult, Interpreter, parse

    calls = []
    def execute(request):
        calls.append(request)
        return CallResult(True, {"script_run_id": "script_run_123", "status": "running"})
    outcome = Interpreter(parse(automation.digest_script("/installed/code/scripts/research_map_digest.py")),
                          {"arguments": {"lineage_id": LINEAGE}, "endpoint": {"target": TARGET}}, execute).run()
    assert outcome.status == "succeeded"
    assert len(calls) == 1 and calls[0].instruction == "RunScript"
    assert calls[0].arguments == {"lineage_id": LINEAGE, "path": "/installed/code/scripts/research_map_digest.py",
                                 "arguments": {"target": TARGET}, "exclusive_key": "research-map:digest"}
    assert outcome.outputs == {"script_run_id": "script_run_123", "status": "running"}


UNTIL = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)
CALLER = "resident_00000000000a"
LINEAGE = "lineage_000000000001"
TARGET = "resident_00000000000b"
DIRECTION = "direction_000000000001"
SINCE = "2026-11-25T12:00:00Z"


class MapCalls:
    def __init__(self, *, quiet=False, refuse="", conflict=False):
        self.calls = []
        self.value = SINCE
        self.refuse = refuse
        self.conflict = conflict
        self.direction = {"direction_id": DIRECTION, "title": "Speed", "stance": "focus", "teams": []}
        self.record = {"revision": 3, "changed_at": "2026-11-26T10:00:00Z",
                       "changed_by": CALLER if quiet else "alice", "directions": [self.direction],
                       "changes": [{"op": "add_note", "direction_id": DIRECTION, "note_id": "n",
                                    "kind": "progress", "text": "measured", "refs": []}]}

    def __call__(self, name, **arguments):
        self.calls.append((name, arguments))
        if name == self.refuse:
            raise PermissionError(name)
        if name == "GetLineage":
            assert arguments == {"lineage_id": LINEAGE}
            return {"lineage": {"project_id": "RenamedProject", "leader_resident_id": TARGET}}
        if name == "GetSharedState":
            return {"value": self.value}
        if name == "GetResearchMap":
            assert arguments["project_id"] == "RenamedProject"
            return {"project_id": "RenamedProject", "revision": 3, "directions": [self.direction], "outputs": {}}
        if name == "ListResearchMapChanges":
            # The last page contains the relevant item; the script must not just inspect page one.
            return {"changes": [self.record]} if arguments.get("cursor") else {"changes": [], "next_cursor": "2"}
        if name == "DeliverMessage":
            assert arguments["resident_id"] == TARGET
            assert "measured" in arguments["message"]["content_parts"][0]["text"]
            return {"message_id": "msg_123", "accepted": True}
        if name == "UpdateSharedState":
            assert arguments["expected_value"] == SINCE
            if not self.conflict:
                self.value = arguments["value"]
            return {"matched": not self.conflict}
        raise AssertionError(name)


def test_daily_script_uses_pages_then_delivery_then_compare_and_set():
    from scripts.research_map_digest import run

    call = MapCalls()
    result = run(call, lineage_id=LINEAGE, caller=CALLER, target=TARGET, until=UNTIL)
    assert result == {"status": "requested", "count": 1, "until": "2026-11-26T12:00:00Z", "message_id": "msg_123"}
    assert [name for name, _ in call.calls][-2:] == ["DeliverMessage", "UpdateSharedState"]
    assert call.value == result["until"]


def test_quiet_script_advances_watermark_without_a_message_or_model():
    from scripts.research_map_digest import run

    call = MapCalls(quiet=True)
    assert run(call, lineage_id=LINEAGE, caller=CALLER, target=TARGET, until=UNTIL)["status"] == "quiet"
    assert "DeliverMessage" not in [name for name, _ in call.calls]
    assert call.value == "2026-11-26T12:00:00Z"


@pytest.mark.parametrize("refuse", ["GetResearchMap", "ListResearchMapChanges", "DeliverMessage"])
def test_failed_read_or_delivery_keeps_watermark(refuse):
    from scripts.research_map_digest import run

    call = MapCalls(refuse=refuse)
    with pytest.raises(PermissionError):
        run(call, lineage_id=LINEAGE, caller=CALLER, target=TARGET, until=UNTIL)
    assert call.value == SINCE
    assert "UpdateSharedState" not in [name for name, _ in call.calls]


def test_watermark_conflict_is_not_overwritten():
    from scripts.research_map_digest import run

    call = MapCalls(conflict=True)
    with pytest.raises(RuntimeError, match="水位冲突"):
        run(call, lineage_id=LINEAGE, caller=CALLER, target=TARGET, until=UNTIL)
    assert call.value == SINCE


def test_manual_watermark_change_during_read_stops_before_delivery():
    from scripts.research_map_digest import run

    base = MapCalls()
    def call(name, **arguments):
        result = base(name, **arguments)
        if name == "GetResearchMap":
            base.value = "2026-11-26T11:00:00Z"
        return result
    with pytest.raises(RuntimeError, match="本次未投递"):
        run(call, lineage_id=LINEAGE, caller=CALLER, target=TARGET, until=UNTIL)
    assert "DeliverMessage" not in [name for name, _ in base.calls]
    assert base.value == "2026-11-26T11:00:00Z"


def test_broken_pagination_stops_without_advancing():
    from scripts.research_map_digest import run

    base = MapCalls()
    def call(name, **arguments):
        if name == "ListResearchMapChanges":
            return {"changes": [], "next_cursor": "2"}
        return base(name, **arguments)
    with pytest.raises(ValueError, match="分页未向前"):
        run(call, lineage_id=LINEAGE, caller=CALLER, target=TARGET, until=UNTIL)
    assert "DeliverMessage" not in [name for name, _ in base.calls]
    assert base.value == SINCE


def test_output_read_preserves_requesting_actor_and_index_author(monkeypatch):
    from management_portal import resources

    calls = []
    def query(**arguments):
        calls.append(arguments)
        return {"total": 1, "resources": [{"resource_id": "experiment_000000000001", "resource_type": "experiment",
                                          "source": {"modified_at": SINCE}, "attributes": {
                                              "version": {"updated_by": {"agent": CALLER, "user": "alice"}},
                                              "relations": [{"kind": "related", "target_id": DIRECTION}, "invalid"]}}]}
    monkeypatch.setattr(resources, "get_resource_service", lambda: SimpleNamespace(query=query))
    result = web._outputs("P", {DIRECTION}, "bob")
    assert calls[0]["actor"] == "bob"
    assert result[DIRECTION][0]["modified_by"] == CALLER
    assert result[DIRECTION][0]["modified_at"] == SINCE


def test_outputs_use_true_time_window_and_author_and_keep_unknown_author():
    from scripts.research_map_digest import build_digest

    def output(identifier, date, author="alice"):
        return {"resource_id": identifier, "resource_type": "experiment", "title": "result",
                "modified_at": date, "modified_by": author}
    current = {"project_id": "P", "revision": 0, "directions": [MapCalls().direction], "outputs": {DIRECTION: [
        output("old", "2026-11-25T12:00:00Z"), output("future", "2026-11-26T12:00:01Z"),
        output("mine", "2026-11-26T10:00:00Z", CALLER),
        output("other", "2026-11-26T11:00:00+01:00"),
        output("unknown", "2026-11-26T12:00:00Z", "")]}}
    digest = build_digest(current, [], caller=CALLER, since=SINCE, until=UNTIL)
    assert [item["resource_id"] for item in digest["directions"][0]["outputs"]] == ["other", "unknown"]


def test_main_uses_runscript_identity_not_arguments(monkeypatch):
    import sys
    from scripts import research_map_digest

    captured = {}
    def run(call, **arguments):
        captured.update(arguments)
        return {"status": "quiet"}
    monkeypatch.setattr(research_map_digest, "run", run)
    monkeypatch.setitem(sys.modules, "portal_script", SimpleNamespace(
        inputs=lambda: {"caller_id": CALLER, "lineage_id": LINEAGE,
                        "arguments": {"target": TARGET, "caller_id": "administrator", "lineage_id": "wrong"}},
        isa=object(), set_output=lambda value: captured.update(output=value)))
    research_map_digest.main()
    assert captured == {"caller": CALLER, "lineage_id": LINEAGE, "target": TARGET, "output": {"status": "quiet"}}
