"""Templates are editable recipes; applying them creates ordinary Lineage elements."""

from copy import deepcopy

import pytest

from tests.cases.isa_test_support import ACTOR, ok
from tests.cases.test_isa_lineage_lifecycle import (
    future_creation_store as future_creation_store,  # noqa: PLC0414 - pytest fixture
)
from tests.cases.test_isa_lineage_lifecycle import graph, resident
from tests.cases.test_isa_lineage_lifecycle import (
    lineage as lineage,  # noqa: PLC0414 - pytest fixture
)


def test_builtin_shelf_and_dry_run(lineage):
    """模板库列出 8 个内置模板，dry_run 创建的模板不保存"""
    from management_portal.society_service import templates

    shelf = templates.list_templates()
    assert {item["builtin_key"] for item in shelf} == {
        "family",
        "agent_loop",
        "fixed_reminder",
        "scheduled_work",
        "leader_pulse",
        "revision_return",
        "expand_ideas",
        "expand_plans",
    }
    for template in shelf:
        assert template["behavior"]["steps"] and template["editor"]["fields"]
    draft = templates.create(
        {"kind": "prompt", "title": "Draft", "body": {"message": "hello"}},
        ACTOR,
        dry_run=True,
    )
    assert draft["behavior"]["persistent"] is False
    assert len(templates.list_templates()) == 8


def test_builtin_refresh_preserves_edits_and_deletions(lineage, monkeypatch):
    """内置模板随代码刷新时保留用户的修改和删除，删除的模板可恢复"""
    from management_portal.society_service import templates
    from management_portal.society_service.templates import builtins, store

    items = templates.list_templates()
    original = deepcopy(builtins.definitions())
    edited, deleted, untouched = items[:3]
    templates.update(edited["template_id"], {"title": "My title"}, ACTOR)
    templates.delete(deleted["template_id"])
    for item in original:
        item["description"] += " code update"
    monkeypatch.setattr(builtins, "definitions", lambda: original)
    store.ensure_builtins()
    assert templates.get(edited["template_id"])["title"] == "My title"
    with pytest.raises(KeyError):
        templates.get(deleted["template_id"])
    assert templates.get(untouched["template_id"])["description"].endswith(
        "code update"
    )
    assert (
        templates.restore(deleted["template_id"], ACTOR)["template_id"]
        == deleted["template_id"]
    )


@pytest.mark.parametrize(
    "key,components,wires,new_residents",
    [
        ("agent_loop", 4, 7, 1),
        ("fixed_reminder", 3, 4, 0),
        ("scheduled_work", 2, 2, 0),
        ("leader_pulse", 2, 2, 0),
    ],
)
def test_structure_builtins_create_owned_elements(
    lineage, key, components, wires, new_residents
):
    """结构类内置模板按定义数量创建组件、连线和 Resident，并全部归入 Function Block"""
    from management_portal.society_service import templates

    target_id = resident(lineage, "Target")
    template = next(
        item for item in templates.list_templates() if item["builtin_key"] == key
    )
    if key == "leader_pulse":
        ok(
            "UpdateLineage",
            {"lineage_id": lineage, "changes": {"leader_resident_id": target_id}},
        )
    result = templates.apply(
        lineage,
        template["template_id"],
        {"kind": "canvas"}
        if key == "leader_pulse"
        else {"kind": "resident", "resident_ids": [target_id]},
        ACTOR,
        parameters={"task": "Do work"} if key == "scheduled_work" else {},
    )
    assert len(result["created"]["resident_ids"]) == new_residents
    assert len(result["created"]["component_ids"]) == components
    assert len(result["created"]["wire_ids"]) == wires
    block = next(
        item
        for item in graph(lineage)["function_blocks"]
        if item["id"] == result["function_block_ids"][0]
    )
    assert block["template_id"] == template["template_id"]
    assert target_id not in {item["id"] for item in block["elements"]}
    assert len(block["elements"]) == components + wires + new_residents


