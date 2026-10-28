"""Publication facts carry the registered Leader through a real Effect."""
from copy import deepcopy
from types import SimpleNamespace
import pytest

from management_common.operation_identity import current_operation_identity, operation_identity_scope
from management_common.principal import OperationIdentity
from management_common import portal_release_events as facts
from management_portal.hatg import routing
from management_portal.society_service import lineage_domain
from management_portal.society_service.script_runs.service import ACTIVATION
from tests.cases.isa_test_support import ok
from tests.cases.test_isa_lineage_lifecycle import future_creation_store, lineage, resident, graph  # noqa: F401


@pytest.mark.parametrize("change_leader", [False, True])
def test_publication_reaches_runscript_without_ambient_identity(lineage, monkeypatch, change_leader):
    """真实注册和事件→Effect链路使用当前 Leader，外层 User 不提供委托。"""
    first = resident(lineage, "Leader")
    second = resident(lineage, "Successor")
    lineage_domain.update_lineage(lineage, {
        **graph(lineage),
        "program": {"components": [
            {"id": "event_source_release_identity", "kind": "event_source",
             "config": {"event_type": "portal.release_published"}},
            {"id": "effect_release_identity", "kind": "effect", "config": {
                "script": 'RunScript path="scripts/audit.py" runtime="python"', "endpoints": []}},
        ], "wires": [{"id": "wire_release_identity", "kind": "route",
                      "source": "event_source_release_identity", "source_port": "output",
                      "target": "effect_release_identity", "target_port": "invoke", "config": {}}]},
    }, "sample_admin")
    observed = []
    original_execute = routing.execute

    def execute(call, context):
        if call["instruction"] != "RunScript":
            return original_execute(call, context)
        observed.append({"call": deepcopy(call), "caller": context.caller,
                         "activation": deepcopy(ACTIVATION.get()),
                         "identity": current_operation_identity().to_dict()})
        return SimpleNamespace(instruction_id="inst_test_release_identity", status="committed",
                               result={"script_run_id": "script_run_test_identity", "status": "running"}, error=None)

    monkeypatch.setattr(routing, "execute", execute)
    monkeypatch.setattr(routing, "release_recovery", lambda *args, **kwargs: None)
    lineage_domain.reconcile_lineage_system_events(lineage, "sample_admin")
    if change_leader:
        ok("UpdateLineage", {"lineage_id": lineage, "changes": {"leader_resident_id": second}})
    facts.record({"ok": True, "status": "completed", "commit": "a" * 40,
                  "active_commit": "a" * 40, "parent_commit": "b" * 40,
                  "started_at": 100, "completed_at": 101, "origin": "direct"})
    with operation_identity_scope(OperationIdentity.for_actor("sample_admin")):
        result = lineage_domain.reconcile_lineage_system_events(lineage, "sample_admin")
        assert current_operation_identity().principal.id == "sample_admin"
    assert len(observed) == 1
    call = observed[0]
    actor = second if change_leader else first
    expected = OperationIdentity.for_actor(actor).to_dict()
    assert call["call"]["instruction"] == "RunScript"
    assert call["caller"] == actor
    assert call["activation"]["identity"] == expected
    assert call["activation"]["trigger_type"] == "portal.release_published"
    assert call["identity"] == expected
    assert result["event_count"] == 1
    assert result["events"][0]["event"]["identity"] == expected
    # A later Leader change does not re-run an accepted publication.
    ok("UpdateLineage", {"lineage_id": lineage, "changes": {"leader_resident_id": first if change_leader else second}})
    assert lineage_domain.reconcile_lineage_system_events(lineage, "sample_admin")["event_count"] == 0
    assert len(observed) == 1
