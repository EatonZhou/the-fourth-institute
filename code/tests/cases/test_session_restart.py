from __future__ import annotations
from management_portal.execution.provider_catalog import AGENT_PROVIDERS
from management_portal.society_service import resident_backend
from management_portal.society_service import resident_catalog
from management_portal.society_service.resident_settings_contract import runtime_settings_from_parts
from management_portal.society_service import resident_conversation_stream
from management_portal.society_service import resident_execution_work
from management_portal.society_service import resident_state
from management_portal.society_service.domain_ports import execution_port
from management_portal.society_service.resident_lifecycle import LifecycleStepPending

from pathlib import Path
from contextlib import nullcontext
from types import SimpleNamespace

from management_common.instance_manifest import load as load_instance

import pytest

from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.hatg import catalog
from management_portal.society_service import resident_domain
from management_portal.society_service import resident_registration_domain


from management_portal.society_service import (
    resident_access,
    resident_execution_domain,
    resident_input_queue_domain,
    resident_lifecycle_steps_domain,
    resident_management_domain,
    resident_record_domain,
    resident_runtime_domain,
)


from management_portal.society_service import resident_restart_domain, resident_session_backend_domain
from management_portal.execution import codex_runtime_settings
from tests.cases.model_api_test_support import DEEPSEEK_PRIMARY, register_apis


def test_restart_model_api_uses_profile_identity_without_native_route(monkeypatch):
    def configuration(provider, route):
        assert provider == 'test-api'
        assert route == '', 'completion profiles reject native session routes'
        return {'account_id': 'test-api', 'backend_route_id': ''}
    monkeypatch.setattr(resident_restart_domain, 'resolve_account_configuration', configuration)
    target = resident_restart_domain._restart_target(
        {'target_account': 'test-api'}, {'account_id': 'kimi-code'}, 'kimi', 'provider_api')
    assert target.route == 'test-api'
    assert target.account['account_id'] == 'test-api'


def test_model_api_restart_defaults_to_current_api_profile(monkeypatch):
    record = {'backend': 'provider_api', 'backend_route_id': 'test-api'}
    monkeypatch.setattr(resident_backend, 'execution_account', lambda *args: {'backend_route_id': ''})
    source = resident_restart_domain._session_restart_source(record, {})
    assert source['account_id'] == 'test-api'
    assert source['has_session'] is False
    monkeypatch.setattr(resident_restart_domain, 'resolve_account_configuration',
                        lambda provider, route: {'account_id': provider, 'backend_route_id': route})
    target = resident_restart_domain._restart_target({}, source, 'provider_api', 'provider_api')
    assert target.route == 'test-api'


def test_api_account_switch_keeps_portal_owned_history_without_import():
    from management_portal.society_service import resident_session_preservation as preservation
    source = {'provider': 'provider_api', 'account_id': 'api-a', 'has_session': False}
    assert preservation.preserve_route_error(source, 'provider_api', 'api-b', has_context=True) == ''
    options = preservation.context_options(source, 'provider_api', 'api-b', has_context=True)
    assert next(option for option in options if option['id'] == 'preserve')['enabled']
    # This does not claim a lossless import of a native provider's tool state.
    assert preservation.preserve_route_error(
        {'provider': 'codex', 'account_id': 'codex-a', 'has_session': True},
        'provider_api', 'api-b', has_context=True)


def _store(tmp_path: Path) -> AgentDataStore:
    return AgentDataStore()


def _bind(
    store: AgentDataStore,
    binding_id: str,
    provider: str,
    backend_session_id: str,
    *,
    backend_route_id: str = "",
) -> None:
    session = store.ensure_provider_session(
        provider=provider,
        backend_route_id=backend_route_id,
        backend_session_id=backend_session_id,
        agent_id=binding_id,
        actor_ref="test",
    )
    store.bind_provider_session(binding_id, session["provider_session_ref"])