def test_agent_loop_delivers_reviewer_event_with_component_address(lineage, monkeypatch):
    """agent_loop 模板在目标完成后通知审阅者，消息带来源组件地址

    :module: lineage.hatg"""
    from management_portal.society_service import lineage_domain, lineage_primitives, templates
    from tests.cases import society_service_test_support

    target = resident(lineage, "Target")
    template = next(t for t in templates.list_templates() if t["builtin_key"] == "agent_loop")
    applied = templates.apply(
        lineage, template["template_id"],
        {"kind": "resident", "resident_ids": [target]}, ACTOR,
    )
    block = next(b for b in graph(lineage)["function_blocks"] if b["id"] == applied["function_block_ids"][0])
    elements = {element["key"]: element["id"] for element in block["elements"]}
    sent = []
    monkeypatch.setattr(society_service_test_support, "NODE_MESSAGE_SENDER",
        lambda node, prompt, actor: sent.append((node["id"], prompt)) or {"message_id": "msg_loop_review"})
    event = lineage_primitives.make_event("resident.work_finished", subject_resident_id=target,
        payload={"message_id": "msg_target_done"}, correlation="template-loop")
    lineage_domain.dispatch_lineage_program_event(
        lineage, elements["target_done"], event, ACTOR, cursor="template-loop:target-done",
    )
    assert len(sent) == 1
    assert sent[0][0] == elements["reviewer"]
    assert f"来源组件：{elements['rounds']}\n" in sent[0][1]
    assert ".continue" not in sent[0][1]


def test_prompt_is_delivered_without_persistent_structure(lineage, monkeypatch):
    """提示词模板只向目标 Resident 投递消息，不改动图"""
    from management_portal.society_service import templates
    from management_portal.society_service.templates import prompt

    target = resident(lineage, "Target")
    delivered = []
    monkeypatch.setattr(
        prompt,
        "deliver",
        lambda rid, text, actor, policy: (
            delivered.append((rid, text, policy)) or {"message_id": "msg_test"}
        ),
    )
    template = next(
        item
        for item in templates.list_templates()
        if item["builtin_key"] == "revision_return"
    )
    before = graph(lineage)
    result = templates.apply(
        lineage,
        template["template_id"],
        {"kind": "resident", "resident_ids": [target]},
        ACTOR,
        parameters={"prompt": "Revise this"},
    )
    assert delivered == [(target, "Revise this", "queue")]
    assert result["delivered"] == [{"resident_id": target, "message_id": "msg_test"}]
    assert graph(lineage)["program"] == before["program"]
    assert result["function_block_ids"] == []


def test_isa_crud_and_selection_roundtrip(lineage):
    """模板经 ISA 创建、应用、预览更新和删除，删除模板不影响已建的 Function Block"""
    source = resident(lineage, "Source")
    shelf = ok("ListTemplates", {})
    template = next(
        item for item in shelf["templates"] if item["builtin_key"] == "scheduled_work"
    )
    applied = ok(
        "ApplyTemplate",
        {
            "lineage_id": lineage,
            "template_id": template["template_id"],
            "target": {"kind": "resident", "resident_ids": [source]},
            "parameters": {"task": "Check work"},
        },
    )
    copied = ok(
        "CreateTemplate",
        {
            "from_selection": {
                "lineage_id": lineage,
                "element_ids": applied["function_block_ids"],
            },
            "template": {"title": "Saved schedule"},
        },
    )["template"]
    assert copied["kind"] == "structure" and copied["title"] == "Saved schedule"
    assert len(copied["body"]["components"]) == 2 and len(copied["body"]["wires"]) == 2
    assert any(role["from"] == "target" for role in copied["body"]["roles"])
    target = resident(lineage, "Other")
    placed = ok(
        "ApplyTemplate",
        {
            "lineage_id": lineage,
            "template_id": copied["template_id"],
            "target": {"kind": "resident", "resident_ids": [target]},
        },
    )
    assert len(placed["function_block_ids"]) == 1
    updated = ok(
        "UpdateTemplate",
        {"template_id": copied["template_id"], "title": "Preview", "dry_run": True},
    )["template"]
    assert updated["title"] == "Preview"
    assert (
        ok("GetTemplate", {"template_id": copied["template_id"]})["template"]["title"]
        == "Saved schedule"
    )
    ok("DeleteTemplate", {"template_id": copied["template_id"]})
    assert any(
        block["id"] == placed["function_block_ids"][0]
        for block in graph(lineage)["function_blocks"]
    )


