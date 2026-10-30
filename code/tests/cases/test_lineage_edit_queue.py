import json
import re
import subprocess
import hashlib


from tests.cases.test_wall07_gap_helper06 import (
    ROOT, browser_owner_server, isolated_owner,
)


# The HATG panel runtime: hatg_panel.js and the parts it creates.
PANEL_SCRIPTS = [
    "hatg_panel.js", "hatg_panel_lineage_loading.js", "hatg_panel_template_shelf.js",
    "hatg_panel_bindings.js", "hatg_panel_mount.js",
]


def panel_functions(names):
    """The named functions of the HATG panel's Lineage loading part, to run under node."""
    source = (ROOT / "management_portal/static/hatg_panel_lineage_loading.js").read_text()
    result = []
    for name in names:
        found = re.search(r"^    (?:async )?function " + re.escape(name) + r"\([^\n]*.*?^    }$", source, re.M | re.S)
        assert found, name
        result.append(found.group())
    return "\n".join(result)


# The production module scripts the probe drives: the Resident ID of a node,
# the edit queue, layout save and Lineage commit, program edits and the
# creation of a dropped component, and object separation.
MODULES = [
    "hatg_graph_model.js", "hatg_lineage_state.js", "hatg_program_edits.js", "hatg_layout.js",
]
DRIVER = ROOT / "tests/cases/javascript/lineage_edit_queue.cjs"


def run_probe(mode, record_property, **values):
    source = "".join((ROOT / "management_portal/static" / name).read_text() for name in MODULES)
    result = subprocess.run(
        ["node", str(DRIVER)], cwd=ROOT,
        input=json.dumps({"mode": mode, "root": str(ROOT), "modules": MODULES, **values}),
        text=True, capture_output=True, timeout=65,
    )
    assert result.returncode == 0, result.stderr[-6000:]
    proof = json.loads(result.stdout)
    record_property("probe", json.dumps(proof, ensure_ascii=False))
    record_property("module_bundle_sha256", hashlib.sha256(source.encode()).hexdigest())
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
    source = "".join((ROOT / "management_portal/static" / name).read_text() for name in PANEL_SCRIPTS)
    assert "activateLineageEdits(lineageId)" in selecting
    assert "await state.layoutSaveInFlight" not in selecting
    assert "activateLineageEdits()" in empty
    destroy_body = source.split("destroy() {\n          destroyed = true;", 1)[1].split("\n        },", 1)[0]
    assert "activateLineageEdits();" in destroy_body
    assert "programMutationTail" not in source
