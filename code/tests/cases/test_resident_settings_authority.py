""":module: resident.session"""
from copy import deepcopy
from types import SimpleNamespace
import os
import secrets

import pytest

from management_portal.society_service import resident_fork_context, resident_runtime_domain
from management_portal.society_service import (
    resident_access,
    resident_execution_domain,
    resident_execution_engine_domain,
    resident_record_domain,
    resident_restart_domain,
    resident_session_backend_domain,
)
from management_portal.society_service.resident_settings_contract import (
    runtime_settings_from_parts, split_runtime_settings, stage_runtime_settings,
)
from management_common.postgres_database import PostgresDatabase
from management_portal.society_service import resident_storage
from management_portal.society_service import resident_catalog, resident_workspace_domain
from tests.cases.resident_execution_test_support import resident_record  # noqa: F401 - shared registered Resident fixture
from management_portal.society_service.state_store import LineageDataStore


def settings(model="current-model"):
    return runtime_settings_from_parts({
        "backend": "codex", "model": model, "sandbox": "read-only",
        "prompt_injection": {"preset": "minimal"}, "allow_subagents": False,
    }, {"mode": "manual", "features": {}})


def test_registered_settings_ignore_files_and_stale_resident_copies(tmp_path, monkeypatch):
    """已注册 Resident 的运行设置只从数据库读取，忽略 settings.json 和记录中的旧副本

    :design: decision_d918ebebc062
    :design-point:
    """
    current = settings()
    calls = []
    store = SimpleNamespace(read_resident_execution_settings=lambda rid: calls.append(rid) or deepcopy(current))
    monkeypatch.setattr(resident_runtime_domain.resident_catalog, "get_catalog", lambda: SimpleNamespace(state_store=store))
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "settings.json").write_text('{"model":"obsolete-file-model"}')
    record = {
        "resident_id": "resident_aaaaaaaaaaaa", "backend": "codex",
        "workspace_path": str(tmp_path), "model": "obsolete-top-level-model",
        "runtime_settings": settings("obsolete-nested-model"),
        "execution_settings": settings("stale-read-snapshot"), "harness_config": {},
    }
    assert resident_runtime_domain.read_resident_runtime_settings(record) == current
    assert calls == [record["resident_id"]]


def test_settings_update_has_one_current_writer_and_only_a_change_receipt(monkeypatch):
    """更新运行设置只写入配置存储一次并发出一条变更记录，Resident 记录不留旧字段

    :design: decision_d918ebebc062
    :design-point:
    """
    current = settings("before")
    written, events = [], []
    store = SimpleNamespace(
        read_resident_execution_settings=lambda _: deepcopy(current),
        write_resident_execution_settings=lambda rid, value: written.append((rid, deepcopy(value))) or deepcopy(value),
    )
    monkeypatch.setattr(resident_runtime_domain.resident_catalog, "get_catalog", lambda: SimpleNamespace(state_store=store))
    monkeypatch.setattr(resident_runtime_domain.resident_conversation_stream, "get_repository", lambda: SimpleNamespace(
        execution_settings_changed=lambda rid, value: events.append((rid, deepcopy(value))),
    ))
    record = {"resident_id": "resident_aaaaaaaaaaaa", "backend": "codex", "model": "before", "runtime_settings": current}
    changed = resident_runtime_domain.write_resident_runtime_settings(record, settings("after"))
    assert written == [(record["resident_id"], changed)]
    assert events == written
    assert record["execution_settings"]["model"] == "after"
    assert "agent_harness" not in record["execution_settings"]
    assert "model" not in record and "runtime_settings" not in record


def test_registration_stages_configuration_until_identity_is_saved(monkeypatch):
    """注册时运行设置先暂存在 Resident 记录上，身份保存时一并写入

    :design: decision_d918ebebc062
    :design-point:
    """
    store = SimpleNamespace(read_resident_execution_settings=lambda _: None)
    monkeypatch.setattr(resident_runtime_domain.resident_catalog, "get_catalog", lambda: SimpleNamespace(state_store=store))
    record = {"resident_id": "resident_bbbbbbbbbbbb", "backend": "codex"}
    staged = resident_runtime_domain.write_resident_runtime_settings(record, settings())
    assert resident_runtime_domain.read_resident_runtime_settings(record) == staged
    assert set(record) == {"resident_id", "backend", "execution_settings", "harness_config"}