@pytest.mark.parametrize(
    "key,seed_type", [("expand_ideas", "idea"), ("expand_plans", "plan")]
)
def test_explore_keeps_seeds_and_syncs_files(lineage, monkeypatch, key, seed_type):
    """探索模板创建 Seed 并同步候选文件，删除探索者后 Seed 和文件仍保留"""
    from management_portal.society_service import lineage_domain, templates
    from management_portal.society_service.templates import explore, prompt

    source = resident(lineage, "Source")

    def fork(lid, payload, actor):
        created = lineage_domain.create_lineage_resident(
            lid, {"title": payload["child_title"], "backend": "codex"}, actor
        )
        return {"created_resident": {"id": created["resident"]["resident_id"]}}

    monkeypatch.setattr(lineage_domain, "fork_lineage_resident", fork)
    delivered = []
    monkeypatch.setattr(
        prompt,
        "deliver",
        lambda rid, text, actor, policy="queue": (
            delivered.append((rid, text)) or {"message_id": "msg_test"}
        ),
    )
    template = next(
        item for item in templates.list_templates() if item["builtin_key"] == key
    )
    applied = templates.apply(
        lineage,
        template["template_id"],
        {"kind": "resident", "resident_ids": [source]},
        ACTOR,
        parameters={"task": "Explore caching", "count": 2},
    )
    assert not applied["function_block_ids"]
    explorer = applied["created"]["resident_ids"][0]
    seeds = [
        item
        for item in graph(lineage)["program"]["components"]
        if item["id"] in applied["created"]["component_ids"]
    ]
    assert len(seeds) == 2 and all(
        item["config"]["seed_type"] == seed_type for item in seeds
    )
    assert len(delivered) == 1 and delivered[0][1].startswith("Explore caching")
    workspace = explore._workspace(lineage, explorer)
    directory = workspace / explore._directory(explorer)
    (directory / "new.md").write_text("# New candidate\n", encoding="utf-8")
    (workspace / seeds[0]["config"]["artifact_path"]).write_text(
        explore.DISMISSED_MARKER + "\n# Dismissed\n", encoding="utf-8"
    )
    assert len(explore.sync_explorer(lineage, explorer, ACTOR)) == 2
    assert explore.sync_explorer(lineage, explorer, ACTOR) == []
    assert (
        explore._shelf(lineage, explorer) / "new.md"
    ).read_text() == "# New candidate\n"
    ok("DeleteResident", {"lineage_id": lineage, "resident_id": explorer})
    assert set(applied["created"]["component_ids"]) <= {
        item["id"] for item in graph(lineage)["program"]["components"]
    }
    assert (explore._shelf(lineage, explorer) / "new.md").is_file()


def test_script_targets_use_applying_actor_and_leave_no_component(lineage, monkeypatch):
    """脚本模板以应用者身份执行一次，参数原样投递且不留下组件"""
    from management_portal.hatg import routing
    from management_portal.hatg.contracts import InstructionResult
    from management_portal.society_service import templates
    from management_portal.society_service.lineage_effect_script import (
        deliver_message_script,
    )

    target = resident(lineage, "Target")
    recipe = templates.create(
        {
            "kind": "script",
            "title": "Send",
            "targets": ["resident"],
            "body": {
                "script": deliver_message_script("{{param.text}}"),
                "endpoints": ["target"],
            },
            "parameters": [{"name": "text", "type": "text", "required": True}],
        },
        ACTOR,
    )
    calls = []
    original = routing.execute

    def execute(call, context):
        if call["instruction"] == "DeliverMessage":
            calls.append((call, context))
            return InstructionResult(
                instruction_id="inst_test",
                instruction="DeliverMessage",
                status="committed",
                result={"resident_id": target, "message_id": "msg_test"},
            )
        return original(call, context)

    monkeypatch.setattr(routing, "execute", execute)
    before = graph(lineage)["program"]
    text = 'A "quote" and ${event.payload.secret}\nnext line'
    applied = templates.apply(
        lineage,
        recipe["template_id"],
        {"kind": "resident", "resident_ids": [target]},
        ACTOR,
        parameters={"text": text},
    )
    assert applied["delivered"] == [{"resident_id": target, "message_id": "msg_test"}]
    assert calls[0][1].caller == ACTOR
    assert calls[0][0]["arguments"]["resident_id"] == target
    assert calls[0][0]["arguments"]["message"]["content_parts"][0]["text"] == text
    assert graph(lineage)["program"] == before


