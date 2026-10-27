"""TAU Family fixture ownership must match its original exact objects.

:module: lineage.hatg
:design: decision_44cf587c1721
"""
import copy
import json
import subprocess
from pathlib import Path

from management_portal.society_service import family_workflows


def test_family_fixture_scope_uses_native_selection_and_ownership():
    """原生选区不等于归属，独立组件不得换成模板内置对象。

    :design-point:
    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    expected = dict(lineageId="lineage_fixture", blockId="fbi_fixture", familyResourceId="family_fixture",
                    facadeId="resident_facade", memberId="resident_member",
                    effectIds=["effect_a", "effect_b"], eventId="event_source_manual")
    ids = [*expected["effectIds"], expected["eventId"]]
    before = {
        "lineage_id": expected["lineageId"], "revision": 1,
        "nodes": [{"id": expected["memberId"], "kind": "resident"}],
        "function_blocks": [{"id": expected["blockId"],
                             "family": {"resource_id": expected["familyResourceId"], "facade": expected["facadeId"],
                                        "members": [expected["memberId"]]},
                             "elements": [{"id": expected["memberId"], "kind": "resident", "key": "member:" + expected["memberId"]}]}],
        "program": {"wires": [], "components": [
            {"id": "effect_a", "kind": "effect", "title": "A", "config": {"failure_recipients": ["fixture-user"]}},
            {"id": "effect_b", "kind": "effect", "title": "B", "config": {"failure_recipients": ["fixture-user"]}},
            {"id": "event_source_manual", "kind": "event_source", "config": {"event_type": "manual.triggered"}},
        ]},
    }
    collected = copy.deepcopy(before)
    family_workflows.update(collected, collected["function_blocks"][0], "fixture-user", collect=True)
    assert collected == before, "disconnected components do not have a Resident boundary"
    adopted = copy.deepcopy(collected)
    family_workflows.update(adopted, adopted["function_blocks"][0], "fixture-user", add=ids)
    assert adopted["program"] == before["program"], "explicit ownership preserves objects and configuration"
    script = Path(__file__).with_name("javascript") / "tau_family_fixture_scope.cjs"
    result = subprocess.run(["node", str(script)], input=json.dumps(dict(before=before, collected=collected,
                            adopted=adopted, expected=expected)), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    print(result.stdout)
