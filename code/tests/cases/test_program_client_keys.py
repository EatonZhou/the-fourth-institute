"""Workflow bindings survive edits and use the same authoring contract over ISA."""
import pytest

from management_portal.hatg.contracts import InstructionCall, InstructionContext
from management_portal.society_service.isa import build_kernel
from tests.cases.test_isa_audit_g4 import make_lineage, registered_members
from tests.cases.test_lineage_generic_primitives import primitive_store, ACTOR


def call(name, arguments):
    return build_kernel().execute(InstructionCall.from_payload({
        "instruction": name, "arguments": arguments,
    }), InstructionContext(caller=ACTOR, entry="web"))


@pytest.mark.parametrize("via_batch", [False, True])
def test_existing_objects_can_acquire_but_not_replace_binding_keys(primitive_store, via_batch):
    """已有组件和连线可补充绑定键且不能替换原键"""
    lid = make_lineage()["lineage_id"]
    created = call("ApplyLineageChanges", {"lineage_id": lid, "operations": [
        {"op": "create", "kind": "component", "ref": "event", "value": {
            "kind": "event_source", "config": {"event_type": "manual.triggered"}}},
        {"op": "create", "kind": "component", "ref": "effect", "value": {
            "kind": "effect", "config": {"script": ""}}},
        {"op": "create", "kind": "wire", "ref": "route", "value": {
            "kind": "route", "source": "$ref:event", "target": "$ref:effect",
            "client_key": "system:test:route"}},
    ]}).raise_for_error()["refs"]

    def update(kind, identifier, changes):
        if via_batch:
            return call("ApplyLineageChanges", {"lineage_id": lid, "operations": [
                {"op": "update", "kind": kind, "id": identifier, "changes": changes}]})
        return call("UpdateComponent" if kind == "component" else "UpdateWire", {
            "lineage_id": lid, "component_id" if kind == "component" else "wire_id": identifier,
            "changes": changes})

    update("component", created["event"], {"client_key": "system:test:manual"}).raise_for_error()
    update("component", created["event"], {"title": "Renamed"}).raise_for_error()
    for kind, identifier in [("component", created["event"]), ("wire", created["route"])]:
        result = update(kind, identifier, {"client_key": "system:test:replacement"})
        assert result.status == "failed"
    graph = call("GetLineage", {"lineage_id": lid}).raise_for_error()["lineage"]["program"]
    assert next(c for c in graph["components"] if c["id"] == created["event"])["client_key"] == "system:test:manual"
    assert next(w for w in graph["wires"] if w["id"] == created["route"])["client_key"] == "system:test:route"