def test_invalid_structure_configuration_does_not_create_residents(lineage):
    """结构模板参数非法时应用失败，不创建任何 Resident"""
    from management_portal.society_service import templates

    target = resident(lineage, "Target")
    template = next(
        item
        for item in templates.list_templates()
        if item["builtin_key"] == "agent_loop"
    )
    before = {item["id"] for item in graph(lineage)["nodes"]}
    with pytest.raises(ValueError):
        templates.apply(
            lineage,
            template["template_id"],
            {"kind": "resident", "resident_ids": [target]},
            ACTOR,
            parameters={"max_rounds": -1},
        )
    assert {item["id"] for item in graph(lineage)["nodes"]} == before


def test_family_builtin_creates_family_function_block(lineage, monkeypatch):
    """family 模板为成员创建 Family Function Block 和三个角色 Resident"""
    from management_portal.society_service import templates

    member = resident(lineage, "Member")
    template = next(
        item for item in templates.list_templates() if item["builtin_key"] == "family"
    )
    applied = templates.apply(
        lineage,
        template["template_id"],
        {"kind": "resident", "resident_ids": [member]},
        ACTOR,
    )
    block = next(
        item
        for item in graph(lineage)["function_blocks"]
        if item["id"] == applied["family_id"]
    )
    assert block["template_id"] == template["template_id"]
    assert block["family"]["members"] == [member]
    assert len(applied["created"]["resident_ids"]) == 3
    assert {item["id"] for item in block["elements"]} >= {
        block["family"][role] for role in ("facade", "steward", "router")
    }


def test_family_template_settings_persist_and_apply_to_new_roles(lineage):
    """Family 模板保存的角色默认配置用于新建 Family

    :module: lineage.hatg
    """
    from management_portal.society_service import resident_protocol, templates

    recipe = next(item for item in templates.list_templates() if item["builtin_key"] == "family")
    settings = {
        "router": {"provider": "codex", "settings": {
            "model": "gpt-5.6-luna", "reasoning_effort": "high", "service_tier": "priority"}},
        "steward": {"provider": "codex", "settings": {"reasoning_effort": "high"}},
    }
    body = {**recipe["body"], "resident_settings": settings}
    templates.update(recipe["template_id"], {"body": body}, ACTOR)
    saved = templates.get(recipe["template_id"])
    assert saved["body"]["resident_settings"] == settings
    assert next(role for role in saved["behavior"]["roles"] if role["key"] == "router")["settings"] == settings["router"]["settings"]

    def apply(title, **kwargs):
        member = resident(lineage, title)
        return templates.apply(lineage, recipe["template_id"],
                               {"kind": "resident", "resident_ids": [member]}, ACTOR, **kwargs)

    def runtime(applied, role):
        return resident_protocol.get_resident_runtime_settings(applied[role + "_resident_id"], ACTOR)["runtime_settings"]

    first = apply("First family")
    for role in settings:
        assert runtime(first, role).items() >= settings[role]["settings"].items()
    original = runtime(first, "router")

    body["resident_settings"]["router"]["settings"]["reasoning_effort"] = "medium"
    templates.update(recipe["template_id"], {"body": body}, ACTOR)
    assert runtime(first, "router") == original
    second = apply("Second family", resident_settings={"router": {"settings": {
        "reasoning_effort": "low", "service_tier": ""}}})
    assert runtime(second, "router")["model"] == "gpt-5.6-luna"
    assert runtime(second, "router")["reasoning_effort"] == "low"
    assert not runtime(second, "router").get("service_tier")
    assert runtime(second, "steward")["reasoning_effort"] == "high"
    assert templates.get(recipe["template_id"])["body"] == body


