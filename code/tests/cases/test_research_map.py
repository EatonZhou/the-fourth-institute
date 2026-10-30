"""A Project's research map: its directions, their history and what they produced.

:module: spec.design-governance
:design: decision_46c37026e8ea
"""
from __future__ import annotations

from itertools import count
from types import SimpleNamespace

import pytest

from management_portal import research_map_store as store
from management_portal import research_map_web_isa as web
from management_portal.hatg.contracts import InstructionError

NOW = "2026-10-29T08:00:00Z"


def ids():
    numbers = count(1)
    return lambda: f"direction_{next(numbers):012x}"


def apply(document, *changes, resident=False, new_id=None):
    return store.apply_changes(document, list(changes), actor="resident_aaaaaaaaaaaa" if resident else "alice",
                               now=NOW, new_id=new_id or ids(), resident=resident)


def test_changes_build_a_tree_with_keys_order_and_default_stances():
    """一次提交可以用 key 指代刚增加的方向；同级按位置排列；Resident 增加的方向默认是候选，User 增加的默认是探索"""
    new_id = ids()
    result = apply(store.empty_document("Faster arms"),
                   {"op": "add_direction", "key": "topic", "title": "Speed up"},
                   {"op": "add_direction", "title": "Trajectories", "parent_id": "$topic"},
                   {"op": "add_direction", "title": "Control", "parent_id": "$topic", "index": 0, "stance": "focus"},
                   new_id=new_id)
    document = result["document"]
    assert result["created"] == {"topic": "direction_000000000001"}
    assert [(item["title"], item["parent_id"], item["stance"]) for item in document["directions"]] == [
        ("Speed up", "", "explore"), ("Control", "direction_000000000001", "focus"),
        ("Trajectories", "direction_000000000001", "explore")]
    proposed = apply(document, {"op": "add_direction", "title": "RL", "proposal_reason": "faster approach"},
                     resident=True, new_id=new_id)
    rl = next(item for item in proposed["document"]["directions"] if item["title"] == "RL")
    assert (rl["stance"], rl["proposed_by"], rl["proposal_reason"]) == ("candidate", "resident_aaaaaaaaaaaa", "faster approach")


def test_moves_removals_relations_and_notes_keep_the_map_consistent():
    """移动不能形成环；删除方向时子方向上移、相关关系一并去掉；关系与时间线只能指向图上的方向"""
    new_id = ids()
    document = apply(store.empty_document(),
                     {"op": "add_direction", "key": "a", "title": "A"},
                     {"op": "add_direction", "key": "b", "title": "B", "parent_id": "$a"},
                     {"op": "add_direction", "key": "c", "title": "C", "parent_id": "$b"},
                     {"op": "add_relation", "from": "$c", "to": "$a", "kind": "builds_on"},
                     new_id=new_id)["document"]
    a, b, c = (item["direction_id"] for item in document["directions"])
    with pytest.raises(ValueError, match="子方向"):
        apply(document, {"op": "move_direction", "direction_id": a, "parent_id": c})
    moved = apply(document, {"op": "move_direction", "direction_id": c, "parent_id": "", "index": 0})["document"]
    assert [item["direction_id"] for item in moved["directions"] if not item["parent_id"]] == [c, a]
    removed = apply(document, {"op": "remove_direction", "direction_id": b})
    assert removed["removed"] == [b]
    assert {item["direction_id"]: item["parent_id"] for item in removed["document"]["directions"]} == {a: "", c: a}
    gone = apply(document, {"op": "remove_direction", "direction_id": a})["document"]
    assert gone["relations"] == []
    noted = apply(document, {"op": "add_note", "direction_id": b, "kind": "progress", "text": "3/3 done",
                             "refs": ["decision_0123456789ab"]},
                  {"op": "update_direction", "direction_id": b, "fields": {"stance": "concluded", "conclusion": "It works"}})
    assert noted["notes"][0]["refs"] == ["decision_0123456789ab"]
    concluded = noted["document"]["directions"][1]
    assert concluded["stance"] == "concluded" and concluded["conclusion"] == {"text": "It works", "by": "alice", "at": NOW}
    for change, message in [
        ({"op": "add_relation", "from": a, "to": a, "kind": "builds_on"}, "同一个方向"),
        ({"op": "add_relation", "from": a, "to": b, "kind": "likes"}, "kind"),
        ({"op": "add_note", "direction_id": "direction_ffffffffffff", "text": "x"}, "不是这张地图上的方向"),
        ({"op": "update_direction", "direction_id": a, "fields": {"stance": "maybe"}}, "stance"),
        ({"op": "update_direction", "direction_id": a, "fields": {"teams": ["Vision"]}}, "teams"),
        ({"op": "add_direction", "title": " "}, "title 不能为空"),
        ({"op": "rename"}, "未知的改动"),
    ]:
        with pytest.raises(ValueError, match=message):
            apply(document, change)
    with pytest.raises(ValueError, match="不能为空"):
        apply(document)