def test_restart_candidate_settings_do_not_change_the_active_provider(monkeypatch):
    """重启候选的账号和设置只暂存，不改变当前生效的运行设置

    :design: decision_d918ebebc062
    :design-point:
    """
    active = settings("active-model")
    store = SimpleNamespace(read_resident_execution_settings=lambda _: deepcopy(active))
    monkeypatch.setattr(resident_runtime_domain.resident_catalog, "get_catalog", lambda: SimpleNamespace(state_store=store))
    candidate = {"resident_id": "resident_aaaaaaaaaaaa", "backend": "codex", "backend_route_id": "active-account"}
    target = settings("candidate-model")
    resident_restart_domain._apply_resident_session_target(
        candidate, "codex", "candidate-account", runtime_settings=target,
    )
    assert candidate["backend_route_id"] == "candidate-account"
    assert candidate["execution_settings"]["model"] == "candidate-model"
    assert resident_runtime_domain.read_resident_runtime_settings(candidate) == active
    assert "runtime_settings" not in candidate


@pytest.fixture
def configuration_store():
    schema = f"{os.environ['PORTAL_DATABASE_NAMESPACE']}_settings_{secrets.token_hex(3)}"
    database = PostgresDatabase(os.environ["PORTAL_DATABASE_URL"], schema=schema)
    from management_portal.resources.workspace_store import get_workspace_store
    workspace = get_workspace_store().ensure("workspace_aaaaaaaaaaaa", "/tmp/settings-authority")
    store = LineageDataStore(database)
    with database.connect() as connection:
        connection.execute("INSERT INTO residents (resident_id,backend,owned_workspace_id) VALUES (%s,%s,%s)",
                           ("resident_aaaaaaaaaaaa", "codex", workspace))
        execution, harness = split_runtime_settings(settings("before"))
        execution.pop("backend")
        resident_storage.put_fields(connection, "resident_execution_configuration",
                                   resident_storage.CONFIGURATION_FIELDS, "resident_aaaaaaaaaaaa",
                                   {"execution_settings": execution, "harness_config": harness})
    try:
        yield store
    finally:
        with database.connect() as connection:
            connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
        database.close()


def test_database_configuration_roundtrip_and_provider_transition_boundary(configuration_store):
    """运行设置可在数据库中往返读写，直接改 Provider 被拒绝，不存在的 Resident 不能写入

    :design: decision_d918ebebc062
    :design-point:
    """
    store = configuration_store
    resident_id = "resident_aaaaaaaaaaaa"
    assert store.read_resident_execution_settings(resident_id)["model"] == "before"
    target = settings("after")
    target["agent_harness"] = {"mode": "manual", "features": {"inline_media_history": {"enabled": True}}}
    saved = store.write_resident_execution_settings(resident_id, target)
    assert saved == store.read_resident_execution_settings(resident_id)
    assert saved["model"] == "after"
    assert saved["agent_harness"]["features"]["inline_media_history"]["enabled"]
    with store.database.read() as connection:
        row = connection.execute("SELECT execution_settings_json,harness_config_json FROM resident_execution_configuration").fetchone()
        from management_common.json_columns import json_value
        execution = json_value(row["execution_settings_json"])
        assert "backend" not in execution and "agent_harness" not in execution
    with pytest.raises(ValueError, match="provider transition"):
        store.write_resident_execution_settings(resident_id, {**target, "backend": "claude"})
    assert store.read_resident_execution_settings(resident_id) == saved
    assert store.read_resident_execution_settings("resident_bbbbbbbbbbbb") is None
    with pytest.raises(KeyError):
        store.write_resident_execution_settings("resident_bbbbbbbbbbbb", target)


