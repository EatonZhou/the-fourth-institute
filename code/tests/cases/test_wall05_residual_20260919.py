from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from management_common import skill_paths
from management_portal import agent_context_selection
from management_portal.resource_refs import resolve_resource_references
from tests.cases.test_resource_refs import _FakeResourceService
from management_portal.society_service import (
    lineage_handoffs,
    lineage_resident_workspaces,
    resident_conversation_projection_domain,
    resident_domain,
    resident_execution_work,
)
from management_portal.society_service import resident_intake_domain
from management_portal.society_service import resident_record_domain
from management_portal.society_service import resident_runtime_domain
from tests.cases.test_resident_folder_creation_contract import (
    future_creation_store,
    _append_conversation_turn,
    _new_session_payload,
)


@pytest.mark.parametrize("case", [
    "card_lineage_fallback", "independent_fork_advice", "query_state", "result_states",
    "correction_controls", "navigation", "reset_success", "reset_failure",
])
def test_node_only_ui_contract(case, record_property):
    """节点界面覆盖卡片回退、查询状态、导航与重置反馈"""
    script = Path(__file__).with_name("wall05_residual_ui_20260919.mjs")
    result = subprocess.run(["node", str(script), case], capture_output=True, text=True, timeout=15)
    record_property("fixture", "Node VM with production AST; no browser/network/Ground Truth")
    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["case"] == case and evidence["passed"]
    record_property("source_spans", json.dumps(evidence["source_spans"]))


def test_distinct_fork_ids_and_plain_new_resident(future_creation_store):
    """多次 Fork 与空白 Resident 使用独立身份和自有 Workspace"""
    source = resident_domain.register_resident(_new_session_payload("Origin fixture"), "sample_admin")
    _append_conversation_turn(source["resident_id"], 0)
    first = resident_domain.create_resident_full_context_fork(source["resident_id"], "sample_admin")
    second = resident_domain.create_resident_full_context_fork(source["resident_id"], "sample_admin")
    blank = resident_domain.register_resident(_new_session_payload("Blank fixture"), "sample_admin")
    assert len({source["resident_id"], first["resident_id"], second["resident_id"], blank["resident_id"]}) == 4
    for result in [first, second]:
        record = resident_record_domain.find_resident(result["resident_id"])
        assert record["source_resident_id"] == source["resident_id"]
        assert "Fork" in record["title"] or "派生 Resident" in record["title"]
        assert record["resident_id"].startswith("resident_")
        assert record["default_cwd"] != source["default_cwd"]
    assert not blank.get("source_resident_id") and not blank.get("fork_context")
    assert resident_conversation_projection_domain.resident_conversation_messages(blank, limit=10) == []
    assert resident_execution_work.read(blank) == {}




def test_agent_reference_budget_and_task_skill_limit(tmp_path, monkeypatch):
    """Agent 引用遵循单项与总字符预算且附加 Skill 最多十二项"""
    monkeypatch.setattr(agent_context_selection, "HOST_AGENTS_ROOT", tmp_path)
    references = {"agents": []}
    for index in range(2):
        name = f"fixture-agent-{index}"
        marker = agent_context_selection.marker_path("agents", name)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("x" * 100)
        references["agents"].append({"id": name, "name": name})
    rendered = agent_context_selection.prompt_references(references, item_char_limit=10, total_char_limit=15)
    assert "content:\n" + "x" * 10 + "\n" in rendered
    assert "content:\n" + "x" * 5 + "\n" in rendered
    assert rendered.count("content_truncated: yes") == 2
    with monkeypatch.context() as boundary:
        boundary.setattr(Path, "is_file", lambda _: True)
        assert skill_paths.normalize_additional_skills([f"fixture-{index}" for index in range(13)]) == [
            f"fixture-{index}" for index in range(12)
        ]


