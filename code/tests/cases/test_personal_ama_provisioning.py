"""Every new account gets its own Ask Me Anything (decision_bd5217d08c09).

:module: lineage.service
:importance: P1
"""
from __future__ import annotations

import hashlib
import secrets
from types import SimpleNamespace

import pytest

from management_portal.society_service import lineage_domain as graphs
from management_portal.society_service import principal_hierarchy_domain, resident_catalog, resident_conversation_stream
from tests.cases.test_resident_folder_creation_contract import future_creation_store  # noqa: F401 - fixture


def test_personal_ama_can_precede_default_ai(future_creation_store, monkeypatch):
    """注册用户先取得 AMA 身份，首个默认 AI 就绪后再绑定执行配置。"""
    from management_portal.execution import account_registrations
    from management_portal.society_service import resident_default_backend, resident_runtime_domain
    from management_portal.society_service import resident_backend
    monkeypatch.setattr(account_registrations, "roles", lambda: [])
    result = principal_hierarchy_domain.ensure_personal_ama("first_user", "first_user")
    record = resident_catalog.get_catalog().get(result["resident_id"])
    assert record["backend_route_id"] == ""
    settings = resident_runtime_domain.read_resident_runtime_settings(record)
    settings.update(allow_subagents=True, permission_preset="", permissions=":read-only",
                    sandbox="read-only", sandbox_explicit=True)
    resident_runtime_domain.write_resident_runtime_settings(record, settings)
    monkeypatch.setattr(resident_default_backend, "role_configuration", lambda _: {
        "backend": "codex", "backend_route_id": "one", "runtime_settings": {"backend_role": "default"}})
    monkeypatch.setattr(resident_backend, "session_record", lambda _: {})
    resident_default_backend.bind_before_execution(record, "first_user")
    saved = resident_catalog.get_catalog().get(result["resident_id"])
    assert saved["backend_route_id"] == "one"
    saved_settings = resident_runtime_domain.read_resident_runtime_settings(saved)
    assert saved_settings["backend_role"] == "default"
    assert saved_settings["allow_subagents"] is True
    assert saved_settings["sandbox"] == "read-only"


@pytest.mark.parametrize("role_model", [None, "deepseek-v4-flash"])
def test_first_api_binding_replaces_source_site_model(future_creation_store, monkeypatch, role_model):
    """首次绑定角色账号时不沿用源站点 GPT 模型；已绑定后不随站点默认变化。"""
    from management_portal.execution import account_registrations
    from management_portal.society_service import resident_default_backend as defaults, resident_runtime_domain as runtime
    from management_portal.society_service import resident_backend
    monkeypatch.setattr(account_registrations, "roles", lambda: [])
    result = principal_hierarchy_domain.ensure_personal_ama("api_user", "api_user")
    record = resident_catalog.get_catalog().get(result["resident_id"])
    settings = runtime.read_resident_runtime_settings(record)
    settings.update(model="gpt-6-luna", reasoning_effort="high", service_tier="priority")
    runtime.write_resident_runtime_settings(record, settings)
    role_settings = {"backend_role": "default", **({"model": role_model} if role_model else {})}
    monkeypatch.setattr(defaults, "role_configuration", lambda _: {
        "backend": "provider_api", "backend_route_id": "deepseek_api", "runtime_settings": role_settings})
    monkeypatch.setattr(resident_backend, "session_record", lambda _: {})
    defaults.bind_before_execution(record, "api_user")
    saved = resident_catalog.get_catalog().get(result["resident_id"])
    actual = runtime.read_resident_runtime_settings(saved)
    assert (saved["backend"], saved["backend_route_id"]) == ("provider_api", "deepseek_api")
    assert (actual["model"], actual["reasoning_effort"], actual["service_tier"]) == (role_model or "", "", "")
    monkeypatch.setattr(defaults, "role_configuration", lambda _: pytest.fail("bound Residents must keep their route"))
    defaults.bind_before_execution(saved, "api_user")
    assert runtime.read_resident_runtime_settings(saved) == actual
    # Exercise the production request and API adapter without a real account or
    # network: both explicit role models and a blank model reach this API.
    from management_portal.society_service import resident_execution_domain as execution
    from management_portal.execution.backends import ChatCompletionService
    from management_portal.execution.contracts import SystemInstructions
    monkeypatch.setattr(execution, "resident_system_instructions", lambda *a: SystemInstructions())
    job = {"actor": "api_user", "user_message_id": "message-test", "raw_message": "hello"}
    request = execution._build_resident_execution_request(saved, job)
    payloads = []
    def transport(provider, endpoint, payload, **kwargs):
        payloads.append((provider, payload))
        return {"choices": [{"message": {"content": "reply"}}], "usage": {}}
    service = ChatCompletionService(SimpleNamespace(get=lambda _: {
        "id": "deepseek_api", "kind": "openai_compatible_chat", "default_model": "deepseek-v4-flash"},
        request=transport))
    service.execute(SimpleNamespace(request=request, attach_system_context=True,
                                   system_context="fixed instructions", user_prompt="hello"),
                    SimpleNamespace(is_cancelled=lambda: False, emit=lambda *a: None))
    assert request.provider == "deepseek_api"
    assert payloads[0][1]["model"] == "deepseek-v4-flash"
    assert payloads[0][1]["messages"][-1] == {"role": "user", "content": "hello"}


