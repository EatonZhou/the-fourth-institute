import copy
import json

import pytest

from management_portal import portal_pulse_backend as pulse
from scripts import portal_pulse_lineage as migration


def _history(root):
    path = root / "summaries/daily/2026-10-14.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"period": "daily", "key": "2026-10-14", "content": {
        "global_summary": "Existing public history",
        "user_private_updates": {
            "alice": {"items": [{"id": "one", "text": "Alice's private history"}]},
            "bob": {"items": [{"id": "two", "text": "Bob's private history"}]},
        },
    }}), encoding="utf-8")
    path.chmod(0o600)
    (root / "index.json").write_text(json.dumps({"latest": {"daily": "2026-10-14"},
        "history": [{"period": "daily", "key": "2026-10-14", "path": str(path)}]}), encoding="utf-8")
    return path


def test_migrated_history_preserves_home_and_publication_ownership(tmp_path, monkeypatch):
    """迁移后的 Pulse 历史首页内容不变，发布仍归原用户所有，他人不能撤回"""
    source, target = tmp_path / "old", tmp_path / "system"
    summary = _history(source)
    before = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    monkeypatch.setenv("PORTAL_PULSE_ROOT", str(source))
    publication = pulse.update_publication("alice", {
        "source_period": "daily", "source_key": "2026-10-14", "item_id": "one",
    })["publication"]
    original_home = pulse.get_home_payload("alice")

    assert migration.copy_history(source, target)["applied"] is False
    assert not target.exists()
    result = migration.copy_history(source, target, apply=True)
    assert result["summary_files"] == 1
    assert (target / summary.relative_to(source)).stat().st_mode & 0o777 == 0o600
    assert json.loads((target / "index.json").read_text())["history"][0]["path"] == str(
        target / summary.relative_to(source))
    assert migration.copy_history(source, target, apply=True) == result
    assert before == {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}

    monkeypatch.setenv("PORTAL_PULSE_ROOT", str(target))
    home = pulse.get_home_payload("alice")
    assert home["periods"] == original_home["periods"]
    assert home["publications"] == original_home["publications"]
    assert home["periods"]["daily"]["mine"]["items"][0]["published"] is True
    assert pulse.get_home_payload("bob")["periods"]["daily"]["mine"]["items"][0]["text"] == "Bob's private history"
    item = {"source_period": publication["source_period"], "source_key": publication["source_key"],
            "item_id": publication["source_item_id"]}
    with pytest.raises(PermissionError):
        pulse.update_publication("bob", {"action": "unpublish", **item})
    pulse.update_publication("alice", {"action": "unpublish", **item})
    assert pulse.get_home_payload("alice")["publications"] == []


def test_copy_rejects_conflicting_destination_before_writing(tmp_path):
    """Pulse 历史复制遇到内容不同的目标文件时，在写入前拒绝"""
    source, target = tmp_path / "old", tmp_path / "system"
    summary = _history(source)
    existing = target / summary.relative_to(source)
    existing.parent.mkdir(parents=True)
    existing.write_text("newer publication", encoding="utf-8")
    with pytest.raises(FileExistsError, match="destination differs"):
        migration.copy_history(source, target, apply=True)
    assert existing.read_text() == "newer publication"
    assert not (target / "index.json").exists()


def test_copy_rejects_missing_summary_and_symlink_before_writing(tmp_path):
    """Pulse 历史复制遇到缺失的摘要或符号链接时，在写入前拒绝"""
    source, target = tmp_path / "old", tmp_path / "system"
    summary = _history(source)
    original = summary.read_bytes()
    summary.unlink()
    with pytest.raises(ValueError, match="missing summary"):
        migration.copy_history(source, target, apply=True)
    assert not target.exists()
    outside = tmp_path / "outside.json"
    outside.write_bytes(original)
    summary.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        migration.copy_history(source, target, apply=True)
    assert not target.exists()


