"""执行账号登记、角色与迁移的数据库契约。

:module: execution.providers
:importance: P1
"""
import pytest
from pathlib import Path
from types import SimpleNamespace

from management_portal.hatg.contracts import InstructionError


@pytest.fixture(autouse=True)
def local_account_paths(monkeypatch, tmp_path):
    # These path-contract tests explicitly configure temporary accounts and
    # secrets. An installed site's config must not redirect their writes.
    from management_common import instance_paths
    monkeypatch.setattr(instance_paths.portal_paths, "CONFIG_PATH", tmp_path / "site.json")


def test_registration_versions_and_roles():
    """账号登记保持审计、版本冲突和角色默认回退。"""
    from management_portal.execution import account_registrations as store
    row = store.create_registration({"account_id": "codex_test", "provider": "codex",
                                     "kind": "subscription_login", "route_id": "test",
                                     "config": {"is_default": True}}, "alice")
    assert row["version"] == 1
    assert store.registration("codex_test")["created_by"] == "alice"
    assert store.create_registration(row, "bob") is None
    changed = store.update_registration("codex_test", {"enabled": False}, 1, "bob")
    assert changed["version"] == 2 and changed["enabled"] is False
    with pytest.raises(InstructionError) as error:
        store.update_registration("codex_test", {"enabled": True}, 1, "alice")
    assert error.value.code == "instruction.version_conflict"
    store.update_registration("codex_test", {"enabled": True}, 2, "bob")
    store.update_role("default", "codex_test", "model", "high", 0, "alice")
    role, fallback = store.resolve_role("coding")
    assert role["account_id"] == "codex_test"
    assert fallback == {"requested": "coding", "used": "default"}
    with pytest.raises(InstructionError):
        store.update_role("default", "codex_test", "model", "low", 0, "bob")


def test_marking_another_default_route_moves_the_mark_in_one_update(monkeypatch):
    """把另一个 Codex 账号设为默认路线时在同一事务中取消原标记；ListProviders 报告实际使用的默认路线

    新登记一个默认账号时已有默认仍被拒绝；被取消标记的账号版本递增。只剩一个启用账号时，它就是报告的默认路线。
    """
    from management_portal.execution import account_registrations as store, isa, provider_updates
    from management_portal.execution.account_adapters import CodexRoutes
    monkeypatch.setattr(provider_updates, "provider_runtime_status", lambda refresh=False: {})
    monkeypatch.setattr(provider_updates, "preparation_status", lambda provider: {"status": "missing"})

    def reported():
        providers = isa.list_providers(SimpleNamespace(argument=lambda name: None))["providers"]
        return next(row["default_route_id"] for row in providers if row["provider"] == "codex")

    for name, default in (("one", True), ("two", False)):
        store.create_registration({"account_id": f"codex_{name}", "provider": "codex", "kind": "subscription_login",
                                   "route_id": name, "config": {"is_default": default}}, "alice")
    with pytest.raises(InstructionError) as error:
        store.create_registration({"account_id": "codex_three", "provider": "codex", "kind": "subscription_login",
                                   "route_id": "three", "config": {"is_default": True}}, "alice")
    assert error.value.code == "instruction.conflict"
    assert reported() == "one"
    moved = store.update_registration("codex_two", {"config": {"is_default": True}}, 1, "bob")
    assert moved["config"]["is_default"] is True
    previous = store.registration("codex_one")
    assert (previous["config"]["is_default"], previous["version"], previous["updated_by"]) == (False, 2, "bob")
    assert CodexRoutes().default_for_new_session().account_id == "two" and reported() == "two"
    store.update_registration("codex_two", {"enabled": False}, 2, "bob")
    assert reported() == "one"


def test_registration_rejects_invalid_identity_and_sensitive_config():
    """登记拒绝越界目录 ID、无效 Provider 和任意敏感配置。"""
    from management_portal.execution import account_registrations as store
    base = {"account_id": "codex_test", "provider": "codex", "kind": "subscription_login"}
    for changes in ({"account_id": "../escape"}, {"provider": "missing"},
                    {"config": {"secret": "rejected-placeholder"}}, {"max_concurrency": 0}):
        with pytest.raises(InstructionError):
            store.create_registration({**base, **changes}, "alice")
    assert store.registrations() == []


