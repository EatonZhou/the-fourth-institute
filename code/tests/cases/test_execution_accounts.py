from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal.execution.accounts import ExecutionAccount, ExecutionAccounts, default_execution_accounts
from management_portal.execution.account_adapters import CodexAccounts
from management_portal.execution.account_monitor import AccountMonitor
from management_portal.execution.contracts import ExecutionRequest, SessionOptions
from management_portal.execution.scheduler import Scheduler
from management_portal.execution.service import ExecutionServiceServer
from management_portal.execution import codex_runtime_settings
from tests.cases.model_api_test_support import register_accounts


class Adapter:
    provider = "claude"

    def accounts(self):
        return tuple(ExecutionAccount(name, self.provider, name, f"account:{name}", 1, "reserve")
                     for name in ("test_alpha", "test_beta"))

    def resolve(self, route_id, options, *, bound=False):
        return next(account for account in self.accounts() if account.route_id == route_id)

    def observe(self, account, *, force=False):
        return {"status": "ok", "rate_limits": [{"id": "week", "label": "每周", "remaining_percent": 80}],
                "quota": {"observed_at": __import__('time').time(), "status": "available", "remaining_percent": 80, "windows": []}}


def request(account):
    return ExecutionRequest.new(actor="test-actor", prompt="account contract", service_type="agent_session", provider="claude",
        operation="turn", session=SessionOptions(mode="persistent", binding_id=f"test:{__import__('uuid').uuid4()}", backend_route_id=account))


