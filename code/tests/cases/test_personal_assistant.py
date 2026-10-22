"""Personal Family continuity, privacy, initialization and maintenance.

:module: lineage.team
:design: decision_bd5217d08c09, decision_44cf587c1721
"""
from __future__ import annotations

import pytest
from types import SimpleNamespace

from management_portal.society_service import (
    family_facade, lineage_domain as graphs, lineage_graph_state, personal_assistant,
    personal_history, principal_hierarchy_domain, resident_catalog, resident_conversation_stream,
    resident_execution_domain, resident_protocol,
)
from tests.cases.test_resident_folder_creation_contract import future_creation_store  # noqa: F401
from tests.cases.test_family_familize import charters  # noqa: F401
from tests.cases.test_personal_ama_provisioning import ui_state  # noqa: F401


@pytest.fixture
def personal(future_creation_store, charters):
    ama = principal_hierarchy_domain.ensure_personal_ama("newcomer", "newcomer")
    assistant = personal_assistant.ensure("newcomer", ama["resident_id"], "newcomer")
    return ama, assistant


def test_personal_family_can_precede_first_ai_account(future_creation_store, charters, monkeypatch):
    """首次注册完整个人 Family 不依赖 Codex 账号，执行配置留待默认 AI 就绪。"""
    from management_portal.execution import account_registrations
    from management_portal.society_service import resident_registration_domain, resident_runtime_domain
    monkeypatch.setattr(account_registrations, "roles", lambda: [])
    def no_account(*args, **kwargs):
        raise ValueError("No enabled default Codex account")
    monkeypatch.setattr(resident_registration_domain, "resolve_account_configuration", no_account)
    ama = principal_hierarchy_domain.ensure_personal_ama("newcomer", "newcomer")
    assistant = personal_assistant.ensure("newcomer", ama["resident_id"], "newcomer")
    graph = graphs.get_lineage(assistant["lineage_id"], "newcomer")
    family = graph["function_blocks"][0]["family"]
    for rid in [ama["resident_id"], *(family[r] for r in ("facade", "router", "steward"))]:
        record = resident_catalog.get_catalog().get(rid)
        assert record["backend_route_id"] == ""
        settings = resident_runtime_domain._read_resident_runtime_settings(record)
        assert settings["backend_role"] == "default"
        assert not settings.get("model")
    assert personal_assistant.ensure("newcomer", ama["resident_id"], "newcomer") == assistant


def test_personal_initialization_is_private_and_resumable(personal):
    """个人助手初始化幂等，内部角色保持私有并退出发现候选"""
    ama, assistant = personal
    graph = graphs.get_lineage(assistant["lineage_id"], "newcomer")
    family = graph["function_blocks"][0]["family"]
    assert graph["title"] == personal_assistant.TITLE
    assert graph["leader_resident_id"] == assistant["resident_id"]
    assert family["members"] == [ama["resident_id"]]
    assert family["personal_ama_id"] == ama["resident_id"]
    keys = {e["key"] for e in graph["function_blocks"][0]["elements"]}
    assert not keys.intersection({"summary_timer", "summary_rollup", "request_summary", "notify_periodic_review"})
    assert "review_personal_activity" in keys
    from management_portal.society_service import resident_access
    for rid in [ama["resident_id"], *(family[r] for r in ("facade", "router", "steward"))]:
        record = resident_catalog.get_catalog().get(rid)
        assert record["personal_owner_id"] == "newcomer"
        assert resident_access.resident_visible_to_actor(record, "newcomer")
        assert not resident_access.resident_visible_to_actor(record, "CASE-A")
    again = personal_assistant.ensure("newcomer", ama["resident_id"], "newcomer")
    assert again == assistant
    assert graphs.get_lineage(assistant["lineage_id"], "newcomer")["revision"] == graph["revision"]
    assert principal_hierarchy_domain.ensure_personal_ama("newcomer", "newcomer")["resident_id"] == ama["resident_id"]
    assert lineage_graph_state.read_cell(assistant["lineage_id"], personal_assistant.preferences_key(assistant["resident_id"]))["value"] == {"preferences": [], "my_teams": []}
    from management_portal.society_service.resident_context.source import LineageResidentSource
    assert not LineageResidentSource().external_resident_ids([ama["resident_id"], family["facade"], family["router"], family["steward"]])