@pytest.mark.empty_execution_registrations
@pytest.mark.parametrize("configure_before_input", [False, True])
def test_first_api_binding_preserves_pending_input_provenance(future_creation_store, monkeypatch, configure_before_input):
    """首个 API 接管待执行输入时保留消息，不能重写旧执行的 Codex 来源。"""
    from management_portal.execution import account_registrations
    from management_portal.execution.agent_data_store import get_agent_data_store
    from management_portal.society_service import (
        resident_default_backend, resident_domain, resident_intake_domain,
        resident_execution_work, resident_execution_domain,
    )
    monkeypatch.setattr(resident_intake_domain, "start_resident_continuation", lambda *a: None)
    ama = principal_hierarchy_domain.ensure_personal_ama("api_user", "api_user")
    account_registrations.create_registration({"account_id": "deepseek_only", "provider": "provider_api",
        "kind": "api_profile", "config": {"api_id": "deepseek_test"}}, "api_user")
    if configure_before_input:
        account_registrations.update_role("default", "deepseek_only", "", "", 0, "api_user")
    resident_domain.deliver_resident_message_content(ama["resident_id"], "api_user", "original input")
    record = resident_catalog.get_catalog().get(ama["resident_id"])
    original = resident_execution_work.read(record)
    store = get_agent_data_store()
    assert store.execution(original["execution_id"])["provider"] == "codex"
    if not configure_before_input:
        account_registrations.update_role("default", "deepseek_only", "", "", 0, "api_user")
    resident_default_backend.bind_before_execution(record, "api_user")
    current = resident_execution_work.read(record)
    # The real store reserves on every progress write: this reproduced the
    # native failure even though registration and binding alone passed.
    resident_execution_work.write(record, current)
    assert current["user_message_id"] == original["user_message_id"]
    assert current["raw_message"] == original["raw_message"]
    assert current.get("continuation_id") == original.get("continuation_id")
    assert current["execution_id"] != original["execution_id"]
    old = store.execution(original["execution_id"])
    assert (old["provider"], old["status"]) == ("codex", "cancelled")
    assert store.execution(current["execution_id"])["provider"] == "deepseek_test"
    request = resident_execution_domain.durable_resident_execution_request(record, current,
        lambda: resident_execution_domain._build_resident_execution_request(record, current))
    assert request.provider == "deepseek_test"
    assert request.execution_id == current["execution_id"]


def test_personal_ama_is_created_once_in_the_users_name(future_creation_store):
    """用户的 Ask Me Anything 以本人名义只建一次

    首次调用新建不属于任何 Project 的个人 Lineage 与 Codex Leader，owner 只有该用户、组织层级为用户层；再次调用返回同一个，不同用户各有一个。

    :design: decision_bd5217d08c09, decision_6eac174d85bf
    :design-point:
    """
    first = principal_hierarchy_domain.ensure_personal_ama("newcomer", "newcomer")
    again = graphs.ensure_personal_ama("newcomer", "newcomer")
    assert first["created"] is True
    assert again == {**first, "created": False}
    graph = graphs.get_lineage(first["lineage_id"], "newcomer")
    assert graph["lineage_membership"] == "personal"
    assert graph["project_id"] == ""
    assert "owner_ids" not in graph
    assert graph["leader_resident_id"] == first["resident_id"]
    assert graph["principal_hierarchy"] == {"level": "user", "scope_id": first["lineage_id"]}
    record = resident_catalog.get_catalog().get(first["resident_id"])
    assert (record["title"], record["backend"], record["created_by"]) == ("Ask Me Anything", "codex", "newcomer")
    assert record["principal_hierarchy"] == {"level": "user", "category": "personal_ama", "scope_ids": ["newcomer"]}
    assert record["personal_owner_id"] == "newcomer"
    assert resident_conversation_stream.get_repository().read_messages(first["resident_id"]) == []
    other = principal_hierarchy_domain.ensure_personal_ama("neighbour", "neighbour")
    assert other["created"] is True and other["lineage_id"] != first["lineage_id"]