def test_the_store_keeps_revisions_history_layout_and_project_identity():
    """地图按修订提交：过期的修订被拒绝，历史修订可以读回，布局不进入修订；方向登记在项目之下，随项目改名或删除"""
    project = "MapProject"
    assert store.read(project, goal="Fast arms")["revision"] == 0
    first = store.apply(project, 0, [{"op": "add_direction", "key": "a", "title": "Speed"}], actor="alice", resident=False,
                        goal="Fast arms")
    direction_id = first["created"]["a"]
    with pytest.raises(store.MapConflict) as conflict:
        store.apply(project, 0, [{"op": "set_goal", "goal": "x"}], actor="alice", resident=False)
    assert conflict.value.current == 1
    store.apply(project, 1, [{"op": "update_direction", "direction_id": direction_id, "fields": {"title": "Speed up"}},
                             {"op": "add_note", "direction_id": direction_id, "kind": "progress", "text": "first run"}],
                actor="resident_aaaaaaaaaaaa", resident=True)
    now = store.read(project)
    assert (now["revision"], now["goal"], now["directions"][0]["title"]) == (2, "Fast arms", "Speed up")
    assert [note["text"] for note in now["notes"]] == ["first run"]
    then = store.read(project, revision=1)
    assert (then["directions"][0]["title"], then["notes"]) == ("Speed", [])
    with pytest.raises(LookupError):
        store.read(project, revision=9)
    assert [entry["revision"] for entry in store.history(project)] == [1, 2]
    assert store.history(project, after=1)[0]["directions"][0]["title"] == "Speed up"

    layout = store.update_layout(project, {direction_id: {"x": 10, "y": -4.25}}, actor="alice")
    assert (layout["layout"], layout["layout_revision"]) == ({direction_id: {"x": 10.0, "y": -4.2}}, 1)
    assert store.read(project)["revision"] == 2, "positions are not revisions"
    with pytest.raises(ValueError):
        store.update_layout(project, {"direction_ffffffffffff": {"x": 1, "y": 1}}, actor="alice")

    assert store.direction(direction_id) == {"project_id": project, "created_by": "alice", "removed": False}
    assert {"resource_id": direction_id, "resource_type": "direction", "project_id": project,
            "created_by": "alice"} in store.permission_records()
    store.record_automation(project, "lineage_000000000001", actor="alice")
    assert store.project_relations(project, "Renamed", write=False) == [direction_id]
    store.project_relations(project, "Renamed")
    assert store.read("Renamed")["revision"] == 2 and store.read(project)["revision"] == 0
    assert store.automation("Renamed")["lineage_id"] == "lineage_000000000001" and store.automation(project) is None
    store.project_relations("Renamed")
    assert store.read("Renamed")["revision"] == 0 and store.direction(direction_id) is None
    assert store.automation("Renamed") is None