def test_custom_title_and_history_branch_remain_outside_family(future_creation_store, charters):
    """保留用户命名与历史分支，只纳入当前 AMA"""
    ama = principal_hierarchy_domain.ensure_personal_ama("newcomer", "newcomer")
    lineage_id = ama["lineage_id"]
    branch = graphs.create_lineage_resident(lineage_id, {"title": "July–August"}, "newcomer")["resident"]["resident_id"]
    def rename(body):
        body["title"] = "My own title"
        return {}
    graphs.commit_lineage_edit(lineage_id, "newcomer", rename, reason="customize")
    assistant = personal_assistant.ensure("newcomer", ama["resident_id"], "newcomer")
    graph = graphs.get_lineage(lineage_id, "newcomer")
    assert graph["title"] == "My own title"
    assert branch not in graph["function_blocks"][0]["family"]["members"]
    assert assistant["resident_id"] != ama["resident_id"]


def _append(rid, number, *, role="user", text="message"):
    mid = f"msg_{number:032x}"
    repository = resident_conversation_stream.get_repository()
    repository.append_message(rid, {"message_id": mid, "role": role, "author_id": "newcomer" if role == "user" else rid,
                                   "created_at": "2026-10-16T10:00:00Z", "content_parts": [{"type": "text", "text": text}]})
    return mid


def test_history_pages_preserve_ids_and_quotes_exclude_later_direct_chat(future_creation_store, charters):
    """历史分页保留消息身份，引用回交 AMA，迁移后直接对话不投影"""
    ama = principal_hierarchy_domain.ensure_personal_ama("newcomer", "newcomer")
    old = [_append(ama["resident_id"], i, role="agent" if i % 2 == 0 else "user") for i in range(1, 7)]
    assistant = personal_assistant.ensure("newcomer", ama["resident_id"], "newcomer")
    facade = resident_catalog.get_catalog().get(assistant["resident_id"])
    later = _append(ama["resident_id"], 8, text="must stay private to direct AMA chat")
    page = resident_protocol.get_resident_conversation_snapshot(facade["resident_id"], "newcomer", history_limit=2)
    seen = [m["message_id"] for m in page["messages"]]
    while page["next_page_token"]:
        page = resident_protocol.get_conversation_messages(facade["resident_id"], "newcomer", page["next_page_token"], limit=2)
        assert all(m["family_history"]["resident_id"] == ama["resident_id"] for m in page["messages"])
        seen = [m["message_id"] for m in page["messages"]] + seen
    assert seen == old
    assert later not in seen
    assert personal_history.message(facade, later) is None
    from management_portal.society_service import message_quotes
    for mid in old:
        quote = message_quotes.accept(facade, {"message_id": mid}, reader="newcomer", message_id="msg_" + "f" * 32)
        assert quote["message_id"] == mid
        assert family_facade.quoted_member(facade, {"quote": quote}) == ama["resident_id"]
    with pytest.raises(ValueError):
        personal_history.page(facade, "personal-history:9999999", limit=2)


def test_personal_instructions_keep_user_customization_and_refresh_each_turn(personal, monkeypatch):
    """个人行为每轮刷新并保留用户定制，压缩后仍按相同时机注入"""
    ama, assistant = personal
    record = resident_catalog.get_catalog().get(ama["resident_id"])
    from management_portal.society_service import resident_runtime_domain
    monkeypatch.setattr(resident_runtime_domain, "_resident_agents_instruction_block", lambda record: "用户自己的语气")
    instructions = resident_execution_domain._resident_system_instructions(record, {}, settings={})
    personal = [p for p in instructions.parts if p.name == "personal_assistant"]
    assert len(personal) == 1 and personal[0].timing == "every_turn"
    assert "portal-resident-handoff" in personal[0].content
    assert "reply_to_message_id" in personal[0].content
    assert any("用户自己的语气" in p.content for p in instructions.parts)
    from management_portal.execution.injection_plan import plan_injection
    for first, compacted in ((True, ""), (False, ""), (False, "2026-10-16T11:00:00Z")):
        planned = plan_injection(instructions.parts, "hello", generation=True, first_injection=first,
                                 injected_at="2026-10-16T10:00:00Z", compacted_at=compacted)
        assert "personal_assistant" in planned.attached_parts


