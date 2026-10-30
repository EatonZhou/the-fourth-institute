"""Common contract, native translation and durable DSH acceptance tests.

The live case opts in with PORTAL_TEST_DSH_LIVE and receives its authorized
route metadata through PORTAL_TEST_DSH_PROFILE_JSON. Credential values remain
in the registered secret file; registration and traces use isolated test SQL.
"""
from tests.cases.agent_data_test_support import session_transcript_ids
import os
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock
import uuid

import pytest

from management_portal.execution.contracts import ExecutionRequest, SessionOptions
from management_portal.execution.backend_contracts import BackendInvocation, BackendResult
from management_portal.execution.backend_services import AgentSessionService
from management_portal.execution.dsh_backend import DshBackend
from management_portal.execution.native_options import prepare_invocation
from management_portal.execution.provider_catalog import AGENT_PROVIDERS


def invocation(provider, options, route=""):
    request = ExecutionRequest.new(service_type="agent_session", provider=provider, operation="turn", actor="test",
        agent_id="resident:test", cwd="/workspace", prompt="hello", permission_profile="owner",
        session=SessionOptions(mode="persistent", binding_id="resident:test", backend_route_id=route), runtime_options=options)
    session = SimpleNamespace(backend_route_id=route, backend_session_id="", is_new_backend_session=True)
    return BackendInvocation(request, session, "hello", "instructions", True, True)


@pytest.mark.parametrize("provider", tuple(AGENT_PROVIDERS))
def test_every_backend_uses_one_service_pipeline(provider):
    """每个 Provider 的轮次都经同一个 AgentSessionService 管线执行"""
    # Native requests and neutral Resident requests enter this same
    # service. Adapters alone translate the latter before the shared Harness.
    events = []
    # The pipeline reads the adapter's final tool catalog from its BackendResult.
    result = BackendResult(output={"message": "result"})
    selected = SimpleNamespace(execute=lambda inv, ctx: events.append((inv, ctx)) or result)
    harness = SimpleNamespace(prepare_invocation=lambda inv, provider=None: inv, should_run=lambda inv: False,
        execute_passthrough=lambda *, invocation, context, execute: execute(invocation, context))
    turn = invocation(provider, {})
    assert AgentSessionService({provider: selected}, harness).execute(turn, "context") is result
    assert events == [(turn, "context")]


@pytest.mark.parametrize("provider", tuple(AGENT_PROVIDERS))
def test_neutral_settings_translate_at_adapter_boundary(provider, monkeypatch, tmp_path):
    """中立 runtime_settings 在适配器边界译为 Provider 原生选项，原业务请求不变"""
    monkeypatch.setattr(type(AGENT_PROVIDERS[provider]), "validate_settings", lambda *a: ("test-model", "high"))
    from management_portal import claude_provider_catalog
    monkeypatch.setattr(claude_provider_catalog, "profile", lambda _: {"kind": "subscription_login", "provider": "claude", "config_dir": str(tmp_path / "target-login")})
    turn = invocation(provider, {"runtime_settings": {"model": "test-model", "reasoning_effort": "high"},
        "authorization_scope": {}, "is_owner": True,
        "resident_environment": {"PORTAL_RESIDENT_ID": "resident_1b6a68df3ee1"}}, "target" if provider != "kimi" else "")
    translated = prepare_invocation(turn).request.runtime_options
    assert "runtime_settings" not in translated
    assert translated["model"] == "test-model"
    assert "runtime_settings" in turn.request.runtime_options  # immutable business request
    if provider == "claude":
        assert translated["config_dir"] == str(tmp_path / "target-login")
    if provider == "codex":
        assert translated["config_overrides"]["shell_environment_policy.set"]["PORTAL_RESIDENT_ID"] == "resident_1b6a68df3ee1"



def test_dsh_is_independent_and_does_not_advertise_unimplemented_operations():
    """dsh 请求拒绝 fork 和 create 操作，接受 compact"""
    turn = invocation("dsh", {}, "deepseek")
    turn.request.validate()
    assert AGENT_PROVIDERS["dsh"].provider == "dsh"
    for operation in ("fork", "create"):
        with pytest.raises(ValueError, match="does not support"):
            replace(turn.request, operation=operation).validate()
    # DSH compacts on demand (the bridge's session/compact runs DSH's own compactNow).
    replace(turn.request, operation="compact").validate()