class Resources:
    def __init__(self, records):
        self.records = records

    def query(self, *, actor, limit, offset=0, resource_types=(), projects=(), q="", include_attributes=False):
        found = [record for record in self.records
                 if (not resource_types or record["resource_type"] in resource_types)
                 and (not projects or set(projects) & set(record.get("attributes", {}).get("project_ids") or ()))
                 and (not q or q == record["resource_id"])]
        return {"resources": found[offset:offset + limit], "total": len(found)}


def invocation(caller="alice", **arguments):
    return SimpleNamespace(caller=caller, argument=arguments.get)


def test_handlers_read_outputs_check_teams_and_tell_open_pages(monkeypatch):
    """GetResearchMap 带出引用方向的产出（历史修订与 include_outputs=false 不带）；ApplyResearchMapChanges 只接受项目中的 Lineage 作团队，并通知打开的页面"""
    from management_portal import resources, society_service, notifications

    project = "HandlerProject"
    created = store.apply(project, 0, [{"op": "add_direction", "key": "a", "title": "Speed"}], actor="alice", resident=False)
    direction_id = created["created"]["a"]
    records = [
        {"resource_id": project, "resource_type": "project", "summary": "Fast arms", "attributes": {}},
        {"resource_id": "decision_0123456789ab", "resource_type": "decision", "title": "Use RTC", "url": "/r/d",
         "source": {"modified_at": "2026-10-28T00:00:00Z"},
         "attributes": {"project_ids": [project], "relations": [{"kind": "related", "target_id": direction_id}]}},
        {"resource_id": "decision_ba9876543210", "resource_type": "decision", "title": "Unrelated", "url": "/r/u",
         "source": {"modified_at": "2026-10-29T00:00:00Z"},
         "attributes": {"project_ids": [project], "relations": [{"kind": "related", "target_id": "decision_000000000000"}]}},
    ]
    monkeypatch.setattr(resources, "get_resource_service", lambda: Resources(records))
    published = []
    monkeypatch.setattr(notifications, "publish_projection_change",
                        lambda projection, target, **kwargs: published.append((projection, target, kwargs.get("fields"))))
    monkeypatch.setattr(society_service, "get_society_service", lambda: SimpleNamespace(project=SimpleNamespace(
        project_teams=lambda ids, actor: {ids[0]: {"project_lineage_id": "lineage_000000000001",
                                                   "lineages": [{"lineage_id": "lineage_000000000002"}]}})))

    result = web.get_research_map(invocation(project_id=project))
    assert result["outputs"] == {direction_id: [{"resource_id": "decision_0123456789ab", "resource_type": "decision",
                                                 "title": "Use RTC", "url": "/r/d", "modified_at": "2026-10-28T00:00:00Z"}]}
    assert web.get_research_map(invocation(project_id=project, revision=1))["outputs"] == {}
    assert web.get_research_map(invocation(project_id=project, include_outputs=False))["outputs"] == {}

    team = {"op": "update_direction", "direction_id": direction_id, "fields": {"teams": ["lineage_000000000002"]}}
    assert web.apply_research_map_changes(invocation(project_id=project, expected_revision=1, changes=[team]))["revision"] == 2
    assert published == [("research-map", project, ["map"])]
    stranger = {**team, "fields": {"teams": ["lineage_00000000000f"]}}
    with pytest.raises(InstructionError) as refused:
        web.apply_research_map_changes(invocation(project_id=project, expected_revision=2, changes=[stranger]))
    assert refused.value.code == "instruction.invalid_arguments"
    with pytest.raises(InstructionError) as stale:
        web.apply_research_map_changes(invocation(project_id=project, expected_revision=1, changes=[team]))
    assert stale.value.code == "instruction.version_conflict"
    assert stale.value.details["current_revision"] == 2

    first = web.list_research_map_changes(invocation(project_id=project, limit=1))
    assert [entry["revision"] for entry in first["changes"]] == [1] and first["next_cursor"] == "1"
    rest = web.list_research_map_changes(invocation(project_id=project, limit=1, cursor=first["next_cursor"]))
    assert [entry["revision"] for entry in rest["changes"]] == [2] and "next_cursor" not in rest
    with pytest.raises(InstructionError) as missing:
        web.get_research_map(invocation(project_id="NoSuchProject"))
    assert missing.value.code == "instruction.not_found"