@pytest.mark.parametrize("part", ["execution", "harness"])
def test_configuration_parts_reject_unowned_fields(part):
    """执行设置或 harness 配置中出现不属于自身的字段时被拒绝

    :design: decision_d918ebebc062
    :design-point:
    """
    execution, harness = split_runtime_settings(settings())
    target = execution if part == "execution" else harness
    target["unowned_metadata"] = {"model": "alternate-authority"}
    with pytest.raises(ValueError, match="unsupported|Unsupported"):
        runtime_settings_from_parts(execution, harness)


def test_split_configuration_is_detached_and_model_changes_preserve_harness():
    """暂存的设置与来源对象脱钩，修改模型时 harness 配置保持不变

    :module: execution.harness
    :design: decision_d918ebebc062
    :design-point:
    """
    source = settings()
    record = {}
    stage_runtime_settings(record, source)
    harness_before = deepcopy(record["harness_config"])
    source["model"] = "next-model"
    assert record["execution_settings"]["model"] == "current-model"
    stage_runtime_settings(record, source)
    assert record["harness_config"] == harness_before


@pytest.mark.parametrize("config", [
    {"features": []},
    {"features": {"unknown_plugin": {"enabled": True}}},
    {"features": {"inline_media_history": {"model": "hidden-copy"}}},
    {"features": {"inline_media_history": True}},
])
def test_harness_rejects_unregistered_features_and_unowned_parameters(config):
    """harness 配置拒绝未注册的功能、非对象功能值和不属于它的参数

    :module: execution.harness
    :design: decision_d918ebebc062
    :design-point:
    """
    with pytest.raises(ValueError):
        runtime_settings_from_parts({"model": "current-model"}, config)


