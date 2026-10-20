from pathlib import Path
from urllib.parse import urlparse

import pytest

from management_portal import http_projects, research_maps


MAP_ID = "plan_0123456789ab"
TASK_ID = "todo_0123456789ab"
BODY = f"""**Research Map: Robot motion**: Faster movement.

## Topic
Move safely.

## 背景和边界
Keep success rate.

## Directions

### D1: Policy
状态：doing

说明：Compare policies.

Research Tasks：
- {TASK_ID}: open

### D2: Trajectory
状态：done

说明：Optimize geometry.

Research Tasks：
- None yet
"""


class Service:
    def __init__(self):
        self.calls = []
        self.denied = set()
        self.records = {
            "RobotWorker": {"resource_type": "project"},
            MAP_ID: {"resource_type": "plan"},
            TASK_ID: {"title": "Evaluate policy", "attributes": {"status": "done"}},
        }

    def get(self, address, *, actor):
        self.calls.append((address, actor))
        if address in self.denied:
            raise PermissionError(address)
        return self.records[address]


def write_map(root: Path, body=BODY, kb_id=MAP_ID):
    directory = root / "RobotWorker" / "knowledge_base" / "plan"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "map.md").write_text(f"---\nkb_id: {kb_id}\ntype: plan\n---\n{body}")


def test_research_map_identity_directions_and_resource_tasks(tmp_path):
    """研究地图解析出主题、背景、各方向状态，并按资源记录补全任务标题与状态"""
    write_map(tmp_path)
    service = Service()
    maps = research_maps.list_research_maps("RobotWorker", actor="alice", service=service, projects_base=tmp_path)
    assert maps[0]["id"] == MAP_ID
    assert maps[0]["topic"] == "Move safely."
    assert maps[0]["background"] == "Keep success rate."
    first, second = maps[0]["directions"]
    assert (first["key"], first["status"], second["status"]) == ("D1", "doing", "done")
    assert first["tasks"] == [{"todo_id": TASK_ID, "map_status": "open", "title": "Evaluate policy", "status": "done"}]
    assert second["tasks"] == []
    assert service.calls == [("RobotWorker", "alice"), (MAP_ID, "alice"), (TASK_ID, "alice")]


@pytest.mark.parametrize("denied", [False, True])
def test_missing_or_unreadable_task_uses_map_status(tmp_path, denied):
    """研究任务不存在或无权读取时，按地图中记录的状态和 ID 显示"""
    write_map(tmp_path)
    service = Service()
    if denied:
        service.denied.add(TASK_ID)
    else:
        del service.records[TASK_ID]
    result = research_maps.list_research_maps("RobotWorker", actor="alice", service=service, projects_base=tmp_path)
    assert result[0]["directions"][0]["tasks"][0] == {
        "todo_id": TASK_ID, "map_status": "open", "title": TASK_ID, "status": "open",
    }


def test_research_map_missing_sections_and_non_map():
    """普通计划不解析为研究地图，缺少的章节留空，None yet 不算任务"""
    assert research_maps._parse_research_map_body("# Ordinary plan") is None
    assert research_maps._parse_research_map_body("**Research Map: Topic**: Summary") == {
        "topic_title": "Topic", "summary": "Summary", "topic": "", "background": "", "directions": [],
    }
    assert research_maps._parse_research_tasks_block("- None yet\n- unrelated") == []


def test_map_requires_canonical_kb_id_and_authorized_project(tmp_path):
    """研究地图要求 Project 可读且文件带 kb_id，Project 下没有地图时返回空列表"""
    write_map(tmp_path, kb_id="")
    service = Service()
    service.denied.add("RobotWorker")
    with pytest.raises(PermissionError):
        research_maps.list_research_maps("RobotWorker", actor="bob", service=service, projects_base=tmp_path)
    service.denied.clear()
    with pytest.raises(ValueError, match="kb_id"):
        research_maps.list_research_maps("RobotWorker", actor="alice", service=service, projects_base=tmp_path)
    assert research_maps.list_research_maps("RobotWorker", actor="alice", service=service, projects_base=tmp_path / "empty") == []


def test_private_plan_is_not_exposed(tmp_path):
    """无权读取的研究地图不出现在列表中，也不读取其中的任务"""
    write_map(tmp_path)
    service = Service()
    service.denied.add(MAP_ID)
    assert research_maps.list_research_maps("RobotWorker", actor="alice", service=service, projects_base=tmp_path) == []
    assert (TASK_ID, "alice") not in service.calls


@pytest.mark.parametrize("project", ["", "..", "../RobotWorker", "RobotWorker/other"])
def test_project_path_cannot_escape(project, tmp_path):
    """研究地图的 Project 参数为空或含路径跳转时被拒绝"""
    service = Service()
    with pytest.raises(ValueError):
        research_maps.list_research_maps(project, actor="alice", service=service, projects_base=tmp_path)
    assert service.calls == []