def test_the_digest_lists_what_others_changed_and_produced(monkeypatch):
    """GetResearchMapDigest 只列出别人提交的修订与新产出，按方向汇总并写成整理请求；没有动静时 count 为 0、不写请求"""
    from management_portal import resources

    project, steward = "DigestProject", "resident_00000000000a"
    first = store.apply(project, 0, [{"op": "add_direction", "key": "a", "title": "Speed", "stance": "focus"},
                                     {"op": "add_direction", "key": "b", "title": "Quiet"}], actor="alice", resident=False)
    speed, quiet = first["created"]["a"], first["created"]["b"]
    store.apply(project, 1, [{"op": "add_note", "direction_id": quiet, "kind": "note", "text": "tidied"}], actor=steward,
                resident=True)
    store.apply(project, 2, [{"op": "update_direction", "direction_id": speed, "fields": {"stance": "watch"}},
                             {"op": "add_note", "direction_id": speed, "kind": "decision", "text": "pause until the gripper",
                              "refs": ["todo_0123456789ab"]}], actor="alice", resident=False)
    records = [
        {"resource_id": project, "resource_type": "project", "summary": "Fast arms", "attributes": {}},
        {"resource_id": "experiment_0123456789ab", "resource_type": "experiment", "title": "Grip test", "url": "/r/e",
         "source": {"modified_at": "2026-10-28T00:00:00Z"},
         "attributes": {"project_ids": [project], "relations": [{"kind": "related", "target_id": quiet}]}},
    ]
    monkeypatch.setattr(resources, "get_resource_service", lambda: Resources(records))

    digest = web.get_research_map_digest(invocation(caller=steward, project_id=project, since="2026-10-01T00:00:00Z"))
    assert (digest["count"], digest["revision"], digest["since"]) == (2, 3, "2026-10-01T00:00:00Z")
    by_id = {entry["direction_id"]: entry for entry in digest["directions"]}
    assert [change["summary"] for change in by_id[speed]["changes"]] == [
        "新增方向（主攻）", "姿态改为观望", "决定：pause until the gripper（引用 todo_0123456789ab）"]
    assert by_id[quiet]["changes"] == [{"revision": 1, "changed_at": by_id[quiet]["changes"][0]["changed_at"],
                                        "changed_by": "alice", "summary": "新增方向（探索）"}], "the caller's own note is not news"
    assert [output["resource_id"] for output in by_id[quiet]["outputs"]] == ["experiment_0123456789ab"]
    assert "《Speed》" in digest["prompt"] and "write-research-map/daily-tidy.md" in digest["prompt"]
    assert "新产出：experiment《Grip test》experiment_0123456789ab" in digest["prompt"]

    later = web.get_research_map_digest(invocation(caller=steward, project_id=project, since=digest["until"]))
    assert (later["count"], later["directions"], later["prompt"]) == (0, [], "")
    with pytest.raises(InstructionError) as wrong:
        web.get_research_map_digest(invocation(project_id=project, since="yesterday"))
    assert wrong.value.code == "instruction.invalid_arguments"


