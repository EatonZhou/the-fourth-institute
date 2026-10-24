import json
import re
import subprocess
import hashlib


from tests.cases.test_wall07_gap_helper06 import (
    ROOT, browser_owner_server, isolated_owner,
)


def panel_functions(names):
    """The named top-level functions of hatg_panel.js, to run under node."""
    source = (ROOT / "management_portal/static/hatg_panel.js").read_text()
    result = []
    for name in names:
        found = re.search(r"^  (?:async )?function " + re.escape(name) + r"\([^\n]*.*?^  }$", source, re.M | re.S)
        assert found, name
        result.append(found.group())
    return "\n".join(result)


# Runtime functions the probe drives. The edit queue, layout save, Lineage
# commit, object separation and the Resident ID of a node come from the
# production module scripts the driver loads.
FUNCTIONS = [
    "applyProgramMutation", "createProgramComponentFromDrop",
]
DRIVER = ROOT / "tests/cases/javascript/lineage_edit_queue.cjs"


def run_probe(mode, record_property, **values):
    source = panel_functions(FUNCTIONS)
    result = subprocess.run(
        ["node", str(DRIVER)], cwd=ROOT,
        input=json.dumps({"mode": mode, "root": str(ROOT), "functions": source, **values}),
        text=True, capture_output=True, timeout=65,
    )
    assert result.returncode == 0, result.stderr[-6000:]
    proof = json.loads(result.stdout)
    record_property("probe", json.dumps(proof, ensure_ascii=False))
    record_property("function_bundle_sha256", hashlib.sha256(source.encode()).hexdigest())
    return proof


def test_lineage_edit_queue_boundaries(record_property, subtests, isolated_portal_case):
    """编辑队列失败时阻止后续创建并保留较新位置，切换或销毁 Lineage 时作废旧编辑意图"""
    for scenario in [
    "failure_blocks_creation", "newer_positions_survive_failure", "drain_positions",
    "switch_does_not_block_other_lineage", "same_id_new_visit_fences_old_intent",
    "destroy_fences_queued_intents", "layout_waits_for_program",
]:
        with subtests.test(scenario=scenario), isolated_portal_case() as case_path:
            assert run_probe("node", record_property, scenario=scenario)["passed"]


def test_delayed_layout_then_creation_uses_committed_obstacles(browser_owner_server, record_property):
    """Node 控制器等待隔离 owner 提交布局，再创建组件并避开已移动节点的位置"""
    origin, observations = browser_owner_server
    proof = run_probe("owner", record_property, origin=origin)
    assert proof["moved_position"] != proof["created_position"]
    assert not proof["writes_before_layout_release"]
    assert all(not row["layout_held"] for row in observations if row["path"].endswith("/components/create"))


def test_navigation_hooks_invalidate_lineage_edit_intents():
    """切换、清空或销毁 Lineage 面板时作废未完成的编辑意图，不等待布局保存"""
    selecting = panel_functions(["selectLineage"])
    empty = panel_functions(["renderNoLineage"])
    source = (ROOT / "management_portal/static/hatg_panel.js").read_text()
    assert "activateLineageEdits(lineageId)" in selecting
    assert "await state.layoutSaveInFlight" not in selecting
    assert "activateLineageEdits()" in empty
    destroy_body = source.split("destroy() {\n        destroyed = true;", 1)[1].split("\n      },", 1)[0]
    assert "activateLineageEdits();" in destroy_body
    assert "programMutationTail" not in source