def _launch(monkeypatch, tmp_path, params: dict) -> tuple[dict, dict, dict]:
    """The plugins (name → row), environment and ``launch.json`` a DSH process starts with."""

    import json
    import yaml
    from management_portal import dsh_client
    runtime = tmp_path / "runtime"
    (runtime / "node_modules/@deepseek-ai/dsh/lib").mkdir(parents=True)
    (runtime / "node_modules/@deepseek-ai/dsh/lib/bin.js").write_text("")
    key = tmp_path / "key"
    key.write_text("test-key")
    monkeypatch.setattr(dsh_client, "RUNTIME", runtime)
    monkeypatch.setattr(dsh_client, "STATE", tmp_path / "state")
    monkeypatch.setattr(dsh_client, "SOCKETS", tmp_path / "sockets")
    monkeypatch.setattr(dsh_client.dsh_provider_catalog, "profile", lambda route: {
        "base_url": "https://api.deepseek.com", "provider": "deepseek", "kind": "openai_compatible_chat",
        "default_model": "deepseek-v4-flash"})
    monkeypatch.setattr(dsh_client.shared_ai_provider_registry, "secret_path", lambda row: str(key))
    launched = {}

    def exited_process(args, **kwargs):
        launched.update(kwargs["env"])
        return SimpleNamespace(poll=lambda: 1)

    monkeypatch.setattr(dsh_client.subprocess, "Popen", exited_process)
    monkeypatch.setattr(dsh_client, "terminate_process_tree", lambda process, **kwargs: None)
    session = "dsh-" + "a" * 32
    with pytest.raises(dsh_client.NativeAgentUnavailable):
        dsh_client.DshClient()._start(session, {"provider_profile": "deepseek", "cwd": str(tmp_path), **params})
    [patch] = yaml.safe_load((tmp_path / "state" / session / "profiles/portal-resident/cordis.patch.yml").read_text())
    plugins = {row["name"].removeprefix("@deepseek-ai/"): row for row in patch["insert"]}
    bridge = next(row["name"] for row in patch["insert"] if row["id"] == "portal-control")
    from pathlib import Path
    assert Path(bridge).read_bytes() == (Path(dsh_client.__file__).parent / "dsh_runtime/bridge.mjs").read_bytes()
    assert Path(bridge).is_relative_to(tmp_path / "state")
    return plugins, launched, json.loads((tmp_path / "state" / session / "launch.json").read_text())


def test_a_dsh_session_has_the_file_search_todo_and_web_tools_of_the_other_providers(monkeypatch, tmp_path):
    """DSH 会话带文件搜索、todo 和 web 工具

    Portal 组装的 DSH 插件包含 glob/grep、todo_write、web_search/web_fetch，与其他 Provider 的模型工具一致；web 搜索使用路线自己的 Messages 地址和密钥。

    :module: execution.providers
    :design: decision_276105b5d67b
    """
    # Portal composes DSH's plugins itself; with the shell and file tools alone
    # its model had no file search, todo list or web, which every other Provider's has.
    plugins, launched, _ = _launch(monkeypatch, tmp_path, {})
    assert plugins["dsh-working-directory"]["config"]["defaultDirectory"] == str(tmp_path)
    assert {"dsh-tool-fs-search", "dsh-tool-todo", "dsh-tool-web", "dsh-web-search-deepseek", "dsh-web-fetch-http"} <= set(plugins)
    assert list(plugins).index("dsh-web") < list(plugins).index("dsh-tool-web")
    # A search is a request to the route's own endpoint, with the route's key.
    assert launched["DEEPSEEK_SEARCH_BASE_URL"] == launched["DEEPSEEK_BASE_URL"] + "/v1"