def test_family_template_provider_override_does_not_copy_other_provider_settings(lineage, monkeypatch):
    """Family 模板切换 Provider 不继承其他 Provider 的账号和模型

    :module: lineage.hatg
    """
    from management_portal.society_service import templates
    from management_portal.society_service.templates import familize

    member = resident(lineage, "Member")
    recipe = templates.create({"kind": "familize", "title": "Custom family", "body": {
        "resident_settings": {"router": {"provider": "claude", "account": "claude_account",
                                         "settings": {"model": "claude-model"}}}}}, ACTOR)
    calls = []
    monkeypatch.setattr(familize, "familize", lambda *args, **kwargs: calls.append(kwargs) or kwargs)
    target = {"kind": "resident", "resident_ids": [member]}
    templates.apply(lineage, recipe["template_id"], target, ACTOR)
    assert calls[-1]["resident_settings"]["router"] == {
        "provider": "claude", "account": "claude_account", "settings": {"model": "claude-model"}}
    templates.apply(lineage, recipe["template_id"], target, ACTOR,
                    resident_settings={"router": {"provider": "codex", "settings": {"model": "codex-model"}}})
    assert calls[-1]["resident_settings"]["router"] == {"provider": "codex", "settings": {"model": "codex-model"}}
    assert "resident_settings" not in calls[-1]["parameters"]


@pytest.mark.parametrize("settings", [
    [], {"facade": {}}, {"router": {"backend": "codex"}},
    {"router": {"settings": []}}, {"steward": {"provider": ""}}, {"router": {"account": []}},
])
def test_family_template_rejects_invalid_role_defaults(settings):
    """Family 模板拒绝无效的内部角色默认配置

    :module: lineage.hatg
    """
    from management_portal.society_service import templates

    with pytest.raises(templates.TemplateError):
        templates.create({"kind": "familize", "title": "Invalid family",
                          "body": {"resident_settings": settings}}, ACTOR, dry_run=True)


def test_from_selection_copies_resident_settings_and_rewrites_references(lineage):
    """从选区创建模板复制 Resident 设置和画布位置，应用时脚本引用改指新 Resident"""
    from management_portal.society_service import templates

    source = resident(lineage, "Source")
    component = ok(
        "CreateComponent",
        {
            "lineage_id": lineage,
            "kind": "effect",
            "title": "Judge",
            "config": {
                "script": f'GetResident resident_id="{source}"',
                "endpoints": [],
            },
        },
    )["component_id"]
    template = ok(
        "CreateTemplate",
        {"from_selection": {"lineage_id": lineage, "element_ids": [source, component]}},
    )["template"]
    role = template["body"]["roles"][0]
    assert role["settings"]
    assert (
        "{{role." + role["key"] + ".id}}"
        in template["body"]["components"][0]["config"]["script"]
    )
    # The graph editor opens with the selection's canvas arrangement.
    placed = {item["id"]: item for item in graph(lineage)["nodes"]}[source]
    assert template["body"]["layout"][role["key"]] == {
        "x": round(placed["x"], 2),
        "y": round(placed["y"], 2),
    }
    assert set(template["body"]["layout"]) == {
        role["key"],
        template["body"]["components"][0]["key"],
    }
    applied = templates.apply(
        lineage, template["template_id"], {"kind": "canvas"}, ACTOR
    )
    copied = next(
        item
        for item in graph(lineage)["program"]["components"]
        if item["id"] == applied["created"]["component_ids"][0]
    )
    assert applied["created"]["resident_ids"][0] in copied["config"]["script"]
    assert source not in copied["config"]["script"]




def test_structure_layout_keeps_only_drawn_keys_and_rejects_bad_points(lineage):
    """结构模板 layout 只保留现有 key 并拒绝非数值坐标

    结构模板的 layout 坐标保留两位小数并去掉已不存在的 key；字符串、布尔值或 NaN 坐标被拒绝。

    :module: lineage.hatg
    """
    from management_portal.society_service import templates
    from management_portal.society_service.templates.registry import TemplateError

    body = {
        "roles": [{"key": "worker", "from": "new"}],
        "components": [
            {"key": "start", "kind": "event_source", "title": "Start", "config": {}}
        ],
        "wires": [
            {'key': 'go', 'kind': 'route', 'source': 'start', 'target': 'worker'}
        ],
        "layout": {
            "worker": {"x": 40.123, "y": 50},
            "start": {"x": 20, "y": 50},
            "gone": {"x": 1, "y": 2},
        },
    }
    draft = templates.create(
        {"kind": "structure", "title": "Drawn", "body": body}, ACTOR, dry_run=True
    )
    assert draft["body"]["layout"] == {
        "worker": {"x": 40.12, "y": 50},
        "start": {"x": 20, "y": 50},
    }
    for point in ({"x": "1", "y": 2}, {"x": True, "y": 2}, {"x": float("nan"), "y": 2}):
        with pytest.raises(TemplateError, match="layout"):
            templates.create(
                {
                    "kind": "structure",
                    "title": "Drawn",
                    "body": {**body, "layout": {"worker": point}},
                },
                ACTOR,
                dry_run=True,
            )