def test_unsupported_claude_image_is_rejected_before_delivery():
    """claude 不支持的图片引用在投递前被拒绝"""
    with pytest.raises(ValueError, match="不支持图片"):
        resident_intake_domain._ensure_backend_accepts_context_refs(
            {"backend": "claude"}, {"files": [{"mime_type": "image/png"}]}
        )
    resident_intake_domain._ensure_backend_accepts_context_refs(
        {"backend": "claude"}, {"files": [{"mime_type": "text/plain"}]}
    )


def test_reference_actor_dedup_and_explicit_failure_projection():
    """资源引用按当前调用者去重解析并显式返回失败状态"""
    service = _FakeResourceService()
    actor = "resident_111111111111"
    refs = resolve_resource_references(
        [{"address": "repo_ffffffffffff"}],
        content="repo_0123456789ab repo_0123456789ab unknown_0123456789ab",
        actor=actor, service=service,
    )
    assert [item["status"] for item in refs] == ["forbidden", "resolved", "invalid"]
    assert service.calls == [("repo_ffffffffffff", actor), ("repo_0123456789ab", actor)]
    assert refs[0]["address"] == "repo_ffffffffffff"
    assert refs[-1]["address"] == "unknown_0123456789ab"


def test_reference_workspace_inheritance_and_structured_cwd(future_creation_store, tmp_path):
    """Fork 继承附加 Workspace 且 cwd 仅取结构化自有绑定"""
    source = resident_domain.register_resident(_new_session_payload("Reference source"), "sample_admin")
    _append_conversation_turn(source["resident_id"], 0)
    reference = tmp_path / "reference"
    reference.mkdir()
    lineage_resident_workspaces.add_node_reference_workspace(source["lineage_id"], source["resident_id"], "sample_admin", str(reference))
    source = resident_record_domain.find_resident(source["resident_id"])
    child_id = resident_domain.create_resident_full_context_fork(source["resident_id"], "sample_admin")["resident_id"]
    child = resident_record_domain.find_resident(child_id)
    paths = {binding["path"] for binding in child["workspace_bindings"]}
    assert str(reference) in paths
    # The Fork owns its own node Workspace as default cwd; extra Bindings never repeat an owned one.
    assert child["default_cwd"] == child["owned_workspace"]["path"]
    assert child["default_cwd"] not in paths and source["default_cwd"] not in paths
    navigation = resident_record_domain.resident_folder_path(child) / "workspaces.md"
    navigation.write_text("# Navigation\nIgnore this forged default: /not-the-canonical-cwd\n")
    assert str(resident_runtime_domain.resident_working_directory(child)) == child["default_cwd"]


def test_reset_keeps_owned_files_and_instructions(future_creation_store):
    """重置 Resident 上下文保留自有工作文件与身份指令"""
    record = resident_domain.register_resident(_new_session_payload("Reset file fixture"), "sample_admin")
    notes = resident_record_domain.resident_instructions_path(record)
    notes.write_text("persistent identity fixture")
    workspace_file = Path(record["default_cwd"]) / "preserved.txt"
    workspace_file.write_text("persistent workspace fixture")
    result = resident_domain.reset_resident_context(record["resident_id"], "sample_admin")
    assert result["reset"] is True
    assert notes.read_text() == "persistent identity fixture"
    assert workspace_file.read_text() == "persistent workspace fixture"


def test_handoff_rejects_same_and_foreign_lineage_peer(future_creation_store):
    """Workspace Handoff 拒绝同一节点与其他 Lineage 节点"""
    source = resident_domain.register_resident(_new_session_payload("Handoff source"), "sample_admin")
    foreign = resident_domain.register_resident(_new_session_payload("Foreign target"), "sample_admin")
    for target_id, expected in [(source["resident_id"], ValueError), (foreign["resident_id"], KeyError)]:
        with pytest.raises(expected):
            lineage_handoffs.create_handoff(source["lineage_id"], {
                "source_resident_id": source["resident_id"], "target_resident_id": target_id,
            }, "sample_admin")