@pytest.mark.parametrize("active,reviewed,expected", [(False, False, "inactive"), (True, True, "unchanged"), (True, False, "requested")])
def test_daily_review_only_calls_model_after_new_activity(active, reviewed, expected):
    """每日回顾只在出现未回顾的新活动时唤醒 steward"""
    from tests.cases.test_family_router_script import _run
    inputs = {"arguments": {"lineage_id": "lineage_aaaaaaaaaaaa", "facade": "resident_bbbbbbbbbbbb", "steward": "resident_aaaaaaaaaaaa", "activity_key": "activity", "review_key": "review"}}
    results = {"GetSharedState": lambda args: {"state": {}, "value": None if not active else "msg_new" if args["key"] == "activity" or reviewed else "msg_old"},
               "ListResidentStates": {"states": [{"state": {"work_state": "idle", "queued_count": 0}}]},
               "DispatchFamilyTask": {"message_id": "msg_review"}, "UpdateSharedState": {}}
    outcome, calls = _run(personal_assistant.REVIEW_SCRIPT, inputs, results)
    assert outcome.outputs["status"] == expected
    assert sum(name == "DispatchFamilyTask" for name, args in calls) == (expected == "requested")
    if expected == "requested":
        assert next(args for name, args in calls if name == "DispatchFamilyTask") == {"resident_id": "resident_bbbbbbbbbbbb", "trigger": "review"}


def test_retry_after_partial_role_creation_reuses_roles(future_creation_store, charters, monkeypatch):
    """初始化中途失败后复用已建角色，不重复创建"""
    ama = principal_hierarchy_domain.ensure_personal_ama("newcomer", "newcomer")
    create = graphs.create_lineage_resident
    failed = False
    created = []
    def interrupted(lineage_id, payload, actor):
        nonlocal failed
        result = create(lineage_id, payload, actor)
        created.append(result["resident"]["resident_id"])
        if not failed and payload["creation_key"].endswith(":steward"):
            failed = True
            raise RuntimeError("role publication interrupted")
        return result
    monkeypatch.setattr(graphs, "create_lineage_resident", interrupted)
    with pytest.raises(RuntimeError, match="interrupted"):
        personal_assistant.ensure("newcomer", ama["resident_id"], "newcomer")
    result = personal_assistant.ensure("newcomer", ama["resident_id"], "newcomer")
    assert created[0] == created[2] == result["resident_id"]
    graph = graphs.get_lineage(ama["lineage_id"], "newcomer")
    assert len(graph["nodes"]) == 4


def test_explicit_discovery_cannot_include_personal_family(personal):
    """显式指定搜索范围也不能将私人 Family 放入候选"""
    from management_portal.society_service.resident_context.service import ResidentContext
    from management_portal.society_service.resident_context.source import LineageResidentSource
    ama, assistant = personal
    documents = [{"resident_id": rid} for rid in (ama["resident_id"], assistant["resident_id"], "resident_ffffffffffff")]
    context = ResidentContext(store=SimpleNamespace(documents=lambda ids: documents), source=LineageResidentSource())
    assert context._discovery_documents([d["resident_id"] for d in documents]) == documents[-1:]