def test_personal_ama_and_nested_forks_represent_the_registered_owner(future_creation_store, monkeypatch):
    """个人 AMA 及其多层 fork 代表登记的所有者

    别人不能 fork 某用户的 AMA；所有者 fork 出的多层 Resident 都登记同一个 personal_owner_id，AMA 的私有规定据此把它们当作所有者：都能管理该 AMA，其他用户管理不了。

    :module: platform.core
    :importance: P2
    :design: decision_bd5217d08c09, decision_b820faf42218
    """
    from management_portal.society_service import lineage_access_policy, personal_ama, resident_domain, resource_client

    first = principal_hierarchy_domain.ensure_personal_ama('newcomer', 'newcomer')
    residents = [first['resident_id']]
    # The AMA is newcomer's alone (sample_admin 2026-10-12).
    with pytest.raises(KeyError, match="resident not found"):
        resident_domain.create_resident_full_context_fork(residents[-1], 'neighbour')
    for _ in range(2):
        child = resident_domain.create_resident_full_context_fork(residents[-1], 'newcomer')
        residents.append(child['resident_id'])
    # Use the actual descriptor producer and the rule's consumer together.
    descriptors = {}
    for rid in residents:
        record = resident_catalog.get_catalog().get(rid)
        assert record['personal_owner_id'] == 'newcomer'
        descriptors[rid] = resource_client._resident_resource_projection(record)['descriptor']
    monkeypatch.setattr(personal_ama, '_DESCRIPTORS', SimpleNamespace(get=lambda rid: descriptors[rid]))
    monkeypatch.setattr(personal_ama, '_OWNERS', {})
    lineage = first['lineage_id']
    owners = {lineage: 'newcomer'}
    for rid in residents:
        assert personal_ama.owner_of(rid) == 'newcomer'
        assert lineage_access_policy.changeable_ids(rid, owners, 'DeleteLineage', 'lineage_id') == {lineage}
    assert lineage_access_policy.changeable_ids('neighbour', owners, 'DeleteLineage', 'lineage_id') == set()


def test_a_deleted_users_personal_ama_goes_with_the_account(future_creation_store, monkeypatch):
    """用户删除后，个人 Ask Me Anything 随账号删除

    账号仍有效时拒绝删除它的 AMA；账号已删除时 Portal 以自身名义删除该用户的个人 Lineage 及其 Resident，其他用户的 AMA 不受影响。

    :design: decision_bd5217d08c09
    """
    from management_portal import portal_access_control

    mine = principal_hierarchy_domain.ensure_personal_ama("newcomer", "newcomer")
    theirs = principal_hierarchy_domain.ensure_personal_ama("neighbour", "neighbour")
    status = {"newcomer": "active", "neighbour": "active"}
    store = portal_access_control.get_portal_access_store()
    monkeypatch.setattr(store, "user", lambda username: {"status": status.get(str(username), "active")})
    with pytest.raises(PermissionError):
        graphs.delete_personal_ama("newcomer", "sample_admin")
    status["newcomer"] = "deleted"
    assert graphs.delete_personal_ama("newcomer", "sample_admin")["deleted_lineage_ids"] == [mine["lineage_id"]]
    personal = {row["lineage_id"] for row in graphs._lineage_catalog_store().listing_records() if row.get("lineage_membership") == "personal"}
    assert personal == {theirs["lineage_id"]}
    with pytest.raises(KeyError):
        resident_catalog.get_catalog().get(mine["resident_id"])
    assert graphs.delete_personal_ama("newcomer", "sample_admin")["deleted_lineage_ids"] == []


