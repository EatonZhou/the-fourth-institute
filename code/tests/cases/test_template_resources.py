"""Template resources use SocietyService's recipe and the shared viewer permissions."""
import json

from tests.cases.isa_test_support import ok, run
from tests.cases.test_isa_lineage_lifecycle import lineage as lineage  # noqa: F401
from tests.cases.test_isa_lineage_lifecycle import future_creation_store as future_creation_store  # noqa: F401


def test_template_resource_reads_and_shared_permissions(lineage, monkeypatch):
    from tests.cases.permission_test_support import enable
    from management_portal import permission_system

    recipe = ok("CreateTemplate", {"template": {
        "kind": "structure", "title": "Resource recipe", "body": {
            "roles": [{"key": "target", "from": "target"}], "components": [], "wires": [],
        },
    }})["template"]
    identifier = recipe["template_id"]
    permissions = enable(monkeypatch)
    permissions.users("reader", "refused")
    permissions.grant("reader", "@shared", "@viewer")
    permissions.deny("refused", "@shared", "@viewer")
    for instruction, arguments in (("GetTemplate", {"template_id": identifier}),
                                    ("ResolveResource", {"address": identifier}),
                                    ("ReadResource", {"address": identifier})):
        assert permission_system.allows("reader", instruction, arguments)
        result = ok(instruction, arguments, actor="reader")
        if instruction == "ReadResource":
            assert json.loads(result["text"])["body"] == recipe["body"]
        if instruction == "ResolveResource":
            assert result["resource"]["title"] == "Resource recipe"
        assert run(instruction, arguments, actor="refused").status == "failed"