def test_role_blocks_account_deletion(monkeypatch):
    """仍被 Backend 角色使用的账号不能删除。"""
    from management_portal.execution import account_registrations as store
    store.create_registration({"account_id": "claude_test", "provider": "claude", "kind": "subscription_login"}, "alice")
    store.update_role("default", "claude_test", "model", "", 0, "alice")
    store.update_role("router_fast", "claude_test", "model", "", 0, "alice")
    with pytest.raises(InstructionError) as error:
        store.delete_registration("claude_test")
    assert (error.value.code, error.value.details["reason"]) == ("instruction.conflict", "account.role_in_use")
    assert error.value.details["roles"] == ["default", "router_fast"]
    from management_portal.execution import isa, account_login, account_hosting
    def unexpected(*args, **kwargs):
        pytest.fail("A referenced account must not lose login data or its home")
    monkeypatch.setattr(isa, "account_busy_counts", lambda _: {"session_count": 0, "execution_count": 0})
    monkeypatch.setattr(account_login, "delete_login_data", unexpected)
    monkeypatch.setattr(account_hosting, "remove_account_home", unexpected)
    with pytest.raises(InstructionError) as refused:
        isa.delete_provider_account(SimpleNamespace(argument={"account_id": "claude_test"}.get))
    assert refused.value.details == error.value.details
    assert store.registration("claude_test") is not None
    assert [row["role"] for row in store.roles()] == ["default", "router_fast"]


def test_delete_checks_real_session_bindings_and_scheduler_work():
    """删除检查 Provider 会话绑定和 Scheduler 中尚未结束的执行。"""
    from management_common.portal_database import portal_database
    from management_portal.execution import isa
    from management_portal.execution.agent_data_store import AgentDataStore
    from management_portal.execution.scheduler import Scheduler
    from management_portal.execution.contracts import ExecutionRequest, SessionOptions
    from management_portal.execution.account_adapters import CodexAccounts
    from management_portal.execution.accounts import ExecutionAccounts
    from management_portal.execution import account_registrations as store
    store.create_registration({"account_id": "codex_one", "provider": "codex", "kind": "subscription_login",
                               "route_id": "one", "config": {"is_default": True}}, "alice")
    agent = AgentDataStore()
    session = agent.ensure_provider_session(provider="codex", backend_route_id="one", backend_session_id="session",
                                            agent_id="resident_000000000001", actor_ref="alice")
    agent.bind_provider_session("test:binding", session["provider_session_ref"])
    scheduler = Scheduler(accounts=ExecutionAccounts((CodexAccounts(),)))
    scheduler.submit(ExecutionRequest.new(actor="alice", prompt="busy", service_type="agent_session", provider="codex",
                                          operation="turn", session=SessionOptions(mode="persistent", binding_id="test:busy", backend_route_id="one")))
    row = {"account_id": "codex_one", "provider": "codex", "route_id": "one", "config": {}}
    assert isa.account_busy_counts(row) == {"session_count": 1, "execution_count": 1}
    assert isa.account_busy_counts({**row, "route_id": "two"}) == {"session_count": 0, "execution_count": 0}
    # Callers see the vocabulary code; the Backend page reads the reason and the counts from details.
    from types import SimpleNamespace
    from management_portal.hatg.contracts import normalize_error
    with pytest.raises(InstructionError) as refused:
        isa.delete_provider_account(SimpleNamespace(argument={"account_id": "codex_one"}.get))
    error = normalize_error(refused.value)
    assert (error.code, error.http_status) == ("instruction.conflict", 409)
    assert error.details == {"reason": "account.busy", "session_count": 1, "execution_count": 1}


def test_model_api_secret_is_private_file_only(tmp_path, monkeypatch):
    """一次性密钥只进入权限 600 的文件，登记和返回值只保留路径。"""
    from management_portal.execution import isa
    from management_portal import model_api_store
    monkeypatch.setenv("PORTAL_SECRETS_DIR", str(tmp_path))
    arguments = {"api_id": "sample", "base_url": "https://invalid.example", "default_model": "sample",
                 "secret": "test-only-placeholder"}
    inv = SimpleNamespace(caller="alice", argument=lambda name, default=None: arguments.get(name, default))
    result = isa.create_model_api(inv)
    path = tmp_path / "sample.key"
    assert result["api"]["secret_file"] == str(path)
    assert path.stat().st_mode & 0o777 == 0o600
    assert "test-only-placeholder" not in str(model_api_store.apis()) + str(result)
    arguments = {"api_id": "sample", "changes": {}, "secret": "rotated-test-placeholder"}
    result = isa.update_model_api(inv)
    assert result["version"] == 2 and path.read_text() == "rotated-test-placeholder"