def test_structure_cleans_created_roles_when_graph_commit_fails(lineage, monkeypatch):
    """结构模板写入图失败时删除已创建的角色 Resident"""
    from management_portal.society_service import function_block_domain, templates

    source = resident(lineage, "Source")
    template = next(
        item
        for item in templates.list_templates()
        if item["builtin_key"] == "agent_loop"
    )

    def fail(*args, **kwargs):
        raise RuntimeError("write failed")

    monkeypatch.setattr(function_block_domain, "functionalize", fail)
    before = {item["id"] for item in graph(lineage)["nodes"]}
    with pytest.raises(RuntimeError, match="write failed"):
        templates.apply(
            lineage,
            template["template_id"],
            {"kind": "resident", "resident_ids": [source]},
            ACTOR,
        )
    assert {item["id"] for item in graph(lineage)["nodes"]} == before


def test_selection_structure_applies_once_per_resident(lineage):
    """选区目标的结构模板为每个选中的 Resident 各应用一次"""
    from management_portal.society_service import templates

    first = resident(lineage, "First")
    second = resident(lineage, "Second")
    builtin = next(
        item
        for item in templates.list_templates()
        if item["builtin_key"] == "fixed_reminder"
    )
    recipe = templates.create({**builtin, "targets": ["selection"]}, ACTOR)
    applied = templates.apply(
        lineage,
        recipe["template_id"],
        {"kind": "selection", "resident_ids": [first, second]},
        ACTOR,
    )
    assert len(applied["function_block_ids"]) == 2
    current = graph(lineage)
    wires = {wire["id"]: wire for wire in current["program"]["wires"]}
    targets = []
    for block in current["function_blocks"]:
        if block["id"] in applied["function_block_ids"]:
            target_wire = next(
                item["id"]
                for item in block["elements"]
                if item["key"] == "remind_target"
            )
            targets.append(wires[target_wire]["target"])
    assert set(targets) == {first, second}


@pytest.mark.parametrize("provider_location", ["template", "application"])
def test_structure_rejects_cross_provider_before_any_creation(
    lineage, monkeypatch, provider_location
):
    """fork 角色指定其他 Provider 时，结构模板在创建任何 Resident 前拒绝"""
    from management_portal.society_service import lineage_domain, templates

    source = resident(lineage, "Source")
    fork = {"key": "copy", "from": "fork", "fork_of": "target"}
    settings = {}
    if provider_location == "template":
        fork["provider"] = "claude"
    else:
        settings = {"copy": {"provider": "claude"}}
    recipe = templates.create(
        {
            "kind": "structure",
            "title": "Invalid fork after new role",
            "body": {
                "roles": [
                    {"key": "target", "from": "target"},
                    {"key": "worker", "from": "new"},
                    fork,
                ],
            },
        },
        ACTOR,
    )

    def unexpected_creation(*args, **kwargs):
        pytest.fail("Provider validation must run before creating any Resident")

    monkeypatch.setattr(lineage_domain, "create_lineage_resident", unexpected_creation)
    monkeypatch.setattr(lineage_domain, "fork_lineage_resident", unexpected_creation)
    with pytest.raises(templates.TemplateError, match="Provider"):
        templates.apply(
            lineage,
            recipe["template_id"],
            {"kind": "resident", "resident_ids": [source]},
            ACTOR,
            resident_settings=settings,
        )


