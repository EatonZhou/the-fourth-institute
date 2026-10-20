"""Structure templates that find existing elements and change them while adding new ones.

:module: lineage.hatg
"""

import pytest

from tests.cases.isa_test_support import ACTOR, ok
from tests.cases.test_isa_lineage_lifecycle import (
    future_creation_store as future_creation_store,  # noqa: PLC0414 - pytest fixture
)
from tests.cases.test_isa_lineage_lifecycle import graph, resident
from tests.cases.test_isa_lineage_lifecycle import (
    lineage as lineage,  # noqa: PLC0414 - pytest fixture
)


def event_source(lineage_id, title, **config):
    return ok("CreateComponent", {
        "lineage_id": lineage_id, "kind": "event_source", "title": title,
        "config": {"event_type": "manual.triggered", **config},
    }, key=f"c-{title}")["component_id"]


def wire(lineage_id, kind, source, target, name, **extra):
    return ok("CreateWire", {
        "lineage_id": lineage_id, "kind": kind, "source": source, "target": target, **extra,
    }, key=f"w-{name}")["wire_id"]


def save(body, title="Rewrite", targets=("resident", "selection")):
    from management_portal.society_service import templates

    return templates.create(
        {"kind": "structure", "title": title, "targets": list(targets), "body": body}, ACTOR,
    )["template_id"]


def apply(lineage_id, template_id, target_id, **options):
    from management_portal.society_service import templates

    return templates.apply(
        lineage_id, template_id, {"kind": "resident", "resident_ids": [target_id]}, ACTOR, **options,
    )


def program(lineage_id):
    current = graph(lineage_id)["program"]
    return {item["id"]: item for item in current["components"]}, {item["id"]: item for item in current["wires"]}


TARGET = {"key": "target", "from": "target"}
INPUTS = [
    {"key": "src", "kind": "event_source"},
    {'key': 'in', 'kind': 'route', 'source': 'src', 'target': 'target', 'count': 'all'},
]


def test_new_roles_join_every_existing_input_with_its_wire_settings(lineage):
    """新建角色接入每个已有输入并沿用原连线配置，Function Block 只含新建元素"""
    target = resident(lineage, "Target")
    sources = [event_source(lineage, name) for name in ("Daily", "Manual")]
    for index, source in enumerate(sources):
        wire(lineage, "route", source, target, f"in-{index}", config={"include_envelope": False})
    template = save({
        "roles": [TARGET, {"key": "first", "from": "new", "title": "First"}, {"key": "second", "from": "new", "title": "Second"}],
        "match": INPUTS,
        "wires": [
            {'key': 'to_first', 'kind': 'route', 'source': 'src', 'target': 'first', 'config_from': 'in'},
            {'key': 'to_second', 'kind': 'route', 'source': 'src', 'target': 'second', 'config_from': 'in'},
        ],
    })

    applied = apply(lineage, template, target)

    first, second = applied["created"]["resident_ids"]
    _, wires = program(lineage)
    created = [wires[identity] for identity in applied["created"]["wire_ids"]]
    assert sorted((item["source"], item["target"]) for item in created) == sorted(
        (source, role) for source in sources for role in (first, second)
    )
    assert all(item["config"]["include_envelope"] is False for item in created)
    assert applied["deleted_ids"] == [] and applied["updated_ids"] == []
    block = next(item for item in graph(lineage)["function_blocks"] if item["id"] == applied["function_block_ids"][0])
    # Found elements stay outside the block; only what the template made joins it.
    assert {item["id"] for item in block["elements"]} == {first, second, *applied["created"]["wire_ids"]}


def test_budget_watchers_are_deleted_and_the_watched_limit_is_cleared(lineage):
    """结构模板删除预算耗尽的监听事件源及其连线，并清除被监听事件源的次数上限"""
    target = resident(lineage, "Target")
    source = event_source(lineage, "Rounds", max_fires=3)
    feed = wire(lineage, "route", source, target, "feed")
    watcher = ok("CreateComponent", {
        "lineage_id": lineage, "kind": "event_source", "title": "Exhausted",
        "config": {"event_type": "budget.exhausted"},
    }, key="c-exhausted")["component_id"]
    watch = wire(lineage, "watch", watcher, source, "watch")
    notice = wire(lineage, "route", watcher, target, "notice")
    template = save({
        "roles": [TARGET],
        "match": [
            {"key": "src", "kind": "event_source", "op": "update", "update": {"config": {"max_fires": None}}},
            {'key': 'feed', 'kind': 'route', 'source': 'src', 'target': 'target', 'count': 'all'},
            {"key": "watch", "kind": "watch", "source": "done", "target": "src", "count": "all"},
            {"key": "done", "kind": "event_source", "op": "delete", "where": {"event_type": "budget.exhausted"}},
        ],
    })

    from management_portal.society_service import templates

    behavior = templates.get(template)["behavior"]
    assert behavior["persistent"] is False
    assert "删除已有的 event_source：done。" in behavior["steps"]

    applied = apply(lineage, template, target)

    components, wires = program(lineage)
    assert watcher not in components and {watch, notice}.isdisjoint(wires)
    assert components[source]["config"].get("max_fires") is None
    assert feed in wires
    assert sorted(applied["deleted_ids"]) == sorted([watcher, watch, notice])
    assert applied["updated_ids"] == [source]
    assert applied["function_block_ids"] == []