def test_adapters_derive_routes_homes_and_fallback_order(tmp_path, monkeypatch):
    """Codex 路由从登记推导路径、保留容量键与备用顺序。"""
    from management_portal.execution import account_registrations as store
    from management_portal.execution.account_adapters import CodexAccounts, CodexRoutes, ClaudeAccounts
    monkeypatch.setenv("PORTAL_ACCOUNTS_ROOT", str(tmp_path / "accounts"))
    monkeypatch.setenv("PORTAL_INSTANCES_ROOT", str(tmp_path / "instances"))
    for name, config in (("one", {"is_default": True, "fallback_account_ids": ["codex_two", "codex_zero"]}),
                         ("two", {}), ("zero", {})):
        store.create_registration({"account_id": f"codex_{name}", "provider": "codex", "kind": "subscription_login",
                                   "route_id": name, "config": config, "max_concurrency": 12}, "alice")
    routes = CodexRoutes()
    assert [r.account_id for r in routes.candidates(allow_fallback=True)] == ["one", "two", "zero"]
    assert routes.get("codex_one").codex_home == tmp_path / "accounts/codex/codex_one"
    assert routes.get("one").socket_path == tmp_path / "instances/sockets/codex-codex_one.sock"
    account = CodexAccounts().resolve("", {})
    assert (account.account_id, account.route_id, account.capacity_key, account.max_concurrency) == ("codex_one", "one", "codex:one", 12)
    store.create_registration({"account_id": "claude_test", "provider": "claude", "kind": "subscription_login",
                               "route_id": "claude_test", "config": {"models": [{"id": "example"}], "default_model": "example"}}, "alice")
    assert ClaudeAccounts().accounts()[0].account_id == "claude_test"
    from management_portal.claude_provider_catalog import profile, config_directory, catalog
    assert config_directory(profile("claude_test")) == tmp_path / "accounts/claude/claude_test"
    assert catalog("claude_test")["models"] == ["example"]


def test_create_resident_resolves_role_and_keeps_explicit_settings(monkeypatch):
    """创建时解析角色并保存回退标记，显式账号和模型优先。"""
    from management_portal.execution import account_registrations as store
    from management_portal.society_service.isa import residents
    from management_portal.execution.resident_runtime_settings import normalize_settings
    store.create_registration({"account_id": "codex_one", "provider": "codex", "kind": "subscription_login", "route_id": "one"}, "alice")
    store.update_role("default", "codex_one", "role-model", "high", 0, "alice")
    created = []
    def create(lineage, payload, actor):
        created.append(payload)
        return {"resident": {"resident_id": "resident_000000000001"}}
    monkeypatch.setattr(residents, "lineage_resident_nodes", lambda: SimpleNamespace(create_lineage_resident=create))
    monkeypatch.setattr(residents, "_family_to_join", lambda *a: "")
    monkeypatch.setattr(residents, "placement", lambda *a: {})
    args = {"lineage_id": "lineage_000000000001", "title": "test", "backend_role": "missing", "account_id": "two", "settings": {"model": "explicit-model"}}
    inv = SimpleNamespace(caller="alice", argument=lambda name, default=None: args.get(name, default))
    result = residents.create_resident(inv)
    assert created[0]["backend_route_id"] == "two"
    settings = normalize_settings(created[0]["runtime_settings"])
    from management_portal.society_service.resident_runtime_payload import runtime_settings_updates_from_payload
    assert runtime_settings_updates_from_payload(created[0]["runtime_settings"])["backend_role"] == "missing"
    from management_portal.execution.provider_catalog import AGENT_PROVIDERS
    AGENT_PROVIDERS["codex"].runtime_adapter().validate_requested_fields(AGENT_PROVIDERS["codex"], created[0]["runtime_settings"])
    from management_portal.execution.resident_runtime_settings import merge_settings
    assert merge_settings({}, created[0]["runtime_settings"])["backend_role_fallback"] == {"requested": "missing", "used": "default"}
    assert settings["model"] == "explicit-model" and settings["reasoning_effort"] == "high"
    assert result["backend_role_fallback"] == settings["backend_role_fallback"] == {"requested": "missing", "used": "default"}