def test_only_the_user_creates_a_personal_ama(future_creation_store):
    """个人 Lineage 只能由用户本人创建

    以其他用户或 Resident 身份创建某用户的 Ask Me Anything 被拒绝，不留下个人 Lineage。

    :design: decision_bd5217d08c09, decision_6eac174d85bf
    :design-point:
    """
    with pytest.raises(PermissionError):
        principal_hierarchy_domain.ensure_personal_ama("newcomer", "sample_admin")
    with pytest.raises(ValueError):
        principal_hierarchy_domain.ensure_personal_ama("resident_0123456789ab", "resident_0123456789ab")
    assert not any(
        row.get("lineage_membership") == "personal" for row in graphs._lineage_catalog_store().listing_records()
    )


def test_a_personal_lineage_outside_projects_inherits_from_nothing(monkeypatch):
    """不属于 Project 的个人 Lineage 在权限树中没有上级

    个人 Lineage 向资源服务登记的描述不指向任何 Project，权限系统据此不把它放在 Project 之下；正式 Lineage 仍放在所属 Project 之下。

    :design: decision_bd5217d08c09, decision_b820faf42218
    :design-point:
    """
    from management_portal.society_service import resource_client
    from management_portal.permission_system.facts import fact_from_record

    published = []
    monkeypatch.setattr(resource_client, "get_resource_service", lambda: SimpleNamespace(
        sync_collaboration_resources=lambda payload: (
            published.extend(item["descriptor"] for item in payload) or {"resources": [{} for _ in payload]})))
    for membership, project_id in (("personal", ""), ("formal", "project_0123456789ab")):
        resource_client.sync_lineage({"lineage_id": "lineage_0123456789ab", "title": "Ask Me Anything",
                                      "lineage_membership": membership, "project_id": project_id, "nodes": []})
        descriptor = published.pop()
        fact = fact_from_record({**descriptor, "resource_id": "lineage_0123456789ab"}, "lineage")
        assert fact.owners == ({} if membership == "personal" else {"project": project_id})


@pytest.mark.parametrize("projection_written", [False, True])
def test_asking_again_finishes_a_creation_whose_registration_failed(future_creation_store, monkeypatch, projection_written):
    """再次确保 Ask Me Anything 会补完中断的资源登记

    首次创建时 Lineage 资源登记失败，再次调用按创建快照补登 Lineage 资源并确认 Leader 注册，不新建第二个。

    Registration may stop before or after publishing descriptor metadata.

    :design: decision_bd5217d08c09
    :design-point:
    """
    from management_common.projection_receipts import CommittedProjectionError

    original = graphs._sync_lineage_resource_identity
    published = []

    def unplaced(projection):
        if projection_written:
            original(projection)
        raise ValueError("已选择 project，规范归属无法解析：missing_canonical_parent")

    monkeypatch.setattr(graphs, "_sync_lineage_resource_identity", unplaced)
    with pytest.raises(CommittedProjectionError):
        principal_hierarchy_domain.ensure_personal_ama("newcomer", "newcomer")
    monkeypatch.setattr(graphs, "_sync_lineage_resource_identity",
                        lambda projection: published.append(projection["lineage_id"]) or original(projection))
    healed = principal_hierarchy_domain.ensure_personal_ama("newcomer", "newcomer")
    assert healed["created"] is False
    assert published == [healed["lineage_id"]]
    assert principal_hierarchy_domain.ensure_personal_ama("newcomer", "newcomer") == healed
    assert published == [healed["lineage_id"]]
    assert "owner_ids" not in graphs._read_lineage_state(healed["lineage_id"])
    assert resident_catalog.get_catalog().get(healed["resident_id"])["principal_hierarchy"]["scope_ids"] == ["newcomer"]
    assert resident_catalog.get_catalog().get(healed["resident_id"])["registration_state"] == "ready"
    assert graphs.get_lineage(healed["lineage_id"], "newcomer")["leader_resident_id"] == healed["resident_id"]


@pytest.fixture
def ui_state(monkeypatch, tmp_path):
    from management_portal import portal_ui_state

    store = portal_ui_state.PortalUIStateStore(lock_root=tmp_path / "locks")
    monkeypatch.setattr(portal_ui_state, "get_portal_ui_state_store", lambda: store)
    return store


