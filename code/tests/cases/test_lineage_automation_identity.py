"""New unattended observations use the current registered Lineage Leader."""
from copy import deepcopy

import pytest

from management_common.operation_identity import operation_identity_scope
from management_common.principal import OperationIdentity
from management_portal.society_service import lineage_automation_identity as identity
from management_portal.society_service import (
    lineage_graph_edits, lineage_lifecycle, lineage_program_occurrences, lineage_program_runtime,
)
from management_portal.society_service import lineage_aggregate
from tests.cases.isa_test_support import ok, run
from tests.cases.test_isa_lineage_lifecycle import future_creation_store, lineage, resident, graph  # noqa: F401
from tests.cases.permission_test_support import permissions  # noqa: F401

USER = {"kind": "user", "id": "sample_admin"}
RESIDENT = "resident_aaaaaaaaaaaa"
SECOND = "resident_bbbbbbbbbbbb"


def test_runtime_actor_uses_current_leader_without_user_configuration():
    definition = {"leader_resident_id": RESIDENT, "nodes": [{"id": RESIDENT}]}
    assert identity.runtime_actor(definition) == RESIDENT


@pytest.mark.parametrize("legacy", [None, USER, {"kind": "user", "id": "disabled"}, "malformed"])
def test_leader_overrides_legacy_user_creator_and_ambient_authority(legacy):
    definition = {"leader_resident_id": RESIDENT, "nodes": [{"id": RESIDENT}],
                  "run_as_principal": legacy, "created_by": "sample_admin"}
    before = deepcopy(definition)
    with operation_identity_scope(OperationIdentity.for_actor("sample_admin")):
        assert identity.runtime_actor(definition) == RESIDENT
        assert identity.readiness_error(definition) == ""
    assert definition == before


@pytest.mark.parametrize("leader,nodes", [
    (None, []), ("", [{"id": RESIDENT}]), ("sample_admin", [{"id": "sample_admin"}]),
    ("portal", [{"id": "portal"}]), ("resident_invalid", [{"id": "resident_invalid"}]),
    (RESIDENT, []), (RESIDENT, [{"id": SECOND}]), (RESIDENT, [{"resident_id": RESIDENT}]),
])
def test_missing_or_invalid_leader_never_borrows_other_authority(leader, nodes):
    definition = {"leader_resident_id": leader, "nodes": nodes, "run_as_principal": USER,
                  "created_by": "sample_admin"}
    with operation_identity_scope(OperationIdentity.for_actor("sample_admin")):
        with pytest.raises(identity.AutomationConfigurationError) as caught:
            identity.runtime_actor(definition)
        assert identity.readiness_error(definition) == str(caught.value)


def test_runtime_actor_follows_current_leader_without_mutating_saved_events():
    definition = {"leader_resident_id": RESIDENT, "nodes": [{"id": RESIDENT}, {"id": SECOND}]}
    accepted = OperationIdentity.for_actor(identity.runtime_actor(definition)).to_dict()
    definition["leader_resident_id"] = SECOND
    assert identity.runtime_actor(definition) == SECOND
    assert accepted == OperationIdentity.for_actor(RESIDENT).to_dict()


@pytest.mark.parametrize("value", [USER, None, "sample_admin", {"kind": "resident", "id": RESIDENT}])
@pytest.mark.parametrize("entry", ["create", "update"])
def test_formal_isa_rejects_removed_user_configuration(lineage, entry, value):
    if entry == "create":
        result = run("CreateLineage", {"project_id": "project_test", "title": "Removed field",
                     "definition": {"run_as_principal": value}})
    else:
        result = run("UpdateLineage", {"lineage_id": lineage, "changes": {"run_as_principal": value}})
    assert result.error and "run_as_principal" in str(result.error)
    assert "run_as_principal" not in graph(lineage)


def test_registered_leader_change_preserves_accepted_runtime_and_graph_edits(lineage):
    first = resident(lineage, "First")
    second = resident(lineage, "Second")
    assert identity.runtime_actor(graph(lineage)) == first
    source = ok("CreateComponent", {"lineage_id": lineage, "kind": "event_source",
                "config": {"event_type": "manual.triggered"}})["component_id"]
    lineage_program_occurrences.claim_event_occurrence(lineage, source, "accepted", max_fires=3)
    before = deepcopy(lineage_program_runtime.read_runtime(lineage))
    ok("UpdateLineage", {"lineage_id": lineage, "changes": {"leader_resident_id": second}})
    assert identity.runtime_actor(graph(lineage)) == second
    assert lineage_program_runtime.read_runtime(lineage) == before
    ok("UpdateLineage", {"lineage_id": lineage, "changes": {"title": "Renamed"}})
    current = graph(lineage)
    assert current["title"] == "Renamed" and identity.runtime_actor(current) == second
    assert "run_as_principal" not in current
    assert lineage_program_runtime.read_runtime(lineage) == before


@pytest.mark.parametrize("entry", ["create", "update", "metadata"])
def test_domain_cannot_reintroduce_removed_user_configuration(lineage, entry):
    with pytest.raises(ValueError, match="run_as_principal"):
        if entry == "create":
            lineage_lifecycle.create_lineage({"run_as_principal": USER}, "sample_admin")
        elif entry == "metadata":
            lineage_lifecycle.update_lineage_metadata(lineage, {"run_as_principal": None}, "sample_admin")
        else:
            current = lineage_aggregate.read_lineage_state(lineage)
            lineage_graph_edits.update_lineage(lineage, {**current, "run_as_principal": USER}, "sample_admin")


def test_registered_leader_without_permission_cannot_borrow_ambient_user(lineage, permissions):
    from management_portal.hatg.contracts import InstructionCall
    from management_portal.society_service import lineage_instruction_context, lineage_primitives
    from management_portal.society_service.isa import build_kernel

    permissions.administrator("sample_admin")
    leader = resident(lineage, "Restricted Leader")
    permissions.deny(leader, lineage, "@viewer")
    assert not permissions.holds(leader, "@viewer", lineage)
    assert run("GetLineage", {"lineage_id": lineage}).error is None
    activation = lineage_primitives.make_event("time.reached", identity=OperationIdentity.for_actor(leader).to_dict())
    with operation_identity_scope(OperationIdentity.for_actor("sample_admin")):
        context = lineage_instruction_context.event_context(activation, leader)
        assert context.caller == leader
        result = build_kernel().execute(InstructionCall.from_payload({
            "instruction": "GetLineage", "arguments": {"lineage_id": lineage},
        }), context)
    assert result.error and result.error["details"]["reason"] == "denied"
