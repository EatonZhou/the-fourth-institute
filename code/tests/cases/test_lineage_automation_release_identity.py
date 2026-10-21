"""Publication facts carry configured authority through an Effect's RunScript."""
from copy import deepcopy
from types import SimpleNamespace
import pytest

from management_common.operation_identity import current_operation_identity, operation_identity_scope
from management_common.principal import OperationIdentity
from management_common import portal_release_events as facts
from management_portal.hatg import routing
from management_portal.society_service import lineage_domain, trigger_paths
from management_portal.society_service.script_runs.service import ACTIVATION
from tests.cases.test_lineage_generic_primitives import primitive_store  # noqa: F401


@pytest.mark.parametrize("configured_user", [False, True])
def test_publication_reaches_runscript_without_ambient_identity(primitive_store, monkeypatch, configured_user):
    """发布事件触发的 RunScript 以配置用户或 portal 身份运行，不沿用外层身份，同一发布只运行一次"""
    current = lineage_domain.create_lineage({
        "title": "publication authority", "run_as_principal": {"kind": "user", "id": "sample_admin"} if configured_user else None,
        "program": {"components": [
            {"id": "event_source_release_identity", "kind": "event_source",
             "config": {"event_type": "portal.release_published"}},
            {"id": "effect_release_identity", "kind": "effect", "config": {
                "script": 'RunScript path="scripts/audit.py" runtime="python"', "endpoints": []}},
        ], "wires": [{"id": "wire_release_identity", "kind": "route",
                      "source": "event_source_release_identity", "source_port": "output",
                      "target": "effect_release_identity", "target_port": "invoke", "config": {}}]},
    }, "sample_admin")
    lineage_id = current["lineage_id"]
    observed = []

    def execute(call, context):
        observed.append({"call": deepcopy(call), "caller": context.caller,
                         "activation": deepcopy(ACTIVATION.get()),
                         "identity": current_operation_identity().to_dict()})
        return SimpleNamespace(instruction_id="inst_test_release_identity", status="committed",
                               result={"script_run_id": "script_run_test_identity", "status": "running"}, error=None)

    monkeypatch.setattr(routing, "execute", execute)
    monkeypatch.setattr(routing, "release_recovery", lambda *args, **kwargs: None)
    lineage_domain.reconcile_lineage_system_events(lineage_id, "sample_admin")
    facts.record({"ok": True, "status": "completed", "commit": "a" * 40,
                  "active_commit": "a" * 40, "parent_commit": "b" * 40,
                  "started_at": 100, "completed_at": 101, "origin": "direct"})
    with operation_identity_scope(OperationIdentity.for_actor("someone_else")):
        result = lineage_domain.reconcile_lineage_system_events(lineage_id, "sample_admin")
        assert current_operation_identity().principal.id == "someone_else"
    assert len(observed) == 1
    call = observed[0]
    actor = "sample_admin" if configured_user else "portal"
    expected = OperationIdentity.for_actor(actor).to_dict()
    assert call["call"]["instruction"] == "RunScript"
    assert call["caller"] == actor
    assert call["activation"]["identity"] == expected
    assert call["activation"]["trigger_type"] == "portal.release_published"
    assert call["identity"] == expected
    assert result["event_count"] == 1
    # A second poll reuses the publication cursor, without launching a second script.
    assert lineage_domain.reconcile_lineage_system_events(lineage_id, "sample_admin")["event_count"] == 0
    assert len(observed) == 1