def test_structure_validates_all_target_providers_before_any_creation(
    lineage, monkeypatch
):
    """多个目标中任一 Provider 不符时，结构模板在首次 fork 前拒绝"""
    from management_portal.society_service import (
        lineage_domain,
        resident_protocol,
        templates,
    )

    first = resident(lineage, "First")
    second = resident(lineage, "Second")
    recipe = templates.create(
        {
            "kind": "structure",
            "title": "Mixed Providers",
            "body": {
                "roles": [
                    {"key": "target", "from": "target"},
                    {
                        "key": "copy",
                        "from": "fork",
                        "fork_of": "target",
                        "provider": "codex",
                    },
                ],
            },
        },
        ACTOR,
    )
    get_resident = resident_protocol.get_resident
    monkeypatch.setattr(
        resident_protocol,
        "get_resident",
        lambda identity, actor: {
            **get_resident(identity, actor),
            "provider": "claude" if identity == second else "codex",
        },
    )

    def unexpected_creation(*args, **kwargs):
        pytest.fail("All target Providers must be checked before the first Fork")

    monkeypatch.setattr(lineage_domain, "fork_lineage_resident", unexpected_creation)
    with pytest.raises(templates.TemplateError, match="Provider"):
        templates.apply(
            lineage,
            recipe["template_id"],
            {"kind": "selection", "resident_ids": [first, second]},
            ACTOR,
        )


def test_structure_validates_fork_provider_through_new_roles(lineage, monkeypatch):
    """嵌套 fork 新角色时沿继承链检查 Provider，在创建前拒绝不符的指定"""
    from management_portal.society_service import lineage_domain, templates

    recipe = templates.create(
        {
            "kind": "structure",
            "title": "Fork a new role",
            "body": {
                "roles": [
                    {"key": "source", "from": "new"},
                    {"key": "copy", "from": "fork", "fork_of": "source"},
                    {
                        "key": "nested_copy",
                        "from": "fork",
                        "fork_of": "copy",
                        "provider": "codex",
                    },
                ],
            },
        },
        ACTOR,
    )

    def unexpected_creation(*args, **kwargs):
        pytest.fail("Fork Provider inheritance must be resolved before creation")

    monkeypatch.setattr(lineage_domain, "create_lineage_resident", unexpected_creation)
    with pytest.raises(templates.TemplateError, match="Provider"):
        templates.apply(
            lineage,
            recipe["template_id"],
            {"kind": "canvas"},
            ACTOR,
            resident_settings={"source": {"provider": "claude"}},
        )


@pytest.mark.parametrize("explicit_provider", [False, True])
def test_fork_same_provider_preserves_settings_and_account(
    monkeypatch, explicit_provider
):
    """同 Provider 的 fork 角色使用所选账号，并合并所选与模板中的运行设置"""
    from management_portal.society_service import lineage_domain, resident_protocol
    from management_portal.society_service.templates.registry import ApplyContext
    from management_portal.society_service.templates.structure import create_role

    calls = []
    monkeypatch.setattr(
        resident_protocol, "get_resident", lambda *args: {"provider": "claude"}
    )

    def fork(lineage_id, payload, actor):
        calls.append(("fork", payload, actor))
        return {"created_resident": {"id": "resident_child"}}

    monkeypatch.setattr(lineage_domain, "fork_lineage_resident", fork)
    monkeypatch.setattr(
        resident_protocol,
        "update_resident_runtime_settings",
        lambda identity, actor, payload: calls.append(("settings", payload, actor)),
    )
    chosen = {"account": "same_provider_account", "settings": {"model": "chosen"}}
    if explicit_provider:
        chosen["provider"] = "claude"
    ctx = ApplyContext("lineage_test", {}, {}, {}, ACTOR, "Copy", {"copy": chosen})
    created = create_role(
        ctx,
        {
            "key": "copy",
            "from": "fork",
            "fork_of": "target",
            "fork_mode": "recent-5",
            "settings": {"model": "default", "append_prompt": "Keep this"},
        },
        {"target": {"id": "resident_source", "title": "Source"}},
    )
    assert created["id"] == "resident_child"
    assert calls == [
        (
            "fork",
            {
                "source": "resident_source",
                "fork_mode": "recent-5",
                "child_title": "Copy",
                "backend_route_id": "same_provider_account",
            },
            ACTOR,
        ),
        (
            "settings",
            {"runtime_settings": {"model": "chosen", "append_prompt": "Keep this"}},
            ACTOR,
        ),
    ]
