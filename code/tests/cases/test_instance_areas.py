"""Areas: the data directory page, moving an area, and how registered directories are stored.

:design: decision_d88306d0bd17
"""
from __future__ import annotations

import json

import pytest

from management_common import host_instances, instance_areas, instance_locations
from management_common.instance_manifest import AREAS, FIXED_DIRECTORIES, load
from tests.cases.instance_test_support import select_instance, write_instance


def _other_instance(root):
    """A second registered instance on the same host, with its own name, ports and prefix."""
    root = write_instance(root)
    document = json.loads((root / "instance.json").read_text(encoding="utf-8"))
    document.update(name="other", instance_id="fixture-other")
    document["deployments"]["release"]["web_port"] = 38086
    document["network"].update(entry_port=38086, postgres_port=35432, nats_port=34222)
    document["services"]["prefix"] = "tfi-other"
    (root / "instance.json").write_text(json.dumps(document), encoding="utf-8")
    return root


def test_data_directory_report_lists_root_areas_and_fixed_directories(monkeypatch, tmp_path):
    """数据目录列出实例根目录、各区域与固定目录的位置、占用和剩余空间

    写了位置的区域标为已配置；固定目录只显示。
    """
    projects = tmp_path / "big-disk" / "projects"
    (projects / "示例").mkdir(parents=True)
    (projects / "示例" / "a.txt").write_bytes(b"12345")
    instance = select_instance(monkeypatch, tmp_path / "instance", areas={"projects": projects})
    report = instance_areas.area_report(instance)
    assert report["root"]["path"] == str(instance.root) and report["root"]["free"] > 0
    assert [item["name"] for item in report["areas"]] == list(AREAS)
    assert [item["name"] for item in report["fixed"]] == list(FIXED_DIRECTORIES)
    by_name = {item["name"]: item for item in report["areas"]}
    assert by_name["projects"] == {**by_name["projects"], "path": str(projects), "configured": True, "size": 5}
    assert by_name["skills"]["configured"] is False
    assert by_name["skills"]["path"] == by_name["skills"]["default"] == str(instance.root / "data" / "skills")
    assert instance_areas.area_report(instance, measure=False)["areas"][0]["size"] is None


def test_moving_an_area_changes_only_its_manifest_entry(monkeypatch, tmp_path):
    """移动区域只改清单中这一项，文件整体搬到新目录

    移回默认位置时删去这一项；其他区域与清单内容不变。
    """
    instance = select_instance(monkeypatch, tmp_path / "instance")
    uploads = instance.area("uploads")
    (uploads / "resident_chat_images").mkdir(parents=True)
    (uploads / "resident_chat_images" / "a.png").write_bytes(b"png")
    target = tmp_path / "bigger-disk" / "uploads"
    result = instance_areas.move_area(instance.root, "uploads", target)
    assert result == {"area": "uploads", "source": str(uploads), "target": str(target), "old_copy_retained": False}
    moved = load(instance.root)
    assert moved.document["areas"] == {"uploads": str(target)}
    assert (target / "resident_chat_images" / "a.png").read_bytes() == b"png" and not uploads.exists()
    assert {key: value for key, value in moved.document.items() if key != "areas"} == {
        key: value for key, value in instance.document.items() if key != "areas"}
    instance_areas.move_area(instance.root, "uploads", uploads)
    assert "areas" not in load(instance.root).document
    assert (uploads / "resident_chat_images" / "a.png").exists()


def test_move_targets_are_checked_before_any_file_moves(monkeypatch, tmp_path):
    """新位置须是绝对路径、空目录，不与其他区域、固定目录或本机其他实例重叠"""
    other = _other_instance(tmp_path / "other")
    other_manifest = load(other)
    host_instances.register(other_manifest)
    instance = select_instance(monkeypatch, tmp_path / "instance")
    host_instances.register(load(instance.root))
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "file").write_text("x")
    for target in ("relative/path", str(occupied), str(instance.area("skills") / "inside"),
                   str(instance.root / "runtime" / "x"), str(other_manifest.area("papers"))):
        with pytest.raises(ValueError):
            instance_areas.validate_target(instance, "papers", target)
    with pytest.raises(ValueError, match="Unknown data area"):
        instance_areas.validate_target(instance, "secrets", str(tmp_path / "s"))
    assert json.loads((instance.root / "instance.json").read_text()) == instance.document


def test_registered_directories_store_area_references_and_reject_other_instances(monkeypatch, tmp_path):
    """登记的目录在区域内保存为“区域:相对路径”，区域外保存规范绝对路径，拒绝本机其他实例的目录

    读取时按当前清单解析，区域搬走后同一记录指向新位置。
    """
    projects = tmp_path / "projects"
    instance = select_instance(monkeypatch, tmp_path / "instance", areas={"projects": projects})
    host_instances.register(load(instance.root))
    other = _other_instance(tmp_path / "other")
    host_instances.register(load(other))
    folder = projects / "AI员工" / "agents" / "lineage_123456abcdef"
    stored = instance_locations.stored_location(folder)
    assert stored == "projects:AI员工/agents/lineage_123456abcdef"
    assert instance_locations.stored_location(tmp_path / "host" / "repo") == str(tmp_path / "host" / "repo")
    for foreign in (other / "data" / "projects" / "x", other / "secrets"):
        with pytest.raises(ValueError):
            instance_locations.stored_location(foreign)
    with pytest.raises(ValueError):
        instance_locations.stored_location(instance.root / "runtime")
    assert instance_locations.located_path(stored) == folder.as_posix()
    moved = tmp_path / "moved-projects"
    write_instance(instance.root, areas={"projects": moved})
    assert instance_locations.located_path(stored) == (moved / "AI员工" / "agents" / "lineage_123456abcdef").as_posix()
    assert instance_locations.located_path("/srv/elsewhere") == "/srv/elsewhere"


def test_workspace_rows_keep_area_references(monkeypatch, tmp_path):
    """Workspace 登记在数据库中保存区域写法，读出时是当前位置的绝对路径"""
    from management_portal.resources.workspace_store import get_workspace_store
    projects = tmp_path / "projects"
    instance = select_instance(monkeypatch, tmp_path / "instance", areas={"projects": projects})
    store = get_workspace_store()
    folder = projects / "Demo" / "agents" / "lineage_123456abcdef" / "residents" / "resident_123456abcdef" / "workspace"
    store.ensure("workspace_0123456789ab", str(folder))
    with store.database.read() as connection:
        raw = connection.execute(f"SELECT path FROM {store.table} WHERE workspace_id=%s",
                                 ("workspace_0123456789ab",)).fetchone()["path"]
    assert raw == "projects:Demo/agents/lineage_123456abcdef/residents/resident_123456abcdef/workspace"
    assert store.paths(["workspace_0123456789ab"]) == {"workspace_0123456789ab": folder.as_posix()}
    assert store.for_path(str(folder), new_id=lambda: "workspace_ffffffffffff") == "workspace_0123456789ab"
    write_instance(instance.root, areas={"projects": tmp_path / "moved"})
    assert store.paths(["workspace_0123456789ab"])["workspace_0123456789ab"].startswith((tmp_path / "moved").as_posix())