def test_function_block_elements_are_never_found(lineage):
    """结构模板查找时跳过 Function Block 内的元素，找不到时不做修改"""
    from management_portal.society_service import templates
    from management_portal.society_service.templates.registry import TemplateError

    target = resident(lineage, "Target")
    loop = next(item for item in templates.list_templates() if item["builtin_key"] == "agent_loop")
    templates.apply(lineage, loop["template_id"], {"kind": "resident", "resident_ids": [target]}, ACTOR)
    template = save({"roles": [TARGET], "match": [{**INPUTS[0], "op": "delete"}, INPUTS[1]]})
    before = program(lineage)

    with pytest.raises(TemplateError, match="没有找到"):
        apply(lineage, template, target)
    assert program(lineage) == before


def test_one_wire_with_several_candidates_is_chosen_before_applying(lineage):
    """连线有多个候选时预览列出候选，未选择时应用报错，选定后只删除所选连线"""
    from management_portal.society_service.templates.registry import TemplateError

    target = resident(lineage, "Target")
    sources = [event_source(lineage, name) for name in ("Left", "Right")]
    for index, source in enumerate(sources):
        wire(lineage, "route", source, target, f"pick-{index}")
    template = save({"roles": [TARGET], "match": [
        {"key": "src", "kind": "event_source", "op": "delete"},
        {'key': 'in', 'kind': 'route', 'source': 'src', 'target': 'target'},
    ]})

    preview = apply(lineage, template, target, dry_run=True)["preview"][0]
    assert preview["ambiguous"][0]["key"] == "in"
    # Unlabeled candidates are named by their ends so the user can tell them apart.
    assert sorted(item["title"] for item in preview["ambiguous"][0]["candidates"]) == ["Left → Target", "Right → Target"]
    assert program(lineage)[0].keys() >= set(sources)
    with pytest.raises(TemplateError, match="2 个候选"):
        apply(lineage, template, target)

    chosen = preview["ambiguous"][0]["candidates"][1]["id"]
    applied = apply(lineage, template, target, match_choices={"in": [chosen]})

    components, wires = program(lineage)
    gone = {item["id"] for item in preview["found"] if item["key"] == "src"} - set(components)
    assert len(gone) == 1 and chosen not in wires
    assert sorted(applied["deleted_ids"]) == sorted([*gone, chosen])


def test_preview_lists_found_removed_and_created_elements_without_changes(lineage):
    """ApplyTemplate 预览列出找到、将删除和将新建的元素，不修改图"""
    target = resident(lineage, "Target")
    source = event_source(lineage, "Only")
    feed = wire(lineage, "route", source, target, "only")
    template = save({
        "roles": [TARGET, {"key": "helper", "from": "new"}],
        "match": [{"key": "src", "kind": "event_source"}, {'key': 'in', 'kind': 'route', 'source': 'src', 'target': 'target', 'op': 'delete'}],
        "wires": [{'key': 'to_helper', 'kind': 'route', 'source': 'src', 'target': 'helper'}],
    })
    before = graph(lineage)

    preview = ok("ApplyTemplate", {
        "lineage_id": lineage, "template_id": template,
        "target": {"kind": "resident", "resident_ids": [target]}, "dry_run": True,
    })["preview"][0]

    assert {item["id"]: item["op"] for item in preview["found"]} == {source: "keep", feed: "delete"}
    assert [item["id"] for item in preview["removed"]] == [feed]
    assert preview["created"] == {"roles": 1, "components": 0, "wires": 1}
    assert graph(lineage)["program"] == before["program"]
    assert len(graph(lineage)["nodes"]) == len(before["nodes"])