def test_dsh_subagents_follow_the_residents_setting(monkeypatch, tmp_path):
    """DSH 的子 Agent 工具随“允许开启 sub agent”加载

    开关打开时 DSH 会话进程加载前台运行的 subagent 工具，关闭时不加载；开关改变或旧进程没有记录时，下一轮启动新进程。
    """
    # "允许开启 sub agent", on by default as for Claude and Kimi (sample_admin 10-11):
    # DSH's delegation tool, in the foreground, so a subagent runs inside its call.
    from management_portal import dsh_client
    subagent_plugins = {"dsh-subagent", "dsh-subagent-spawn-in-process", "dsh-tool-subagent"}

    plugins, _, launch = _launch(monkeypatch, tmp_path / "on", {"allow_subagents": True})
    assert subagent_plugins <= set(plugins)
    assert plugins["dsh-tool-subagent"]["config"] == {"provider": "spawn", "toolName": "subagent", "enableRunInBackground": False}
    assert launch["subagents"] is True
    plugins, _, launch = _launch(monkeypatch, tmp_path / "off", {"allow_subagents": False})
    assert not subagent_plugins & set(plugins)
    assert launch["subagents"] is False

    backend = DshBackend()
    assert backend.turn_options({}) == {"allow_subagents": True}
    assert backend.turn_options({"allow_subagents": False}) == {"allow_subagents": False}

    # The tool is fixed when the DSH process starts: a changed switch starts a new one.
    session = "dsh-" + "a" * 32
    closed: list[str] = []
    monkeypatch.setattr(DshBackend, "client_factory", staticmethod(
        lambda: SimpleNamespace(close_session=lambda key, session_id: closed.append(session_id))
    ))
    backend.prepare_native_session(session_key="k", backend_session_id=session, options={"allow_subagents": False})
    assert closed == []
    backend.prepare_native_session(session_key="k", backend_session_id=session, options={})
    assert closed == [session]
    # A process started before Portal recorded the switch has no subagent tool.
    (dsh_client.STATE / session / "launch.json").write_text('{"threshold_ratio": 0.8}')
    assert dsh_client.launched_with_subagents(session) is None
    backend.prepare_native_session(session_key="k", backend_session_id=session, options={})
    assert closed == [session, session]


def test_catalogs_use_common_account_adapter_without_exposing_secrets(monkeypatch):
    """dsh 路线经通用账号适配器解析，运行时目录不暴露密钥字段"""
    from management_portal.execution.accounts import ExecutionAccounts
    from management_portal.execution.account_adapters import ConfiguredAccounts
    from management_portal import dsh_provider_catalog
    catalog = {"backend_route_id": "deepseek", "label": "test", "models": ["m"], "default_model": "m"}
    monkeypatch.setattr(dsh_provider_catalog, "routes", lambda: [catalog])
    monkeypatch.setattr(dsh_provider_catalog, "catalog", lambda _: dict(catalog))
    accounts = ExecutionAccounts((ConfiguredAccounts(AGENT_PROVIDERS["dsh"]),))
    account = accounts.resolve("dsh", "deepseek", {}, bound=True)
    result = accounts.runtime_catalog(account.account_id)
    assert result["provider"] == "dsh"
    assert result["runtime_settings"] == ["model", "reasoning_effort"]
    assert result["operations"] == ["compact", "release", "turn"]
    assert not {"api_key", "api_key_file", "api_key_env"} & result.keys()


@pytest.mark.empty_execution_registrations
def test_the_dsh_route_uses_its_explicit_api_and_never_falls_back():
    """dsh 路线只用显式绑定的模型 API，停用或解绑后不回退到其他 API"""
    from management_portal import dsh_provider_catalog
    from management_portal import model_api_store
    from tests.cases.model_api_test_support import register_apis
    deepseek = {"kind": "openai_compatible_chat", "provider": "deepseek", "default_model": "m",
                "models": [{"id": "m", "support_efforts": ["high", "medium"]}]}
    rows = [{**deepseek, "id": "deepseek_off", "enabled": False},
            {"id": "other_chat", "kind": "openai_compatible_chat", "provider": "openrouter", "models": []},
            {**deepseek, "id": "deepseek_main"},
            {**deepseek, "id": "deepseek_spare", "routes": [{"backend": "dsh", "route_id": "deepseek"}]}]
    register_apis(*rows)

    (route,) = dsh_provider_catalog.routes()
    assert (route["backend_route_id"], route["provider_profile"]) == ("deepseek", "deepseek_spare")
    assert route["efforts_by_model"] == {"m": ["high"]}
    assert dsh_provider_catalog.profile("deepseek")["id"] == "deepseek_spare"
    with model_api_store.database().transaction("apis") as connection:
        connection.execute("UPDATE apis SET position = -position")
    assert dsh_provider_catalog.profile("deepseek")["id"] == "deepseek_spare"
    with pytest.raises(ValueError):
        dsh_provider_catalog.profile("deepseek_main")
    model_api_store.update_api("deepseek_spare", {"enabled": False}, "test")
    assert dsh_provider_catalog.routes() == []
    model_api_store.update_api("deepseek_spare", {"enabled": True, "routes": []}, "test")
    assert dsh_provider_catalog.routes() == []