def test_the_first_map_change_draws_the_daily_digest_into_the_project_lineage(monkeypatch):
    """第一次修改地图时在 Project 层 Lineage 画出每日整理（定时 Event → Effect → Project Resident），只画一次，失败不影响地图修改"""
    from management_portal import isa_client, research_map_automation as automation, society_service
    from management_portal.society_service.lineage_program_diagnostics import effect_script_analysis

    calls, components = [], []

    def call(actor, instruction, arguments=None, **options):
        calls.append((actor, instruction, arguments, options))
        if instruction == "GetLineage":
            return {"lineage": {"revision": 7, "program": {"components": list(components)}}}
        return {"created_ids": ["x"]}

    monkeypatch.setattr(isa_client, "call", call)
    monkeypatch.setattr(society_service, "get_society_service", lambda: SimpleNamespace(project=SimpleNamespace(
        project_teams=lambda ids, actor: {ids[0]: {"project_lineage_id": "lineage_000000000001",
                                                   "project_resident_id": "resident_00000000000a", "lineages": []}})))
    assert automation.ensure("AutoProject", "alice") == "installed"
    applied = [entry for entry in calls if entry[1] == "ApplyLineageChanges"]
    assert len(applied) == 1 and applied[0][3]["version"] == 7
    operations = applied[0][2]["operations"]
    event, effect, invoke, target = (operation["value"] for operation in operations)
    assert event["config"]["event_type"] == "time.reached" and event["config"]["calendar_frequency"] == "daily"
    assert invoke["config"]["invoke_profiles"]["effect"]["arguments"] == {"lineage_id": {"value": "lineage_000000000001"}}
    assert (target["source"], target["target"]) == ("$ref:digest_effect", "resident_00000000000a")
    analysis = effect_script_analysis({"script": effect["config"]["script"], "endpoint_names": ["target"],
                                       "endpoints": {"target": "resident_00000000000a"}})
    assert analysis.diagnostics == []

    calls.clear()
    assert automation.ensure("AutoProject", "alice") == "installed" and calls == [], "drawn once"

    components.append({"client_key": automation.key("event")})
    assert automation.ensure("DrawnByHand", "alice") == "installed"
    assert [entry[1] for entry in calls] == ["GetLineage"] and store.automation("DrawnByHand")

    def refuse(actor, instruction, arguments=None, **options):
        raise InstructionError("instruction.forbidden", "no")

    monkeypatch.setattr(isa_client, "call", refuse)
    assert automation.ensure("Refused", "bob") == "failed" and store.automation("Refused") is None


def test_knowledge_entries_record_what_they_name_as_related(tmp_path):
    """知识条目在资源索引中记下 links.related 指向的对象，研究方向据此找到引用它的产出"""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "knowledge_board_lan"))
    import knowledge_source
    from management_portal import resource_index_knowledge

    entry = tmp_path / "knowledge_base" / "decision" / "20261029__decision__Use_RTC__decision_0123456789ab.md"
    entry.parent.mkdir(parents=True)
    entry.write_text("---\nkb_id: decision_0123456789ab\ntype: decision\nproject: Demo\ncreated_at: '2026-10-29'\n"
                     "links:\n  related:\n  - direction_00000000000a\n  - direction_00000000000a\n  - decision_ba9876543210\n"
                     "---\n# Use RTC\n\nBody\n", encoding="utf-8")
    item = knowledge_source._read_kb_file(str(entry), "Demo", {"decision"}, {}, entry_root=entry.parent, entry_files=[entry])
    assert item["related"] == ["direction_00000000000a", "decision_ba9876543210"]
    adapter = resource_index_knowledge.ProjectKnowledgeAdapter()
    record = adapter._record_for_item(item, statuses={}, order=0)
    assert record["attributes"]["relations"] == [{"kind": "related", "target_id": "direction_00000000000a"},
                                                  {"kind": "related", "target_id": "decision_ba9876543210"}]


def test_a_direction_is_a_project_resource_that_opens_on_its_map():
    """研究方向放在所属项目之下，链接打开项目页上它所在的研究地图"""
    from management_portal.permission_system.scheme.vocabulary import PLACEMENTS
    from management_portal.resources import portal as resource_portal

    assert PLACEMENTS["direction"] == {"project": "belongs"}
    assert resource_portal.portal_url_for_address("direction_0123456789ab") == "/projects?direction=direction_0123456789ab"