def test_graph_change_after_finding_abandons_the_whole_application(lineage, monkeypatch):
    """查找之后图发生变化时，整个模板应用放弃且图保持原样"""
    from management_portal.society_service.templates import structure
    from management_portal.society_service.templates.registry import TemplateError

    target = resident(lineage, "Target")
    source = event_source(lineage, "Moving")
    wire(lineage, "route", source, target, "moving")
    template = save({
        "roles": [TARGET, {"key": "helper", "from": "new"}],
        "match": [{"key": "src", "kind": "event_source", "op": "delete"}, INPUTS[1]],
    })
    real = structure._find
    calls = []

    def changed(ctx, found, anchors):
        calls.append(1)
        return real(ctx, found, anchors) if len(calls) == 1 else []

    monkeypatch.setattr(structure, "_find", changed)
    before = graph(lineage)
    with pytest.raises(TemplateError, match="发生了变化"):
        apply(lineage, template, target)
    assert graph(lineage)["program"] == before["program"]
    assert {item["id"] for item in graph(lineage)["nodes"]} == {item["id"] for item in before["nodes"]}


def test_selection_becomes_existing_structure_found_on_another_resident(lineage):
    """从选区创建的结构模板可在另一个 Resident 上找到同样结构，选区不能含 Function Block"""
    from management_portal.society_service import templates
    from management_portal.society_service.templates.registry import TemplateError

    first = resident(lineage, "First")
    source = event_source(lineage, "First timer")
    first_wire = wire(lineage, "route", source, first, "first")
    template = ok("CreateTemplate", {"from_selection": {
        "lineage_id": lineage, "element_ids": [first, source], "rewrite": True,
    }})["template"]
    body = template["body"]
    assert body["roles"] == [{"key": "target", "from": "target", "description": "First"}]
    assert [(item["key"], item["kind"]) for item in body["match"]] == [("event_1", "event_source"), ("wire_1", "route")]
    assert body["match"][0]["where"] == {"event_type": "manual.triggered"}
    assert (body["match"][1]["source"], body["match"][1]["target"]) == ("event_1", "target")
    assert set(body["layout"]) == {"target", "event_1"}

    second = resident(lineage, "Second")
    other = event_source(lineage, "Second timer")
    other_wire = wire(lineage, "route", other, second, "second")
    body["match"][1]["op"] = "delete"
    templates.update(template["template_id"], {"body": body}, ACTOR)
    applied = apply(lineage, template["template_id"], second)
    assert applied["deleted_ids"] == [other_wire]
    assert first_wire in program(lineage)[1]

    block = templates.apply(
        lineage,
        next(item for item in templates.list_templates() if item["builtin_key"] == "agent_loop")["template_id"],
        {"kind": "resident", "resident_ids": [first]}, ACTOR,
    )["function_block_ids"][0]
    with pytest.raises(TemplateError, match="Function Block"):
        templates.create(None, ACTOR, from_selection={"lineage_id": lineage, "element_ids": [first, block], "rewrite": True})


@pytest.mark.parametrize("body,message", [
    ({"roles": [TARGET], "match": [{"key": "loose", "kind": "loop"}]}, "需要通过已有连线"),
    ({"roles": [{"key": "worker", "from": "new"}], "match": [INPUTS[0]]}, "查找起点"),
    ({"roles": [TARGET], "match": [{"key": "who", "kind": "resident", "op": "delete"}]}, "只能保留"),
    ({"roles": [TARGET], "match": [{**INPUTS[0], "op": "delete"}, INPUTS[1]],
      "wires": [{'key': 'again', 'kind': 'route', 'source': 'src', 'target': 'target'}]}, "将删除"),
    ({"roles": [TARGET], "match": INPUTS,
      "components": [{"key": "copy", "kind": "loop", "config_from": "src"}]}, "同类型"),
    ({"roles": [TARGET, {"key": "worker", "from": "new", "title": "{{match.src.title}}"}], "match": INPUTS}, "角色不能引用"),
    ({"roles": [TARGET], "match": [{**INPUTS[0], "op": "update"}, INPUTS[1]]}, "update.config"),
])
def test_existing_elements_are_validated_when_saved(lineage, body, message):
    """保存结构模板时校验查找条件，孤立元素、删除 Resident、重复连线等写法被拒绝"""
    from management_portal.society_service import templates
    from management_portal.society_service.templates.registry import TemplateError

    with pytest.raises(TemplateError, match=message):
        templates.create({"kind": "structure", "title": "Bad", "body": body}, ACTOR, dry_run=True)