def test_preference_updates_survive_login_and_router_reads_them(personal):
    """个人偏好跨登录保留，Router 指南提供按需查询入口而不预加载偏好"""
    from tests.cases.test_family_router_script import _run, MEMBERS, ROUTER, FACADE
    from management_portal.society_service.family_definition import FAMILY_ROUTER_SCRIPT
    ama, assistant = personal
    key = personal_assistant.preferences_key(assistant["resident_id"])
    preference = {"preferences": [{"topic": "代码", "resident_id": "resident_ffffffffffff"}], "my_teams": []}
    lineage_graph_state.update_cell(assistant["lineage_id"], key, preference)
    personal_assistant.ensure("newcomer", ama["resident_id"], "newcomer")
    assert lineage_graph_state.read_cell(assistant["lineage_id"], key)["value"] == preference
    inputs = {"arguments": {"members": MEMBERS, "router": ROUTER, "lineage_id": assistant["lineage_id"],
                             "preferences_key": key, "corrections_key": "corrections"},
              "event": {"payload": {"resident_id": FACADE, "message_id": "msg_123", "relay_depth": 0}}}
    results = {"GetMessage": {"message": {"author_id": "newcomer", "content_parts": [{"type": "text", "text": "继续"}]}},
               "GetResidentSummaries": {"summaries": []}, "ListResidentStates": {"states": []},
               "ListRecentRounds": {"rounds": []}, "DispatchFamilyTask": {"message_id": "msg_route"},
               "GetSharedState": lambda args: {"value": preference if args["key"] == key else []}}
    outcome, calls = _run(FAMILY_ROUTER_SCRIPT, inputs, results)
    assert outcome.status == "succeeded"
    assert next(args for name, args in calls if name == "DispatchFamilyTask") == {
        "resident_id": FACADE, "trigger": "route", "source_message_id": "msg_123"}
    assert not any(name == "GetSharedState" for name, _ in calls)
    from management_portal.society_service import family_tasks
    from management_portal.society_service.resident_workspace_domain import _find_resident
    _, block = family_tasks.context(_find_resident(assistant["resident_id"]))
    _, _, guide = family_tasks.guide(_find_resident(block["family"]["router"]))
    assert key in guide and "同话题优先保持上一轮成员" in guide
    assert "resident_ffffffffffff" not in guide


def test_input_scoped_snapshot_never_includes_personal_history(personal):
    """单请求授权的对话快照不附带私人历史"""
    from management_portal.society_service import resident_input_scope
    ama, assistant = personal
    mid = "msg_" + "c" * 32
    repo = resident_conversation_stream.get_repository()
    repo.accept_user_message(assistant["resident_id"], {"message_id": mid, "role": "user", "author_id": "newcomer",
        "content_parts": [{"type": "text", "text": "only this request"}], "created_at": "2026-10-16T12:00:00Z"})
    repo.set_input_state(assistant["resident_id"], mid, "queued")
    resident_input_scope.register(resident_id=assistant["resident_id"], message_id=mid, actor="newcomer", profile={})
    result = resident_protocol.get_resident_conversation_snapshot(assistant["resident_id"], "newcomer", input_message_id=mid)
    assert "family_history" not in result
    assert all("family_history" not in m for m in result["messages"])


def test_login_keeps_ama_entry_and_recovers_family_failure(ui_state, monkeypatch):
    """登录保留原 AMA 入口，Family 失败后重试建立独立关联"""
    from management_portal import personal_ama, society_service
    ui_state.put("newcomer", personal_ama.NAMESPACE, "primary", {"resident_id": "resident_aaaaaaaaaaaa"})
    attempted = []
    def assistant(username, ama_id, actor):
        attempted.append((username, ama_id, actor))
        if len(attempted) == 1:
            raise RuntimeError("temporary failure")
        return {"resident_id": "resident_bbbbbbbbbbbb", "lineage_id": "lineage_aaaaaaaaaaaa", "family_id": "family_aaaaaaaaaaaa"}
    monkeypatch.setattr(society_service, "get_society_service", lambda: SimpleNamespace(lineage=SimpleNamespace(
        ensure_personal_ama=lambda *args: {"resident_id": "resident_cccccccccccc", "lineage_id": "lineage_aaaaaaaaaaaa"},
        ensure_personal_assistant=assistant)))
    assert personal_ama.provision_account("newcomer") == {"error": "temporary failure"}
    assert ui_state.get("newcomer", personal_assistant.NAMESPACE, "primary") is None
    assert personal_ama.provision_account("newcomer")["personal_assistant"]["resident_id"] == "resident_bbbbbbbbbbbb"
    assert attempted == [("newcomer", "resident_aaaaaaaaaaaa", "newcomer")] * 2
    assert ui_state.get("newcomer", personal_ama.NAMESPACE, "primary")["value"] == {"resident_id": "resident_aaaaaaaaaaaa"}
    assert ui_state.get("newcomer", personal_assistant.NAMESPACE, "primary")["value"]["presentation"] == {"kind": "personal-assistant"}