@pytest.fixture
def society_service(monkeypatch):
    import management_portal.society_service as society_service

    calls = []

    def ensure(username, actor):
        calls.append((username, actor))
        return {"lineage_id": "lineage_0123456789ab", "resident_id": "resident_0123456789ab", "created": len(calls) == 1}

    monkeypatch.setattr(society_service, "get_society_service",
                        lambda: SimpleNamespace(lineage=SimpleNamespace(ensure_personal_ama=ensure,
                            ensure_personal_assistant=lambda username, ama_id, actor: {
                                "resident_id": "resident_111111111111", "lineage_id": "lineage_0123456789ab",
                                "family_id": "family_111111111111"})))
    return calls


def test_provision_points_a_missing_entry_at_the_new_ama(ui_state, society_service):
    """补建只在个人入口缺失时写入关联

    关联缺失时指向新建的 Ask Me Anything；重复调用不再写入，用户已有的关联保持不变。

    :module: security.identity-access
    :design: decision_bd5217d08c09, decision_6eac174d85bf
    :design-point:
    """
    from management_portal import personal_ama

    first = personal_ama.provision("newcomer")
    assert society_service == [("newcomer", "newcomer")]
    assert first == {"lineage_id": "lineage_0123456789ab", "resident_id": "resident_0123456789ab",
                     "created": True, "associated": True, "personal_assistant": {
                         "resident_id": "resident_111111111111", "lineage_id": "lineage_0123456789ab",
                         "family_id": "family_111111111111"}}
    assert ui_state.get("newcomer", "lineage-workbench.personal-ama", "primary")["value"] == {
        "resident_id": "resident_0123456789ab", "lineage_id": "lineage_0123456789ab",
        "presentation": {"kind": "personal-ama", "title": "Ask Me Anything"},
    }
    assert personal_ama.provision("newcomer")["associated"] is False
    ui_state.put("chosen", "lineage-workbench.personal-ama", "primary", {"resident_id": "resident_ffffffffffff"})
    assert personal_ama.provision("chosen")["associated"] is False
    assert ui_state.get("chosen", "lineage-workbench.personal-ama", "primary")["value"] == {"resident_id": "resident_ffffffffffff"}


@pytest.fixture
def portal(monkeypatch, tmp_path):
    from management_portal import serve_management_portal as portal
    from management_portal.portal_access_control import PortalAccessStore, PortalCredentialStore
    from management_portal.user_enrollment import UserEnrollment

    enrollment = UserEnrollment(PortalAccessStore(), PortalCredentialStore())
    monkeypatch.setattr(portal, "_USER_ENROLLMENT", enrollment)
    monkeypatch.setattr(portal, "_PORTAL_ACCESS", enrollment.access)
    enrollment.access.upsert_user("CASE-A", actor="CASE-A")
    return portal


def test_account_creation_provisions_the_ama_and_survives_its_failure(portal, monkeypatch):
    """创建账号时建好 Ask Me Anything，失败不撤销账号

    CreateUser 返回新账号的 Ask Me Anything；建 Ask Me Anything 失败时账号照常创建，结果只带 error。

    :module: security.identity-access
    :design: decision_bd5217d08c09, decision_6eac174d85bf
    :design-point:
    """
    from management_portal import personal_ama, web_isa
    from management_portal.hatg.contracts import InstructionCall, InstructionContext

    provisioned = []
    monkeypatch.setattr(personal_ama, "provision", lambda username: provisioned.append(username) or {"resident_id": "r"})
    kernel = web_isa.build_kernel()
    created = kernel.execute(InstructionCall("CreateUser", {"user_id": "sample_editor", "initial_password": "Sample-Editor-password-1234"}),
                             InstructionContext(caller="CASE-A", entry="cli"))
    assert created.status == "committed", created.error
    assert provisioned == ["sample_editor"]
    assert created.result["personal_ama"] == {"resident_id": "r"}

    def unavailable(_username):
        raise RuntimeError("SocietyService unavailable")

    monkeypatch.setattr(personal_ama, "provision", unavailable)
    result = portal._admin_access_action("CASE-A", {"action": "create", "username": "sample_reviewer", "password": "Sample-Reviewer-password-1234"})
    assert result["personal_ama"] == {"error": "SocietyService unavailable"}
    assert portal._PORTAL_ACCESS.user("sample_reviewer")["configured"]