def test_accounts_use_same_capacity_interval_and_postgres_settings():
    """执行账号按各自并发上限调度，scheduler.capacity.configure 设置的并发和派发间隔持久生效"""
    accounts = ExecutionAccounts((Adapter(),))
    scheduler = Scheduler(accounts=accounts, dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    scheduler.register_worker(worker_id="test:accounts", build_id="test", capabilities=["agent_session:claude"])
    for name in ("test_alpha", "test_alpha", "test_beta"):
        scheduler.submit(request(name))
    claim = lambda: scheduler.claim(worker_id="test:accounts", capabilities=["agent_session:claude"])
    assert claim()["execution"]["request"]["session"]["backend_route_id"] == "test_alpha"
    assert claim()["execution"]["request"]["session"]["backend_route_id"] == "test_beta"
    assert not claim()["claimed"]
    # Same generic RPC updates either account; no native daemon is involved.
    server = object.__new__(ExecutionServiceServer)
    server.accounts, server.scheduler = accounts, scheduler
    result = server.dispatch("scheduler.capacity.configure", {"account_id": "test_alpha", "max_concurrency": 2,
        "dispatch_interval_min_seconds": 20, "dispatch_interval_max_seconds": 20, "actor": "admin"})
    assert result["account_id"] == "test_alpha"
    settings = Scheduler(accounts=accounts).health()["capacity_partitions"]["account:test_alpha"]
    assert settings["limit"] == 2
    assert settings["dispatch_interval_min_seconds"] == settings["dispatch_interval_max_seconds"] == 20
    assert claim()["reason"] == "dispatch_interval"
    server.dispatch("scheduler.capacity.configure", {"account_id": "test_alpha", "max_concurrency": 2,
        "dispatch_interval_min_seconds": 0, "dispatch_interval_max_seconds": 0, "actor": "admin"})
    assert claim()["claimed"]
    with pytest.raises(ValueError, match="unknown Execution account"):
        server.dispatch("scheduler.capacity.configure", {"account_id": "missing"})


def test_api_and_native_provider_share_the_same_account_concurrency():
    """同一账号的 API 补全与原生 Provider 轮次共享并发上限

    :design: decision_d40f59f5508f
    :design-point:
    """
    accounts = ExecutionAccounts((Adapter(),))
    scheduler = Scheduler(accounts=accounts, dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    capabilities = ["agent_session:claude", "chat_completion"]
    scheduler.register_worker(worker_id="test:shared-account", build_id="test", capabilities=capabilities)
    native = request("test_alpha")
    api = ExecutionRequest.new(actor="test", prompt="api", service_type="chat_completion",
                               provider="test_alpha", operation="generate")
    other = request("test_beta")
    for item in (native, api, other):
        scheduler.submit(item)
    claim = lambda: scheduler.claim(worker_id="test:shared-account", capabilities=capabilities)
    assert claim()["execution"]["execution_id"] == native.execution_id
    assert claim()["execution"]["execution_id"] == other.execution_id
    assert not claim()["claimed"]
    assert scheduler.snapshot(api.execution_id)["status"] == "queued"


def test_codex_runtime_catalog_rpc_uses_execution_defaults(monkeypatch):
    """codex 运行时目录 RPC 用 Portal 默认模型和推理强度覆盖原生默认值"""
    from management_portal import codex_capabilities, codex_app_server_client

    raw = {"source": "model/list", "models": ["test-model", "limited-model"],
           "default_model": "test-model", "default_reasoning_effort": "low",
           "efforts_by_model": {"test-model": ["low", "high"], "limited-model": ["low"]},
           "default_efforts_by_model": {"test-model": "low", "limited-model": "low"}}
    monkeypatch.setattr(codex_runtime_settings, "PORTAL_DEFAULT_MODEL", "test-model")
    monkeypatch.setattr(codex_runtime_settings, "PORTAL_DEFAULT_REASONING_EFFORT", "high")
    monkeypatch.setattr(codex_app_server_client, "client_for_socket", lambda path: object())
    monkeypatch.setattr(codex_capabilities, "runtime_catalog", lambda client, account: raw)
    routes = {name: SimpleNamespace(account_id=name, max_concurrency=1, socket_path=name)
              for name in ("one", "five")}
    registry = SimpleNamespace(routes=lambda: tuple(routes.values()), get=routes.__getitem__,
                               default_for_new_session=lambda **kw: routes["one"])
    server = object.__new__(ExecutionServiceServer)
    server.accounts = ExecutionAccounts((CodexAccounts(registry),))
    for account_id in routes:
        catalog = server.dispatch("runtime.catalog", {"account_id": account_id})
        assert catalog["default_model"] == "test-model"
        assert catalog["default_reasoning_effort"] == "high"
        assert catalog["default_efforts_by_model"] == {"test-model": "high", "limited-model": "low"}
    assert raw["default_reasoning_effort"] == "low"


@pytest.fixture
def model_api_catalog(monkeypatch):
    from management_portal.execution import account_adapters, provider_catalog

    native = Adapter()
    native.catalog = lambda account: {"models": ["native-model"], "default_model": "native-model"}
    monkeypatch.setattr(provider_catalog, "AGENT_PROVIDERS", {
        "claude": provider_catalog.AgentProvider("claude", "Claude Code", frozenset({"turn"}),
            ("model", "reasoning_effort"), account_adapter="TestAccounts"),
    })
    monkeypatch.setattr(account_adapters, "TestAccounts", lambda **kwargs: native, raising=False)
    entries = [
        {"id": "test_alpha", "kind": "openai_compatible_chat", "default_model": "api-model",
         "models": [{"id": "api-model"}, {"id": "hidden-model", "enabled": False}]},
        {"id": "test_beta", "kind": "claude_code_subscription"},
        {"id": "api_a", "kind": "openai_compatible_chat", "default_model": "other-model",
         "models": [{"id": "other-model"}]},
        {"id": "jev_a", "kind": "typesafe_system_one", "provider": "typesafe",
         "default_model": "jev-latest", "models": [{"id": "jev-latest"}, {"id": "jev-preview"}]},
        {"id": "disabled", "kind": "openai_compatible_chat", "enabled": False},
        {"id": "unknown_protocol", "kind": "future_protocol"},
    ]
    registry = SimpleNamespace(entries=lambda: entries,
                               get=lambda key: next(row for row in entries if row["id"] == key))
    register_accounts(*({"account_id": key, "provider": "provider_api", "kind": "api_profile",
                         "config": {"api_id": key}}
                        for key in ("test_alpha", "api_a", "jev_a")))
    return default_execution_accounts(provider_registry=registry)


def test_model_api_catalog_includes_shared_native_account_without_duplicate_capacity(model_api_catalog):
    """模型 API 目录列出与原生共用的账号，账号容量只计一份"""
    accounts = model_api_catalog
    catalog = accounts.runtime_catalog(include_backends=True)
    api = next(row for row in catalog["backends"] if row["backend"] == "provider_api")
    assert [route["account_id"] for route in api["routes"]] == ["test_alpha", "api_a", "jev_a"]
    jev = accounts.runtime_catalog("jev_a", backend="provider_api")
    assert jev["api_protocol"] == "typesafe_system_one"
    assert jev["models"] == ["jev-latest", "jev-preview"]
    assert jev["default_model"] == "jev-latest"
    assert jev["operations"] == []
    shared = api["routes"][0]
    assert shared["backend_route_id"] == "test_alpha"
    assert shared["models"] == ["api-model"]
    assert shared["default_model"] == "api-model"
    assert shared["operations"] == []
    assert shared["runtime_capabilities"]["harness_capabilities"] == []
    assert "permissions" not in shared["runtime_settings"]
    native = accounts.runtime_catalog("test_alpha", backend="claude")
    assert native["backend"] == "claude"
    assert native["models"] == ["native-model"]
    assert sum(a.account_id == "test_alpha" for a in accounts.accounts()) == 1
    direct = accounts.for_request({"service_type": "chat_completion", "provider": "test_alpha"})
    assert direct == accounts.resolve("claude", "test_alpha")
    assert direct.capacity_key == "account:test_alpha"


def test_runtime_catalog_rpc_respects_explicit_api_backend_for_shared_account(model_api_catalog):
    """共用账号的运行时目录 RPC 按显式指定的 provider_api 后端返回"""
    server = object.__new__(ExecutionServiceServer)
    server.accounts = model_api_catalog
    api = server.dispatch("runtime.catalog", {"account_id": "test_alpha", "backend": "provider_api"})
    assert api["backend"] == "provider_api"
    assert api["models"] == ["api-model"]
    assert api["efforts_by_model"] == {}
    assert model_api_catalog.runtime_catalog("api_a")["default_model"] == "other-model"


@pytest.mark.parametrize("account_id", ["test_beta", "disabled", "unknown_protocol"])
def test_model_api_catalog_rejects_unsupported_routes(model_api_catalog, account_id):
    """模型 API 目录拒绝订阅类、已停用或未知协议的账号"""
    with pytest.raises(ValueError, match="enabled Model API"):
        model_api_catalog.runtime_catalog(account_id, backend="provider_api")


def test_native_names_preserve_bound_route_and_capacity_settings():
    """codex 账号的公开名和原路线名解析到同一账号与容量键"""
    route = SimpleNamespace(account_id="five", max_concurrency=12)
    registry = SimpleNamespace(routes=lambda: (route,), get=lambda name: route,
                               default_for_new_session=lambda **kw: route)
    accounts = ExecutionAccounts((CodexAccounts(registry),))
    old = accounts.resolve("codex", "five")
    new = accounts.resolve("codex", "codex_five")
    assert old == new
    assert new.account_id == "codex_five"
    assert new.route_id == "five"
    assert new.capacity_key == "codex:five"
    assert accounts.get("five") == accounts.get("codex_five")


def test_shared_quota_policy_and_status_for_an_adapter():
    """账号监视器为适配器账号汇报额度保留状态，原生读取失败时限额标记为过期"""
    accounts = ExecutionAccounts((Adapter(),))
    scheduler = Scheduler(accounts=accounts, quota_reserve_enabled=True, dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    monitor = AccountMonitor(accounts, scheduler)
    for account in accounts.accounts():
        monitor.refresh_account(account)
    snapshot = monitor.snapshot()
    assert [item["account_id"] for item in snapshot["accounts"]] == ["test_alpha", "test_beta"]
    assert all(item["capabilities"]["scheduler_settings"] for item in snapshot["accounts"])
    assert all(item["quota_reserve"]["status"] == "available" for item in snapshot["accounts"])
    assert all("quota" not in item for item in snapshot["accounts"])
    adapter = accounts.adapters["claude"]
    adapter.observe = Mock(side_effect=RuntimeError("native unavailable"))
    monitor.refresh_account(accounts.get("test_alpha"))
    assert monitor.snapshot()["accounts"][0]["rate_limits"][0]["stale"]


def test_chat_header_uses_execution_account_name_without_provider_prefix():
    """Chat 标题栏显示执行账号名，不带 Provider 前缀"""
    from management_portal.resident_chat_projection import project_session
    result = project_session({"resident_id": "resident_123456789abc", "resident": {"backend": "claude"},
                              "execution_account": {"account_id": "test_alpha"}})
    parts = result["chat_workspace"]["header"]["subtitle_parts"]
    assert parts[0]["text"] == "账号 test_alpha"


def test_completion_account_is_shared_by_rpc_resident_query_and_chat(monkeypatch, tmp_path):
    """API Resident 的补全账号在 accounts.resolve、Resident 查询和 Chat 标题中一致"""
    from management_portal.execution import accounts as account_module
    from management_portal.execution.account_adapters import ApiProfileAccounts
    from management_portal.ai_provider_registry import shared_ai_provider_registry
    from management_portal.society_service import resident_backend, resident_protocol, result_contracts
    from management_portal.resident_chat_projection import project_session

    registration, = register_accounts({"account_id": "future_primary", "provider": "provider_api",
                                       "kind": "api_profile", "config": {"api_id": "future_primary"}})
    accounts = ExecutionAccounts((Adapter(), ApiProfileAccounts({"id": "future_primary"}, registration)))
    monkeypatch.setattr(account_module, "default_execution_accounts", lambda **kwargs: accounts)
    monkeypatch.setattr(shared_ai_provider_registry, "get", lambda key: {
        "id": key, "provider": "future_vendor", "enabled": True,
        "api_key_file": "/private/credentials", "models": ["example-model"],
    })
    lineage_folder = tmp_path / "lineage_123456789abc"
    record = {"resident_id": "resident_123456789abc", "lineage_id": "lineage_123456789abc",
              "backend": "provider_api", "title": "Completion",
              "backend_route_id": "future_primary", "execution_settings": {"model": "example-model"},
              "workspace_path": str(lineage_folder), "owned_workspace_id": "workspace_123456789abc",
              "owned_workspace": {"workspace_id": "workspace_123456789abc",
                                  "path": str(lineage_folder / "residents" / "resident_123456789abc" / "workspace")},
              "workspace_bindings": []}
    monkeypatch.setattr(resident_backend, "session_record", lambda record: {})
    monkeypatch.setattr(resident_protocol, "_target", lambda rid, actor: SimpleNamespace(resident_id=rid, record=record))
    server = object.__new__(ExecutionServiceServer)
    server.accounts = accounts
    expected = {"account_id": "future_primary", "provider": "future_primary",
                "backend_route_id": "", "capacity_key": "account:future_primary", "quota_protection_enabled": True,
                "enabled": True, "account_status": "enabled"}
    assert server.dispatch("accounts.resolve", {"provider": "future_primary"}) == expected
    query = resident_protocol.get_resident(record["resident_id"], "test-actor")
    result_contracts.validate_result("resident", query)
    assert query["account"] == expected["account_id"]
    projection = project_session({"resident_id": record["resident_id"], "resident": record,
                                  "execution_account": resident_backend.execution_account(record)})
    assert projection["chat_workspace"]["header"]["subtitle_parts"][0]["text"] == "账号 future_primary"
    assert accounts.for_request({"service_type": "chat_completion", "provider": "future_primary"}) == accounts.get("future_primary")


def test_api_completions_obey_saved_account_capacity_interval_and_close_threshold():
    """API 补全按账号保存的并发、派发间隔和余额关停阈值调度"""
    import time
    from management_portal.execution.account_adapters import ApiProfileAccounts
    registrations = register_accounts(*({"account_id": key, "provider": "provider_api", "kind": "api_profile",
                                         "config": {"api_id": key}} for key in ("api_a", "api_b")))
    accounts = ExecutionAccounts(tuple(ApiProfileAccounts({"id": row["config"]["api_id"]}, row)
                                        for row in registrations))
    scheduler = Scheduler(accounts=accounts, quota_reserve_enabled=True, dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    server = object.__new__(ExecutionServiceServer)
    server.accounts, server.scheduler = accounts, scheduler
    caps = ["chat_completion"]
    scheduler.register_worker(worker_id="api-settings", build_id="test", capabilities=caps)
    for key in ("api_a", "api_b"):
        scheduler.update_account_quota(key, {"status": "available", "complete": True,
            "remaining_balance": 10, "currency": "USD", "observed_at": time.time(), "max_age_seconds": 120})
        server.dispatch("scheduler.capacity.configure", {"account_id": key, "max_concurrency": 1,
            "dispatch_interval_min_seconds": 0, "dispatch_interval_max_seconds": 0,
            "close_threshold": {"value": 0, "unit": "USD"}, "actor": "admin"})
    for key in ("api_a", "api_a", "api_b"):
        scheduler.submit(ExecutionRequest.new(actor="test", prompt="test", service_type="chat_completion", provider=key, operation="generate"))
    claim = lambda: scheduler.claim(worker_id="api-settings", capabilities=caps)
    assert claim()["execution"]["request"]["provider"] == "api_a"
    assert claim()["execution"]["request"]["provider"] == "api_b"
    assert not claim()["claimed"]
    settings = {"account_id": "api_a", "max_concurrency": 2, "actor": "admin",
                "dispatch_interval_min_seconds": 60, "dispatch_interval_max_seconds": 60}
    server.dispatch("scheduler.capacity.configure", settings)
    assert claim()["reason"] == "dispatch_interval"
    server.dispatch("scheduler.capacity.configure", {**settings, "dispatch_interval_min_seconds": 0,
        "dispatch_interval_max_seconds": 0, "close_threshold": {"value": 10, "unit": "USD"}})
    assert claim()["reason"] == "quota_reserve"
    restored = Scheduler(accounts=accounts, quota_reserve_enabled=True)
    saved = restored.health()["capacity_partitions"]["account:api_a"]
    assert saved["limit"] == 2
    assert saved["close_threshold"]["value"] == 10
    server.dispatch("scheduler.capacity.configure", {**settings, "dispatch_interval_min_seconds": 0,
        "dispatch_interval_max_seconds": 0, "close_threshold": {"value": 0, "unit": "USD"}})
    assert claim()["claimed"]


def test_direct_api_resident_uses_the_same_quota_account_lookup():
    """直连 API 的 Resident 按 backend_route_id 查到同一额度账号"""
    from management_portal.society_service.resident_intake_domain import _resident_quota_account_resolver
    resolver = _resident_quota_account_resolver([
        {"account_id": "api_a", "provider": "api_a", "backend_route_id": ""},
        {"account_id": "shared_profile", "provider": "native_adapter", "backend_route_id": "shared_profile"}])
    for key in ("api_a", "shared_profile"):
        record = {"backend": "provider_api", "backend_route_id": key}
        assert resolver(record, {}) == key


def test_default_accounts_add_api_profiles_without_duplicating_native_accounts(monkeypatch):
    """默认账号列表来自登记，与原生账号同名时不重复，也不自动发现其他 API"""
    from management_portal.execution import account_adapters, provider_catalog
    from management_portal.ai_provider_registry import shared_ai_provider_registry
    monkeypatch.setattr(provider_catalog, "AGENT_PROVIDERS", {
        "claude": SimpleNamespace(account_adapter="TestAccounts", provider="claude")})
    monkeypatch.setattr(account_adapters, "TestAccounts", lambda **kwargs: Adapter(), raising=False)
    entries = [{"id": key, "provider": "future_vendor", "enabled": enabled}
               for key, enabled in (("test_alpha", True), ("api_a", True), ("unregistered_api", True), ("disabled", False))]
    register_accounts(*({"account_id": key, "provider": "provider_api", "kind": "api_profile",
                         "config": {"api_id": key}} for key in ("test_alpha", "api_a")))
    monkeypatch.setattr(shared_ai_provider_registry, "entries", lambda: entries)
    accounts = default_execution_accounts(provider_registry=SimpleNamespace(entries=lambda: entries))
    assert [a.account_id for a in accounts.accounts()] == ["test_alpha", "test_beta", "api_a"]
    assert accounts.for_request({"service_type": "chat_completion", "provider": "test_alpha"}) == accounts.get("test_alpha")
    assert accounts.get("test_alpha").max_concurrency == 1
    assert accounts.get("api_a").billing_account_id == "api_a"
    catalog = accounts.usage_catalog()
    assert {"provider": "future_vendor", "account_id": "api_a"} in catalog
    assert all(row["provider"] != "api_a" for row in catalog)


@pytest.mark.parametrize("enabled", [True, False])
def test_configuration_preserves_native_routes_and_does_not_invent_missing_profiles(monkeypatch, enabled):
    """原生路线的账号配置不查模型 API 注册表，不存在的配置返回空"""
    from management_portal.ai_provider_registry import shared_ai_provider_registry
    lookup = Mock(side_effect=ValueError("unavailable or disabled profile"))
    monkeypatch.setattr(shared_ai_provider_registry, "get", lookup)
    register_accounts({"account_id": "test_alpha", "provider": "claude", "kind": "subscription_login",
                       "route_id": "test_alpha", "enabled": enabled, "max_concurrency": 1})
    accounts = ExecutionAccounts((Adapter(),))
    assert accounts.configuration("claude", "test_alpha") == {
        "account_id": "test_alpha", "provider": "claude", "backend_route_id": "test_alpha",
        "capacity_key": "account:test_alpha", "quota_protection_enabled": True,
        "enabled": enabled, "account_status": "enabled" if enabled else "disabled"}
    lookup.assert_not_called()
    assert accounts.configuration("missing_profile") == {}


def test_registered_backend_catalog_provides_accounts_without_common_layer_changes(monkeypatch):
    """新注册的后端目录无需修改通用层即可提供默认路线账号"""
    from management_portal.execution.account_adapters import ConfiguredAccounts
    from management_portal.execution import account_registrations
    definition = SimpleNamespace(provider="future", default_route="primary", default_concurrency=8,
        routes=Mock(side_effect=AssertionError("account routes come from registrations")))
    read = Mock(return_value=[{"account_id": "future_primary", "provider": "future", "kind": "api_profile",
                               "route_id": "primary", "config": {"api_id": "future_api"}, "max_concurrency": 8}])
    monkeypatch.setattr(account_registrations, "registrations", read)
    accounts = ExecutionAccounts((ConfiguredAccounts(definition),))
    read.assert_called_once_with("future")
    definition.routes.assert_not_called()
    account = accounts.resolve("future")
    assert account.account_id == "future_primary"
    assert accounts.for_request({"service_type": "agent_session", "provider": "future"}) == account
    assert accounts.resolve("future", "future_primary") == account


def test_a_dsh_route_runs_as_many_turns_at_once_as_claude_code_and_kimi_code():
    """DSH 路线的默认并发与 Claude Code、Kimi Code 一致

    DSH 每个会话有独立进程；路线账号默认并发为 8，与 Claude Code、Kimi Code 相同，不再让整个 Portal 同时只运行一个 DSH 轮次。

    :design: decision_276105b5d67b
    """
    # Each DSH session has its own process; one route used to run one DSH turn at a time in all of Portal.
    from management_portal.execution.account_adapters import ConfiguredAccounts
    from management_portal.execution.provider_catalog import AGENT_PROVIDERS
    from tests.cases.model_api_test_support import DEEPSEEK_PRIMARY, register_apis

    register_apis({**DEEPSEEK_PRIMARY, "routes": [{"backend": "dsh", "route_id": "deepseek"}]})
    register_accounts({"account_id": "dsh_deepseek", "provider": "dsh", "kind": "api_profile",
                       "route_id": "deepseek", "config": {"api_id": DEEPSEEK_PRIMARY["id"]}})
    accounts = ConfiguredAccounts(AGENT_PROVIDERS["dsh"]).accounts()
    assert accounts and {account.max_concurrency for account in accounts} == {
        AGENT_PROVIDERS["claude"].default_concurrency, AGENT_PROVIDERS["kimi"].default_concurrency} == {8}


@pytest.mark.parametrize("limit", [True, 1.5, 0, 257])
def test_scheduler_rejects_invalid_shared_account_limit(limit):
    """调度器拒绝布尔、小数、0 或超过 256 的账号并发上限"""
    scheduler = Scheduler(accounts=ExecutionAccounts((Adapter(),)))
    with pytest.raises(ValueError):
        scheduler.configure_capacity("account:test_alpha", limit, 0, 1, actor="admin")


def test_public_alias_is_normalized_before_checking_bound_session():
    """检查已绑定会话前先把公开账号别名换成原路线名"""
    from management_portal.execution.service import ExecutionPreparation
    route = SimpleNamespace(account_id="five", max_concurrency=12)
    registry = SimpleNamespace(routes=lambda: (route,), get=lambda name: route)
    accounts = ExecutionAccounts((CodexAccounts(registry),))
    manager = Mock()
    manager.prepare.return_value = SimpleNamespace(backend_route_id="five", backend_session_id="existing")
    incoming = ExecutionRequest.new(actor="test", prompt="test", service_type="agent_session", provider="codex",
        operation="turn", session=SessionOptions(mode="persistent", binding_id="test:existing", backend_route_id="codex_five"))
    prepared = ExecutionPreparation(accounts=accounts, session_manager=manager).prepare(incoming)
    assert manager.prepare.call_args.args[0].session.backend_route_id == "five"
    assert prepared.session.backend_route_id == "five"


def test_quota_expiry_uses_native_sample_time_without_extending_it():
    """额度样本到原生 valid_until 即变为 unknown，不按采样时间延长，reserved 保持不变"""
    from management_portal.execution.account_quota import quota_state
    sample = {"status": "available", "observed_at": 100, "valid_until": 150, "max_age_seconds": 300}
    assert quota_state(sample, now=149)["status"] == "available"
    assert quota_state(sample, now=150)["status"] == "unknown"
    assert quota_state({**sample, "status": "reserved"}, now=151)["status"] == "reserved"


@pytest.mark.parametrize("provider", ["claude", "dsh"])
def test_reroute_updates_native_options_with_route_before_dispatch(provider):
    """排队执行改换路线时同步更新原生 provider_profile，其他运行选项不变"""
    from management_portal.execution.account_adapters import ClaudeAccounts, ConfiguredAccounts
    register_accounts(*({"account_id": f"{provider}_{name}", "provider": provider, "kind": "api_profile",
                         "route_id": name, "config": {"api_id": "test_api"}}
                        for name in ("alpha", "beta")))
    if provider == "claude":
        adapter = ClaudeAccounts()
    else:
        adapter = ConfiguredAccounts(SimpleNamespace(provider=provider, default_route="alpha", default_concurrency=8))
    accounts = ExecutionAccounts((adapter,))
    scheduler = Scheduler(accounts=accounts)
    incoming = ExecutionRequest.new(actor="test-actor", prompt="route", service_type="agent_session", provider=provider,
        operation="turn", session=SessionOptions(mode="persistent", binding_id="test:route", backend_route_id="alpha"),
        runtime_options={"provider_profile": "alpha", "model": "keep-model"})
    scheduler.submit(incoming)
    result = scheduler.reassign_queued_route(incoming.execution_id, actor="test-actor", backend_route_id="beta")
    saved = result["request"]
    assert saved["runtime_options"] == {"provider_profile": "beta", "model": "keep-model"}
    assert accounts.for_request(saved).route_id == "beta"


def test_public_account_rpcs_share_catalog_monitor_and_usage_schema():
    """公开账号 RPC 的解析、列表和用量共用同一账号目录与用量 schema"""
    from management_portal.execution.service import ExecutionService
    accounts = ExecutionAccounts((Adapter(),))
    server = object.__new__(ExecutionServiceServer)
    server.accounts = accounts
    server.quota_monitor = AccountMonitor(accounts, Scheduler(accounts=accounts))
    facade = ExecutionService(client=SimpleNamespace(request=server.dispatch))
    assert facade.resolve_account("claude", "test_alpha")["account_id"] == "test_alpha"
    assert facade.accounts()["accounts"][0]["account_id"] == "test_alpha"
    usage = facade.account_usage()
    assert usage["schema"] == "tfi.execution-account-usage.v1"
    assert usage["accounts"] == []
    assert {"provider": "claude", "account_id": "test_alpha"} in usage["catalog"]
    server.quota_monitor.accounts.adapters["claude"].usage = lambda account: {"status": "ok"}
    server.quota_monitor.refresh_usage(accounts.get("test_alpha"))
    assert facade.account_usage()["accounts"][0]["provider"] == "claude"


def test_configured_routes_fail_explicitly_in_execution_and_tolerate_only_web(monkeypatch):
    """路线读取失败时执行层直接报错，只有 Web 容忍并得到空账号列表"""
    from types import SimpleNamespace
    from management_portal.execution.account_adapters import ConfiguredAccounts
    from management_portal.execution import account_registrations
    definition = SimpleNamespace(provider="test")
    monkeypatch.setattr(account_registrations, "registrations", Mock(side_effect=OSError("temporary read failure")))
    with pytest.raises(OSError, match="temporary"):
        ConfiguredAccounts(definition)
    assert ConfiguredAccounts(definition, tolerate_unavailable=True).accounts() == ()


def test_account_refresh_tracks_inflight_reads_and_throttles_requests(monkeypatch):
    """账号额度刷新进行中时快照标记刷新中，期间重复请求被节流并给出等待秒数"""
    import threading
    accounts = ExecutionAccounts((Adapter(),))
    monitor = AccountMonitor(accounts, Scheduler(accounts=accounts))
    started, release = threading.Event(), threading.Event()
    def refresh(account, *, force):
        started.set()
        assert release.wait(3)
        monitor._samples[account.account_id] = {"status": "ok"}
    monkeypatch.setattr(monitor, "refresh_account", refresh)
    monitor.request_refresh()
    requested = monitor._last_refresh_request
    monitor.start()
    try:
        assert started.wait(2)
        assert not monitor.wakeup.is_set()
        snapshot = monitor.snapshot()
        assert snapshot["refreshing"]
        # The accepted request reports no wait; only the refused second one does.
        assert snapshot["refresh_retry_after_seconds"] == 0
        monitor.request_refresh()
        assert monitor._last_refresh_request == requested
        assert monitor.snapshot()["refresh_retry_after_seconds"] > 0
    finally:
        release.set()
        monitor.close()
    assert not monitor._refreshing


@pytest.mark.parametrize("value", [True, 1.9, "2", 0])
def test_account_registration_rejects_non_positive_integer_concurrency(value):
    """账号登记拒绝非正整数的初始 max_concurrency"""
    with pytest.raises(ValueError, match="max_concurrency"):
        register_accounts({"account_id": "codex_one", "provider": "codex", "kind": "subscription_login",
                           "max_concurrency": value})