def test_disabling_account_prevents_queued_work_dispatch():
    """停用立即阻止该账号的排队工作，重新启用后可继续派发。"""
    from management_portal.execution import account_registrations as store
    from management_portal.execution.account_adapters import CodexAccounts
    from management_portal.execution.accounts import ExecutionAccounts
    from management_portal.execution.scheduler import Scheduler
    from management_portal.execution.contracts import ExecutionRequest, SessionOptions
    store.create_registration({"account_id": "codex_one", "provider": "codex", "kind": "subscription_login",
                               "route_id": "one", "config": {"is_default": True}}, "alice")
    accounts = ExecutionAccounts((CodexAccounts(),))
    scheduler = Scheduler(accounts=accounts, dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    scheduler.register_worker(worker_id="test:disable", build_id="test", capabilities=["agent_session:codex"])
    scheduler.submit(ExecutionRequest.new(actor="alice", prompt="queued", service_type="agent_session", provider="codex",
                                          operation="turn", session=SessionOptions(mode="persistent", binding_id="test:disable", backend_route_id="one")))
    store.update_registration("codex_one", {"enabled": False}, 1, "alice")
    assert not scheduler.claim(worker_id="test:disable", capabilities=["agent_session:codex"])["claimed"]
    assert accounts.configuration("codex", "one")["account_status"] == "disabled"
    store.update_registration("codex_one", {"enabled": True}, 2, "alice")
    assert scheduler.claim(worker_id="test:disable", capabilities=["agent_session:codex"])["claimed"]




def test_new_registration_refreshes_scheduler_defaults_without_overwriting_settings():
    """长驻 Scheduler 发现新账号的初始容量与配额，并保留已保存的调度设置。"""
    from management_portal.execution import account_registrations as store
    from management_portal.execution.accounts import default_execution_accounts
    from management_portal.execution.scheduler import Scheduler
    store.create_registration({"account_id": "codex_one", "provider": "codex", "kind": "subscription_login",
                               "route_id": "one", "config": {"is_default": True}, "max_concurrency": 2}, "alice")
    accounts = default_execution_accounts()
    scheduler = Scheduler(accounts=accounts)
    scheduler.configure_capacity("codex:one", 4, 0, 0, actor="alice")
    store.create_registration({"account_id": "codex_two", "provider": "codex", "kind": "subscription_login",
                               "route_id": "two", "max_concurrency": 3}, "alice")
    store.update_registration("codex_one", {"max_concurrency": 1}, 1, "alice")
    accounts._refreshed_at = 0
    with scheduler.changes.connect() as connection:
        assert scheduler.capacity.capacity_limit(connection, "codex:two") == 3
        assert scheduler.capacity.capacity_limit(connection, "codex:one") == 4
    assert "codex:two" in scheduler.quota_capacity_keys


def test_disabling_registration_keeps_running_provider_process(monkeypatch):
    """停用阻止派发但不停止正在为既有执行服务的 Provider 进程。"""
    from management_portal.execution import account_registrations as store, account_hosting as host, isa
    store.create_registration({"account_id": "codex_one", "provider": "codex", "kind": "subscription_login",
                               "route_id": "one"}, "alice")
    stopped = []
    monkeypatch.setattr(host, "stop_codex_instance", stopped.append)
    monkeypatch.setattr(isa, "_registration_public", lambda row, actor: row)
    args = {"account_id": "codex_one", "changes": {"enabled": False}}
    result = isa.update_provider_account_registration(SimpleNamespace(caller="alice", argument=args.get,
                                                                      expected_version_for=lambda account_id: 1))
    assert not result["registration"]["enabled"] and stopped == []




def test_runtime_catalog_handles_empty_or_disabled_accounts():
    """空站点和停用账号不破坏 Backend 目录，既有会话仍可读取停用账号能力。"""
    from management_portal.execution import account_registrations as store
    from management_portal.execution.accounts import default_execution_accounts
    from management_portal.claude_provider_catalog import profile
    catalog = default_execution_accounts().runtime_catalog(include_backends=True)
    assert all(not row["routes"] for row in catalog["backends"])
    store.create_registration({"account_id": "codex_one", "provider": "codex", "kind": "subscription_login",
                               "route_id": "one", "enabled": False, "config": {"is_default": True}}, "alice")
    store.create_registration({"account_id": "claude_one", "provider": "claude", "kind": "subscription_login",
                               "route_id": "claude_one", "enabled": False,
                               "config": {"models": [{"id": "sample"}], "default_model": "sample"}}, "alice")
    accounts = default_execution_accounts()
    assert all(not row["routes"] for row in accounts.runtime_catalog(include_backends=True)["backends"])
    assert accounts.runtime_catalog("claude_one")["models"] == ["sample"]
    assert accounts.configuration("claude", "claude_one")["account_status"] == "disabled"
    with pytest.raises(ValueError):
        profile("claude_one")
















def test_registration_and_role_updates_read_version_from_call_envelope(monkeypatch):
    """登记与角色更新从信封读取标量或对象版本：省略时直接更新，过期时返回版本冲突。"""
    from management_portal.execution import account_registrations as store, isa
    from management_portal.hatg.catalog import instruction_catalog
    from management_portal.hatg.contracts import InstructionCall
    from management_portal.hatg.kernel import Invocation
    catalog = instruction_catalog()
    for name in ("UpdateProviderAccountRegistration", "UpdateBackendRole"):
        assert "expected_version" not in catalog[name].parameter_map
    assert "backend_role" in catalog["UpdateBackendRole"].parameter_map
    assert "role" not in catalog["UpdateBackendRole"].parameter_map
    store.create_registration({"account_id": "codex_test", "provider": "codex", "kind": "subscription_login",
                               "route_id": "test"}, "alice")
    monkeypatch.setattr(isa, "_registration_public", lambda row, actor: row)
    def invoke(name, arguments, expected):
        return Invocation(catalog[name], InstructionCall(name, arguments, expected_version=expected),
                          SimpleNamespace(caller="alice"), "test-version-envelope")
    args = {"account_id": "codex_test", "changes": {"max_concurrency": 3}}
    result = isa.update_provider_account_registration(invoke("UpdateProviderAccountRegistration", args, {"codex_test": 1}))
    assert result["registration"]["version"] == 2
    with pytest.raises(InstructionError) as conflict:
        isa.update_provider_account_registration(invoke("UpdateProviderAccountRegistration", args, 1))
    assert conflict.value.code == "instruction.version_conflict"
    assert conflict.value.details == {"object_id": "codex_test", "expected_version": 1, "current_version": 2}
    result = isa.update_provider_account_registration(invoke("UpdateProviderAccountRegistration", args, None))
    assert result["registration"]["version"] == 3
    with pytest.raises(InstructionError) as malformed:
        isa.update_provider_account_registration(invoke("UpdateProviderAccountRegistration", args, "3"))
    assert malformed.value.code == "instruction.invalid_arguments"
    args = {"backend_role": "coding", "account_id": "codex_test", "model": "sample"}
    role = isa.update_backend_role(invoke("UpdateBackendRole", args, 0))["role"]
    assert role["role"] == "coding" and role["version"] == 1
    assert isa.update_backend_role(invoke("UpdateBackendRole", args, {"coding": 1}))["role"]["version"] == 2
    with pytest.raises(InstructionError) as conflict:
        isa.update_backend_role(invoke("UpdateBackendRole", args, 0))
    assert conflict.value.code == "instruction.version_conflict"
    assert conflict.value.details == {"object_id": "coding", "expected_version": 0, "current_version": 2}
    assert isa.update_backend_role(invoke("UpdateBackendRole", args, None))["role"]["version"] == 3


def test_only_model_api_instructions_compare_model_api_versions(monkeypatch):
    """执行服务只对模型接口指令按模型接口版本比较，同名的执行账号或 Backend 角色不受影响。"""
    from management_portal.execution import isa
    from management_portal.hatg.catalog import instruction_catalog
    from management_portal.hatg.contracts import InstructionCall
    from management_portal.hatg.kernel import Invocation
    catalog = instruction_catalog()
    monkeypatch.setattr(isa.model_api_store, "api_version", lambda api_id: 5 if api_id == "shared_name" else None)
    def invoke(name, arguments):
        return Invocation(catalog[name], InstructionCall(name, arguments, expected_version=1),
                          SimpleNamespace(caller="alice"), "test-version-scope")
    assert isa.version_of(invoke("UpdateModelApi", {"api_id": "shared_name", "changes": {}}), "shared_name") == 5
    assert isa.version_of(invoke("UpdateProviderAccountRegistration",
                                 {"account_id": "shared_name", "changes": {}}), "shared_name") is None
    assert isa.version_of(invoke("UpdateBackendRole",
                                 {"backend_role": "shared_name", "account_id": "codex_test"}), "shared_name") is None


def test_reenable_starts_registered_codex_instance(tmp_path, monkeypatch):
    """恢复登记时启动该账号的模板实例。"""
    from management_portal.execution import account_registrations as store, account_hosting as host, isa
    monkeypatch.setenv("PORTAL_ACCOUNTS_ROOT", str(tmp_path / "accounts"))
    home = host.account_home("codex", "codex_one")
    home.parent.mkdir(parents=True)
    home.mkdir()
    store.create_registration({"account_id": "codex_one", "provider": "codex", "kind": "subscription_login",
                               "route_id": "one", "enabled": False}, "alice")
    started = []
    monkeypatch.setattr(host, "start_codex_instance", started.append)
    monkeypatch.setattr(isa, "_registration_public", lambda row, actor: row)
    args = {"account_id": "codex_one", "changes": {"enabled": True}}
    result = isa.update_provider_account_registration(SimpleNamespace(caller="alice", argument=args.get,
                                                                      expected_version_for=lambda account_id: 1))
    assert result["registration"]["enabled"]
    assert started == ["codex_one"]
