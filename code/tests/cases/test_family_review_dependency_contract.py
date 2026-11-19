from copy import deepcopy

from management_portal.society_service.family_definition import bind_review_activity, family_elements
from tests.cases.test_society_service_layers import _imports


def test_family_review_dependencies_follow_ownership():
    imports = _imports()
    forbidden = {
        "family_familize": {"family_review_duty"},
        "family_tasks": {"family_review_duty"},
        "family_review_duty": {"templates.familize", "templates.builtins", "lineage_state"},
        "templates.familize": {"family_review_duty"},
    }
    violations = [
        f"{source}:{imports[source][target]} -> {target}"
        for source, targets in forbidden.items()
        for target in sorted(targets & imports[source].keys())
    ]
    assert not violations, "\n".join(violations)


def test_shared_activity_binding_keeps_the_existing_wire_and_checks_the_holder():
    parent = {
        "id": "family",
        "family": {"steward": "current"},
        "elements": [{"id": "review", "key": "duty:review"}, {"id": "done", "key": "member_done"}],
    }
    duty = {
        "id": "review",
        "duty": {"holder_id": "former"},
        "elements": [{"id": "activity", "key": "review_activity"}],
    }
    graph = {"lineage_id": "lineage", "function_blocks": [parent, duty], "program": {"wires": []}}
    bind_review_activity(graph, parent)
    assert family_elements(graph, parent) == parent["elements"]
    assert graph["program"]["wires"] == []

    duty["duty"]["holder_id"] = "current"
    bind_review_activity(graph, parent)
    wire, = graph["program"]["wires"]
    assert (wire["source"], wire["target"]) == ("done", "activity")
    assert wire["config"]["invoke_profiles"]["effect"]["arguments"]["family_id"]["value"] == parent["id"]
    before = deepcopy(graph)
    bind_review_activity(graph, parent)
    assert graph == before