@pytest.mark.skipif(not os.getenv("PORTAL_TEST_DSH_LIVE"), reason="explicit live DSH opt-in required")
def test_live_dsh_postgres_result_recovery_and_followup(tmp_path):
    """Live dsh 轮次在持久化中断后从检查点恢复结果，冷启动后追问仍接续同一会话"""
    import json
    import psycopg
    from psycopg import sql
    from management_common.postgres_database import PostgresDatabase
    from management_portal.execution import agent_data_store
    from management_portal.execution.agent_data_projection import AgentDataProjector
    from management_portal.execution.session_manager import SessionManager
    from management_portal.execution.harness import AgentHarness
    from management_portal.execution.runtime import ExecutionRuntime, RuntimeContext
    from management_portal.dsh_client import shared_client
    from tests.cases.model_api_test_support import register_apis

    # pytest owns an isolated registry. The caller supplies the authorized
    # live route's metadata (credential file references, never raw keys).
    register_apis({**json.loads(os.environ["PORTAL_TEST_DSH_PROFILE_JSON"]),
                   "routes": [{"backend": "dsh", "route_id": "deepseek"}]})

    dsn = os.environ["PORTAL_POSTGRES_TEST_DSN"]
    schema = "test_dsh_" + uuid.uuid4().hex
    databases, checkpoints, session_id = [], [], ""
    binding = "dsh-acceptance:" + uuid.uuid4().hex
    marker = "DSH_PG_" + uuid.uuid4().hex
    backend = DshBackend()
    accepted_at = datetime.now(timezone.utc).isoformat()
    def database_factory(*args, **kwargs):
        database = PostgresDatabase(dsn, schema=schema, min_size=1, max_size=2)
        databases.append(database)
        return database
    try:
        with mock.patch.object(agent_data_store, "portal_database", side_effect=database_factory):
            def runtime():
                store = agent_data_store.AgentDataStore()
                return ExecutionRuntime(agent_providers={"dsh": backend}, agent_harness=AgentHarness(),
                    session_manager=SessionManager(agent_data=store), agent_data_projector=AgentDataProjector(store),
                    state_root=tmp_path), store
            engine, store = runtime()
            request = ExecutionRequest.new(service_type="agent_session", provider="dsh", operation="turn", actor="test",
                agent_id=binding, cwd=str(tmp_path), permission_profile="owner",
                prompt=f"Remember {marker}. Reply with exactly this marker. Do not use tools.",
                session=SessionOptions(mode="persistent", binding_id=binding, backend_route_id="deepseek"),
                runtime_options={"runtime_settings": {"model": "deepseek-v4-flash", "reasoning_effort": "low"},
                                 "is_owner": True, "authorization_scope": {"mode": "A"}}).validate()
            with mock.patch.object(engine.sessions, "complete", side_effect=RuntimeError("test persistence interruption")):
                with pytest.raises(RuntimeError, match="test persistence interruption"):
                    engine.execute(request, RuntimeContext(accepted_at=accepted_at, checkpoint_provider=checkpoints.append))
            checkpoint = next(c for c in reversed(checkpoints) if c.get("provider_completed"))
            session_id = checkpoint["backend_session_id"]
            assert checkpoint["output"]["message"].strip() == marker
            store.database.close()
            recovered, store = runtime()
            with mock.patch.object(backend, "execute", side_effect=AssertionError("unexpected provider replay")):
                result = recovered.execute(request, RuntimeContext(accepted_at=accepted_at, recovery_checkpoint=checkpoint))
            assert result.output["message"].strip() == marker
            usage = result.usage["last"]
            assert usage["inputTokens"] + usage["outputTokens"] == usage["totalTokens"]
            assert usage["cachedInputTokens"] <= usage["inputTokens"]
            bound = recovered.sessions.provider_binding(binding)
            assert bound["provider"] == "dsh"
            assert bound["backend_route_id"] == "deepseek"
            before = session_transcript_ids(store, bound["provider_session_ref"])
            assert before
            shared_client().close_session(binding, session_id)  # cold native recovery, not just SQL reopen
            followup = replace(request, execution_id="execution-" + uuid.uuid4().hex[:24],
                prompt="Reply with exactly the marker I asked you to remember. Do not use tools.")
            answer = recovered.execute(followup)
            assert answer.output["message"].strip() == marker
            assert answer.backend_session_id == session_id
            after = session_transcript_ids(store, bound["provider_session_ref"])
            assert after[:len(before)] == before
            assert len(after) > len(before) and len(after) == len(set(after))
    finally:
        if session_id:
            shared_client().close_session(binding, session_id)
        for database in databases:
            database.close()
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