def test_accepted_invitation_provisions_the_ama(portal, monkeypatch):
    """领取邀请建好账号时同样建好 Ask Me Anything

    邀请领取成功后为新账号建好 Ask Me Anything。

    :module: security.identity-access
    :design: decision_bd5217d08c09, decision_6eac174d85bf
    :design-point:
    """
    from management_portal import http_user_enrollment, personal_ama

    secret = secrets.token_urlsafe(32)
    portal._USER_ENROLLMENT.create_invitation("CASE-A", "sample_editor", hashlib.sha256(secret.encode()).hexdigest())
    provisioned = []
    monkeypatch.setattr(personal_ama, "provision", lambda username: provisioned.append(username) or {})
    monkeypatch.setattr(portal, "_read_bounded_json_object", lambda *_args, **_kwargs: {
        "invitation_secret": secret, "password": "Sample-Editor-password-1234",
    })
    monkeypatch.setattr(portal, "_serve_json", lambda _request, status, payload: (status, payload))
    request = SimpleNamespace(headers={"Content-Type": "application/json"})
    status, payload = http_user_enrollment.accept_invitation(request, None, "")
    assert (status, payload["username"]) == (201, "sample_editor")
    assert provisioned == ["sample_editor"]


def test_successful_login_retries_personal_ama_without_blocking_session(portal, monkeypatch):
    """个人 AMA 创建失败不阻塞登录会话，下次登录时重试创建

    :module: platform.core
    :importance: P2
    """
    from management_portal import personal_ama
    attempted = []
    def provision(username):
        attempted.append(username)
        if len(attempted) == 1:
            raise RuntimeError('temporary Lineage failure')
        return {'resident_id': 'resident_0123456789ab', 'created': True}
    monkeypatch.setattr(personal_ama, 'provision', provision)
    monkeypatch.setattr(portal, '_session_authority', lambda: SimpleNamespace(issue=lambda *a, **kw: 'valid-session'))
    assert portal._issue_session_token('sample_editor') == 'valid-session'
    assert portal._issue_session_token('sample_editor') == 'valid-session'
    assert attempted == ['sample_editor', 'sample_editor']


def test_an_existing_session_finishes_the_personal_assistant_in_the_background(ui_state, monkeypatch):
    """已有会话打开页面时在后台补建个人助手

    会话最长 30 天，不等下次登录：每个进程对同一用户只检查一次，入口已存在时不再初始化；
    失败后在重试间隔之后的页面再试。

    :module: security.identity-access
    :design: decision_bd5217d08c09, decision_44cf587c1721
    :design-point:
    """
    from management_portal import personal_ama
    from management_portal.http_portal_session import PortalSessionHttp

    clock = [100.0]
    monkeypatch.setattr(personal_ama.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(personal_ama, "_session_next_attempt", {})
    outcomes = [{"error": "temporary Lineage failure"}, {"resident_id": "resident_0123456789ab"}]
    attempted = []
    monkeypatch.setattr(personal_ama, "provision_account",
                        lambda username: attempted.append(username) or outcomes[len(attempted) - 1])
    opened = []
    http = PortalSessionHttp(
        respond=lambda _request, status, payload: (status, payload), exception_response=None, read_json=None,
        session_actor=lambda _request: "sample_editor", access=lambda: SimpleNamespace(user=lambda _actor: {}),
        avatar=lambda _actor: None, instance="dev", build_id="",
        preferences=lambda _actor: {}, set_preferences=None, conversation_payload=None, workspace_adapter=None,
        session_opened=lambda actor: opened.append(personal_ama.provision_session(actor)))

    assert http.me(None, None, "")[1]["username"] == "sample_editor"
    opened[-1].join()
    assert attempted == ["sample_editor"]
    http.me(None, None, "")
    assert opened[-1] is None and attempted == ["sample_editor"]
    clock[0] += personal_ama.SESSION_RETRY_SECONDS
    http.me(None, None, "")
    opened[-1].join()
    assert attempted == ["sample_editor", "sample_editor"]
    clock[0] += personal_ama.SESSION_RETRY_SECONDS
    assert personal_ama.provision_session("sample_editor") is None

    ui_state.put("settled", "lineage-workbench.personal-assistant", "primary", {"resident_id": "resident_111111111111"})
    personal_ama.provision_session("settled").join()
    assert attempted == ["sample_editor", "sample_editor"]