def test_prepared_session_replacement_is_atomic_contracts(tmp_path, subtests, isolated_portal_case):
    """预备会话绑定原子替换并支持业务提交失败回滚"""
    with subtests.test(contract='test_prepared_session_replacement_is_atomic'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = _store(tmp_path)
        _bind(store, 'resident:resident_source', 'codex', 'thread_old', backend_route_id='zero')
        _bind(store, 'session-restart:restart_one', 'claude', 'session_new')
        replacement = store.replace_provider_binding('resident:resident_source', 'session-restart:restart_one', expected_provider='codex', expected_backend_session_id='thread_old')
        assert replacement['previous_binding']['backend_session_id'] == 'thread_old'
        assert replacement['current_binding']['provider'] == 'claude'
        assert store.provider_binding('session-restart:restart_one') == {}
        active = store.provider_binding('resident:resident_source')
        assert active['provider'] == 'claude'
        assert active['backend_session_id'] == 'session_new'
    with subtests.test(contract='test_failed_compare_does_not_change_either_binding'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = _store(tmp_path)
        _bind(store, 'resident:resident_source', 'codex', 'thread_old')
        _bind(store, 'session-restart:restart_two', 'kimi', 'session_new')
        with pytest.raises(RuntimeError, match='changed before replacement'):
            store.replace_provider_binding('resident:resident_source', 'session-restart:restart_two', expected_backend_session_id='another_thread')
        assert store.provider_binding('resident:resident_source')['backend_session_id'] == 'thread_old'
        assert store.provider_binding('session-restart:restart_two')['backend_session_id'] == 'session_new'
    with subtests.test(contract='test_provider_binding_preserves_fields_that_are_not_provided'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = _store(tmp_path)
        session = store.ensure_provider_session(provider='codex', backend_route_id='zero', backend_session_id='thread_context', agent_id='resident_context', actor_ref='test')
        binding_id = 'resident:resident_context'
        session_ref = str(session['provider_session_ref'])
        store.bind_provider_session(binding_id, session_ref, context_fingerprint='sha256:context', context_injected_at='2026-08-24T01:02:03Z', context_injection_count=4, harness_tool_names=('apply_patch', 'exec_command'))
        preserved = store.bind_provider_session(binding_id, session_ref)
        assert preserved['context_fingerprint'] == 'sha256:context'
        assert preserved['context_injected_at'] == '2026-08-24T01:02:03Z'
        assert preserved['context_injection_count'] == 4
        assert preserved['harness_tool_names'] == ('apply_patch', 'exec_command')
        cleared = store.bind_provider_session(binding_id, session_ref, context_fingerprint='', context_injected_at='', context_injection_count=0, harness_tool_names=())
        assert cleared['context_fingerprint'] == ''
        assert cleared['context_injected_at'] == ''
        assert cleared['context_injection_count'] == 0
        assert cleared['harness_tool_names'] == ()
    with subtests.test(contract='test_replacement_can_be_restored_when_business_write_fails'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = _store(tmp_path)
        _bind(store, 'resident:resident_source', 'codex', 'thread_old')
        _bind(store, 'session-restart:restart_three', 'kimi', 'session_new')
        replacement = store.replace_provider_binding('resident:resident_source', 'session-restart:restart_three')
        restored = store.restore_provider_binding('resident:resident_source', expected_provider_session_ref=replacement['current_binding']['provider_session_ref'], binding=replacement['previous_binding'])
        assert restored['backend_session_id'] == 'thread_old'
        assert store.provider_binding('resident:resident_source')['backend_session_id'] == 'thread_old'


class ExecutionStub(SimpleNamespace):
    def resolve_account(self, provider, route="", **kwargs):
        definition = AGENT_PROVIDERS[provider]
        route = route or definition.default_route or ("zero" if provider == "codex" else "")
        return {"account_id": route, "backend_route_id": route,
                "capacity_key": f"codex:{route}" if provider == "codex" else f"account:{provider}:{route}",
                "quota_protection_enabled": provider == "codex"}

    def runtime_catalog(self, **kwargs):
        return {"backends": [{"provider": p.provider, "routes": [{"backend_route_id": p.default_route, "label": p.label}]}
                              for p in AGENT_PROVIDERS.values()]}


def _restart_domain(
    monkeypatch: pytest.MonkeyPatch,
    *,
    source_provider: str = "codex",
    source_route: str = "zero",
) -> tuple[dict, list[dict], list[dict]]:
    # Claude's default route reads its registration from the isolated test database.
    register_apis(DEEPSEEK_PRIMARY)
    configuration = ExecutionStub()
    monkeypatch.setattr(resident_restart_domain, "resolve_account_configuration", configuration.resolve_account)
    monkeypatch.setattr(resident_backend, "resolve_account_configuration", configuration.resolve_account)
    monkeypatch.setattr(resident_restart_domain, "runtime_configuration_catalog", configuration.runtime_catalog)
    monkeypatch.setattr(resident_input_queue_domain, "sync_resident_quota_overrides", lambda _records: True)
    monkeypatch.setattr(
        execution_port,
        "health",
        lambda: {
            "scheduler": {
                "capacity_partitions": {
                    "codex:zero": {
                        "quota_reserve": {
                            "status": "reserved",
                            "remaining": 2.0, "remaining_percent": 2.0, "fresh": True,
                            "observed_at": 1.0,
                        },
                    },
                    "codex:one": {
                        "quota_reserve": {
                            "status": "reserved",
                            "remaining": 2.0, "remaining_percent": 2.0, "fresh": True,
                            "observed_at": 1.0,
                        },
                    },
                },
            },
        },
    )
    monkeypatch.setattr(
        execution_port,
        "configure_binding_quota_overrides",
        lambda policies: {"updated": len(policies)},
    )
    monkeypatch.setattr(
        execution_port,
        "forget_binding_quota_overrides",
        lambda binding_ids: {"removed": len(binding_ids)},
    )
    record = {
        "resident_id": "resident_aaaaaaaaaaaa",
        "backend": "codex",
        "title": "Restart target",
        "intake_open": False,
        "workspace_path": str(load_instance().root / "restart-target-workspace"),
    }
    history = AgentDataStore()
    history.append_agent_segments(record['resident_id'], [
        history.put_segment(segment_type='input', content='old request'),
        history.put_segment(segment_type='model', content='old result'),
    ], actor_ref='test')
    source_session = history.ensure_provider_session(
        provider=source_provider, backend_route_id=source_route,
        backend_session_id="session_old", agent_id=record["resident_id"], actor_ref="alice",
    )
    history.bind_provider_session("resident:" + record["resident_id"], source_session["provider_session_ref"],
                                  harness_tool_names=("hatg_instruction",))
    runtime_settings = {
        "backend": "codex",
        "model": "gpt-6-astra",
        "reasoning_effort": "xhigh",
        "service_tier": "priority",
    }
    monkeypatch.setattr(resident_execution_domain, "resident_agents_instruction_block", lambda record: "<resident-identity>Restart target</resident-identity>")
    monkeypatch.setattr(resident_runtime_domain, "resident_runtime_identity_block", lambda record: "<resident-runtime-identity>Restart target</resident-runtime-identity>")
    monkeypatch.setattr(
        resident_runtime_domain,
        "read_resident_runtime_settings",
        lambda *_args, **_kwargs: dict(runtime_settings),
    )
    monkeypatch.setattr(
        resident_runtime_domain,
        "write_resident_runtime_settings",
        lambda _record, value: runtime_settings.update(value) or dict(runtime_settings),
    )
    monkeypatch.setattr(
        codex_runtime_settings,
        "codex_runtime_catalog",
        lambda **_kwargs: {
            "models": ["gpt-6-astra", "gpt-5.6-sol"],
            "default_model": "gpt-6-astra",
            "default_reasoning_effort": "xhigh",
            "efforts_by_model": {
                "gpt-6-astra": ["high", "xhigh"],
                "gpt-5.6-sol": ["high", "xhigh"],
            },
            "default_efforts_by_model": {
                "gpt-6-astra": "xhigh",
                "gpt-5.6-sol": "high",
            },
            "service_tiers_by_model": {
                "gpt-6-astra": [{"id": "priority"}],
                "gpt-5.6-sol": [{"id": "priority"}],
            },
        },
    )
    operations: list[dict] = []
    saved: list[dict] = []
    target = resident_domain.ResidentRuntimeTarget(
        resident_id=record["resident_id"],
        record=record,
    )
    monkeypatch.setattr(
        resident_record_domain,
        "resolve_resident_runtime_target",
        lambda *_args, **_kwargs: target,
    )
    monkeypatch.setattr(resident_access, "can_manage", lambda *_args: True)
    monkeypatch.setattr(
        resident_backend,
        "session_record",
        lambda *_args, **_kwargs: history.provider_binding("resident:" + record["resident_id"]),
    )
    monkeypatch.setattr(resident_execution_work, "read", lambda *_args: {})
    monkeypatch.setattr(resident_execution_work, "is_active", lambda *_args: False)
    monkeypatch.setattr(
        resident_conversation_stream.get_repository(),
        "current_state",
        lambda *_args, **_kwargs: {"intake_state": "closed"},
    )
    monkeypatch.setattr(
        resident_restart_domain,
        "_session_restart_runtime_options",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        resident_runtime_domain,
        "resident_working_directory",
        lambda *_args, **_kwargs: Path("/tmp/restart-target"),
    )
    monkeypatch.setattr(
        resident_conversation_stream.get_repository(),
        "snapshot",
        lambda *_args, **_kwargs: {
            "messages": [
                {"role": "user", "content_parts": [{"type": "text", "text": "old request"}]},
                {"role": "agent", "content_parts": [{"type": "text", "text": "old result"}]},
            ]
        },
    )

    original_build = resident_session_backend_domain.build_backend_session_operation_request
    def build_request(**kwargs):
        operations.append(kwargs)
        return original_build(**kwargs)
    monkeypatch.setattr(resident_session_backend_domain, "build_backend_session_operation_request", build_request)
    def enqueue(_resident_id, steps):
        operations[-1]["step"] = steps[0]
        return "operation_restart"
    monkeypatch.setattr(resident_record_domain, "lifecycle_operations", lambda: SimpleNamespace(pending=lambda *_: False, enqueue=enqueue))
    monkeypatch.setattr(resident_catalog.get_catalog().state_store, "transaction", lambda commit: commit())
    def resolve_account(provider, route="", **kwargs):
        definition = AGENT_PROVIDERS[provider]
        route = route or definition.default_route or ("zero" if provider == "codex" else "")
        return {"account_id": route, "backend_route_id": route, "capacity_key": f"codex:{route}" if provider == "codex" else f"account:{provider}:{route}",
                "quota_protection_enabled": provider == "codex"}
    monkeypatch.setattr(execution_port, "service", lambda: ExecutionStub(
        resolve_account=resolve_account,
        resume=lambda *args, **kwargs: {"status": "completed", "result": {"status": "completed", "backend_session_id": "session_new"}},
        submit=lambda *_: pytest.fail("Accepted execution must be resumed"),
    ))
    monkeypatch.setattr(
        resident_backend,
        "session_record_for_binding",
        lambda binding_id: {
            "provider": operations[-1]["backend"],
            "backend_session_id": "session_new",
            "backend_route_id": operations[-1].get("backend_route_id", ""),
        } if binding_id.startswith("session-restart:") else {},
    )
    monkeypatch.setattr(
        resident_backend,
        "replace_with_prepared_session",
        lambda *_args, **_kwargs: {
            "previous_binding": {"provider_session_ref": "provider_old"},
            "current_binding": {"provider_session_ref": "provider_new"},
        },
    )
    monkeypatch.setattr(resident_record_domain, "state_lock", nullcontext)
    monkeypatch.setattr(resident_record_domain, "find_resident", lambda *_args: record)
    monkeypatch.setattr(resident_record_domain, "find_resident_canonical", lambda *_args: record)

    def save_record(value, *, commit_configuration=False, commit_intake=False):
        from management_portal.society_service.resident_storage import validate_record_fields
        validate_record_fields(value)
        resident_backend.validate_resident_session_identity(value)
        record.update(value)
        saved.append(dict(value))

    monkeypatch.setattr(
        resident_record_domain,
        "save_resident_record",
        save_record,
    )
    monkeypatch.setattr(
        resident_backend,
        "discard_prepared_session",
        lambda *_args: None,
    )
    return record, operations, saved


def test_account_selection_without_session_saves_only_configuration(monkeypatch, subtests, isolated_portal_case):
    """尚无会话时选择账号仅保存配置且保持幂等"""
    for provider, account, backend, model in [
    ('codex', 'one', 'codex', None), ('claude', '', 'claude', 'deepseek-v4-pro'),
    ('kimi', '', 'kimi', 'kimi-k2.6'),
]:
        with subtests.test(provider=provider, account=account, backend=backend), isolated_portal_case() as case_path:
            with monkeypatch.context() as monkeypatch:
                record, operations, saved = _restart_domain(monkeypatch)
                record.update(backend_route_id='zero')
                monkeypatch.setattr(resident_backend, 'session_record', lambda *_args, **_kwargs: {})
                monkeypatch.setattr(resident_conversation_stream.get_repository(), 'snapshot',
                                    lambda *_args, **_kwargs: pytest.fail('configuration must not read or initialize history'))
                payload = {'target_provider': provider, 'target_account': account,
                           'context_strategy': 'preserve', 'idempotency_key': 'select-without-session'}
                if model:
                    payload['model'] = model
                result = resident_domain.restart_resident_session(record['resident_id'], 'alice', payload)
                assert result['status'] == 'completed'
                assert result['configuration_only'] is True
                assert result['previous_session_retained'] is False
                expected_account = 'deepseek_primary' if provider == 'claude' and not account else account
                assert result['target'] == {'provider': provider, 'account_id': expected_account}
                assert record['backend'] == backend
                assert saved[-1].get('backend_route_id', '') == expected_account
                assert operations == [] and len(saved) == 1
                assert resident_domain.restart_resident_session(record['resident_id'], 'alice', payload) == result
                assert len(saved) == 1


def test_account_selection_reconciles_runtime_tuple_for_target_catalog(
    monkeypatch: pytest.MonkeyPatch,
    isolated_portal_case,
) -> None:
    """选择账号时按目标目录协调模型、推理强度与服务层级"""
    with isolated_portal_case():
        record, operations, saved = _restart_domain(monkeypatch)
        record["backend_route_id"] = "zero"
        monkeypatch.setattr(
            resident_backend,
            "session_record",
            lambda *_args, **_kwargs: {},
        )
        monkeypatch.setattr(
            codex_runtime_settings,
            "codex_runtime_catalog",
            lambda *, account_id: {
                "models": ["account-one-model"],
                "default_model": "account-one-model",
                "efforts_by_model": {"account-one-model": ["medium"]},
                "default_efforts_by_model": {"account-one-model": "medium"},
                "service_tiers_by_model": {"account-one-model": []},
            },
        )

        result = resident_domain.restart_resident_session(
            record["resident_id"],
            "alice",
            {"target_provider": "codex", "target_account": "one"},
        )

        assert result["configuration_only"] is True
        assert operations == []
        assert saved[-1]["backend_route_id"] == "one"
        assert {
            field: runtime_settings_from_parts(saved[-1]["execution_settings"], saved[-1]["harness_config"])[field]
            for field in ("model", "reasoning_effort", "service_tier")
        } == {
            "model": "account-one-model",
            "reasoning_effort": "medium",
            "service_tier": "",
        }












def test_send_controls_depend_on_permissions_not_deleted_initialization_fields(monkeypatch):
    """发送控件按权限开放且忽略已删除初始化字段"""
    from management_portal.society_service import resident_protocol
    record = {"resident_id": "resident_aaaaaaaaaaaa", "backend": "codex"}
    target = SimpleNamespace(resident_id=record["resident_id"], record=record)
    monkeypatch.setattr(resident_protocol.resident_access, "can_manage_resident", lambda *a: True)
    assert resident_protocol._conversation_controls(target, "alice", {})["can_send"]
    monkeypatch.setattr(resident_protocol.resident_access, "can_manage_resident", lambda *a: False)
    assert "无发送权限" in resident_protocol._conversation_controls(target, "visitor", {})["disabled_reason"]


def test_retired_model_handoff_modes_do_not_submit(monkeypatch, subtests, isolated_portal_case):
    import copy
    """移除的模型整理方式明确拒绝，不发送后台整理轮次"""
    for strategy in ('compact','visible','local','native','smart','fresh'):
        with subtests.test(strategy=strategy), isolated_portal_case():
            with monkeypatch.context() as patch:
                record, operations, saved = _restart_domain(patch)
                before = copy.deepcopy(resident_backend.session_record(record))
                with pytest.raises(ValueError, match='使用 preserve'):
                    resident_domain.restart_resident_session(record['resident_id'],'alice',{'context_strategy':strategy})
                assert not operations and not saved
                assert resident_backend.session_record(record) == before


def test_session_initialization_preserves_user_words_contracts(subtests, isolated_portal_case):
    """会话初始化保留用户原话且重启指令参数明确"""
    with subtests.test(contract='test_session_initialization_preserves_user_words'), isolated_portal_case() as case_path:
        content = '请解释 The user interrupted the previous turn on purpose. 的来源。'
        from management_portal.execution.codex_history import response_items_from_segments
        messages = response_items_from_segments([{'segment_id': 'user-input', 'segment_type': 'input', 'content': content}])
        assert messages[0]['content'][0]['text'] == content
    with subtests.test(contract='test_restart_is_one_named_instruction'), isolated_portal_case() as case_path:
        restart = catalog.declaration('RestartSession')
        parameters = {parameter.name: parameter for parameter in restart.parameters}
        assert restart.owner == 'lineage' and restart.mutates and restart.target == 'resident_id'
        assert parameters['resident_id'].required is True
        assert parameters['account_selection'].enum == ('auto', 'fixed')
        assert {'context_strategy', 'active_execution_policy', 'active_job_policy'}.isdisjoint(parameters)
        cancel = {parameter.name: parameter for parameter in catalog.declaration('CancelSessionRestart').parameters}
        assert cancel['resident_id'].required is True and cancel['restart_id'].required is True
        assert catalog.declaration('GetSessionRestart').mutates is False




            # A restart with other parameters replaces this one: test_session_restart_supersede.


def test_manual_session_preparation_does_not_read_health_or_block_on_quota(
    monkeypatch: pytest.MonkeyPatch,
    isolated_portal_case,
) -> None:
    """手动固定账号的无模型准备不因额度被阻挡，业务绑定不获豁免。"""
    with isolated_portal_case():
        record, operations, _saved = _restart_domain(monkeypatch)
        monkeypatch.setattr(execution_port, "health", lambda: pytest.fail("ordinary startup must not read health"))
        result = resident_domain.restart_resident_session(
            record["resident_id"],
            "alice",
            {"target_provider": "codex", "target_account": "one"},
        )
        assert result["status"] == "completed" and result["model_operation"] is False
        assert operations == []
        assert not record.get("quota_reserve_override")












@pytest.mark.parametrize("provider,settings", [
    ("claude", {"model": "selected-model", "reasoning_effort": "high"}),
    ("codex", {"model": "selected-model", "reasoning_effort": "high", "collaboration_mode": "plan", "service_tier": "", "permission_preset": ""}),
    ("provider_api", {"model": "api-model", "prompt_context": {"history": {"enabled": False}},
                      "backend_options": {"parameters": {}, "timeout_seconds": 45}}),
])
def test_restart_session_instruction_maps_arguments(
    monkeypatch: pytest.MonkeyPatch, provider, settings,
) -> None:
    """RestartSession 指令把参数映射到领域请求

    RestartSession 把 Provider、账号、上下文策略和幂等键传给领域，不携带来源 Session 的版本前提。

    :module: resident.session
    :aspects: functional, behavior
    :design: decision_d19b9909a674
    """
    from management_portal.hatg.contracts import InstructionCall, InstructionContext
    from management_portal.hatg.kernel import Invocation
    from management_portal.society_service.isa import residents

    resident_id = "resident_aaaaaaaaaaaa"
    calls: list[tuple] = []

    def restart(target, actor, body):
        # Superseding an unfinished restart happens inside the domain (test_session_restart_supersede).
        calls.append(("restart", target, actor, dict(body)))
        return {"status": "accepted", "restart_id": "restart_new"}

    monkeypatch.setattr(resident_domain, "restart_resident_session", restart)
    invocation = Invocation(
        declaration=catalog.declaration("RestartSession"),
        call=InstructionCall.from_payload({
            "instruction": "RestartSession",
            "arguments": {"resident_id": resident_id, "provider": provider, "account_id": "one",
                          "context_strategy": "preserve", **settings},
            "idempotency_key": "restart-one",
        }),
        context=InstructionContext(caller="alice", entry="web"),
        instruction_id="instruction_111111111111",
    )
    assert residents.restart_session(invocation)["restart_id"] == "restart_new"
    body = {"target_provider": provider, "target_account": "one", "context_strategy": "preserve",
            "idempotency_key": "restart-one", **settings}
    assert calls == [("restart", resident_id, "alice", body)]


def test_deferred_restart_is_durable_idempotent_and_cancellable(monkeypatch):
    """延迟重启持久保存并支持幂等请求与取消

    :module: resident.session
    :aspects: functional, behavior
    :design: decision_44cf587c1721, decision_d19b9909a674
    """
    from management_portal.society_service import resident_deferred_restart
    record, operations, saved = _restart_domain(monkeypatch)
    monkeypatch.setattr(resident_execution_work, "read", lambda *a: {"status": "running", "execution_id": "already-running"})
    monkeypatch.setattr(resident_execution_work, "is_active", lambda *a: True)
    payload = {"target_provider": "claude", "model": "deepseek-v4-pro", "context_strategy": "preserve", "active_execution_policy": "wait", "idempotency_key": "deferred-once"}
    receipt = resident_domain.restart_resident_session(record["resident_id"], "alice", payload)
    assert receipt["phase"] == "waiting_current_execution" and not operations
    current = saved[-1]
    monkeypatch.setattr(resident_record_domain, "find_resident", lambda *a: current)
    target = resident_domain.ResidentRuntimeTarget(resident_id=record["resident_id"], record=current)
    monkeypatch.setattr(resident_record_domain, "resolve_resident_runtime_target", lambda *a, **kw: target)
    assert resident_domain.restart_resident_session(record["resident_id"], "alice", payload)["restart_id"] == receipt["restart_id"]
    assert resident_deferred_restart.advance(current) is False
    assert not operations
    cancelled = resident_domain.cancel_resident_session_restart(record["resident_id"], "alice", receipt["restart_id"])
    assert cancelled["status"] == "cancelled"
    assert "deferred" not in saved[-1]["session_restart"]


@pytest.mark.parametrize("portal_reason", ["", "steward_quota_recovery"])
def test_deferred_restart_retains_origin_and_runs_only_after_current_turn(monkeypatch, portal_reason):
    """延迟重启保留发起者和原因且在当前轮结束后启动"""
    from management_portal.society_service import resident_deferred_restart
    record, operations, saved = _restart_domain(monkeypatch)
    monkeypatch.setattr(resident_execution_work, "read", lambda *a: {"status": "running", "execution_id": "already-running"})
    monkeypatch.setattr(resident_execution_work, "is_active", lambda *a: True)
    receipt = resident_restart_domain.restart_resident_session(
        record["resident_id"], "alice", {"target_provider": "claude", "model": "deepseek-v4-pro", "context_strategy": "preserve", "idempotency_key": "finish-first"},
        portal_reason=portal_reason,
    )
    assert receipt.get("reason", "") == portal_reason
    current = saved[-1]
    monkeypatch.setattr(resident_record_domain, "find_resident", lambda *a: current)
    calls = []
    def restart(rid, actor, body, **options):
        calls.append((rid, body, actor, options))
        current["session_restart"] = {**current["session_restart"], "request_id": body["idempotency_key"], "status": "completed"}
        return current["session_restart"]
    monkeypatch.setattr(resident_restart_domain, "restart_resident_session", restart)
    assert resident_deferred_restart.advance(current) is False and not calls
    monkeypatch.setattr(resident_execution_work, "is_active", lambda *a: False)
    assert resident_deferred_restart.advance(current) is False
    assert len(calls) == 1 and calls[0][2] == "alice"
    assert calls[0][1]["idempotency_key"] == "finish-first:execute"
    assert calls[0][3] == {"portal_reason": portal_reason}
    assert "deferred" not in saved[-1]["session_restart"]


def test_deferred_restart_does_not_reopen_a_manually_closed_intake(monkeypatch):
    """取消延迟重启时保留手工关闭的接收状态

    :module: resident.session
    :aspects: functional, behavior
    :design: decision_44cf587c1721, decision_d19b9909a674
    """
    from management_portal.society_service import resident_deferred_restart
    record, operations, saved = _restart_domain(monkeypatch)
    monkeypatch.setattr(resident_execution_work, "read", lambda *a: {"status": "running", "execution_id": "current"})
    monkeypatch.setattr(resident_execution_work, "is_active", lambda *a: True)
    receipt = resident_domain.restart_resident_session(record["resident_id"], "alice", {"target_provider": "claude", "model": "deepseek-v4-pro", "context_strategy": "preserve", "idempotency_key": "keep-closed"})
    current = saved[-1]
    assert current["session_restart"]["deferred"]["intake_before"] == "closed"
    monkeypatch.setattr(resident_input_queue_domain, "commit_resident_intake_state", lambda *a, **k: pytest.fail("manual intake must stay closed"))
    assert resident_deferred_restart.cancel(current, receipt["restart_id"])["status"] == "cancelled"
    assert not operations


@pytest.mark.parametrize("model,effort,valid", [("chosen-model", "high", True), ("unknown-model", "high", False), ("chosen-model", "unsupported", False)])
def test_migration_validates_target_model_and_effort_before_changing_configuration(monkeypatch, isolated_portal_case, model, effort, valid):
    """迁移前校验目标模型与推理强度且拒绝无效配置

    :module: resident.session
    :aspects: functional, behavior
    :design: decision_44cf587c1721, decision_d19b9909a674
    """
    from management_portal.execution.account_adapters import CodexAccounts
    with isolated_portal_case():
        record, operations, saved = _restart_domain(monkeypatch)
        monkeypatch.setattr(resident_backend, "session_record", lambda *_args, **_kwargs: {})
        target_catalog = {
            "models": ["chosen-model"], "default_model": "chosen-model",
            "efforts_by_model": {"chosen-model": ["medium", "high"]},
            "default_efforts_by_model": {"chosen-model": "medium"},
            "service_tiers_by_model": {"chosen-model": []},
        }
        monkeypatch.setattr(codex_runtime_settings, "codex_runtime_catalog", lambda *, account_id: target_catalog)
        monkeypatch.setattr(CodexAccounts, "catalog", lambda self, account: target_catalog)
        body = {"target_provider": "codex", "target_account": "one", "model": model, "reasoning_effort": effort, "collaboration_mode": "plan", "service_tier": "", "permission_preset": "auto-review"}
        if not valid:
            with pytest.raises(ValueError):
                resident_domain.restart_resident_session(record["resident_id"], "alice", body)
            assert operations == [] and saved == []
        else:
            result = resident_domain.restart_resident_session(record["resident_id"], "alice", body)
            assert result["configuration_only"] is True
            assert runtime_settings_from_parts(saved[-1]["execution_settings"], saved[-1]["harness_config"])["model"] == model
            assert runtime_settings_from_parts(saved[-1]["execution_settings"], saved[-1]["harness_config"])["reasoning_effort"] == effort
            settings = runtime_settings_from_parts(saved[-1]["execution_settings"], saved[-1]["harness_config"])
            assert settings["collaboration_mode"] == "plan"
            assert settings["service_tier"] == ""
            assert settings["permission_preset"] == "auto-review"




def test_restart_rechecks_binding_identity_without_rejecting_metadata_refresh(monkeypatch):
    """重启复核绑定身份时允许上下文元数据刷新"""
    record, operations, _saved = _restart_domain(monkeypatch)
    initial = dict(resident_backend.session_record(record), context_injection_count=1, updated_at="earlier")
    checks = []

    def session_record(_record, *, expected_binding=None):
        if expected_binding is not None:
            checks.append(expected_binding)
            assert expected_binding == initial
            return dict(initial, context_injection_count=2, updated_at="later")
        return dict(initial)

    monkeypatch.setattr(resident_backend, "session_record", session_record)
    result = resident_domain.restart_resident_session(
        record["resident_id"], "alice", {"target_provider": "claude", "model": "deepseek-v4-pro", "context_strategy": "preserve"},
    )
    assert result["status"] == "completed"
    assert checks and not operations


def _model_api_restart(monkeypatch, *, protocol='openai_compatible_chat'):
    from management_portal.ai_provider_registry import shared_ai_provider_registry
    record, operations, saved = _restart_domain(monkeypatch)
    entry = {'id': 'test-api', 'kind': protocol, 'enabled': True, 'default_model': 'api-model', 'models': [{'id': 'api-model'}]}
    monkeypatch.setattr(shared_ai_provider_registry, 'get', lambda _: entry)
    original = resident_restart_domain.resolve_account_configuration
    def resolve(provider, route='', **kwargs):
        if provider == 'test-api':
            return {'account_id': 'test-api', 'backend_route_id': 'test-api', 'quota_protection_enabled': False}
        return original(provider, route, **kwargs)
    monkeypatch.setattr(resident_restart_domain, 'resolve_account_configuration', resolve)
    monkeypatch.setattr(resident_backend, 'resolve_account_configuration', resolve)
    monkeypatch.setattr(resident_restart_domain, 'runtime_configuration_catalog', lambda: {'backends': [
        {'provider': 'codex', 'label': 'Codex', 'operations': ['fork'], 'routes': [{'backend_route_id': 'zero', 'label': 'zero'}]},
        {'provider': 'provider_api', 'label': 'Model API', 'operations': [], 'routes': [{'backend_route_id': 'test-api', 'label': 'test-api', 'account_id': 'test-api'}]},
    ]})
    return record, operations, saved


@pytest.mark.parametrize('protocol', ['openai_compatible_chat', 'typesafe_system_one'])
def test_native_to_model_api_migration_saves_configuration_without_model_call(monkeypatch, protocol):
    """原生后端迁移到模型 API 仅保存配置且不调用模型"""
    record, operations, saved = _model_api_restart(monkeypatch, protocol=protocol)
    detached = []
    monkeypatch.setattr(resident_backend, 'delete_session_identity', lambda *args, **kwargs: detached.append(kwargs))
    options = resident_restart_domain.get_resident_restart_options(record['resident_id'], 'alice')
    assert 'provider_api' in [row['id'] for row in options['providers']]
    questions = {'ready': {'type': 'noul', 'instructions': 'Is it ready?'}}
    body = {'target_provider': 'provider_api', 'target_account': 'test-api', 'model': 'api-model', 'context_strategy': 'preserve',
            'backend_options': {'parameters': {'questions': questions} if protocol == 'typesafe_system_one' else {}, 'max_retries': 0},
            'idempotency_key': 'migrate-model-api'}
    result = resident_domain.restart_resident_session(record['resident_id'], 'alice', body)
    assert result['status'] == 'completed' and result['configuration_only']
    assert result['previous_session_retained']
    assert record['backend'] == 'provider_api' and record['backend_route_id'] == 'test-api'
    assert record['execution_settings']['model'] == 'api-model'
    assert record['execution_settings']['prompt_context']['history']['enabled'] == (protocol == 'openai_compatible_chat')
    assert operations == [] and len(detached) == 1
    assert resident_domain.restart_resident_session(record['resident_id'], 'alice', body) == result
    assert len(detached) == 1




def test_model_api_migration_validation_and_save_failure_preserve_binding(monkeypatch):
    """迁移到模型 API 校验或保存失败时保留原绑定"""
    record, operations, saved = _model_api_restart(monkeypatch)
    detached, restored = [], []
    monkeypatch.setattr(resident_backend, 'delete_session_identity', lambda *a, **k: detached.append(True))
    monkeypatch.setattr(resident_backend, 'restore_replaced_session', lambda record, replacement: restored.append(replacement))
    body = {'target_provider': 'provider_api', 'target_account': 'test-api', 'model': 'api-model', 'context_strategy': 'preserve'}
    with pytest.raises(ValueError, match='does not offer'):
        resident_domain.restart_resident_session(record['resident_id'], 'alice', {**body, 'model': 'wrong'})
    assert not detached and record['backend'] == 'codex'
    def fail(*a, **k): raise RuntimeError('save failed')
    monkeypatch.setattr(resident_record_domain, 'save_resident_record', fail)
    with pytest.raises(RuntimeError, match='save failed'):
        resident_domain.restart_resident_session(record['resident_id'], 'alice', body)
    assert detached and restored[0]['current_binding']['detached'] is True
    assert record['backend'] == 'codex'


def test_detached_native_binding_can_be_restored_only_while_absent():
    """已分离的原生会话绑定仅可在目标为空时恢复"""
    store = AgentDataStore()
    binding_id = 'resident:resident_aaaaaaaaaaaa'
    _bind(store, binding_id, 'codex', 'prior', backend_route_id='zero')
    before = store.provider_binding(binding_id)
    store.delete_provider_binding(binding_id, expected_provider_session_ref=before['provider_session_ref'])
    restored = store.restore_provider_binding(binding_id, binding=before, expected_provider_session_ref='', expected_absent=True)
    assert restored['backend_session_id'] == 'prior'
    with pytest.raises(RuntimeError, match='changed after detachment'):
        store.restore_provider_binding(binding_id, binding=before, expected_provider_session_ref='', expected_absent=True)


@pytest.mark.parametrize("source_provider", ["codex", "antigravity"])
def test_explicit_auto_restart_resolves_by_target_model_without_pinning_source(monkeypatch, source_provider):
    calls = []
    def resolve(provider, route, **kwargs):
        calls.append((provider, route, kwargs))
        return {"account_id": "selected", "backend_route_id": "selected"}
    monkeypatch.setattr(resident_restart_domain, "resolve_account_configuration", resolve)
    target = resident_restart_domain._restart_target(
        {"account_selection": "auto", "model": "target-model", "reasoning_effort": "high"},
        {"account_id": "old-account"}, source_provider, "codex")
    assert target.route == "selected"
    assert calls == [("codex", "", {"runtime_options": {"model": "target-model", "reasoning_effort": "high"}})]


@pytest.mark.parametrize("body,provider", [
    ({"account_selection": "auto", "target_account": "one"}, "codex"),
    ({"account_selection": "auto"}, "provider_api"),
    ({"account_selection": "invalid"}, "codex"),
])
def test_invalid_restart_account_policy_is_rejected_before_resolution(monkeypatch, body, provider):
    monkeypatch.setattr(resident_restart_domain, "resolve_account_configuration",
                        lambda *a, **kw: pytest.fail("invalid requests must not resolve or mutate an account"))
    with pytest.raises(ValueError):
        resident_restart_domain._restart_target(body, {"account_id": "old"}, "antigravity", provider)


@pytest.mark.parametrize("policy", ["auto", "fixed"])
def test_cross_provider_restart_commits_account_policy(monkeypatch, isolated_portal_case, policy):
    with isolated_portal_case():
        record, operations, saved = _restart_domain(monkeypatch, source_provider="antigravity", source_route="ag")
        record["backend"] = "antigravity"
        from management_portal.execution.account_adapters import CodexAccounts
        monkeypatch.setattr(CodexAccounts, "catalog", lambda self, account: codex_runtime_settings.codex_runtime_catalog(account_id=account.account_id))
        monkeypatch.setattr(resident_backend, "session_record", lambda *a, **kw: {})
        calls = []
        def resolve(provider, route="", **kwargs):
            calls.append((provider, route, kwargs))
            return {"account_id": "one", "backend_route_id": "one", "capacity_key": "codex:one"}
        monkeypatch.setattr(resident_restart_domain, "resolve_account_configuration", resolve)
        body = {"target_provider": "codex", "account_selection": policy, "model": "gpt-6-astra", "reasoning_effort": "high"}
        if policy == "fixed":
            body["target_account"] = "one"
        result = resident_domain.restart_resident_session(record["resident_id"], "alice", body)
        assert result["configuration_only"] is True
        assert operations == []
        settings = runtime_settings_from_parts(saved[-1]["execution_settings"], saved[-1]["harness_config"])
        assert settings["account_selection"] == policy
        assert settings["model"] == "gpt-6-astra"
        assert settings["reasoning_effort"] == "high"
        assert calls[0][1] == ("" if policy == "auto" else "one")


def test_restart_isa_passes_explicit_auto_account_selection(monkeypatch):
    from management_portal.society_service.isa import residents
    payload = {"resident_id": "test", "provider": "codex", "model": "target", "account_selection": "auto"}
    calls = []
    monkeypatch.setattr(resident_domain, "restart_resident_session",
                        lambda *args: calls.append(args) or {"status": "accepted"})
    residents.restart_session(SimpleNamespace(argument=payload.get, caller="alice", idempotency_key="same-request"))
    assert calls == [("test", "alice", {"target_provider": "codex", "model": "target", "account_selection": "auto", "idempotency_key": "same-request"})]