def test_root_switch_waits_for_completed_copy_and_never_revives_old_history(tmp_path, monkeypatch):
    """Pulse 存储根只在复制完成后切换到新目录，切换后不再回退读取旧历史"""
    source, target = tmp_path / "old/memory/pulse", tmp_path / "system/memory/pulse"
    _history(source)
    monkeypatch.delenv("PORTAL_PULSE_ROOT", raising=False)
    monkeypatch.setattr(pulse, "read_site_config", lambda: {"deployment": {"pulse": {
        "lineage_id": "lineage_111111111111", "legacy_lineage_id": "lineage_222222222222"}}})
    monkeypatch.setattr(pulse, "canonical_lineage_folder", lambda lid:
        tmp_path / ("system" if lid == "lineage_111111111111" else "old"))
    before = pulse.get_home_payload("alice")["periods"]
    assert pulse._pulse_root() == source
    assert before["daily"]["public"]["global_summary"] == "Existing public history"
    target.mkdir(parents=True)
    (target / "index.json").write_bytes((source / "index.json").read_bytes())
    assert pulse._pulse_root() == source  # a partial copy is not a cutover
    (target / "index.json").unlink()
    migration.copy_history(source, target, apply=True)
    assert pulse._pulse_root() == target
    assert pulse.get_home_payload("alice")["periods"] == before
    (target / "index.json").unlink()
    assert pulse._pulse_root() == target
    assert pulse.get_home_payload("alice")["periods"]["daily"]["public"] == {}


def _definitions(tmp_path, monkeypatch):
    monkeypatch.setattr(migration, "canonical_lineage_folder", lambda lid: tmp_path / lid)
    workspace = tmp_path / migration.SOURCE_LINEAGE / "residents" / migration.RESIDENT / "workspace"
    old_workspace = f"/share/Projects/AI员工/knowledge_base/agents/{migration.SOURCE_LINEAGE}/residents/{migration.RESIDENT}/workspace"
    old_data = f"/share/Projects/AI员工/knowledge_base/agents/{migration.SOURCE_LINEAGE}/memory/pulse"
    (workspace / "tools").mkdir(parents=True)
    (workspace / "duties").mkdir()
    (workspace / "tools/pulsetool.py").write_text(f'PULSE_ROOT = Path("{old_data}")\n', encoding="utf-8")
    (workspace / "duties/pulse.md").write_text(f"cd {old_workspace}/tools", encoding="utf-8")
    source = {"lineage_id": migration.SOURCE_LINEAGE, "revision": 26,
        "nodes": [{"id": migration.RESIDENT}, {"id": "resident_000000000001"}],
        "program": {"components": [{"id": eid, "config": {"schedule_mode": "recurring"}} for eid in migration.EVENTS],
                    "wires": [{"id": f"wire_{i:012x}", "source": eid, "target": migration.RESIDENT,
                        "config": {"queue_limit": 10, "unavailable_policy": "queue", "invoke_profiles": {
                            "agent": {"arguments": {"prompt": {"value": f"读 {old_workspace}/duties/pulse.md"}}}}}}
                        for i, eid in enumerate(migration.EVENTS)]},
        "program_event_source_runtime": {migration.EVENTS[1]: {"paused": True}}}
    target = {"lineage_id": migration.TARGET_LINEAGE, "revision": 53, "nodes": [], "function_blocks": []}
    return source, target


def test_plan_moves_only_pulse_and_preserves_disabled_events(tmp_path, monkeypatch):
    """Pulse 迁移计划只移动 Pulse 的 Resident 和事件，已停用的事件迁移后保持停用"""
    source, target = _definitions(tmp_path, monkeypatch)
    before = copy.deepcopy((source, target))
    plan = migration.build_plan(source, target)
    assert (source, target) == before
    move = plan["move"][0]
    assert move["arguments"]["object_ids"] == [migration.RESIDENT, *migration.EVENTS]
    assert move["expected_version"] == {migration.SOURCE_LINEAGE: 26, migration.TARGET_LINEAGE: 53}
    assert len(plan["pause"]) == len(plan["resume"]) == 3
    assert all(call["arguments"]["component_id"] != migration.EVENTS[1] for call in plan["resume"])
    assert all(call["arguments"]["lineage_id"] == migration.TARGET_LINEAGE for call in plan["resume"])
    assert len(plan["wires"]) == 4
    assert all(call["arguments"]["changes"]["config"]["queue_limit"] == 10 for call in plan["wires"])
    assert migration.SOURCE_LINEAGE not in json.dumps(plan["workspace_files"])
    assert migration.SOURCE_LINEAGE not in json.dumps(plan["wires"])
    assert plan["function_block"][0]["arguments"]["title"] == migration.BLOCK_TITLE


def test_plan_refuses_to_detach_an_unrelated_workflow(tmp_path, monkeypatch):
    """Pulse Resident 连有外部 wire 时，迁移计划拒绝拆开无关工作流"""
    source, target = _definitions(tmp_path, monkeypatch)
    source["program"]["wires"].append({"id": "wire_external", "source": migration.RESIDENT,
                                        "target": "resident_000000000001"})
    with pytest.raises(ValueError, match="external wire"):
        migration.build_plan(source, target)
