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

import pytest

from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.hatg import catalog
from management_portal.society_service import resident_domain
from management_portal.society_service import resident_registration_domain


from management_portal.society_service import resident_execution_domain, resident_intake_domain, resident_lifecycle_domain, resident_runtime_domain, resident_workspace_domain


from management_portal.society_service import resident_directory_domain, resident_restart_domain, resident_session_backend_domain
from management_portal.execution import codex_runtime_settings
from tests.cases.model_api_test_support import DEEPSEEK_PRIMARY, register_apis

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
    monkeypatch.setattr(resident_intake_domain, "sync_resident_quota_overrides", lambda _records: True)
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
    }
    runtime_settings = {
        "backend": "codex",
        "model": "gpt-6-astra",
        "reasoning_effort": "xhigh",
        "service_tier": "priority",
    }
    monkeypatch.setattr(resident_runtime_domain, "resident_agents_instruction_block", lambda record: "<resident-identity>Restart target</resident-identity>")
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
        resident_workspace_domain,
        "resolve_resident_runtime_target",
        lambda *_args, **_kwargs: target,
    )
    monkeypatch.setattr(resident_directory_domain, "can_manage", lambda *_args: True)
    monkeypatch.setattr(
        resident_backend,
        "session_record",
        lambda *_args, **_kwargs: {
            "binding_id": "resident:resident_aaaaaaaaaaaa",
            "provider": source_provider,
            "backend_session_id": "session_old",
            "backend_route_id": source_route,
            "harness_tool_names": ("hatg_instruction",),
        },
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
    monkeypatch.setattr(resident_lifecycle_domain, "lifecycle_operations", lambda: SimpleNamespace(pending=lambda *_: False, enqueue=enqueue))
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
    monkeypatch.setattr(resident_lifecycle_domain, "state_lock", nullcontext)
    monkeypatch.setattr(resident_workspace_domain, "find_resident", lambda *_args: record)
    monkeypatch.setattr(resident_workspace_domain, "find_resident_canonical", lambda *_args: record)

    def save_record(value, *, commit_configuration=False, commit_intake=False):
        from management_portal.society_service.resident_storage import validate_record_fields
        validate_record_fields(value)
        resident_backend.validate_resident_session_identity(value)
        record.update(value)
        saved.append(dict(value))

    monkeypatch.setattr(
        resident_workspace_domain,
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
    for provider, account, backend in [
    ('codex', 'one', 'codex'), ('claude', '', 'claude'), ('kimi', '', 'kimi'),
]:
        with subtests.test(provider=provider, account=account, backend=backend), isolated_portal_case() as case_path:
            with monkeypatch.context() as monkeypatch:
                record, operations, saved = _restart_domain(monkeypatch)
                record.update(backend_route_id='zero')
                monkeypatch.setattr(resident_backend, 'session_record', lambda *_args, **_kwargs: {})
                monkeypatch.setattr(resident_conversation_stream.get_repository(), 'snapshot',
                                    lambda *_args, **_kwargs: pytest.fail('configuration must not read or initialize history'))
                payload = {'target_provider': provider, 'target_account': account,
                           'context_strategy': 'fresh', 'idempotency_key': 'select-without-session'}
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


def test_restart_options_show_unbound_residents_configured_account_contracts(monkeypatch, subtests, isolated_portal_case):
    """重启选项展示未绑定 Resident 的已配置账号"""
    with subtests.test(contract='test_restart_options_show_unbound_residents_configured_account'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            record, _, _ = _restart_domain(monkeypatch)
            record['backend_route_id'] = 'one'
            monkeypatch.setattr(resident_backend, 'session_record', lambda *_args, **_kwargs: {})
            options = resident_domain.get_resident_restart_options(record['resident_id'], 'alice')
            assert {key: options['source'][key] for key in ('provider', 'account_id', 'has_session')} == {'provider': 'codex', 'account_id': 'one', 'has_session': False}
            assert len(options['source']['generation']) == 64
            assert options['source']['binding_status'] == 'unbound'
            assert options['source']['backend_session_id'] == ''
            assert options['source']['provider_session_ref'] == ''
    with subtests.test(contract='test_restart_options_project_current_binding_snapshot'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            record, _, _ = _restart_domain(monkeypatch)
            binding = {'provider': 'codex', 'backend_route_id': 'zero', 'backend_session_id': 'session-bound', 'provider_session_ref': 'provider-session-bound'}
            monkeypatch.setattr(resident_backend, 'session_record', lambda *_args, **_kwargs: binding)
            source = resident_domain.get_resident_restart_options(record['resident_id'], 'alice')['source']
            assert source['binding_status'] == 'current'
            assert source['backend_session_id'] == binding['backend_session_id']
            assert source['provider_session_ref'] == binding['provider_session_ref']
            assert source['account_id'] == binding['backend_route_id']
            binding['backend_session_id'] = 'session-replaced'
            replacement = resident_domain.get_resident_restart_options(record['resident_id'], 'alice')['source']
            assert replacement['generation'] != source['generation']
            assert source['backend_session_id'] == 'session-bound'
    with subtests.test(contract='test_account_selection_rechecks_session_before_saving'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            record, operations, saved = _restart_domain(monkeypatch)
            bindings = iter([{}, {'provider': 'codex', 'backend_session_id': 'concurrent-session', 'backend_route_id': 'zero'}])
            monkeypatch.setattr(resident_backend, 'session_record', lambda *_args, **_kwargs: next(bindings))
            with pytest.raises(ValueError, match='Session.*变化'):
                resident_domain.restart_resident_session(record['resident_id'], 'alice', {'target_account': 'one'})
            assert operations == [] and saved == []
    with subtests.test(contract='test_unbound_account_selection_does_not_change_running_work'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            record, operations, saved = _restart_domain(monkeypatch)
            monkeypatch.setattr(resident_backend, 'session_record', lambda *_args, **_kwargs: {})
            monkeypatch.setattr(resident_execution_work, 'read', lambda *_args: {'status': 'running'})
            monkeypatch.setattr(resident_execution_work, 'is_active', lambda *_args: True)
            receipt = resident_domain.restart_resident_session(record['resident_id'], 'alice', {'target_account': 'one'})
            assert receipt['status'] == 'accepted' and receipt['phase'] == 'waiting_current_execution'
            assert operations == [] and saved[-1]['backend'] == 'codex'
            assert saved[-1]['session_restart']['deferred']['payload']['target_account'] == 'one'
    with subtests.test(contract='test_unbound_account_selection_preserves_and_reroutes_queued_execution'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            record, operations, saved = _restart_domain(monkeypatch)
            job = { 'execution_id': 'execution_current', 'actor': 'alice', "submitted_at": "2026-10-14T00:00:00Z"}
            reassigned = []
            monkeypatch.setattr(resident_backend, 'session_record', lambda *_args, **_kwargs: {})
            monkeypatch.setattr(resident_execution_work, 'read', lambda *_args: job)
            monkeypatch.setattr(resident_execution_work, 'is_active', lambda value: bool(value))
            monkeypatch.setattr(execution_port, 'service', lambda: ExecutionStub(
                reassign_queued_route=lambda *args, **kwargs: reassigned.append((args, kwargs)) or {'status': 'queued'},
            ))
            result = resident_domain.restart_resident_session(record['resident_id'], 'alice', {
                'target_account': 'one', 'active_execution_policy': 'wait',
            })
            assert result['status'] == 'completed'
            assert result['configuration_only'] is True
            assert result['active_execution_reassigned'] is True
            assert result['execution_id'] == 'execution_current'
            assert reassigned == [(('execution_current',), {
                'actor': 'alice', 'backend_route_id': 'one', 'performed_by': 'alice',
            })]
            assert operations == [] and saved[-1]['backend_route_id'] == 'one'
    with subtests.test(contract='test_bound_session_restart_preserves_intake_held_input'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            record, operations, _saved = _restart_domain(monkeypatch)
            held = {
                'execution_id': 'job_held', 'user_message_id': 'msg_held',
                resident_state.INTAKE_FIELD: resident_state.INTAKE_CLOSED,
                'activation_policy': 'enqueue_and_trigger',
            }
            monkeypatch.setattr(resident_execution_work, 'read', lambda *_args: held)
            monkeypatch.setattr(resident_execution_work, 'is_active', lambda value: bool(value.get('execution_id')))
            monkeypatch.setattr(resident_execution_work, 'write', lambda _record, value: held.update(value))
            monkeypatch.setattr(
                resident_conversation_stream.get_repository(),
                'set_state',
                lambda *_args, **_kwargs: None,
            )
            cancelled = []
            monkeypatch.setattr(resident_intake_domain, 'request_resident_execution_cancellation', lambda *args: cancelled.append(args))
            result = resident_domain.restart_resident_session(record['resident_id'], 'alice', {
                'target_account': 'one', 'active_execution_policy': 'wait',
            })
            assert result['status'] == 'accepted'
            assert result['preserved_held_input'] == {
                'execution_id': 'job_held', 'message_id': 'msg_held',
            }
            assert operations and cancelled == []
            resident_restart_domain.resume_resident_session_restart(operations[-1]['step'])
            assert record['session_restart']['status'] == 'completed'
            assert record["intake_open"] is False
    with subtests.test(contract='test_unbound_account_selection_preserves_manual_intake_closure'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            record, operations, _saved = _restart_domain(monkeypatch)
            held = {
                'execution_id': 'job_held', 'user_message_id': 'msg_held',
                resident_state.INTAKE_FIELD: resident_state.INTAKE_CLOSED,
                'activation_policy': 'enqueue_and_trigger',
            }
            resumed = []
            monkeypatch.setattr(resident_backend, 'session_record', lambda *_args, **_kwargs: {})
            monkeypatch.setattr(resident_execution_work, 'read', lambda *_args: held)
            monkeypatch.setattr(resident_execution_work, 'is_active', lambda value: bool(value.get('execution_id')))
            monkeypatch.setattr(resident_execution_work, 'write', lambda _record, value: held.update(value))
            monkeypatch.setattr(
                resident_conversation_stream.get_repository(),
                'set_state',
                lambda *_args, **_kwargs: None,
            )
            monkeypatch.setattr(resident_execution_domain, 'resume_resident_execution', lambda resident_id: resumed.append(resident_id))
            result = resident_domain.restart_resident_session(record['resident_id'], 'alice', {
                'target_account': 'one', 'active_execution_policy': 'wait',
            })
            assert result['status'] == 'completed'
            assert operations == []
            assert record["intake_open"] is False
            assert resumed == []
    with subtests.test(contract='test_restart_cancellation_carries_the_selected_input_identity'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            record, operations, _saved = _restart_domain(monkeypatch)
            job = {'execution_id': 'execution_current', 'user_message_id': 'msg_current'}
            cancellations = []
            monkeypatch.setattr(resident_execution_work, 'read', lambda *_args: job)
            monkeypatch.setattr(resident_execution_work, 'is_active', lambda value: bool(value))
            monkeypatch.setattr(resident_intake_domain, 'request_resident_execution_cancellation', lambda *args: cancellations.append(args))
            with pytest.raises(RuntimeError, match='当前工作正在停止'):
                resident_domain.restart_resident_session(record['resident_id'], 'alice', {'active_execution_policy': 'cancel'})
            assert cancellations == [(record['resident_id'], 'alice', 'msg_current')]
            assert operations == []
    with subtests.test(contract='test_cross_provider_smart_restart_uses_portable_handoff_and_replaces'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            record, operations, saved = _restart_domain(monkeypatch)
            result = resident_domain.restart_resident_session(record['resident_id'], 'alice', {'target_provider': 'claude', 'context_strategy': 'smart'})
            assert result['status'] == 'accepted'
            assert result['context_strategy'] == 'compact'
            assert operations[0]['backend'] == 'claude'
            assert operations[0]['operation'] == 'turn'
            assert 'old request' in operations[0]['prompt']
            resident_restart_domain.resume_resident_session_restart(operations[0]['step'])
            assert saved[-1]['session_restart']['status'] == 'completed'
            assert saved[-1]['resident_id'] == record['resident_id']
            assert saved[-1]['backend'] == 'claude'
            assert saved[-1]['session_restart']['context_strategy'] == 'compact'
            assert saved[-1]['session_restart']['target'] == {'provider': 'claude', 'account_id': 'deepseek_primary'}


@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled"])
def test_fresh_restart_resets_model_context_only_after_success(monkeypatch, outcome):
    """全新重启仅在初始化成功后重置模型上下文"""
    record, operations, _saved = _restart_domain(monkeypatch)
    resets = []
    monkeypatch.setattr(execution_port, "reset_provider_context", lambda *args, **kwargs: resets.append((args, kwargs)))
    monkeypatch.setattr(resident_conversation_stream.get_repository(), "snapshot",
                        lambda *a, **kw: pytest.fail("Fresh restart must not read old conversation"))
    result = resident_domain.restart_resident_session(record["resident_id"], "alice", {"context_strategy": "fresh"})
    assert result["status"] == "accepted"
    assert operations[0]["operation"] == "turn" and not operations[0]["source_binding_id"]
    if outcome != "completed":
        monkeypatch.setattr(execution_port, "service", lambda: ExecutionStub(
            resume=lambda *a, **kw: {"status": outcome, "result": {"status": outcome, "error": outcome}},
        ))
    if outcome == "cancelled":
        record["session_restart"]["status"] = "cancelling"
    resident_restart_domain.resume_resident_session_restart(operations[0]["step"])
    assert bool(resets) is (outcome == "completed")
    if resets:
        assert resets[0][0] == (record["resident_id"],)
        assert resets[0][1]["reset_at"] == record["session_restart"]["submitted_at"]


def test_fresh_recovery_does_not_reassign_an_active_unbound_execution(monkeypatch):
    """无绑定执行仍活跃时全新重启等待当前执行结束"""
    record, operations, _saved = _restart_domain(monkeypatch)
    monkeypatch.setattr(execution_port, "sessions", lambda: SimpleNamespace(has_accepted_input=lambda *a, **kw: False))
    monkeypatch.setattr(resident_backend, "session_record", lambda *a, **kw: {})
    monkeypatch.setattr(resident_execution_work, "read", lambda *a: {"execution_id": "queued-one", "status": "running"})
    monkeypatch.setattr(resident_execution_work, "is_active", lambda *a: True)
    monkeypatch.setattr(resident_execution_work, "is_execution_held", lambda *a: False)
    monkeypatch.setattr(execution_port, "service", lambda: ExecutionStub(
        reassign_queued_route=lambda *a, **kw: pytest.fail("Recovery must not change an active Execution before rejecting it"),
    ))
    receipt = resident_domain.restart_resident_session(record["resident_id"], "alice", {"context_strategy": "fresh"})
    assert receipt["phase"] == "waiting_current_execution"
    assert operations == []


def test_send_controls_depend_on_permissions_not_retired_initialization_fields(monkeypatch):
    """发送控件按权限开放且忽略已退役初始化字段"""
    from management_portal.society_service import resident_protocol
    record = {"resident_id": "resident_aaaaaaaaaaaa", "backend": "codex"}
    target = SimpleNamespace(resident_id=record["resident_id"], record=record)
    monkeypatch.setattr(resident_protocol.resident_access, "can_manage_resident", lambda *a: True)
    assert resident_protocol._conversation_controls(target, "alice", {})["can_send"]
    monkeypatch.setattr(resident_protocol.resident_access, "can_manage_resident", lambda *a: False)
    assert "无发送权限" in resident_protocol._conversation_controls(target, "visitor", {})["disabled_reason"]


def test_session_initialization_prompt_is_bounded_to_current_task(monkeypatch, subtests, isolated_portal_case):
    """会话初始化限定当前初始化任务且按策略携带历史"""
    for strategy in ["fresh", "compact", "visible"]:
        with subtests.test(strategy=strategy), isolated_portal_case() as case_path:
            with monkeypatch.context() as monkeypatch:
                record, operations, _saved = _restart_domain(monkeypatch)
                result = resident_domain.restart_resident_session(
                    record["resident_id"],
                    "alice",
                    {"target_provider": "claude", "context_strategy": strategy},
                )
                assert result["status"] == "accepted"
                prompt = operations[0]["prompt"]
                parts = operations[0]["system_instructions"].parts
                assert any(part.name == "resident_md" and "Restart target" in part.content for part in parts)
                assert "确认初始化完成" in prompt
                assert "本次初始化不派发业务任务，也不启动后台目标" in prompt
                for wording in ("等待用户", "下一条消息", "下一轮", "Handoff", "不要恢复"):
                    assert wording not in prompt
                assert ("old request" in prompt) is (strategy != "fresh")


def test_session_initialization_preserves_user_words_contracts(subtests, isolated_portal_case):
    """会话初始化保留用户原话且重启指令参数明确"""
    with subtests.test(contract='test_session_initialization_preserves_user_words'), isolated_portal_case() as case_path:
        content = '请解释 The user interrupted the previous turn on purpose. 的来源。'
        prompt = resident_restart_domain._session_restart_handoff_prompt({'resident_id': 'resident_test', 'codex_goal': {'objective': 'current task'}}, {'messages': [{'role': 'user', 'content_parts': [{'type': 'text', 'text': content}]}]}, strategy='visible')
        assert f'user: {content}' in prompt
    with subtests.test(contract='test_restart_is_one_named_instruction'), isolated_portal_case() as case_path:
        restart = catalog.declaration('RestartSession')
        parameters = {parameter.name: parameter for parameter in restart.parameters}
        assert restart.owner == 'lineage' and restart.mutates and restart.target == 'resident_id'
        assert parameters['resident_id'].required is True
        assert parameters['context_strategy'].enum == ('smart', 'native', 'compact', 'visible', 'fresh')
        assert parameters['active_execution_policy'].enum == ('wait', 'cancel')
        assert 'active_job_policy' not in parameters
        cancel = {parameter.name: parameter for parameter in catalog.declaration('CancelSessionRestart').parameters}
        assert cancel['resident_id'].required is True and cancel['restart_id'].required is True
        assert catalog.declaration('GetSessionRestart').mutates is False


@pytest.mark.parametrize("transitions", [(), ("close",), ("open",), ("open", "close")])
@pytest.mark.parametrize("backend_fails", [False, True])
def test_restart_restores_only_its_own_intake_transition(monkeypatch, transitions, backend_fails):
    """重启恢复接收状态时保留期间的人为切换"""
    record, _operations, saved = _restart_domain(monkeypatch)
    record["intake_open"] = True
    protocol = {"intake_state": "open"}
    repository = SimpleNamespace(
        current_state=lambda *_: dict(protocol),
        published_state=lambda *_: dict(protocol),
        set_state=lambda _resident_id, value: protocol.update(value),
    )
    monkeypatch.setattr(resident_conversation_stream, "get_repository", lambda: repository)
    monkeypatch.setattr(resident_intake_domain, "ensure_resident_visible_to_actor", lambda *_: None)

    def save(value, *, commit_configuration=False, commit_intake=False):
        record.update(value)
        saved.append(dict(value))

    monkeypatch.setattr(resident_workspace_domain, "save_resident_record", save)
    def resume(*args, **kwargs):
        assert protocol["intake_state"] == "closed"
        for transition in transitions:
            resident_domain.transition_resident_state(record["resident_id"], "alice", transition=transition)
        return {"status": "failed" if backend_fails else "completed", "result": {
            "status": "failed" if backend_fails else "completed",
            "backend_session_id": "session_new", "error": "provider unavailable" if backend_fails else "",
        }}
    monkeypatch.setattr(execution_port, "service", lambda: ExecutionStub(resume=resume))
    assert resident_domain.restart_resident_session(record["resident_id"], "alice", {"context_strategy": "native"})["status"] == "accepted"
    resident_restart_domain.resume_resident_session_restart(_operations[0]["step"])
    assert saved[-1]["session_restart"]["status"] == ("failed" if backend_fails else "completed")
    assert protocol["intake_state"] == ("closed" if transitions and transitions[-1] == "close" else "open")
    if transitions:
        assert record["quota_reserve_override"]["enabled"] == (transitions[-1] == "open")
    else:
        assert "quota_reserve_override" not in record


def test_same_provider_smart_restart_uses_native_fork_contracts(monkeypatch, subtests, isolated_portal_case):
    """同后端同账号智能重启使用原生 Fork"""
    with subtests.test(contract='test_same_provider_smart_restart_uses_native_fork'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            record, operations, _saved = _restart_domain(monkeypatch)
            result = resident_domain.restart_resident_session(record['resident_id'], 'alice', {'target_provider': 'codex', 'context_strategy': 'smart'})
            assert result['status'] == 'accepted'
            assert result['context_strategy'] == 'native'
            assert operations[0]['backend'] == 'codex'
            assert operations[0]['operation'] == 'fork'
    with subtests.test(contract='test_cross_account_smart_restart_uses_portable_handoff'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            record, operations, saved = _restart_domain(monkeypatch)
            result = resident_domain.restart_resident_session(record['resident_id'], 'alice', {'target_provider': 'codex', 'target_account': 'one', 'context_strategy': 'smart'})
            assert result['status'] == 'accepted'
            assert result['context_strategy'] == 'compact'
            assert operations[0]['backend'] == 'codex'
            assert operations[0]['backend_route_id'] == 'one'
            assert operations[0]['operation'] == 'turn'
            resident_restart_domain.resume_resident_session_restart(operations[0]['step'])
            assert saved[-1]['backend_route_id'] == 'one'
    with subtests.test(contract='test_native_restart_rejects_cross_provider_before_creating_session'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            record, operations, _saved = _restart_domain(monkeypatch)
            with pytest.raises(ValueError, match='原生 Fork'):
                resident_domain.restart_resident_session(record['resident_id'], 'alice', {'target_provider': 'kimi', 'context_strategy': 'native'})
            assert operations == []


    with subtests.test(contract='test_business_write_failure_rolls_binding_back'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            record, _operations, _saved = _restart_domain(monkeypatch)
            resident_domain.restart_resident_session(record['resident_id'], 'alice', {'target_provider': 'claude', 'context_strategy': 'compact'})
            restored: list[dict] = []
            monkeypatch.setattr(resident_workspace_domain, 'save_resident_record', lambda value, **_kwargs: (_ for _ in ()).throw(RuntimeError('catalog unavailable')) if value.get('session_restart', {}).get('status') == 'completed' else None)
            monkeypatch.setattr(resident_backend, 'restore_replaced_session', lambda _record, replacement: restored.append(dict(replacement)))
            with pytest.raises(RuntimeError, match='catalog unavailable'):
                resident_restart_domain.resume_resident_session_restart(_operations[0]['step'])
            assert len(restored) == 1
    with subtests.test(contract='test_restart_admission_is_durable_and_does_not_wait_for_provider'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            record, operations, saved = _restart_domain(monkeypatch)
            monkeypatch.setattr(execution_port, 'service', lambda: pytest.fail('HTTP admission must not invoke Execution'))
            payload = {'target_provider': 'claude', 'idempotency_key': 'restart-one'}
            result = resident_domain.restart_resident_session(record['resident_id'], 'alice', payload)
            step = operations[0]['step']
            assert result['status'] == 'accepted'
            assert step['request']['execution_id']
            assert step['request']['correlation_id'] == result['restart_id']
            assert saved[-1]['backend'] == 'codex'
            monkeypatch.setattr(resident_lifecycle_domain, 'lifecycle_operations', lambda: SimpleNamespace(pending=lambda *_: True))
            assert resident_domain.restart_resident_session(record['resident_id'], 'alice', payload) == result
            assert len(operations) == 1
            assert resident_domain.restart_resident_session(record['resident_id'], 'alice', {**payload, 'idempotency_key': 'restart-two'}) == result
            # A restart with other parameters replaces this one: test_session_restart_supersede.


@pytest.mark.parametrize("metric", ["remaining_percent", "remaining_balance"])
def test_explicit_session_restart_uses_positive_reserved_quota_and_rejects_zero(
    monkeypatch: pytest.MonkeyPatch,
    isolated_portal_case,
    metric,
) -> None:
    """显式重启可使用正数保留额度且拒绝零额度"""
    with isolated_portal_case():
        record, operations, _saved = _restart_domain(monkeypatch)
        quota = {"status": "reserved", "remaining": 2.0, metric: 2.0, "fresh": True, "observed_at": 1.0}
        monkeypatch.setattr(execution_port, "health", lambda: {"scheduler": {"capacity_partitions": {
            "codex:one": {"quota_reserve": quota}}}})
        result = resident_domain.restart_resident_session(
            record["resident_id"],
            "alice",
            {"target_provider": "codex", "target_account": "one"},
        )
        assert result["status"] == "accepted"
        assert result["quota_admission"]["remaining"] == 2.0
        assert result["quota_admission"] == quota
        # The new thread is the Resident's: it starts with the HATG ISA tool.
        assert operations[0]["step"]["request"]["metadata"]["resident_id"] == record["resident_id"]
        assert operations[0]["step"]["quota_override"] == {
            "binding_id": f"session-restart:{result['restart_id']}",
            "revision": 1,
            "enabled": True,
        }

    with isolated_portal_case():
        record, operations, _saved = _restart_domain(monkeypatch)
        monkeypatch.setattr(
            execution_port,
            "health",
            lambda: {
                "scheduler": {
                    "capacity_partitions": {
                        "codex:one": {
                            "quota_reserve": {
                                "status": "reserved",
                                "remaining": 0.0, "remaining_percent": 0.0, "fresh": True,
                            },
                        },
                    },
                },
            },
        )
        with pytest.raises(ValueError, match="没有可用额度"):
            resident_domain.restart_resident_session(
                record["resident_id"],
                "alice",
                {"target_provider": "codex", "target_account": "one"},
            )
        assert operations == []


def test_session_restart_cancellation_is_quota_independent_and_preserves_source(
    monkeypatch: pytest.MonkeyPatch,
    isolated_portal_case,
) -> None:
    """取消会话重启不依赖额度且保留原账号绑定"""
    with isolated_portal_case():
        record, operations, saved = _restart_domain(monkeypatch)
        accepted = resident_domain.restart_resident_session(
            record["resident_id"],
            "alice",
            {"target_provider": "codex", "target_account": "one"},
        )
        cancellations = []
        monkeypatch.setattr(
            execution_port,
            "health",
            lambda: pytest.fail("cancellation must not read quota"),
        )
        monkeypatch.setattr(
            execution_port,
            "service",
            lambda: ExecutionStub(
                cancel=lambda execution_id, **kwargs: cancellations.append((execution_id, kwargs)) or {
                    "status": "cancelled",
                    "result": {"status": "cancelled"},
                },
                resume=lambda *_args, **_kwargs: {
                    "status": "cancelled",
                    "result": {"status": "cancelled"},
                },
            ),
        )

        cancelling = resident_domain.cancel_resident_session_restart(
            record["resident_id"],
            "alice",
            accepted["restart_id"],
        )
        assert cancelling["status"] == "cancelling"
        assert cancellations and cancellations[0][0] == accepted["execution_id"]

        resident_restart_domain.resume_resident_session_restart(operations[0]["step"])
        assert saved[-1]["session_restart"]["status"] == "cancelled"
        assert record.get("backend_route_id", "") != "one"


def test_restart_projects_real_progress_without_an_extra_execution_query(monkeypatch, subtests, isolated_portal_case):
    """重启进度读取持久状态且不额外查询执行服务"""
    for status, reason, has_session, phase in [
    ("queued", "quota_reserve", False, "waiting_quota_reserve"),
    ("queued", "capacity", False, "waiting_capacity"),
    ("claimed", "", False, "creating_session"),
    ("running", "", True, "initializing"),
]:
        with subtests.test(status=status, reason=reason, has_session=has_session, phase=phase), isolated_portal_case() as case_path:
            with monkeypatch.context() as monkeypatch:
                record, operations, _saved = _restart_domain(monkeypatch)
                receipt = resident_domain.restart_resident_session(record["resident_id"], "alice", {})
                monkeypatch.setattr(execution_port, "service", lambda: ExecutionStub(
                    resume=lambda *args, **kwargs: {"status": status, "scheduler": {"queue_reason": reason}},
                ))
                monkeypatch.setattr(resident_backend, "session_record_for_binding", lambda *_: {
                    "backend_session_id": "prepared" if has_session else "",
                })
                with pytest.raises(LifecycleStepPending):
                    resident_restart_domain.resume_resident_session_restart(operations[0]["step"])
                monkeypatch.setattr(execution_port, "service", lambda: pytest.fail("Progress reads must use persisted state"))
                state = resident_domain.get_resident_restart_options(record["resident_id"], "alice")["restart"]
                assert state["status"] == "accepted"
                assert state["phase"] == phase
                assert state["phase_started_at"]
                assert state["restart_id"] == receipt["restart_id"]
                assert state["execution_id"] == operations[0]["step"]["request"]["execution_id"]


def test_restart_recovery_reuses_receipt_after_lost_submission_response(monkeypatch):
    """重启提交响应丢失后复用回执且不重复提交"""
    record, operations, _saved = _restart_domain(monkeypatch)
    resident_domain.restart_resident_session(record["resident_id"], "alice", {})
    step = operations[0]["step"]
    submitted = []
    snapshot = {"status": "running"}
    def resume(execution_id, **kwargs):
        if not submitted:
            raise KeyError(execution_id)
        assert execution_id == submitted[0]["execution_id"]
        return snapshot
    def submit(request):
        submitted.append(request.to_payload())
        raise TimeoutError("reply lost after acceptance")
    monkeypatch.setattr(execution_port, "service", lambda: ExecutionStub(resume=resume, submit=submit))
    with pytest.raises(TimeoutError):
        resident_restart_domain.resume_resident_session_restart(step)
    with pytest.raises(LifecycleStepPending):
        resident_restart_domain.resume_resident_session_restart(step)
    assert record["session_restart"]["status"] == "accepted"
    snapshot.update(status="completed", result={"status": "completed", "backend_session_id": "session_new"})
    resident_restart_domain.resume_resident_session_restart(step)
    resident_restart_domain.resume_resident_session_restart(step)
    assert submitted == [step["request"]]
    assert record["session_restart"]["status"] == "completed"


def test_restart_real_outbox_recovers_one_execution_then_acknowledges(tmp_path, monkeypatch):
    """重启发件箱恢复同一执行并在完成后确认"""
    from management_portal.society_service.state_store import LineageDataStore
    from management_portal.society_service.resident_lifecycle import ResidentLifecycleOperations

    record, _operations, _saved = _restart_domain(monkeypatch)
    database = LineageDataStore()
    outbox = ResidentLifecycleOperations(database)
    monkeypatch.setattr(resident_lifecycle_domain, "lifecycle_operations", lambda: outbox)
    monkeypatch.setattr(resident_catalog.get_catalog().state_store, "transaction", database.transaction)
    submitted, acknowledged = [], []
    snapshot = {"status": "running"}
    def resume(execution_id, **kwargs):
        if not submitted:
            raise KeyError(execution_id)
        assert execution_id == submitted[0].execution_id
        return snapshot
    def submit(request):
        submitted.append(request)
        return snapshot
    monkeypatch.setattr(execution_port, "service", lambda: ExecutionStub(
        resume=resume, submit=submit, acknowledge=lambda execution_id, **kwargs: acknowledged.append(execution_id),
    ))
    receipt = resident_domain.restart_resident_session(record["resident_id"], "alice", {})
    assert receipt["status"] == "accepted" and submitted == []
    operation_id = outbox.ready()[0]
    assert not outbox.run(operation_id, resident_lifecycle_domain.apply_resident_lifecycle_step)
    assert outbox.health()["last_error"] == "" and acknowledged == []
    recovered = ResidentLifecycleOperations(LineageDataStore())
    recovered.store.database.write(lambda connection: connection.execute(
        'UPDATE resident_lifecycle_operations SET available_at=to_timestamp(0) WHERE operation_id=%s', (operation_id,),
    ))
    snapshot.update(status="completed", result={"status": "completed", "backend_session_id": "session_new"})
    assert recovered.run(operation_id, resident_lifecycle_domain.apply_resident_lifecycle_step) == record["resident_id"]
    assert len(submitted) == 1 and acknowledged == [submitted[0].execution_id]
    assert recovered.ready() == [] and record["session_restart"]["status"] == "completed"


def test_restart_submission_rejection_is_terminal_and_retains_old_binding_contracts(monkeypatch, subtests, isolated_portal_case):
    """重启提交被拒绝后终结失败且保留原会话绑定"""
    with subtests.test(contract='test_restart_submission_rejection_is_terminal_and_retains_old_binding'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            record, operations, _saved = _restart_domain(monkeypatch)
            resident_domain.restart_resident_session(record['resident_id'], 'alice', {})

            def resume(*args, **kwargs):
                raise KeyError('not submitted')

            def submit(request):
                raise ValueError('invalid target configuration')
            monkeypatch.setattr(execution_port, 'service', lambda: ExecutionStub(resume=resume, submit=submit))
            monkeypatch.setattr(resident_backend, 'replace_with_prepared_session', lambda *_args, **_kwargs: pytest.fail('Rejected session must not be promoted'))
            resident_restart_domain.resume_resident_session_restart(operations[0]['step'])
            assert record['session_restart']['status'] == 'failed'
            assert 'invalid target configuration' in record['session_restart']['error']
    with subtests.test(contract='test_restart_recovers_promoted_binding_without_replacing_again'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            record, operations, _saved = _restart_domain(monkeypatch)
            resident_domain.restart_resident_session(record['resident_id'], 'alice', {})
            monkeypatch.setattr(resident_backend, 'session_record_for_binding', lambda *_: {})
            monkeypatch.setattr(resident_backend, 'session_record', lambda *_: {'provider': 'codex', 'backend_route_id': 'zero', 'backend_session_id': 'session_new', 'provider_session_ref': 'provider_new'})
            monkeypatch.setattr(resident_backend, 'replace_with_prepared_session', lambda *_args, **_kwargs: pytest.fail('Promotion already committed'))
            resident_restart_domain.resume_resident_session_restart(operations[0]['step'])
            assert record['session_restart']['status'] == 'completed'
    with subtests.test(contract='test_restart_failed_initialization_retains_old_binding'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            record, operations, _saved = _restart_domain(monkeypatch)
            resident_domain.restart_resident_session(record['resident_id'], 'alice', {})
            monkeypatch.setattr(execution_port, 'service', lambda: ExecutionStub(resume=lambda *_args, **_kwargs: {'status': 'failed', 'result': {'status': 'failed', 'error': 'initialization rejected'}}))
            monkeypatch.setattr(resident_backend, 'replace_with_prepared_session', lambda *_args, **_kwargs: pytest.fail('Failed session must not be promoted'))
            resident_restart_domain.resume_resident_session_restart(operations[0]['step'])
            assert record['session_restart']['status'] == 'failed'
            assert resident_backend.session_record(record)['backend_session_id'] == 'session_old'


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
                          "context_strategy": "compact", **settings},
            "idempotency_key": "restart-one",
        }),
        context=InstructionContext(caller="alice", entry="web"),
        instruction_id="instruction_111111111111",
    )
    assert residents.restart_session(invocation)["restart_id"] == "restart_new"
    body = {"target_provider": provider, "target_account": "one", "context_strategy": "compact",
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
    payload = {"target_provider": "claude", "context_strategy": "compact", "active_execution_policy": "wait", "idempotency_key": "deferred-once"}
    receipt = resident_domain.restart_resident_session(record["resident_id"], "alice", payload)
    assert receipt["phase"] == "waiting_current_execution" and not operations
    current = saved[-1]
    monkeypatch.setattr(resident_workspace_domain, "find_resident", lambda *a: current)
    target = resident_domain.ResidentRuntimeTarget(resident_id=record["resident_id"], record=current)
    monkeypatch.setattr(resident_workspace_domain, "resolve_resident_runtime_target", lambda *a, **kw: target)
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
        record["resident_id"], "alice", {"target_provider": "claude", "idempotency_key": "finish-first"},
        portal_reason=portal_reason,
    )
    assert receipt.get("reason", "") == portal_reason
    current = saved[-1]
    monkeypatch.setattr(resident_workspace_domain, "find_resident", lambda *a: current)
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
    receipt = resident_domain.restart_resident_session(record["resident_id"], "alice", {"target_provider": "claude", "idempotency_key": "keep-closed"})
    current = saved[-1]
    assert current["session_restart"]["deferred"]["intake_before"] == "closed"
    monkeypatch.setattr(resident_intake_domain, "commit_resident_intake_state", lambda *a, **k: pytest.fail("manual intake must stay closed"))
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


def test_restart_configuration_owner_commits_only_after_binding_promotion(monkeypatch):
    """会话绑定提升成功后才提交重启目标配置

    :module: resident.session"""
    record, operations, _saved = _restart_domain(monkeypatch)
    saves = []
    promoted = []
    original_save = resident_workspace_domain.save_resident_record
    original_promote = resident_backend.replace_with_prepared_session

    def promote(*args, **kwargs):
        replacement = original_promote(*args, **kwargs)
        promoted.append(replacement)
        return replacement

    def save(value, *, commit_configuration=False, commit_intake=False):
        state = value.get("session_restart") or {}
        saves.append((state.get("status"), commit_configuration))
        if commit_configuration:
            assert promoted
            assert state.get("status") == "completed"
        original_save(value, commit_configuration=commit_configuration)

    monkeypatch.setattr(resident_backend, "replace_with_prepared_session", promote)
    monkeypatch.setattr(resident_workspace_domain, "save_resident_record", save)
    accepted = resident_domain.restart_resident_session(
        record["resident_id"], "alice", {"target_provider": "claude", "context_strategy": "compact"},
    )
    assert accepted["status"] == "accepted"
    assert record["backend"] == "codex"
    assert all(not commit for _state, commit in saves)
    resident_restart_domain.resume_resident_session_restart(operations[0]["step"])
    assert record["backend"] == "claude"
    assert [(state, commit) for state, commit in saves if commit] == [("completed", True)]


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
        record["resident_id"], "alice", {"target_provider": "claude", "context_strategy": "compact"},
    )
    assert result["status"] == "accepted"
    assert checks and operations


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
    body = {'target_provider': 'provider_api', 'target_account': 'test-api', 'model': 'api-model',
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


def test_model_api_to_native_migration_initializes_with_visible_conversation(monkeypatch):
    """模型 API 迁移到原生后端时用可见会话初始化"""
    record, operations, saved = _model_api_restart(monkeypatch)
    record.update(backend='provider_api', backend_route_id='test-api')
    monkeypatch.setattr(resident_backend, 'session_record', lambda *a, **k: {})
    result = resident_domain.restart_resident_session(record['resident_id'], 'alice', {'target_provider': 'codex', 'target_account': 'zero'})
    assert result['status'] == 'accepted'
    assert result['source']['provider'] == 'provider_api'
    assert operations[0]['backend'] == 'codex'
    assert 'old request' in operations[0]['prompt'] and 'old result' in operations[0]['prompt']
    assert record['backend'] == 'provider_api'  # remains until native initialization succeeds


def test_model_api_migration_validation_and_save_failure_preserve_binding(monkeypatch):
    """迁移到模型 API 校验或保存失败时保留原绑定"""
    record, operations, saved = _model_api_restart(monkeypatch)
    detached, restored = [], []
    monkeypatch.setattr(resident_backend, 'delete_session_identity', lambda *a, **k: detached.append(True))
    monkeypatch.setattr(resident_backend, 'restore_replaced_session', lambda record, replacement: restored.append(replacement))
    body = {'target_provider': 'provider_api', 'target_account': 'test-api'}
    with pytest.raises(ValueError, match='does not offer'):
        resident_domain.restart_resident_session(record['resident_id'], 'alice', {**body, 'model': 'wrong'})
    assert not detached and record['backend'] == 'codex'
    def fail(*a, **k): raise RuntimeError('save failed')
    monkeypatch.setattr(resident_workspace_domain, 'save_resident_record', fail)
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