def test_frozen_fork_creates_an_independent_session_after_source_is_gone(monkeypatch):
    """full fork 创建会话时新建独立会话，不引用来源 Resident 的绑定

    :design: decision_d918ebebc062
    :design-point:
    """
    record = {
        "resident_id": "resident_bbbbbbbbbbbb", "backend": "codex",
        "source_resident_id": "resident_aaaaaaaaaaaa", "backend_route_id": "one",
        "fork_mode": "full",
    }
    calls = []
    monkeypatch.setattr(resident_runtime_domain, "resident_working_directory", lambda _: "/own-workspace")
    monkeypatch.setattr(resident_access, "has_resident_workspace_authority", lambda *_: True)
    monkeypatch.setattr(resident_execution_engine_domain, "resident_execution_runtime_options", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(resident_session_backend_domain, "run_backend_session_operation", lambda **kwargs: calls.append(kwargs) or {
        "backend_session_id": "independent-session", "backend_route_id": "one",
    })
    result = resident_session_backend_domain.create_exact_resident_session(record, {
        "operation": "context_fork_create", "actor": "CASE-A",
    })
    assert result["id"] == "independent-session"
    assert calls[0]["operation"] == "create"
    assert calls[0]["binding_id"] == "resident:resident_bbbbbbbbbbbb"
    assert "source_binding_id" not in calls[0]
    assert calls[0]["backend_route_id"] == "one"


@pytest.mark.parametrize("kind", ["native_fork_create", "fresh_fork_create"])
def test_old_creation_jobs_require_one_time_migration(kind):
    """旧的 native_fork_create 与 fresh_fork_create 创建任务被拒绝，需先迁移

    :design: decision_d918ebebc062
    :design-point:
    """
    with pytest.raises(ValueError, match="unsupported Resident creation job"):
        resident_session_backend_domain.create_exact_resident_session({}, {"operation": kind})


def test_stale_runtime_state_save_preserves_current_configuration(resident_record):
    """用旧记录保存运行状态时，当前账号和运行设置不被覆盖

    :design: decision_d918ebebc062
    :design-point:
    """
    resident_id = str(resident_record["resident_id"])
    store = resident_catalog.get_catalog().state_store
    stale = deepcopy(resident_catalog.get_catalog().get(resident_id))
    owner = deepcopy(stale)
    owner["backend_route_id"] = "one"
    resident_record_domain.save_resident_record(owner, commit_configuration=True)
    changed = resident_runtime_domain.read_resident_runtime_settings(owner)
    changed["model"] = "configuration-after"
    changed["agent_harness"] = {"mode": "manual", "features": {"provider_error_continue": {"enabled": False}}}
    expected = resident_runtime_domain.write_resident_runtime_settings(owner, changed)

    stale["last_error"] = "runtime observation from an older record"
    stale["session_state"] = "failed"
    resident_record_domain.save_resident_record(stale)
    saved = resident_catalog.get_catalog().get(resident_id)
    assert saved["last_error"] == stale["last_error"]
    assert saved["session_state"] == "failed"
    assert saved["backend_route_id"] == "one"
    assert store.read_resident_execution_settings(resident_id) == expected
    assert expected["agent_harness"]["mode"] == "manual"
    assert expected["agent_harness"]["features"]["provider_error_continue"]["enabled"] is False


def test_provider_configuration_commits_atomically_and_survives_old_state_save(resident_record):
    """切换 Provider 时身份与运行设置在同一事务提交，之后旧记录保存不能改回

    :design: decision_d918ebebc062
    :design-point:
    """
    resident_id = str(resident_record["resident_id"])
    catalog = resident_catalog.get_catalog()
    store = catalog.state_store
    original = deepcopy(catalog.get(resident_id))
    before = store.read_resident_execution_settings(resident_id)
    candidate = deepcopy(original)
    candidate["backend"] = "claude"
    candidate["backend_route_id"] = "claude_primary"
    target_settings = {**before, "backend": "claude", "model": "claude-target-model",
                       "agent_harness": {"mode": "manual", "features": {"inline_media_history": {"enabled": True}}}}
    stage_runtime_settings(candidate, target_settings)

    def interrupted_commit():
        resident_record_domain.save_resident_record(candidate, commit_configuration=True)
        raise RuntimeError("provider commit interrupted")

    with pytest.raises(RuntimeError, match="provider commit interrupted"):
        store.transaction(interrupted_commit)
    assert catalog.get(resident_id)["backend"] == "codex"
    assert catalog.get(resident_id).get("backend_route_id", "") == original.get("backend_route_id", "")
    assert store.read_resident_execution_settings(resident_id) == before

    store.transaction(lambda: resident_record_domain.save_resident_record(candidate, commit_configuration=True))
    saved = catalog.get(resident_id)
    after = store.read_resident_execution_settings(resident_id)
    assert saved["backend"] == after["backend"] == "claude"
    assert saved["backend_route_id"] == "claude_primary"
    assert after["model"] == "claude-target-model"
    assert after["agent_harness"]["features"]["inline_media_history"]["enabled"] is True

    original["last_error"] = "old Codex execution completed late"
    resident_record_domain.save_resident_record(original)
    assert catalog.get(resident_id)["backend"] == "claude"
    assert catalog.get(resident_id)["backend_route_id"] == "claude_primary"
    assert catalog.get(resident_id)["last_error"] == original["last_error"]
    assert store.read_resident_execution_settings(resident_id) == after


def test_account_reconciliation_rolls_back_settings_with_its_configuration_commit(resident_record, monkeypatch):
    """codex 账号校正的配置提交失败时运行设置一并回滚，成功时一次提交

    :design: decision_d918ebebc062
    :design-point:
    """
    resident_id = str(resident_record["resident_id"])
    catalog = resident_catalog.get_catalog()
    store = catalog.state_store
    original = deepcopy(catalog.get(resident_id))
    before = store.read_resident_execution_settings(resident_id)
    definition = SimpleNamespace(runtime_adapter=lambda: SimpleNamespace(
        reconcile=lambda _definition, _route, current: {**current, "model": "reconciled-after"},
    ))
    monkeypatch.setattr(resident_runtime_domain, "AGENT_PROVIDERS", {"codex": definition})
    original_save = resident_record_domain.save_resident_record

    def interrupted_save(record, *, commit_configuration=False):
        assert commit_configuration is True
        raise RuntimeError("account configuration commit interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(resident_record_domain, "save_resident_record", interrupted_save)
        with pytest.raises(RuntimeError, match="account configuration commit interrupted"):
            resident_runtime_domain.reconcile_resident_codex_runtime_configuration(original)
    assert store.read_resident_execution_settings(resident_id) == before
    assert catalog.get(resident_id).get("backend_route_id", "") == original.get("backend_route_id", "")
    assert not catalog.get(resident_id).get("runtime_provider_reconciled_at")

    commits = []
    def save(record, *, commit_configuration=False):
        commits.append(commit_configuration)
        original_save(record, commit_configuration=commit_configuration)
    monkeypatch.setattr(resident_record_domain, "save_resident_record", save)
    reconciled = resident_runtime_domain.reconcile_resident_codex_runtime_configuration(original)
    assert commits == [True]
    assert reconciled["backend_route_id"] == "zero"
    assert catalog.get(resident_id)["backend_route_id"] == "zero"
    assert store.read_resident_execution_settings(resident_id)["model"] == "reconciled-after"


def test_api_settings_use_the_current_configuration_owner(resident_record, monkeypatch):
    """provider_api Resident 修改模型后，旧记录上的普通编辑不覆盖运行设置，不支持的设置被拒绝

    :module: execution.harness"""
    from management_portal import ai_provider_registry
    monkeypatch.setattr(ai_provider_registry, "shared_ai_provider_registry", SimpleNamespace(get=lambda route: {
        "enabled": True, "kind": "openai_compatible_chat", "models": ["before", "after"]}))
    catalog = resident_catalog.get_catalog()
    resident_id = resident_record["resident_id"]
    owner = deepcopy(catalog.get(resident_id))
    owner.update(backend="provider_api", backend_route_id="test-api")
    stage_runtime_settings(owner, {"backend": "provider_api", "model": "before",
                                  "prompt_context": {"system_prompt": "Review"}})
    resident_record_domain.save_resident_record(owner, commit_configuration=True)
    stale = deepcopy(catalog.get(resident_id))
    result = resident_execution_domain.change_resident_runtime_settings(resident_id, "CASE-A", {"model": "after"})
    assert result["runtime_settings"]["model"] == "after"
    with monkeypatch.context() as patch:
        patch.setattr(resident_record_domain, "find_resident", lambda _resident_id: deepcopy(stale))
        resident_workspace_domain.update_resident_record(resident_id, {"title": "plain edit"}, "CASE-A")
    saved = catalog.state_store.read_resident_execution_settings(resident_id)
    assert saved["model"] == "after"
    assert saved["prompt_context"]["system_prompt"] == "Review"
    with pytest.raises(ValueError, match="does not support"):
        resident_execution_domain.change_resident_runtime_settings(resident_id, "CASE-A", {"allow_subagents": True})
    assert catalog.state_store.read_resident_execution_settings(resident_id) == saved

def test_concurrent_old_state_save_waits_for_configuration_owner_and_preserves_commit(resident_record):
    """并发的旧记录保存等待配置事务提交后再写，不覆盖新配置

    :design: decision_d918ebebc062
    :design-point:
    """
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    catalog = resident_catalog.get_catalog()
    store = catalog.state_store
    resident_id = resident_record["resident_id"]
    stale = deepcopy(catalog.get(resident_id))
    owner = deepcopy(stale)
    current = store.read_resident_execution_settings(resident_id)
    stage_runtime_settings(owner, {**current, "model": "concurrent-configuration", "agent_harness": {
        "mode": "manual", "features": {"provider_error_continue": {"enabled": False}},
    }})
    owner["backend_route_id"] = "one"
    locked, release, state_started = Event(), Event(), Event()

    def configuration_commit():
        def commit():
            store.put_resident_record(resident_id, owner, commit_configuration=True)
            locked.set()
            assert release.wait(5), "configuration owner was not released"
        store.transaction(commit)

    def old_state_commit():
        stale["last_error"] = "concurrent stale execution state"
        state_started.set()
        store.put_resident_record(resident_id, stale)

    with ThreadPoolExecutor(max_workers=2) as pool:
        owner_future = pool.submit(configuration_commit)
        try:
            assert locked.wait(5), "configuration owner did not acquire the record lock"
            state_future = pool.submit(old_state_commit)
            assert state_started.wait(5)
            assert not state_future.done(), "state save bypassed the pending owner transaction"
        finally:
            release.set()
        owner_future.result(timeout=5)
        state_future.result(timeout=5)
    saved = catalog.get(resident_id)
    configured = store.read_resident_execution_settings(resident_id)
    assert saved["last_error"] == "concurrent stale execution state"
    assert saved["backend_route_id"] == "one"
    assert configured["model"] == "concurrent-configuration"
    assert configured["agent_harness"]["mode"] == "manual"
    assert configured["agent_harness"]["features"]["provider_error_continue"]["enabled"] is False


def test_model_api_account_and_parameters_commit_atomically(resident_record, monkeypatch):
    """模型 API 的账号、模型和参数一起提交，失败时全部回滚

    :module: resident.identity"""
    from management_portal import ai_provider_registry
    registry = {
        'chat': {'id': 'chat', 'kind': 'openai_compatible_chat', 'models': [{'id': 'text'}]},
        'jev': {'id': 'jev', 'kind': 'typesafe_system_one', 'models': [{'id': 'decision'}]},
    }
    monkeypatch.setattr(ai_provider_registry, 'shared_ai_provider_registry', SimpleNamespace(get=lambda route: registry[route]))
    catalog = resident_catalog.get_catalog()
    rid = resident_record['resident_id']
    record = deepcopy(catalog.get(rid))
    record.update(backend='provider_api', backend_route_id='chat')
    stage_runtime_settings(record, {'backend': 'provider_api', 'model': 'text',
        'prompt_context': {'history': {'enabled': True, 'max_tokens': 8000}},
        'backend_options': {'parameters': {'temperature': 0.2}}})
    resident_record_domain.save_resident_record(record, commit_configuration=True)
    changes = {'backend_route_id': 'jev', 'model': 'decision', 'backend_options': {
        'parameters': {'questions': {'ready': {'type': 'noul', 'instructions': 'Ready?'}}}, 'timeout_seconds': 30}}
    before = catalog.state_store.read_resident_execution_settings(rid)
    with monkeypatch.context() as patch:
        patch.setattr(resident_record_domain, 'save_resident_record', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('rollback')))
        with pytest.raises(RuntimeError, match='rollback'):
            resident_execution_domain.change_resident_runtime_settings(rid, 'CASE-A', changes)
    assert catalog.get(rid)['backend_route_id'] == 'chat'
    assert catalog.state_store.read_resident_execution_settings(rid) == before
    result = resident_execution_domain.change_resident_runtime_settings(rid, 'CASE-A', changes)
    assert catalog.get(rid)['backend_route_id'] == 'jev'
    assert result['runtime_settings']['model'] == 'decision'
    assert result['runtime_settings']['prompt_context']['history']['enabled'] is True
    assert 'backend_route_id' not in result['runtime_settings']
    assert 'backend_route_id' in result['next_turn_fields']


def test_api_history_reads_the_canonical_conversation_and_excludes_scoped_inputs(resident_record):
    """provider_api 的历史上下文取自对话记录，排除带范围限定的输入

    :module: resident.identity"""
    from management_portal.society_service import resident_api_context, resident_conversation_stream, resident_input_scope
    from tests.cases.test_agent_data_store import _chat
    rid = resident_record['resident_id']
    repository = resident_conversation_stream.get_repository()
    for mid, text in [('one', 'Remember GREEN'), ('scoped', 'Private job')]:
        repository.agent_data.display_chat_message(rid, _chat(mid, 'user', text, author_id='CASE-A'))
        repository.agent_data.display_chat_message(rid, _chat(mid + '-reply', 'agent', 'GREEN', reply_to_message_id=mid))
        repository.set_input_state(rid, mid, 'completed')
    resident_input_scope.register(resident_id=rid, message_id='scoped', actor='CASE-A', profile={})
    repository.agent_data.display_chat_message(rid, _chat('now', 'user', 'Which color?', author_id='CASE-A'))
    messages, report = resident_api_context.assemble_api_messages(rid, {'user_message_id': 'now'}, {'history': {'enabled': True, 'max_tokens': 8000}})
    assert messages == [{'role': 'user', 'content': 'Remember GREEN'}, {'role': 'assistant', 'content': 'GREEN'}]
    assert report['included_turns'] == 1
