""":importance: P2"""
from __future__ import annotations
from tests.cases.resident_delivery_test_support import published_delivery as _published_delivery
from tests.cases.agent_data_test_support import register_session
from management_portal.execution import event_projection as execution_events
from management_portal.society_service import invariant_locks
from management_portal.society_service import resident_backend
from management_portal.society_service import resident_catalog
from management_portal.society_service import resident_conversation_stream
from management_portal.society_service.domain_ports import execution_port
from management_portal.execution.accounts import default_execution_accounts
from management_portal.society_service.resident_domain_common import LINEAGE_FOLDER_SCHEMA_VERSION
from management_portal.execution import resident_runtime_settings, codex_runtime_settings, slash_commands

import json
import base64
from tests.cases.database_test_support import DatabaseError, IntegrityError, InterfaceError, OperationalError, connect
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

pytestmark = pytest.mark.usefixtures("registered_execution_defaults")

from management_portal.society_service import (
    lineage_domain,
    resident_hatg_credentials,
    resident_access,
    resident_conversation_projection_domain,
    resident_directory_domain,
    resident_execution_domain,
    resident_execution_provider,
    resident_fork_domain,
    resident_intake_domain,
    resident_execution_work,
    resident_lifecycle_domain,
    resident_lineage,
    resident_protocol,
    resident_record_domain,
    resident_registration_domain,
    resident_runtime_domain,
    resident_session_backend_domain,
    resource_client,
    workspace_domain,
    workspace_contracts,
    resident_domain_common,
)
from management_portal.society_service import lifecycle
from management_portal.society_service import resident_domain as backend
from management_portal.society_service import resident_media_domain
from management_portal.society_service import resident_state
from management_portal.society_service import workspace_attachments
from management_portal.society_service.state_store import LineageDataStore
from management_portal.society_service.resident_execution_work import bind_execution
from management_portal.execution.agent_data_store import AgentDataStore, get_agent_data_store, stable_segment_id
from management_portal.execution.account_adapters import CodexRoutes
from management_portal.execution.service import ExecutionService
from management_portal.execution.session_manager import SessionManager
from management_portal.society_service import execution_client
from tests.cases.society_service_test_support import install_resource_boundary


VALID_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)




def test_resident_default_runtime_reaches_execution_contracts(monkeypatch, subtests, isolated_portal_case):
    """Resident 默认运行设置传入执行层并保留当前上下文采样"""
    with subtests.test(contract='test_resident_default_runtime_reaches_execution'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            monkeypatch.delenv('RESIDENT_AGENT_CODEX_MODEL', raising=False)
            monkeypatch.delenv('RESIDENT_AGENT_CODEX_EFFORT', raising=False)
            monkeypatch.setattr(codex_runtime_settings, 'PORTAL_DEFAULT_MODEL', 'gpt-6.1-sol')
            monkeypatch.setattr(codex_runtime_settings, 'PORTAL_DEFAULT_REASONING_EFFORT', 'high')
            from management_portal.execution.native_options import CodexOptions
            from tests.cases.test_runtime_capability_contract import _Invocation, _Request

            def prepared(runtime_settings):
                return CodexOptions().prepare(_Invocation(_Request({"runtime_settings": runtime_settings}))).request.runtime_options

            settings = resident_runtime_domain.resident_runtime_defaults({'backend': 'codex'})
            options = prepared(settings)
            assert options['model'] == 'gpt-6.1-sol'
            assert options['effort'] == 'high'
            explicit = prepared({**settings, 'model': 'gpt-6-astra', 'reasoning_effort': 'xhigh'})
            assert explicit['model'] == 'gpt-6-astra'
            assert explicit['effort'] == 'xhigh'
            assert prepared({'inherit_session_runtime': True}) == {}
    with subtests.test(contract='test_resident_session_state_uses_preloaded_provider_binding'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            monkeypatch.setattr(resident_backend, 'record_session_id', lambda _record: pytest.fail('preloaded binding must avoid a point lookup'))
            state = resident_record_domain._backend_session_state('active', backend_session_id='session-preloaded')
            assert state == 'active'
    with subtests.test(contract='test_runtime_activity_stays_out_of_the_canonical_message_lane'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            execution_id = 'execution-' + '4' * 24
            tool_detail = {'id': 'runtime:2:tool', 'kind': 'tool', 'title': 'Shell', 'content': 'pytest -q', 'status': 'completed'}
            monkeypatch.setattr(execution_events, 'execution_projection_from_execution', lambda *args, **kwargs: {'execution_id': execution_id, 'outputs': [{'output_id': 'output-owned-by-transcript'}], 'runtime_details': [tool_detail]})
            activity = resident_directory_domain.runtime_activity_for_execution('resident_444444444444', {'execution_id': execution_id, 'status': 'running', 'submitted_at': '2026-10-14T00:00:00Z'})
            assert activity == {'live': True, 'execution_id': execution_id, 'details': [{'activity_id': 'runtime:2:tool', 'resident_id': 'resident_444444444444', 'message_id': '', 'kind': 'tool', 'status': 'completed', 'title': 'Shell', 'content': 'pytest -q', 'started_at': '', 'updated_at': ''}]}
    with subtests.test(contract='test_a_running_subagent_activity_carries_its_card'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            execution_id = 'execution-' + '5' * 24
            card = {'tool_use_id': 'toolu_a', 'agent_type': 'Explore', 'status': 'running', 'steps': [{'kind': 'tool', 'tool_name': 'Bash', 'text': 'ls', 'error': False, 'running': True}]}
            subagent_detail = {'id': 'runtime-update:subagent:toolu_a', 'kind': 'subagent', 'title': '子 agent', 'content': 'Survey', 'status': 'running', 'subagent': card}
            monkeypatch.setattr(execution_events, 'execution_projection_from_execution', lambda *args, **kwargs: {'execution_id': execution_id, 'outputs': [], 'runtime_details': [subagent_detail]})
            detail, = resident_directory_domain.runtime_activity_for_execution('resident_444444444444', {'execution_id': execution_id, 'status': 'running', 'submitted_at': '2026-10-14T00:00:00Z'})['details']
            assert (detail['kind'], detail['status'], detail['subagent']) == ('subagent', 'running', card)
            published, = resident_conversation_stream.protocol_activities('resident_444444444444', [detail])
            assert published['subagent'] == card
            # Only a subagent activity carries a card.
            other, = resident_conversation_stream.protocol_activities('resident_444444444444', [{**detail, 'kind': 'tool'}])
            assert 'subagent' not in other
    with subtests.test(contract='test_actual_sent_prompt_decorates_canonical_user_message'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            messages = [{'message_id': 'msg_0123456789abcdef', 'role': 'user', 'author_id': 'sample_admin', 'content_parts': [{'type': 'text', 'text': '检查投影协议'}], 'created_at': '2026-08-23T00:00:00Z'}]
            monkeypatch.setattr(resident_conversation_projection_domain, '_read_provider_actual_sent_prompts', lambda _provider, _session_id: [{'user_message_id': 'msg_0123456789abcdef', 'raw_message': '检查投影协议', 'actual_sent_prompt': '<portal-context>guard</portal-context>\n检查投影协议', 'created_at': '2026-08-23T00:00:00Z', 'source': 'provider_transcript'}])
            resident_conversation_projection_domain.attach_actual_sent_prompts(messages, session_ids=['019e63b0-031d-7913-9dc8-0d972899bed4'], provider='codex')
            assert messages[0]['actual_sent_prompt'].startswith('<portal-context>')
    with subtests.test(contract='test_active_context_pressure_uses_execution_checkpoint_sample'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            scope = {'last_token_usage': {'input_tokens': 206012, 'total_tokens': 207258}, 'last_token_usage_at': '2026-08-11T15:14:30Z', 'last_model_context_window': 258400}
            job = { 'status': 'running', 'execution_id': 'execution-live-pressure', 'actor': 'sample_admin', "submitted_at": "2026-10-14T00:00:00Z"}
            monkeypatch.setattr(resident_execution_work, 'execution_snapshot', lambda _job: {'status': 'running', 'updated_at': '2026-08-11T15:38:21Z', 'checkpoint': {'context_usage': {'input_tokens': 152363, 'cached_input_tokens': 151296, 'output_tokens': 880, 'reasoning_output_tokens': 648, 'total_tokens': 153243}, 'model_context_window': 258400, 'context_usage_at': '2026-08-11T15:38:21Z'}})
            pressure = resident_directory_domain._resident_scope_context_pressure(scope, active_job=job)
            assert pressure['context_tokens'] == 152363
            assert pressure['used_percent'] == 59.0
            assert pressure['source'] == 'active_execution'
            assert pressure['updated_at'] == '2026-08-11T15:38:21Z'
    with subtests.test(contract='test_active_context_pressure_does_not_reuse_previous_turn_sample'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            monkeypatch.setattr(resident_execution_work, 'execution_snapshot', lambda _job: {'status': 'running', 'updated_at': '2026-08-11T15:21:38Z', 'checkpoint': {'turn_id': 'turn-live'}})
            pressure = resident_directory_domain._resident_scope_context_pressure({'last_token_usage': {'input_tokens': 206012, 'total_tokens': 207258}, 'last_model_context_window': 258400}, active_job={'execution_id': 'job_live', 'status': 'running'})
            assert pressure['context_tokens'] == 0
            assert pressure['used_percent'] is None
            assert pressure['source'] == 'active_execution_pending'


def _message_text(message: dict[str, object]) -> str:
    return "\n".join(
        str(part.get("text") or "")
        for part in message.get("content_parts") or []
        if isinstance(part, dict) and part.get("type") == "text"
    )


def _activity(resident_id: str, kind: str = "thinking") -> dict[str, str]:
    return {
        "activity_id": f"activity:{kind}",
        "resident_id": resident_id,
        "message_id": "message_active",
        "kind": kind,
        "status": "running",
        "title": kind.title(),
        "content": "",
        "started_at": "2026-08-28T00:00:00Z",
        "updated_at": "2026-08-28T00:00:00Z",
    }


def _deliver_session(
    record: dict[str, object],
    job: dict[str, object],
    *,
    persist_messages: bool = True,
) -> resident_execution_provider.DeliveryResult:
    provider = resident_execution_provider.provider_for(record)
    return provider.deliver(
        resident_execution_provider.DeliveryRequest(
            lineage_id=str(record.get("lineage_id") or ""),
            resident_id=str(record.get("resident_id") or ""),
            actor_id=str(job.get("actor") or "CASE-A"),
            message=str(job.get("message") or ""),
            resident=record,
            delivery=job,
            persist_messages=persist_messages,
        )
    )


def test_deliver_resident_input_requires_client_message_identity_contracts(subtests, isolated_portal_case):
    """Resident 输入要求客户端消息标识且拒绝内部顶层字段"""
    with subtests.test(contract='test_deliver_resident_input_requires_client_message_identity'), isolated_portal_case() as case_path:
        with pytest.raises(ValueError, match='message.message_id required'):
            resident_protocol.deliver_resident_input({'resident_id': 'resident_0123456789ab', 'message': {'role': 'user', 'content_parts': [{'type': 'text', 'text': 'hello'}]}}, 'sample_admin')
    with subtests.test(contract='test_deliver_resident_input_rejects_internal_top_level_fields'), isolated_portal_case() as case_path:
        with pytest.raises(ValueError, match='unsupported fields: runtime_context'):
            resident_protocol.deliver_resident_input({'resident_id': 'resident_0123456789ab', 'message': {'message_id': 'msg_0123456789abcdef', 'content_parts': [{'type': 'text', 'text': 'hello'}]}, 'runtime_context': 'hidden side channel'}, 'sample_admin')
    with subtests.test(contract='test_failure_message_identity_is_scoped_to_one_terminal_event'), isolated_portal_case() as case_path:
        job = { 'execution_id': 'execution-first', 'finished_at': '2026-08-25T01:00:00Z', "submitted_at": "2026-10-14T00:00:00Z"}
        first = resident_execution_work.failed_message_id(job)
        job['failure_message_id'] = first
        assert resident_execution_work.failed_message_id(job) == first
        job.pop('failure_message_id')
        job['execution_id'] = 'execution-second'
        job['finished_at'] = '2026-08-25T02:00:00Z'
        assert resident_execution_work.failed_message_id(job) != first
    with subtests.test(contract='test_kimi_resident_tools_exclude_unattended_user_questions'), isolated_portal_case() as case_path:
        from management_portal.execution.native_options import KIMI_OWNER_TOOLS, KIMI_READER_TOOLS
        assert 'AskUserQuestion' not in KIMI_OWNER_TOOLS + KIMI_READER_TOOLS
    with subtests.test(contract='test_resident_execution_projection_uses_compact_status'), isolated_portal_case() as case_path:
        service = mock.Mock()
        service.status.return_value = {'execution_id': 'execution_1234567890abcdef', 'status': 'running', 'events': [], 'result': {}}
        with mock.patch('management_portal.execution.service.ExecutionService', return_value=service):
            snapshot = resident_execution_work.execution_snapshot({'execution_id': 'execution_1234567890abcdef', 'actor': 'sample_admin', 'submitted_at': '2026-10-14T00:00:00Z'})
        assert snapshot['status'] == 'running'
        service.status.assert_called_once_with('execution_1234567890abcdef', actor='sample_admin', include_events=False, include_result=False)
    with subtests.test(contract='test_resident_execution_projection_tolerates_expired_operational_record'), isolated_portal_case() as case_path:
        service = mock.Mock()
        service.status.side_effect = KeyError('execution not found')
        with mock.patch('management_portal.execution.service.ExecutionService', return_value=service):
            snapshot = resident_execution_work.execution_snapshot({'execution_id': 'execution_1234567890abcdef', 'actor': 'sample_admin', 'submitted_at': '2026-10-14T00:00:00Z'})
        assert snapshot == {}


def test_deliver_resident_input_resolves_message_context_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resident 输入仅解析一次消息上下文并传递运行参数"""
    captured: dict[str, object] = {}

    def deliver(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"ok": True}

    monkeypatch.setattr(
        resident_execution_domain,
        "deliver_resident_message_content",
        deliver,
    )
    result = resident_protocol.deliver_resident_input(
        {
            "resident_id": "resident_0123456789ab",
            "message": {
                "message_id": "msg_0123456789abcdef",
                "content_parts": [
                    {"type": "text", "text": "hello"},
                    {
                        "type": "context_reference",
                        "context": {
                            "resources": ["article_0123456789ab"],
                            "context_text": "resolved context",
                            "instruction_skills": ["skill-one"],
                            "working_directory": "/tmp/workspace",
                        },
                    },
                ],
            },
            "activation_policy": "enqueue_and_trigger",
        },
        "sample_admin",
        delivery_context=resident_protocol.ResidentDeliveryContext(
            lineage_id="lineage_0123456789ab",
            business_ref="business-one",
        ),
    )

    assert result == {"ok": True}
    kwargs = captured["kwargs"]
    assert kwargs["runtime_context"] == "resolved context"
    assert kwargs["additional_skills"] == ["skill-one"]
    assert kwargs["working_directory"] == "/tmp/workspace"
    assert kwargs["lineage_id"] == "lineage_0123456789ab"
    assert kwargs["business_ref"] == "business-one"
    assert "trigger_path_id" not in kwargs
    assert kwargs["protocol_input"]["content_parts"][1]["context"][
        "context_text"
    ] == "resolved context"


def test_backend_session_operation_uses_unified_execution_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """后端会话操作通过统一执行服务提交并确认结果"""
    request = mock.Mock(execution_id="execution-test")
    service = mock.Mock()
    service.wait.return_value = {
        "status": "completed",
        "result": {"status": "completed", "backend_session_id": "session-test"},
    }
    monkeypatch.setattr(
        resident_session_backend_domain,
        "build_agent_session_request",
        lambda **_kwargs: request,
    )
    service_factory = mock.Mock(return_value=service)
    monkeypatch.setattr(execution_port, "service", service_factory)

    result = resident_session_backend_domain.run_backend_session_operation(
        backend="codex",
        operation="create",
        actor="sample_admin",
        cwd=tmp_path,
        binding_id="resident:resident_test",
    )

    assert result["backend_session_id"] == "session-test"
    service_factory.assert_called_once_with()
    service.submit.assert_called_once_with(request)
    service.acknowledge.assert_called_once_with(
        "execution-test",
        actor="sample_admin",
    )


def test_managed_backend_session_operation_uses_execution_work_correlation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """受管后端会话操作关联所属执行工作和 Resident"""
    captured: dict[str, object] = {}
    request = mock.Mock(execution_id="execution-managed", actor="sample_admin")
    service = mock.Mock()

    def build_request(**kwargs):
        captured.update(kwargs)
        return request

    monkeypatch.setattr(resident_session_backend_domain, "build_agent_session_request", build_request)
    monkeypatch.setattr(execution_port, "service", lambda: service)
    monkeypatch.setattr(
        resident_execution_domain,
        "durable_resident_execution_request",
        lambda _record, _job, factory: factory(),
    )
    monkeypatch.setattr(
        resident_execution_domain,
        "bind_or_resume_resident_execution",
        lambda *_args: "execution-managed",
    )
    monkeypatch.setattr(
        resident_execution_domain,
        "wait_for_resident_execution",
        lambda *_args, **_kwargs: {
            "status": "completed",
            "result": {
                "status": "completed",
                "backend_session_id": "session-managed",
            },
        },
    )
    record = {"resident_id": "resident_managed"}
    job = {'execution_id': "execution-" + "6" * 24}

    result = resident_session_backend_domain.run_backend_session_operation(
        backend="codex",
        operation="fork",
        actor="sample_admin",
        cwd=tmp_path,
        binding_id="resident:resident_managed",
        resident_record=record,
        execution_work=job,
    )

    assert result["backend_session_id"] == "session-managed"
    assert captured["correlation_id"] == job['execution_id']
    assert captured["agent_id"] == record['resident_id']


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


@pytest.fixture
def resident_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    monkeypatch.setattr(execution_client, "configure_binding_quota_overrides", mock.Mock(return_value={"updated": 0}))
    from dataclasses import asdict
    from management_portal.execution.accounts import default_execution_accounts
    monkeypatch.setattr(execution_client, "health", lambda: {})
    def account_catalog(_service, **kwargs):
        try:
            partitions = execution_client.health().get("scheduler", {}).get("capacity_partitions", {})
        except RuntimeError:
            partitions = {}
        return {"accounts": [{**asdict(a), "backend_route_id": a.route_id,
                "quota_reserve": partitions.get(a.capacity_key, {}).get("quota_reserve", {})}
                for a in default_execution_accounts(account_registry=CodexRoutes()).accounts()]}
    def resolve_account(_service, provider, backend_route_id="", runtime_options=None, **kwargs):
        account = default_execution_accounts(account_registry=CodexRoutes()).resolve(provider, backend_route_id, runtime_options)
        return {**asdict(account), "backend_route_id": account.route_id} if account else {}
    monkeypatch.setattr(ExecutionService, "states", lambda self, executions: {key: {"status": "running"} for key in executions})
    monkeypatch.setattr(ExecutionService, "accounts", account_catalog)
    monkeypatch.setattr(ExecutionService, "resolve_account", resolve_account)
    monkeypatch.setattr(resident_registration_domain, "resolve_account_configuration", lambda *a, **kw: resolve_account(None, *a, **kw))
    monkeypatch.setattr(resident_backend, "resolve_account_configuration", lambda *a, **kw: resolve_account(None, *a, **kw))

    lineage_root = tmp_path / "lineages"
    workspace = lineage_root / "lineage_000000000001"
    default_cwd = workspace / "residents" / "resident_aaaaaaaaaaaa" / "workspace"
    monkeypatch.setattr(lineage_domain, "DATA_ROOT", tmp_path / "lineage-state")
    monkeypatch.setattr(lineage_domain, "LINEAGE_FOLDER_ROOT", lineage_root)
    monkeypatch.setenv("PORTAL_LINEAGE_FOLDER_ROOT", str(lineage_root))
    session_store = AgentDataStore()
    monkeypatch.setattr(
        execution_client,
        "sessions",
        lambda: SessionManager(agent_data=session_store),
    )
    deliver_resident_input = resident_protocol.deliver_resident_input
    install_resource_boundary(monkeypatch)
    monkeypatch.setattr(resident_protocol, "deliver_resident_input", deliver_resident_input)
    record: dict[str, object] = {
        "resident_id": "resident_aaaaaaaaaaaa",
        "folder_schema": LINEAGE_FOLDER_SCHEMA_VERSION,
        "lineage_id": "lineage_000000000001",
        "project_id": "project_test",
        "workspace_path": str(workspace),
        "owned_workspace_id": "workspace_111111111111",
        "owned_workspace": {
            "workspace_id": "workspace_111111111111",
            "path": str(default_cwd),
        },
        "workspace_bindings": [],
        "default_cwd": str(default_cwd),
        "backend": "codex",
        "session_state": "active",
    }
    resource_client.sync_lineage({
        "lineage_id": record["lineage_id"],
    })
    # Canonical membership now requires its aggregate to exist before a
    # Resident is inserted; initialize graph attributes after membership.
    with lineage_domain._lineage_scope(str(record["lineage_id"])):
        lineage_domain._create_lineage_in_scope(
            {"title": "Resident protocol test", "project_id": record["project_id"],
             "nodes": [], "edges": []},
            "CASE-A",
            str(record["lineage_id"]),
        )
    default_cwd.mkdir(parents=True)
    resident_record_domain.save_resident_record(record)
    _bind(session_store,
        "resident:resident_aaaaaaaaaaaa",
        "codex",
        "019f0000-0000-7000-8000-000000000001",
        backend_route_id="zero",
    )
    lineage_domain.update_lineage(
        str(record["lineage_id"]),
        {**lineage_domain._read_lineage(str(record["lineage_id"]), actor="CASE-A"),
         "leader_resident_id": record["resident_id"]},
        "CASE-A",
    )
    return record


@pytest.fixture
def control_record(resident_record, tmp_path, monkeypatch):
    # Polling starts from a fully registered Resident, including its canonical
    # Provider configuration and required owned Workspace Binding.
    store = resident_catalog.get_catalog().state_store
    monkeypatch.setattr(resident_execution_work, "get_lineage_data_store", lambda: store)
    monkeypatch.setattr(invariant_locks, "_ROOT_PROVIDER", lambda: tmp_path)
    return resident_record


@pytest.mark.parametrize("job,expected", [
    ({}, (False, False)),
    ({"execution_id": "control-job", "actor": "CASE-A"}, (True, False)),
    ({"execution_id": "control-job", "actor": "CASE-A", "stop_requested_at": "2026-09-06T12:00:00Z"}, (False, True)),
    ({"execution_id": "new-job", "actor": "CASE-A", "stop_requested_at": "2026-09-06T12:00:00Z"}, (False, False)),
    ({"execution_id": "control-job", "actor": "CASE-A", "outcome": "completed"}, (False, False)),
    ({"execution_id": "control-job", "actor": "CASE-A", "outcome": "cancelled", "stop_requested_at": "now"}, (False, False)),
    ({"execution_id": "control-job", "actor": "CASE-A", "finished_at": "now"}, (False, False)),
])
def test_execution_control_poll_reads_only_committed_resident_state(
    control_record, monkeypatch, job, expected,
) -> None:
    """执行控制轮询只读已提交状态

    验证 Stop 意图及时可见、job 及终态隔离，禁止控制轮询在 Resident 锁内读取资源权限或执行 bootstrap。

    :importance: P1
    :aspects: functional, performance, architecture-conformance
    :design: decision_edc470e09f5a, decision_1b2bdf5c33d6
    """
    resident_id = str(control_record["resident_id"])
    resident_execution_work.get_lineage_data_store().put_resident_execution_record(resident_id, job)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("control polling must not hydrate resources or finalize bootstrap")

    assert (
        resident_execution_domain._resident_execution_still_current(resident_id, "control-job"),
        resident_execution_domain._resident_execution_cancellation_requested(resident_id, "control-job"),
    ) == expected
    # No cache: a newly committed Stop must be seen by the next poll.
    resident_execution_work.get_lineage_data_store().put_resident_execution_record(
        resident_id, {'execution_id': 'control-stop', 'actor': 'CASE-A', 'stop_requested_at': 'now'},
    )
    assert resident_execution_domain._resident_execution_cancellation_requested(resident_id, "control-stop")
    assert not resident_execution_domain._resident_execution_still_current(resident_id, "control-stop")


def test_execution_control_poll_rejects_missing_resident(control_record) -> None:
    """执行控制轮询对缺失 Resident 返回无效状态"""
    missing = "resident_ffffffffffff"
    assert not resident_execution_domain._resident_execution_still_current(missing, "control-job")
    assert not resident_execution_domain._resident_execution_cancellation_requested(missing, "control-job")


def test_snapshot_and_history_pages_do_not_read_full_conversation(
    resident_record: dict[str, object],
) -> None:
    """对话快照与历史分页仅读取所需消息页"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    for index in range(1, 6):
        repository.append_message(
            resident_id,
            {
                "message_id": f"msg_page_{index:02d}",
                "role": "user",
                "content_parts": [{"type": "text", "text": f"message {index}"}],
                "created_at": f"2026-08-30T00:00:0{index}Z",
            },
        )

    with mock.patch.object(
        repository.agent_data,
        "read_chat_messages",
        side_effect=AssertionError("full conversation read"),
    ):
        snapshot = repository.snapshot(resident_id, history_limit=2)
        older = repository.messages_page(
            resident_id,
            page_token=snapshot["next_page_token"],
            limit=2,
        )

    assert [item["message_id"] for item in snapshot["messages"]] == [
        "msg_page_04",
        "msg_page_05",
    ]
    assert [item["message_id"] for item in older["messages"]] == [
        "msg_page_02",
        "msg_page_03",
    ]
    assert older["next_page_token"]


def test_running_job_projection_does_not_run_recovery(
    resident_record: dict[str, object],
) -> None:
    """读取运行中任务投影不启动恢复"""
    resident_execution_work.write(resident_record, {

        "execution_id": "execution-projection-read",
        "actor": "CASE-A",
        "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"})

    with mock.patch.object(
        resident_execution_domain,
        "_prepare_resident_continuation",
        side_effect=AssertionError("read projection invoked recovery"),
    ):
        projected = resident_intake_domain._active_resident_execution(resident_record)

    assert projected['execution_id'] == 'execution-projection-read'


def test_reopened_job_failure_appends_a_new_terminal_event(
    resident_record: dict[str, object],
) -> None:
    """任务重新执行失败时追加独立终态消息"""
    resident_id = str(resident_record["resident_id"])
    job = {

        "execution_id": "execution-first",
        "finished_at": "2026-08-25T01:00:00Z",
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_intake_domain.persist_failed_execution_message(
        resident_id,
        job,
        "first failure",
        {"message": "first failure"},
    )
    first_message_id = str(job["failure_message_id"])

    job.pop("failure_message_id")
    job.pop("failure_message_persisted_at")
    job["execution_id"] = "execution-second"
    job["finished_at"] = "2026-08-25T02:00:00Z"
    resident_intake_domain.persist_failed_execution_message(
        resident_id,
        job,
        "second failure",
        {"message": "second failure"},
    )

    assert job["failure_message_id"] != first_message_id
    assert resident_conversation_stream.get_repository().message(
        resident_id,
        str(job["failure_message_id"]),
    )["content_parts"][0]["text"] == "second failure"


def test_promoted_failures_each_persist_their_own_notice(resident_record):
    """队列逐项提升失败时分别保存通知并清除前轮状态

    :module: resident.conversation"""
    resident_id = str(resident_record["resident_id"])
    job = { "execution_id": "execution-first",
           "finished_at": "2026-10-11T00:00:00Z", "runtime_error": {"message": "first"},
           "execution_event_cursor": 23, "submitted_at": "2026-10-14T00:00:00Z"}
    resident_intake_domain.persist_failed_execution_message(resident_id, job, "first", {"message": "first"})
    first_id = job["failure_message_id"]
    resident_execution_work.promote_queue_item(job, {'execution_id': "job_second", "user_message_id": "second"}, [], "now")
    assert not set(job) & {"failure_message_id", "failure_message_persisted_at", "runtime_error", "execution_event_cursor"}
    resident_intake_domain.persist_failed_execution_message(resident_id, job, "second", {"message": "second"})
    assert job["failure_message_id"] != first_id
    repository = resident_conversation_stream.get_repository()
    assert repository.message(resident_id, first_id)["content_parts"][0]["text"] == "first"
    assert repository.message(resident_id, job["failure_message_id"])["content_parts"][0]["text"] == "second"


@pytest.mark.parametrize("location", ["send_now", "group", "info"])
def test_delivery_retry_finds_every_durable_input_location(resident_record, location):
    """投递重试识别即时输入、合并输入和暂存信息

    :module: resident.conversation"""
    from management_portal.society_service import resident_held_infos
    rid = resident_record["resident_id"]
    message = {"message_id": "msg_retry_location", "role": "user", "author_id": "CASE-A",
               "content_parts": [{"type": "text", "text": "same input"}], "created_at": "2026-10-11T00:00:00Z"}
    repository = resident_conversation_stream.get_repository()
    repository.accept_user_message(rid, message)
    repository.set_input_state(rid, message["message_id"], "queued")
    item = {"user_message_id": message["message_id"], "input_message": message}
    job = {'execution_id': "job_current", "user_message_id": "msg_current"}
    if location == "info":
        resident_held_infos.hold(resident_record, message)
    elif location == "group":
        job["queue"] = [{"user_message_id": "msg_group", "mailbox_members": [item]}]
    else:
        job["send_now_inputs"] = [item]
    assert resident_intake_domain.existing_resident_delivery(rid, message, bound_job=job, require_execution_binding=True)
    if location != "info":
        with pytest.raises(ValueError, match="duplicate message_id"):
            resident_execution_work.normalized_queue(job, [*(job.get("queue") or []), item])


def test_recovered_request_consumes_the_info_it_already_carries(resident_record, monkeypatch):
    """恢复请求消费已携带的信息且不重新拼装请求"""
    from management_portal.society_service import resident_held_infos
    rid = resident_record["resident_id"]
    message = {"message_id": "msg_held_before_crash", "role": "user", "author_id": "CASE-A",
               "content_parts": [{"type": "text", "text": "background fact"}], "created_at": "2026-10-11T00:00:00Z"}
    repository = resident_conversation_stream.get_repository()
    repository.accept_user_message(rid, message)
    repository.set_input_state(rid, message["message_id"], "queued")
    resident_held_infos.hold(resident_record, message)
    job = {'execution_id': "execution-000000000000000000000000", "actor": "CASE-A", "user_message_id": "msg_request_after_info",
           "actual_sent_prompt": "do work", "message": "do work", "prompt_cwd": resident_record["default_cwd"], "queue": []}
    resident_execution_work.write(resident_record, job)
    consume = resident_held_infos.consume
    monkeypatch.setattr(resident_held_infos, "consume", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("crashed before consume")))
    with pytest.raises(RuntimeError, match="crashed before consume"):
        resident_execution_domain._send_resident_execution(resident_record, job)
    restored = resident_execution_work.read(resident_record)
    assert "background fact" in restored["execution_request"]["prompt"]
    monkeypatch.setattr(resident_held_infos, "attach", lambda *_: pytest.fail("request must be recovered unchanged"))
    def consume_and_stop(*args, **kwargs):
        consume(*args, **kwargs)
        raise RuntimeError("stop before model submission")
    monkeypatch.setattr(resident_held_infos, "consume", consume_and_stop)
    with pytest.raises(RuntimeError, match="stop before model submission"):
        resident_execution_domain._send_resident_execution(resident_record, restored)
    assert resident_held_infos.peek(resident_record) == []
    assert repository.input_state(rid, message["message_id"])["state"] == "completed"


@pytest.mark.parametrize("outcome", ["failed", "cancelled", "completed"])
def test_preparation_terminal_job_publishes_input_before_leaving_active_storage(resident_record, outcome):
    """执行准备结束后发布输入终态

    创建 Execution 前失败、取消或结束，轮次归档后仍提交输入终态，使通知和投递清理使用同一结束事实。

    :module: execution.persistence
    """
    resident_id = resident_record["resident_id"]
    repository = resident_conversation_stream.get_repository()
    repository.set_input_state(resident_id, "msg_prepare_finished", "running")
    resident_execution_work.write(resident_record, {
        'execution_id': "job_prepare_finished", "user_message_id": "msg_prepare_finished",
        "actor": "CASE-A", "outcome": outcome, "error": "preparation failed",
        "finished_at": repository.input_state(resident_id, "msg_prepare_finished")["updated_at"],
    })
    assert not resident_execution_work.read(resident_record).get('execution_id')
    with repository.state_store.database.read() as connection:
        row = connection.execute(
            "SELECT state FROM conversation_input_states WHERE resident_id=%s AND message_id=%s",
            (resident_id, "msg_prepare_finished"),
        ).fetchone()
    assert row["state"] == outcome
    assert repository.input_state(resident_id, "msg_prepare_finished")["state"] == outcome


def test_recovery_never_reopens_a_terminal_execution_work(
    resident_record: dict[str, object],
) -> None:
    """恢复过程不重新打开已终结的执行工作"""
    resident_id = str(resident_record["resident_id"])
    resident_execution_work.write(resident_record, {

        "execution_id": "execution-terminal-projection",
        "actor": "CASE-A",
        "outcome": "failed",
        "execution_terminal_status": "failed",
        "execution_event_cursor": 42,
        "finished_at": "2026-08-25T03:00:00Z",
        "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"})

    with (
        mock.patch.object(
            resident_execution_work,
            "execution_snapshot",
            return_value={"status": "failed"},
        ),
        mock.patch.object(
            resident_execution_domain,
            "_project_resident_execution_snapshot",
        ) as project,
    ):
        backend.resume_resident_execution(resident_id)

    project.assert_not_called()
    assert resident_execution_work.read(resident_record)["execution_event_cursor"] == 42


def _add_created_resident_to_lineage(
    record: dict[str, object],
    created_resident_id: str,
    actor: str = "CASE-A",
) -> None:
    created_resident_record = resident_catalog.get_catalog().get(created_resident_id)
    created_resident_record["workspace_path"] = record["workspace_path"]
    updated = lineage_domain.add_registered_resident_node(
        created_resident_record,
        actor,
    )
    assert updated is not None


def test_conversation_message_is_persisted_with_its_durable_event(
    resident_record: dict[str, object],
) -> None:
    """对话消息与持久事件一并保存"""
    observed: list[tuple[str, object]] = []
    message = {
        "message_id": "msg_0123456789abcdef",
        "role": "agent",
        "content_parts": [{"type": "text", "text": "完整回答"}],
    }
    repository = resident_conversation_stream.get_repository()
    resident_id = str(resident_record["resident_id"])
    before_cursor = repository.current_projection(resident_id)["cursor"]
    project = repository.agent_data.project_chat_message
    with mock.patch.object(
            repository.agent_data,
            "project_chat_message",
            side_effect=lambda target_id, item, **kwargs: (
                observed.append(("persist", item))
                or project(target_id, item, **kwargs)
            ),
    ):
        result = resident_lifecycle_domain.append_conversation_message(
            resident_id,
            message,
        )

    assert result["message_id"] == message["message_id"]
    assert observed[0][0] == "persist"
    assert observed[0][1]["message_id"] == message["message_id"]
    assert observed[0][1]["content_parts"] == [
        {"type": "text", "text": "完整回答"}
    ]
    assert observed[0][1]["message_id"] == message["message_id"]
    events = repository.events_after(resident_id, before_cursor)["events"]
    assert len(events) == 1
    assert events[0]["event_type"] == "conversation.message.appended"
    assert events[0]["payload"]["message"]["message_id"] == message["message_id"]


def test_execution_display_message_reaches_transcript_and_notification(
    resident_record: dict[str, object],
) -> None:
    """执行展示消息进入记录和通知且最终回答留给结果通道"""
    resident_id = str(resident_record["resident_id"])
    execution_id = "execution-" + "3" * 24
    job = {

        "execution_id": execution_id,
        "status": "running",
        "actor": "CASE-A",
        "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.write(resident_record, job)
    output = {
        "output_id": "msg_30000000000000000000000000000000",
        "phase": "commentary",
        "text": "完整展示消息",
        "created_at": "2026-08-17T00:00:00Z",
    }
    provider_final = {
        "output_id": "msg_30000000000000000000000000000001",
        "phase": "final_answer",
        "text": "最终回答由 ExecutionResult 统一提交",
        "created_at": "2026-08-17T00:00:01Z",
    }

    repository = resident_conversation_stream.get_repository()
    before_cursor = repository.current_projection(resident_id)["cursor"]
    persisted = resident_execution_domain._persist_execution_display_messages(
        resident_id,

        execution_id,
        [output, provider_final],
    )

    assert len(persisted) == 1
    assert persisted[0]["role"] == "agent"
    stored = resident_conversation_stream.get_repository().read_messages(
        resident_id
    )[-1]
    assert stored["message_id"] == persisted[0]["message_id"]
    assert _message_text(stored) == _message_text(persisted[0])
    assert all(
        item["message_id"] != provider_final["output_id"]
        for item in resident_conversation_stream.get_repository().read_messages(
            resident_id
        )
    )
    events = repository.events_after(resident_id, before_cursor)["events"]
    assert len(events) == 1
    published = events[0]["payload"]["message"]
    assert published["message_id"] == persisted[0]["message_id"]


def test_captured_outputs_enter_trace_and_publish_in_one_queue_order(resident_record) -> None:
    """捕获输出按投递队列顺序进入轨迹并发布"""
    resident_id = str(resident_record["resident_id"])
    job_id, execution_id = "ordered-delivery", "execution-" + "7" * 24
    resident_execution_work.write(resident_record, {
         "execution_id": execution_id, "actor": "CASE-A", "status": "running",
     "submitted_at": "2026-10-14T00:00:00Z"})
    repository = resident_conversation_stream.get_repository()
    store = repository.agent_data
    store.begin_execution(
        execution_id=execution_id, agent_id=resident_id,
        actor_ref="CASE-A", provider="codex", started_at="2026-10-12T00:00:00Z",
    )
    segment_ids = {}
    # Capture storage may already contain later content; only the delivery
    # queue decides when a visible message enters the Resident trace.
    for number in (1, 2, 4, 3):
        message_id = "msg_" + f"{number:032x}"
        segment_id = stable_segment_id("provider-model", execution_id, str(number), "commentary")
        created_at = f"2026-10-12T00:00:0{number}Z"
        store.put_segment(
            segment_id=segment_id, segment_type="model", content=f"output {number}",
            started_at=created_at, created_by_agent_id=resident_id,
            execution_id=execution_id, created_by_actor_ref=resident_id,
            metadata={"role": "assistant", "kind": "commentary", "message": {
                "message_id": message_id, "role": "agent", "author_id": resident_id,
                "created_at": created_at, "execution_id": execution_id,
                "parts": [{"type": "text"}],
            }},
        )
        segment_ids[number] = segment_id

    outputs = [
        {"output_id": "msg_" + f"{number:032x}", "text": f"output {number}",
         "created_at": f"2026-10-12T00:00:0{number}Z"}
        for number in (1, 2, 3, 4)
    ]

    assert all(store.chat_message(resident_id, item["output_id"]) is None for item in outputs)
    for output in outputs:
        resident_execution_domain._persist_execution_display_messages(
            resident_id,  execution_id, [output],
        )

    expected = [item["output_id"] for item in outputs]
    assert [item["message_id"] for item in repository.snapshot(resident_id)["messages"]] == expected
    assert [event["payload"]["message"]["message_id"]
            for event in repository.events_after(resident_id, 0)["events"]
            if event["event_type"] == "conversation.message.appended"] == expected
    assert store.sequence_segment_ids(store.trace_sequence_id(resident_id)) == [
        segment_ids[number] for number in (1, 2, 3, 4)
    ]


def test_running_snapshot_and_cancel_check_read_only_lightweight_job_controls(resident_record, monkeypatch) -> None:
    """运行快照与取消检查只读取轻量任务控制信息

    验证大任务正文存在时，当前任务检查与 running 快照投影均不读取完整执行记录；当前任务检查不额外读取 Resident，快照正确保持运行状态。

    :module: resident.conversation
    :importance: P1
    :aspects: functional, performance
    """
    resident_id = str(resident_record['resident_id'])
    job_id, execution_id = 'lightweight-job', 'execution-' + 'f' * 24
    resident_execution_work.write(resident_record, { 'execution_id': execution_id, 'actor': 'CASE-A',
                                         'user_message_id': 'message-current', 'message': 'opaque' * 200_000, "submitted_at": "2026-10-14T00:00:00Z"})
    repository = resident_conversation_stream.get_repository()
    repository.set_state(resident_id, {'work_state': 'running', 'active_message_id': 'message-current',
                                       'queued_count': 0, 'intake_state': 'open'})
    store = resident_execution_work.get_lineage_data_store()
    monkeypatch.setattr(store, 'resident_execution_record_with_revision',
                        lambda *_args: pytest.fail('runtime observation read the complete execution payload'))
    monkeypatch.setattr(execution_events, 'execution_projection_from_execution',
                        lambda *_args, **_kwargs: {'outputs': [], 'runtime_details': []})
    observed = []
    original = resident_execution_domain.resident_record_for_execution

    def read_record(identifier):
        observed.append(identifier)
        return original(identifier)

    monkeypatch.setattr(resident_execution_domain, 'resident_record_for_execution', read_record)
    assert resident_execution_domain._resident_execution_still_current(resident_id, execution_id)
    assert observed == []
    resident_execution_domain._project_resident_execution_snapshot(resident_id,  execution_id, {'status': 'running'})
    assert observed == [resident_id]
    assert repository.current_projection(resident_id)['state']['work_state'] == 'running'


def test_execution_message_callbacks_do_not_hydrate_resources(resident_record, monkeypatch) -> None:
    """消息与执行投影回调不依赖资源服务

    验证中间消息、Final、事件游标及错误投影只使用已绑定执行的本地事实，保留顺序、单调性和旧 execution 隔离。

    :importance: P1
    :aspects: functional, performance, architecture-conformance
    :design: decision_edc470e09f5a, decision_1b2bdf5c33d6
    """
    resident_id = str(resident_record["resident_id"])
    job_id, execution_id = "callback-job", "execution-" + "b" * 24
    resident_execution_work.write(resident_record, { "actor": "CASE-A", "execution_id": execution_id, "actor": "CASE-A", "submitted_at": "2026-10-14T00:00:00Z"})

    def forbidden(*_args, **_kwargs):
        raise AssertionError("execution message callbacks must not hydrate resources")

    monkeypatch.setattr(
        execution_events, "execution_projection_from_execution",
        lambda *_args, **_kwargs: {"outputs": [], "details": []},
    )
    resident_execution_domain._project_resident_execution_snapshot(
        resident_id,  execution_id, {"execution_id": execution_id, "status": "running"},
    )
    output = {"output_id": "msg_" + "b" * 32, "text": "intermediate", "phase": "commentary"}
    assert resident_execution_domain._persist_execution_display_messages(resident_id, "old-execution", [output]) == []
    messages = resident_execution_domain._persist_execution_display_messages(resident_id,  execution_id, [output])
    assert len(messages) == 1
    resident_execution_domain._advance_resident_execution_event_cursor(resident_id,  execution_id, 7)
    resident_execution_domain._advance_resident_execution_event_cursor(resident_id,  execution_id, 3)
    resident_execution_domain._advance_resident_execution_event_cursor(resident_id, "old-execution", 99)
    assert resident_execution_domain._resident_execution_event_cursor(resident_id,  execution_id) == 7
    assert resident_execution_domain._resident_execution_event_cursor(resident_id,  "old-execution") == 0
    final = resident_intake_domain.build_message("agent", "final", message_id="msg_" + "c" * 32)
    assert resident_execution_domain._persist_execution_final_message(resident_id,  "old-execution", final) is None
    assert resident_execution_domain._persist_execution_final_message(resident_id,  execution_id, final)["message_id"] == final["message_id"]
    stored = resident_conversation_stream.get_repository().read_messages(resident_id)
    assert [item["message_id"] for item in stored] == [output["output_id"], final["message_id"]]
    poison_message_id = "msg_" + "d" * 32
    poison_output = {
        "output_id": poison_message_id,
        "text": "must remain quarantined",
        "phase": "commentary",
    }
    projection_attempts = 0

    def reject_poison(*_args, **_kwargs):
        nonlocal projection_attempts
        projection_attempts += 1
        raise ValueError("test projection fault")

    monkeypatch.setattr(resident_lifecycle_domain, "append_conversation_message", reject_poison)
    with pytest.raises(ValueError, match="test projection fault") as raised:
        resident_execution_domain._persist_execution_display_messages(
            resident_id,  execution_id, [poison_output],
        )
    projection_error = raised.value
    assert projection_error._resident_projection_message_id == poison_message_id
    resident_execution_domain._record_resident_projection_error(
        resident_id,  execution_id, projection_error,
    )
    stored_job = resident_execution_work.read(resident_record)
    assert stored_job["conversation_projection_error"]["execution_id"] == execution_id
    assert stored_job["conversation_projection_quarantine"] == {
        "execution_id": execution_id,
        "message_ids": [poison_message_id],
        "last_event_sequence": 0,
        "updated_at": stored_job["conversation_projection_error"]["recorded_at"],
    }
    assert resident_execution_domain._persist_execution_display_messages(
        resident_id,  execution_id, [poison_output],
    ) == []
    assert projection_attempts == 1
    stored = resident_conversation_stream.get_repository().read_messages(resident_id)
    assert poison_message_id not in {item["message_id"] for item in stored}


def test_execution_cursor_checkpoint_does_not_republish_runtime(
    resident_record, monkeypatch,
) -> None:
    """执行游标检查点单调推进且不重复发布运行状态"""
    resident_id = str(resident_record["resident_id"])
    job_id, execution_id = "cursor-job", "execution-" + "d" * 24
    resident_execution_work.write(resident_record, {

        "execution_id": execution_id,
        "actor": "CASE-A",
     "submitted_at": "2026-10-14T00:00:00Z"})
    before = resident_execution_work.read(resident_record)
    store = resident_execution_work.get_lineage_data_store()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("internal cursor progress must not republish runtime state")

    monkeypatch.setattr(resident_execution_work, "publish_conversation_state", forbidden)
    monkeypatch.setattr(resident_execution_work, "notify_changed", forbidden)
    monkeypatch.setattr(lifecycle, "notify_execution_binding", forbidden)
    monkeypatch.setattr(lifecycle, "notify_execution_cancellation", forbidden)
    with mock.patch.object(
        store, "advance_resident_execution_cursor",
        wraps=store.advance_resident_execution_cursor,
    ) as persist:
        resident_execution_domain._advance_resident_execution_event_cursor(resident_id,  execution_id, 7)
        resident_execution_domain._advance_resident_execution_event_cursor(resident_id,  execution_id, 7)
        resident_execution_domain._advance_resident_execution_event_cursor(resident_id,  execution_id, 3)
        resident_execution_domain._advance_resident_execution_event_cursor(resident_id,  execution_id, 0)
        resident_execution_domain._advance_resident_execution_event_cursor(resident_id, "old-execution", 99)
        resident_execution_domain._advance_resident_execution_event_cursor(resident_id,  "old-execution", 99)
    assert persist.call_count == 5
    assert resident_execution_work.read(resident_record) == {**before, "execution_event_cursor": 7}
    assert resident_execution_domain._resident_execution_event_cursor(resident_id,  execution_id) == 7


def test_execution_cursor_checkpoint_failure_keeps_retry_position(
    resident_record,
) -> None:
    """游标检查点保存失败时保留原重试位置"""
    resident_id = str(resident_record["resident_id"])
    job_id, execution_id = "cursor-retry", "execution-" + "e" * 24
    resident_execution_work.write(resident_record, {

        "execution_id": execution_id,
        "execution_event_cursor": 3,
        "actor": "CASE-A",
     "submitted_at": "2026-10-14T00:00:00Z"})
    store = resident_execution_work.get_lineage_data_store()
    with mock.patch.object(
        store, "advance_resident_execution_cursor", side_effect=OSError("checkpoint unavailable"),
    ), pytest.raises(OSError, match="checkpoint unavailable"):
        resident_execution_domain._advance_resident_execution_event_cursor(resident_id,  execution_id, 7)
    assert resident_execution_domain._resident_execution_event_cursor(resident_id,  execution_id) == 3
    resident_execution_domain._advance_resident_execution_event_cursor(resident_id,  execution_id, 7)
    assert resident_execution_domain._resident_execution_event_cursor(resident_id,  execution_id) == 7


def test_execution_display_message_replay_is_idempotent(
    resident_record: dict[str, object],
) -> None:
    """执行展示消息重放仅保存一份记录"""
    resident_id = str(resident_record["resident_id"])
    execution_id = "execution-" + "7" * 24
    job = {

        "execution_id": execution_id,
        "status": "running",
        "actor": "CASE-A",
        "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.write(resident_record, job)
    output = {
        "output_id": "msg_70000000000000000000000000000000",
        "phase": "commentary",
        "text": "durable update",
        "created_at": "2026-08-17T00:00:00Z",
    }

    first = resident_execution_domain._persist_execution_display_messages(
        resident_id,

        execution_id,
        [output],
    )
    replay = resident_execution_domain._persist_execution_display_messages(
        resident_id,

        execution_id,
        [output],
    )

    assert replay == first
    assert len(
        resident_conversation_stream.get_repository().read_messages(
            resident_id
        )
    ) == 1


def test_execution_display_batch_releases_state_lock_between_messages(resident_record, monkeypatch):
    """批量展示消息在逐条处理之间释放状态锁

    :design: decision_c524c775e0be
    :design-point:
    """
    resident_id = str(resident_record["resident_id"])
    job_id, execution_id = "incremental-batch", "execution-" + "c" * 24
    resident_execution_work.write(resident_record, { "actor": "CASE-A", "execution_id": execution_id, "submitted_at": "2026-10-14T00:00:00Z"})
    original_lock = resident_record_domain.state_lock
    depth = 0

    @contextmanager
    def observed_lock(target):
        nonlocal depth
        with original_lock(target):
            depth += 1
            try:
                yield
            finally:
                depth -= 1

    def outputs():
        for index in range(3):
            assert depth == 0, "history iteration must not occupy the state command lock"
            yield {"output_id": "msg_" + f"{index:032x}", "text": f"update-{index}"}

    monkeypatch.setattr(resident_record_domain, "state_lock", observed_lock)
    assert len(resident_execution_domain._persist_execution_display_messages(resident_id,  execution_id, outputs())) == 3


def test_execution_display_batch_refences_each_message(resident_record, monkeypatch):
    """批量展示消息每条重新核对当前执行身份

    :design: decision_c524c775e0be
    :design-point:
    """
    resident_id = str(resident_record["resident_id"])
    job_id, execution_id = "incremental-fence", "execution-" + "d" * 24
    resident_execution_work.write(resident_record, { "actor": "CASE-A", "execution_id": execution_id, "submitted_at": "2026-10-14T00:00:00Z"})
    append = resident_lifecycle_domain.append_conversation_message

    def replace_job_after_append(target, message):
        committed = append(target, message)
        resident_execution_work.write(resident_record, { "actor": "CASE-A", "execution_id": "replacement-execution", "submitted_at": "2026-10-14T00:00:00Z"})
        return committed

    monkeypatch.setattr(resident_lifecycle_domain, "append_conversation_message", replace_job_after_append)
    outputs = [{"output_id": "msg_" + f"{index:032x}", "text": f"update-{index}"} for index in range(3)]
    assert len(resident_execution_domain._persist_execution_display_messages(resident_id,  execution_id, outputs)) == 1


def test_execution_display_batch_allows_stop_before_recovery_finishes(resident_record):
    """批量展示消息恢复完成前仍允许停止任务

    :design: decision_c524c775e0be
    :design-point:
    """
    resident_id = str(resident_record["resident_id"])
    message_id = "msg_" + "e" * 32
    job_id, execution_id = "incremental-stop", "execution-" + "e" * 24
    resident_execution_work.write(resident_record, { "actor": "CASE-A", "execution_id": execution_id,
                                         "actor": "CASE-A", "user_message_id": message_id, "submitted_at": "2026-10-14T00:00:00Z"})
    stopped = threading.Event()
    errors = []

    def stop():
        try:
            resident_intake_domain.cancel_resident_execution_for_target(resident_id, "CASE-A", message_id)
        except Exception as error:
            errors.append(error)
        finally:
            stopped.set()

    thread = threading.Thread(target=stop)

    def outputs():
        yield {"output_id": "msg_" + "1" * 32, "text": "first recovery message"}
        thread.start()
        assert stopped.wait(2), "Stop was blocked by the remaining recovery batch"
        assert not errors
        assert resident_execution_work.read(resident_record)["stop_requested_at"]
        yield {"output_id": "msg_" + "2" * 32, "text": "second recovery message"}

    try:
        assert len(resident_execution_domain._persist_execution_display_messages(resident_id,  execution_id, outputs())) == 2
    finally:
        if thread.ident is not None:
            thread.join(timeout=3)


@pytest.mark.parametrize("fail_once", [False, True])
def test_execution_waiter_uses_incremental_durable_projection(resident_record, monkeypatch, tmp_path, fail_once):
    """执行等待者增量投影持久消息且失败后可重试

    :design: decision_c524c775e0be
    :design-point:
    """
    from tests.cases.test_execution_activity_incremental import _record

    resident_id = str(resident_record["resident_id"])
    job_id, execution_id = "incremental-waiter", "execution-" + "a" * 24
    resident_execution_work.write(resident_record, { "actor": "CASE-A", "execution_id": execution_id, "submitted_at": "2026-10-14T00:00:00Z"})
    path = tmp_path / f"{execution_id}.jsonl"
    path.write_bytes(_record(1))
    monkeypatch.setattr(execution_events, "execution_activity_path", lambda _id: path)
    monkeypatch.setattr(execution_events, "execution_projection_from_execution",
                        lambda *_args, **_kwargs: pytest.fail("waiter scanned full historical projection"))
    batches = []
    persist = resident_execution_domain._persist_execution_display_messages

    def observe(*args):
        batches.append(len(args[-1]))
        if fail_once and len(batches) == 1:
            raise RuntimeError("temporary projection failure")
        return persist(*args)

    monkeypatch.setattr(resident_execution_domain, "_persist_execution_display_messages", observe)

    class Service:
        @staticmethod
        def wait(_execution_id, **kwargs):
            snapshot = {"status": "running"}
            kwargs["on_events"]([{"sequence": 1, "type": "started", "details": {}}])
            kwargs["on_snapshot"](snapshot)
            kwargs["on_snapshot"](snapshot)
            with path.open("ab") as stream:
                stream.write(_record(2))
            kwargs["on_snapshot"](snapshot)
            kwargs["on_snapshot"](snapshot)
            return snapshot

    result = resident_execution_domain.wait_for_resident_execution(Service(), execution_id=execution_id,
                                                 actor="CASE-A", resident_id=resident_id)
    assert len(result["projection_errors"]) == int(fail_once)
    assert batches == ([1, 1, 1, 0] if fail_once else [1, 0, 1, 0])
    messages = resident_conversation_stream.get_repository().read_messages(resident_id)
    assert [_message_text(message) for message in messages] == ["message-1", "message-2"]


def test_execution_snapshot_recovers_display_messages_from_durable_transcript(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """执行快照从持久记录恢复展示消息且不重复发布"""
    resident_id = str(resident_record["resident_id"])
    execution_id = "execution-" + "8" * 24
    job = {
        'execution_id': execution_id,
        "actor": "CASE-A",
        "user_message_id": "msg_snapshot_input",
        **resident_execution_work.continuation_fields(resident_record),
    }
    bind_execution(
        job,
        execution_id=execution_id,


        now="2026-08-17T00:00:00Z",
    )
    resident_execution_work.write(resident_record, job)
    commentary = {
        "output_id": "msg_80000000000000000000000000000000",
        "phase": "commentary",
        "text": "restart-safe update",
        "created_at": "2026-08-17T00:00:01Z",
    }
    provider_final = {
        "output_id": "msg_80000000000000000000000000000001",
        "phase": "final_answer",
        "text": "final stays on the result path",
        "created_at": "2026-08-17T00:00:02Z",
    }
    monkeypatch.setattr(
        execution_events,
        "execution_projection_from_execution",
        lambda *args, **kwargs: {
            "execution_id": execution_id,
            "outputs": [commentary, provider_final],
            "runtime_details": [],
        },
    )

    repository = resident_conversation_stream.get_repository()
    before_cursor = repository.current_projection(resident_id)["cursor"]
    for _ in range(2):
        resident_execution_domain._project_resident_execution_snapshot(
            resident_id,

            execution_id,
            {"status": "running"},
        )

    messages = repository.read_messages(resident_id)
    assert [item["message_id"] for item in messages].count(
        commentary["output_id"]
    ) == 1
    assert all(item["message_id"] != provider_final["output_id"] for item in messages)
    appended = [
        event
        for event in repository.events_after(resident_id, before_cursor)["events"]
        if event["event_type"] == "conversation.message.appended"
        and event["payload"]["message"]["message_id"] == commentary["output_id"]
    ]
    assert len(appended) == 1


def test_execution_snapshot_replay_keeps_message_time_and_unblocks_later_updates(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """快照重放保留消息时间且允许后续更新继续写入

    :design: decision_c524c775e0be
    :design-point:
    """
    resident_id = str(resident_record["resident_id"])
    execution_id = "execution-" + "9" * 24
    job_id = "job_replay_observation_time"
    job = {
        'execution_id': execution_id,
        "actor": "CASE-A",
        "user_message_id": "msg_90000000000000000000000000000002",
        **resident_execution_work.continuation_fields(resident_record),
    }
    bind_execution(job, execution_id=execution_id,
                    now="2026-09-07T07:49:00Z")
    resident_execution_work.write(resident_record, job)
    first = {
        "output_id": "msg_90000000000000000000000000000000", "phase": "commentary",
        "text": "same message content", "created_at": "2026-09-07T07:49:42.724000Z",
    }
    repository = resident_conversation_stream.get_repository()
    resident_execution_domain._persist_execution_display_messages(resident_id,  execution_id, [first])
    cursor = repository.current_projection(resident_id)["cursor"]
    replay = {**first, "created_at": "2026-09-07T07:52:43.881Z"}
    later = {**first, "output_id": "msg_90000000000000000000000000000001",
             "created_at": "2026-09-07T08:18:18.251Z"}
    monkeypatch.setattr(execution_events, "execution_projection_from_execution",
                        lambda *args, **kwargs: {"outputs": [replay, later], "runtime_details": []})
    for _ in range(2):
        resident_execution_domain._project_resident_execution_snapshot(resident_id,  execution_id,
                                                      {"status": "running"})
    messages = repository.read_messages(resident_id)
    assert [m["message_id"] for m in messages] == [first["output_id"], later["output_id"]]
    assert messages[0]["created_at"] == first["created_at"]
    assert messages[1]["created_at"] == later["created_at"]
    appended = [e for e in repository.events_after(resident_id, cursor)["events"]
                if e["event_type"] == "conversation.message.appended"]
    assert len(appended) == 1
    with pytest.raises(ValueError, match="different immutable content"):
        resident_execution_domain._persist_execution_display_messages(
            resident_id,  execution_id, [{**replay, "text": "changed body"}])
    resident_execution_work.write(resident_record, {**resident_execution_work.read(resident_record),
                                         "user_message_id": "msg_90000000000000000000000000000003"})
    with pytest.raises(ValueError, match="different immutable content"):
        resident_execution_domain._persist_execution_display_messages(resident_id,  execution_id, [replay])


def test_execution_display_message_replay_rejects_identity_conflict(
    resident_record: dict[str, object],
) -> None:
    """展示消息重放拒绝相同标识对应不同内容"""
    resident_id = str(resident_record["resident_id"])
    execution_id = "execution-" + "8" * 24
    job = {

        "execution_id": execution_id,
        "status": "running",
        "actor": "CASE-A",
        "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.write(resident_record, job)
    output = {
        "output_id": "msg_80000000000000000000000000000000",
        "phase": "commentary",
        "text": "first content",
        "created_at": "2026-08-17T00:00:00Z",
    }
    resident_execution_domain._persist_execution_display_messages(
        resident_id,

        execution_id,
        [output],
    )

    with pytest.raises(ValueError, match="different immutable content"):
        resident_execution_domain._persist_execution_display_messages(
            resident_id,

            execution_id,
            [{
                **output,
                "text": "conflicting content",
            }],
        )


def test_execution_display_messages_append_after_injected_user_message(
    resident_record: dict[str, object],
) -> None:
    """执行展示消息排在已注入的用户消息之后"""
    resident_id = str(resident_record["resident_id"])
    execution_id = "execution-" + "4" * 24
    user_message_id = "msg_40000000000000000000000000000000"
    job = {

        "execution_id": execution_id,
        "user_message_id": user_message_id,
        "status": "running",
        "actor": "CASE-A",
        "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.write(resident_record, job)
    resident_lifecycle_domain.append_conversation_message(resident_id, {
        "message_id": user_message_id,
        "role": "user",
        "author_id": "CASE-A",
        "content_parts": [{"type": "text", "text": "question"}],
        "created_at": "2026-08-17T00:00:00Z",
    })
    resident_lifecycle_domain.append_conversation_message(resident_id, {
        "message_id": "msg_40000000000000000000000000000003",
        "role": "user",
        "content_parts": [{"type": "text", "text": "injected follow-up"}],
        "created_at": "2026-08-17T00:00:03Z",
    })

    for output in (
        {
            "output_id": "msg_40000000000000000000000000000001",
            "phase": "commentary",
            "text": "first update",
            "created_at": "2026-08-17T00:00:01Z",
        },
        {
            "output_id": "msg_40000000000000000000000000000002",
            "phase": "commentary",
            "text": "second update",
            "created_at": "2026-08-17T00:00:02Z",
        },
    ):
        resident_execution_domain._persist_execution_display_messages(
            resident_id,

            execution_id,
            [output],
        )

    assert [
        _message_text(item)
        for item in resident_conversation_stream.get_repository().read_messages(
            resident_id
        )
    ] == ["question", "injected follow-up", "first update", "second update"]


def test_execution_final_message_follows_projected_updates(
    resident_record: dict[str, object],
) -> None:
    """执行最终消息排在已投影的进度更新之后"""
    resident_id = str(resident_record["resident_id"])
    execution_id = "execution-" + "5" * 24
    user_message_id = "msg_50000000000000000000000000000000"
    job = {

        "execution_id": execution_id,
        "user_message_id": user_message_id,
        "status": "running",
        "actor": "CASE-A",
        "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.write(resident_record, job)
    resident_lifecycle_domain.append_conversation_message(resident_id, {
        "message_id": user_message_id,
        "role": "user",
        "author_id": "CASE-A",
        "content_parts": [{"type": "text", "text": "question"}],
        "created_at": "2026-08-17T00:00:00Z",
    })
    resident_execution_domain._persist_execution_display_messages(
        resident_id,

        execution_id,
        [{
            "output_id": "msg_50000000000000000000000000000001",
            "phase": "commentary",
            "text": "update",
            "created_at": "2026-08-17T00:00:01Z",
        }],
    )
    resident_execution_domain._persist_execution_display_messages(
        resident_id,

        execution_id,
        [{
            "output_id": "msg_50000000000000000000000000000002",
            "phase": "resident_reply",
            "text": "final",
            "created_at": "2026-08-17T00:00:02Z",
        }],
    )

    assert [
        _message_text(item)
        for item in resident_conversation_stream.get_repository().read_messages(
            resident_id
        )
    ] == ["question", "update", "final"]




def test_runnable_local_jobs_wake_the_lineage_coordinator(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """可运行的本地任务唤醒 Lineage 协调者"""
    notifications: list[str] = []
    monkeypatch.setattr(
        lifecycle,
        "notify_execution_binding",
        lambda resident_id: notifications.append(str(resident_id)),
    )
    resident_execution_work.write(
        resident_record,
        {
            'execution_id': "main_local_wake",
            'operation': "main_bootstrap",
            "actor": "CASE-A",
            "status": "queued",
            "queue_reason": "starting",
        },
    )
    assert notifications == [
        str(resident_record["resident_id"]),
    ]
    execution_work = resident_execution_work.read(resident_record)
    assert execution_work["execution_id"] == "main_local_wake"
    assert resident_execution_work.execution_id(execution_work)
    assert resident_execution_work.continuation_needed(resident_record, execution_work)


def test_execution_bound_job_wakes_completion_continuation(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已绑定执行的任务唤醒完成续接"""
    notifications: list[str] = []
    monkeypatch.setattr(
        lifecycle,
        "notify_execution_binding",
        lambda resident_id: notifications.append(str(resident_id)),
    )

    resident_execution_work.write(
        resident_record,
        {

            "actor": "CASE-A",
            "execution_id": "execution-" + "8" * 24,
            "queue": [],
         "submitted_at": "2026-10-14T00:00:00Z"},
    )
    assert notifications == [str(resident_record["resident_id"])]


def test_exact_resident_creation_job_is_applied_to_its_own_execution_work(
    resident_record: dict[str, object],
) -> None:
    """派生 Resident 创建完成仅应用于其自身执行工作"""
    from tests.cases.fork_context_test_support import record_session_turn
    record_session_turn(resident_record)
    detail = backend.create_resident_full_context_fork(
        str(resident_record["resident_id"]),
        "CASE-A",
        defer_backend_start=True,
    )
    created_resident_id = str(detail["resident_id"])
    _add_created_resident_to_lineage(resident_record, created_resident_id)
    exact_record = resident_record_domain.find_resident(created_resident_id)
    assert exact_record is not None
    fork_mode = exact_record["fork_mode"]
    forked_from_at = exact_record["forked_from_at"]
    job = {
        'execution_id': "execution-" + "3" * 24,
        'operation': "context_fork_create",
        "resident_id": created_resident_id,
        "actor": "CASE-A",
        "queue": [],
        **resident_execution_work.continuation_fields(exact_record),
    }
    continuation_id = str(job["continuation_id"])
    resident_execution_work.write(exact_record, job)

    created_session_id = "01a00000-0000-7000-8000-000000000199"

    def create_exact(record, current_job):
        bind_execution(
            current_job,
            execution_id="execution-" + "3" * 24,


            now="2026-08-27T00:00:00Z",
        )
        resident_execution_work.write(record, current_job)
        return {
            "id": created_session_id,
            "backend_route_id": "zero",
        }

    with (
        mock.patch.object(
            resident_session_backend_domain,
            "create_exact_resident_session",
            side_effect=create_exact,
        ) as create_session,
        mock.patch.object(
            resident_execution_domain,
            "_finalize_applied_execution",
            return_value=False,
        ) as finalize,
    ):
        resident_execution_domain.run_resident_execution(created_resident_id, continuation_id)

    create_session.assert_called_once()
    finalize.assert_called_once()
    persisted = resident_record_domain.find_resident(created_resident_id)
    assert persisted is not None
    assert persisted["session_state"] == "active"
    assert persisted["fork_mode"] == fork_mode == "full"
    assert persisted["forked_from_at"] == forked_from_at
    assert persisted["session_created_via"] == "execution_service"
    assert "native_backend_fork" not in persisted
    first_turn = resident_execution_domain._portable_context_execution(
        persisted,
        {"actor": "CASE-A", "message": "continue"},
    )
    assert first_turn["fork_context_input"]["segments"] == [
        {"segment_id": item["segment_id"], "provider_turn_id": item.get("provider_turn_id", "")}
        for item in get_agent_data_store().agent_context_snapshot(created_resident_id)["segments"]
    ]
    assert resident_execution_work.completion_applied(resident_execution_work.read(persisted))


def test_exact_resident_native_fork_uses_cross_lineage_source_binding(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """跨 Lineage 分叉先复制选定数据再创建目标绑定会话"""
    source_id = str(resident_record["resident_id"])
    target_id = "resident_bbbbbbbbbbbb"
    target = {
        **resident_record,
        "resident_id": target_id,
        "lineage_id": "lineage_bbbbbbbbbbbb",
        "source_resident_id": source_id,
        "backend_route_id": "zero",
    }
    owned_workspace = resident_record_domain.resident_owned_workspace_path(target, str(target["resident_id"]))
    owned_workspace.mkdir(parents=True, exist_ok=True)
    target["owned_workspace_id"] = "workspace_bbbbbbbbbbbb"
    target["default_cwd"] = str(owned_workspace)
    target["owned_workspace"] = {
        "workspace_id": target["owned_workspace_id"],
        "path": str(owned_workspace),
    }
    job = {
        'execution_id': "execution-" + "7" * 24,
        'operation': "context_fork_create",
        "actor": "CASE-A",
        "queue": [],
        "agent_data_fork": {
            "source_agent_id": source_id,
            "target_agent_id": target_id,
            "segment_ids": ["segment_chosen"],
        },
    }
    monkeypatch.setattr(
        resident_record_domain,
        "find_resident",
        lambda resident_id: (
            resident_record if resident_id == source_id else None
        ),
    )
    monkeypatch.setattr(
        resident_access,
        "has_resident_workspace_authority",
        lambda *_args, **_kwargs: True,
    )
    captured: dict[str, object] = {}
    operations: list[str] = []
    monkeypatch.setattr(
        execution_port,
        "fork_agent_data",
        lambda *_args, **_kwargs: operations.append("agent_data"),
    )
    monkeypatch.setattr(
        "management_portal.society_service.changes.resident_changed",
        lambda *_args, **_kwargs: None,
    )

    def create_between_bindings(**kwargs):
        operations.append("provider")
        captured.update(kwargs)
        return {
            "backend_session_id": "01a00000-0000-7000-8000-000000000299",
            "forkedFromId": resident_backend.record_session_id(
                resident_record
            ),
            "backend_route_id": "zero",
        }

    monkeypatch.setattr(
        resident_session_backend_domain,
        "run_backend_session_operation",
        create_between_bindings,
    )

    result = resident_session_backend_domain.create_exact_resident_session(target, job)

    assert result["id"] == "01a00000-0000-7000-8000-000000000299"
    assert "source_binding_id" not in captured
    assert captured["operation"] == "create"
    assert captured["binding_id"] == f"resident:{target_id}"
    assert captured["resident_record"] is target
    assert captured["execution_work"] is job
    assert captured["backend_route_id"] == "zero"
    assert operations == ["agent_data", "provider"]


def test_queued_agent_data_fork_replays_authoritative_materialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """排队的数据分叉按权威选段重新物化并发布变化"""
    target_id = "resident_bbbbbbbbbbbb"
    copied: list[tuple[tuple[object, ...], dict[str, object]]] = []
    changed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        execution_port,
        "fork_agent_data",
        lambda *args, **kwargs: copied.append((args, kwargs)),
    )
    monkeypatch.setattr(
        "management_portal.society_service.changes.resident_changed",
        lambda resident_id, *, scope: changed.append((resident_id, scope)),
    )
    job = {
        "actor": "CASE-A",
        "agent_data_fork": {
            "source_agent_id": "resident_aaaaaaaaaaaa",
            "target_agent_id": target_id,
            "segment_ids": ["segment_selected_1", "segment_selected_2"],
        },
    }

    resident_session_backend_domain._materialize_queued_agent_data_fork(
        {"resident_id": target_id},
        job,
    )

    assert copied == [(('resident_aaaaaaaaaaaa', target_id), {
        "actor": "CASE-A",
        "segment_ids": ["segment_selected_1", "segment_selected_2"],
    })]
    assert changed == [(target_id, "conversation")]

    resident_session_backend_domain._materialize_queued_agent_data_fork(
        {"resident_id": target_id},
        job,
    )
    assert len(copied) == 2
    assert changed == [
        (target_id, "conversation"),
        (target_id, "conversation"),
    ]


def test_applied_completion_retries_ack_without_reapplying_completion(
    resident_record: dict[str, object],
) -> None:
    """完成结果已应用时仅重试确认且不重复应用"""
    job = {
        'execution_id': "execution-" + "6" * 24,
        "actor": "CASE-A",
        "queue": [],
        **resident_execution_work.continuation_fields(resident_record),
    }
    bind_execution(
        job,
        execution_id="execution-" + "6" * 24,


        now="2026-08-17T00:00:00Z",
    )
    resident_execution_domain._mark_completion_applied(job, "2026-08-17T00:01:00Z")
    continuation_id = str(job["continuation_id"])
    resident_execution_work.write(resident_record, job)

    with mock.patch.object(
        resident_intake_domain,
        "acknowledge_execution",
        side_effect=[False, True],
    ):
        assert resident_execution_domain._finalize_applied_execution(
            str(resident_record["resident_id"]),
            continuation_id,
            job,
        ) is False
        assert resident_execution_work.completion_applied(
            resident_execution_work.read(resident_record)
        )
        assert resident_execution_domain._finalize_applied_execution(
            str(resident_record["resident_id"]),
            continuation_id,
            job,
        ) is False

    assert resident_execution_work.read(resident_record) == {}


def test_a_second_finalizer_of_one_completion_keeps_the_promoted_queue_item(
    resident_record: dict[str, object],
) -> None:
    """同一完成结果被二次收尾时保留已提升的队列输入

    :module: resident.conversation
    :aspects: functional, performance
    """
    # Two runners can finalize the same applied completion (the runner and a recovery).
    # Promotion keeps the continuation, so only the job ID shows the queue already advanced.
    resident_id = str(resident_record["resident_id"])
    completed = {
        'execution_id': "execution-" + "7" * 24,
        "actor": "test-actor",
        "user_message_id": "message_first_batch",
        "queue": [{'execution_id': "job_second_batch", "user_message_id": "message_second_batch",
                   "message": "second batch", "actor": "test-actor",
                   "input_message": {"message_id": "message_second_batch", "role": "user", "author_id": "test-actor",
                                     "content_parts": [{"type": "text", "text": "second batch"}]}}],
        **resident_execution_work.continuation_fields(resident_record),
    }
    bind_execution(completed, execution_id="execution-" + "7" * 24,
                    now="2026-09-29T08:25:35Z")
    resident_execution_domain._mark_completion_applied(completed, "2026-09-29T08:26:01Z")
    continuation_id = str(completed["continuation_id"])
    resident_execution_work.write(resident_record, completed)

    with mock.patch.object(resident_intake_domain, "acknowledge_execution", return_value=True), \
            mock.patch.object(resident_intake_domain, "reconcile_queued_turn_inputs", return_value=True):
        assert resident_execution_domain._finalize_applied_execution(resident_id, continuation_id, completed) is True
        assert resident_execution_domain._finalize_applied_execution(resident_id, continuation_id, completed) is False

    current = resident_execution_work.read(resident_record)
    assert current['execution_id'].startswith('execution-')
    assert current["user_message_id"] == "message_second_batch"


def test_resident_success_uses_shared_applied_completion_finalizer(
    resident_record: dict[str, object],
) -> None:
    """Resident 成功执行通过共享收尾器处理已应用结果"""
    resident_id = str(resident_record["resident_id"])
    resident_conversation_stream.get_repository().append_message(
        resident_id,
        {
            "message_id": "message_main_next",
            "role": "user",
            "content_parts": [{"type": "text", "text": "continue"}],
            "created_at": "2026-08-17T00:00:00Z",
            "author_id": "CASE-A",
        },
    )
    job = {
        'execution_id': "execution-" + "9" * 24,
        "actor": "CASE-A",
        "queue": [{
            'execution_id': "job_main_next",
            "user_message_id": "message_main_next",
            "message": "continue",
            "raw_message": "continue",
            "actor": "CASE-A",
        }],
        **resident_execution_work.continuation_fields(resident_record),
    }
    bind_execution(
        job,
        execution_id="execution-" + "9" * 24,


        now="2026-08-17T00:00:00Z",
    )
    continuation_id = str(job["continuation_id"])
    resident_execution_work.write(resident_record, job)
    updated_record = dict(resident_record)
    updated_record["session_state"] = "active"

    with (
        mock.patch.object(
            resident_execution_domain,
            "send_to_resident_backend",
            return_value=(
                {
                    'execution_id': job['execution_id'],
                    "created_at": "2026-08-17T00:01:00Z",
                },
                updated_record,
            ),
        ) as send,
        mock.patch.object(
            resident_execution_domain,
            "_finalize_applied_execution",
            return_value=False,
        ) as finalize,
        mock.patch.object(resident_execution_domain, "_notify_lineage_message_completed"),
    ):
        resident_execution_domain.run_resident_execution(resident_id, continuation_id)

    send.assert_called_once()
    finalize.assert_called_once()
    persisted = resident_execution_work.read(resident_record)
    assert resident_execution_work.completion_applied(persisted)
    assert finalize.call_args.args[:2] == (resident_id, continuation_id)


def test_fork_operation_commits_created_resident_before_publishing_work(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """分叉先提交新 Resident 再发布执行工作"""
    from tests.cases.fork_context_test_support import record_session_turn
    selected_ids = record_session_turn(resident_record)
    root_id = str(resident_record["resident_id"])
    notifications: list[str] = []

    def assert_committed_before_wake(resident_id: str) -> None:
        target_id = str(resident_id)
        notifications.append(target_id)
        catalog_record = resident_catalog.get_catalog().get(target_id)
        assert catalog_record["resident_id"] == target_id
        root = resident_record_domain.find_resident(root_id)
        assert root is not None
        family = resident_lineage.snapshot(root)
        assert resident_lineage.find_in_snapshot(family, target_id) is not None
        job = resident_execution_work.read(catalog_record)
        assert "status" not in job
        assert "queue_reason" not in job
        assert job["agent_data_fork"] == {
            "source_agent_id": root_id,
            "target_agent_id": target_id,
            # Only the turn input and answer are trace content.
            "segment_ids": selected_ids,
        }

    monkeypatch.setattr(
        lifecycle,
        "notify_execution_binding",
        assert_committed_before_wake,
    )

    created = backend.create_resident_full_context_fork(
        root_id,
        "CASE-A",
    )

    assert notifications == [created["resident_id"]]



def test_fork_creation_persists_and_projects_inherited_runtime_settings(
    resident_record: dict[str, object],
) -> None:
    """分叉保存并投影继承的模型、推理强度和服务等级"""
    from tests.cases.fork_context_test_support import record_session_turn

    inherited = resident_runtime_settings.mark_portal_manual_authority(
        resident_runtime_settings.merge_settings(
            resident_runtime_domain.read_resident_runtime_settings(resident_record),
            {
                "model": "gpt-5.6-sol",
                "reasoning_effort": "xhigh",
                "service_tier": "priority",
            },
            actor="CASE-A",
            updated_at="2026-09-20T09:09:01Z",
        ),
        actor="CASE-A",
        updated_at="2026-09-20T09:09:01Z",
    )
    resident_runtime_domain.write_resident_runtime_settings(resident_record, inherited)
    resident_record_domain.save_resident_record(resident_record)
    record_session_turn(resident_record)

    created = backend.create_resident_full_context_fork(
        str(resident_record["resident_id"]),
        "CASE-A",
        defer_backend_start=True,
    )
    child = resident_catalog.get_catalog().get(created["resident_id"])
    persisted = resident_runtime_domain.read_resident_runtime_settings(child)
    assert persisted["model"] == "gpt-5.6-sol"
    assert persisted["reasoning_effort"] == "xhigh"
    assert persisted["service_tier"] == "priority"
    projected = resident_conversation_stream.get_repository().current_projection(
        created["resident_id"],
    )
    assert projected["runtime_settings"]["model"] == "gpt-5.6-sol"
    assert projected["runtime_settings"]["reasoning_effort"] == "xhigh"
    assert projected["runtime_settings"]["service_tier"] == "priority"


def test_fork_creation_does_not_enter_shared_registry_or_family_locks(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """分叉创建避开共享注册表和聚合状态锁"""
    @contextmanager
    def forbidden_family_lock(_record):
        raise AssertionError("fork creation entered a family write lock")
        yield

    @contextmanager
    def forbidden_state_lock():
        raise AssertionError("fork creation entered the aggregate state lock")
        yield

    monkeypatch.setattr(resident_record_domain, "state_lock", forbidden_state_lock)
    monkeypatch.setattr(
        resident_hatg_credentials,
        "ensure_resident_credential",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("fork creation entered the credential registry lock")
        ),
    )

    from tests.cases.fork_context_test_support import record_session_turn
    record_session_turn(resident_record)
    created = backend.create_resident_full_context_fork(
        str(resident_record["resident_id"]),
        "CASE-A",
        defer_backend_start=True,
    )

    assert created["schema"] == "tfi.resident-creation-result.v1"
    assert resident_catalog.get_catalog().get(created["resident_id"])[
        "resident_id"
    ] == created["resident_id"]


def test_concurrent_fork_creation_commits_distinct_residents_without_family_merge(
    resident_record: dict[str, object],
) -> None:
    """并发分叉提交不同 Resident 且两者均保留在所属图中"""
    from tests.cases.fork_context_test_support import record_session_turn
    record_session_turn(resident_record)
    root_id = str(resident_record["resident_id"])
    barrier = threading.Barrier(3)
    created: list[dict[str, object]] = []
    errors: list[str] = []

    def create() -> None:
        try:
            barrier.wait(timeout=2)
            created.append(backend.create_resident_full_context_fork(
                root_id,
                "CASE-A",
                defer_backend_start=True,
            ))
        except BaseException:  # pragma: no cover - asserted below
            errors.append(traceback.format_exc())

    threads = [threading.Thread(target=create) for _index in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=5)

    assert not errors, "\n\n".join(errors)
    assert len(created) == 2
    resident_ids = {str(item["resident_id"]) for item in created}
    assert len(resident_ids) == 2
    family = resident_lineage.snapshot(
        resident_record_domain.find_resident(root_id)
    )
    assert resident_ids.issubset({
        str(item.get("resident_id") or "")
        for item in family["residents"]
    })


def test_chat_attachments_use_the_exact_resident_identity(
    resident_record: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """聊天图片和附件使用目标 Resident 的准确身份"""
    root_id = str(resident_record["resident_id"])
    child_id = "resident_bbbbbbbbbbbb"
    registry = resident_lineage.snapshot(resident_record)
    registry["residents"].append({
        "resident_id": child_id,
        "source_resident_id": root_id,
        "lineage_id": resident_record["lineage_id"],
        "session_state": "active",
        "created_by": "CASE-A",
        "backend": "codex",
    })
    resident_fork_domain._persist_derived_resident(resident_record, registry["residents"][-1])
    monkeypatch.setattr(workspace_domain, "CHAT_IMAGE_CACHE_ROOT", tmp_path / "images")
    monkeypatch.setattr(workspace_contracts, "CHAT_IMAGE_CACHE_ROOT", tmp_path / "images")
    monkeypatch.setattr(
        workspace_domain,
        "CHAT_ATTACHMENT_CACHE_ROOT",
        tmp_path / "attachments",
    )

    image = workspace_domain.save_workspace_pasted_image({
        "resident_id": child_id,
        "filename": "clipboard.png",
        "content_type": "image/png",
        "data_bytes": VALID_PNG_BYTES,
    }, "CASE-A")
    attachment = workspace_domain.save_workspace_attachment({
        "resident_id": child_id,
        "filename": "notes.txt",
        "content_type": "text/plain",
        "data_bytes": b"attachment body",
    }, "CASE-A")

    for result in (image, attachment):
        meta_path = Path(result["path"]).with_suffix(
            Path(result["path"]).suffix + ".json"
        )
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["resident_id"] == child_id
        assert "fork_id" not in meta


def test_uploaded_chat_image_preview_can_be_resolved_immediately(
    resident_record: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """聊天图片上传后可立即解析预览内容"""
    resident_id = str(resident_record["resident_id"])
    image_root = tmp_path / "images"
    monkeypatch.setattr(workspace_domain, "CHAT_IMAGE_CACHE_ROOT", image_root)
    monkeypatch.setattr(workspace_contracts, "CHAT_IMAGE_CACHE_ROOT", image_root)
    image_bytes = VALID_PNG_BYTES

    uploaded = workspace_domain.save_workspace_pasted_image({
        "resident_id": resident_id,
        "filename": "preview.png",
        "content_type": "image/png",
        "data_bytes": image_bytes,
    }, "CASE-A")
    path, content_type = workspace_domain.resolve_workspace_pasted_image(
        str(uploaded["file_id"]),
        "CASE-A",
    )

    assert path.read_bytes() == image_bytes
    assert content_type == "image/png"


def test_resident_image_reaches_execution_as_native_input_media(
    resident_record: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resident 图片暂存后以原生媒体传入执行请求"""
    resident_id = str(resident_record["resident_id"])
    monkeypatch.setattr(workspace_domain, "CHAT_IMAGE_CACHE_ROOT", tmp_path / "images")
    monkeypatch.setattr(workspace_contracts, "CHAT_IMAGE_CACHE_ROOT", tmp_path / "images")
    image = workspace_domain.save_workspace_pasted_image({
        "resident_id": resident_id,
        "filename": "latency-probe.png",
        "content_type": "image/png",
        "data_bytes": VALID_PNG_BYTES,
    }, "CASE-A")
    content = f"识别图片中的内容。\n\n[image: {image['path']}]"
    with (
        mock.patch.object(
            resident_session_backend_domain,
            "handle_resident_native_command",
            return_value={},
        ),
        mock.patch.object(
            slash_commands,
            "handle_slash_command",
            return_value={"handled": False},
        ),
        mock.patch.object(resident_record_domain, "resolve_context_refs", return_value={}),
    ):
        result = _published_delivery(backend.deliver_resident_message_content(
            resident_id,
            "CASE-A",
            content,
            input_media=[{
                "media_type": "image",
                "source_type": "local_path",
                "source": image["path"],
                "mime_type": "image/png",
                "name": "latency-probe.png",
            }],
    ))

    assert result["resident_id"] == resident_id
    assert result["message"]["message_id"] == result["message_id"]
    assert result["input_state"]["state"] == "running"
    assert result["state"]["work_phase"] == "starting_session"
    job = resident_execution_work.read(resident_record)
    staged_image = Path(job["input_media"][0]["source"])
    assert staged_image != Path(image["path"])
    assert staged_image.read_bytes() == VALID_PNG_BYTES
    assert job["input_media"][0]["original_source"] == image["path"]
    assert "actual_sent_prompt" not in job

    Path(image["path"]).unlink()
    prepared = resident_execution_domain._prepare_resident_prompt(resident_record, job)
    assert str(image["path"]) not in prepared["actual_sent_prompt"]
    assert "[attached image: latency-probe.png]" in prepared["actual_sent_prompt"]
    request = resident_execution_domain._build_resident_execution_request(
        resident_record,
        prepared,
    )
    assert request.input_media[0].source == str(staged_image)
    assert staged_image.is_file()
    assert request.runtime_options.get("images") is None

    job["outcome"] = "completed"
    job["finished_at"] = "2026-09-28T00:00:00Z"
    resident_execution_work.write(resident_record, job)
    # A terminal Job alone cannot discard a still-running Conversation input.
    assert staged_image.exists()


@pytest.mark.parametrize("queued", [False, True])
@pytest.mark.parametrize("media_type", ["image", "file"])
def test_inline_media_positions_survive_delivery_and_provider_capture(resident_record, monkeypatch, tmp_path, queued, media_type):
    """消息执行前后保留图片与文件的插入位置

    首次发送和排队执行均保留有空白与空段的图文顺序；模型输入仍包含文件读取路径。

    :module: resident.conversation
    """
    from management_portal.execution.agent_data_projection import AgentDataProjector
    from management_portal.execution.message_projection import unwrap_message
    from tests.cases.test_agent_data_projection import _runtime_session, _invocation

    resident_id = resident_record["resident_id"]
    message_id = "msg_" + "d" * 32
    upload_id = "upload_" + "a" * 24
    attachment = tmp_path / upload_id / ("inline.png" if media_type == "image" else "manuscript.docx")
    attachment.parent.mkdir()
    attachment.write_bytes(VALID_PNG_BYTES if media_type == "image" else b"file-content")
    monkeypatch.setattr(resident_media_domain, "resident_input_media", lambda *a, **kw: [{
        "media_type": media_type, "source_type": "local_path", "source": str(attachment),
        "mime_type": "image/png" if media_type == "image" else "application/octet-stream", "name": attachment.name,
    }])
    monkeypatch.setattr(resident_record_domain, "resolve_context_refs", lambda *a, **kw: {})
    if queued:
        _published_delivery(backend.deliver_resident_message_content(resident_id, "sample_admin", "Previous work"))
    parts = [
        {"type": "text", "text": "\n1. Before "},
        {"type": media_type, "upload_id": upload_id, "name": attachment.name},
        {"type": "text", "text": ""},
        {"type": "text", "text": "\n2. After.\n"},
    ]
    result = _published_delivery(resident_protocol.deliver_resident_input({"resident_id": resident_id, "message": {
        "message_id": message_id, "role": "user", "content_parts": parts,
    }}, "sample_admin"))
    job = resident_execution_work.read(resident_record)
    if queued:
        assert result["input_state"]["state"] == "queued"
        waiting = resident_execution_work.waiting_input_queue(job)
        job = resident_execution_work.promote_queue_item(job, waiting[0], [], "2026-10-13T10:00:00Z")
        resident_execution_work.write(resident_record, job)
    body = "\n".join(part["text"] for part in parts if part["type"] == "text")
    prepared = resident_execution_domain._prepare_resident_prompt(resident_record, job)
    provider_body = body if media_type == "image" else body + f"\n\n[file: {attachment}]"
    assert unwrap_message(prepared["actual_sent_prompt"]) == provider_body
    request = resident_execution_domain._build_resident_execution_request(resident_record, prepared)
    store = get_agent_data_store()
    session = _runtime_session(request, "inline-roundtrip-session")
    projector = AgentDataProjector(store)
    projector.begin(request, session)
    projector.observe_invocation(request.execution_id, _invocation(request, session))
    assert store.chat_message(resident_id, message_id)["content_parts"] == parts


def test_resident_workspace_image_marker_becomes_native_input_media(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workspace 图片标记转为原生输入媒体"""
    resident_id = str(resident_record["resident_id"])
    image_path = Path(str(resident_record["default_cwd"])) / "workspace-probe.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nworkspace-image")
    content = f"识别这张 workspace 图片。\n\n[image: {image_path}]"
    with (
        mock.patch.object(
            resident_session_backend_domain,
            "handle_resident_native_command",
            return_value={},
        ),
        mock.patch.object(
            slash_commands,
            "handle_slash_command",
            return_value={"handled": False},
        ),
        mock.patch.object(resident_record_domain, "resolve_context_refs", return_value={}),
    ):
        result = _published_delivery(backend.deliver_resident_message_content(
            resident_id,
            "CASE-A",
            content,
    ))

    assert result["resident_id"] == resident_id
    assert result["message"]["message_id"] == result["message_id"]
    assert result["input_state"]["state"] == "running"
    assert result["state"]["work_phase"] == "starting_session"
    job = resident_execution_work.read(resident_record)
    assert Path(job["input_media"][0]["source"]).read_bytes() == image_path.read_bytes()
    assert job["input_media"][0]["original_source"] == str(image_path)
    assert "actual_sent_prompt" not in job
    prepared = resident_execution_domain._prepare_resident_prompt(resident_record, job)
    assert str(image_path) not in prepared["actual_sent_prompt"]
    assert "[attached image: workspace-probe.png]" in prepared["actual_sent_prompt"]


def test_resident_input_media_rejects_path_outside_workspace(
    resident_record: dict[str, object],
    tmp_path: Path,
) -> None:
    """Resident 输入媒体拒绝上传区和 Workspace 外路径"""
    outside_path = tmp_path / "outside.png"
    outside_path.write_bytes(b"\x89PNG\r\n\x1a\noutside-image")

    with pytest.raises(PermissionError, match="outside its upload and workspace"):
        resident_media_domain.resident_input_media(
            [{"kind": "image", "path": str(outside_path)}],
            actor="CASE-A",
            resident_id=str(resident_record["resident_id"]),
            content=f"[image: {outside_path}]",
            workspace_roots=resident_runtime_domain.resident_media_workspace_roots(
                resident_record
            ),
        )


def test_resident_input_media_rejects_another_residents_image(
    resident_record: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """复用其他 Resident 图片时要求可访问其对话"""
    root_id = str(resident_record["resident_id"])
    child_id = "resident_bbbbbbbbbbbb"
    registry = resident_lineage.snapshot(resident_record)
    registry["residents"].append({
        "resident_id": child_id,
        "source_resident_id": root_id,
        "lineage_id": resident_record["lineage_id"],
        "created_by": "CASE-A",
        "backend": "codex",
    })
    resident_fork_domain._persist_derived_resident(resident_record, registry["residents"][-1])
    monkeypatch.setattr(workspace_domain, "CHAT_IMAGE_CACHE_ROOT", tmp_path / "images")
    monkeypatch.setattr(workspace_contracts, "CHAT_IMAGE_CACHE_ROOT", tmp_path / "images")
    image = workspace_domain.save_workspace_pasted_image({
        "resident_id": root_id,
        "filename": "root-only.png",
        "content_type": "image/png",
        "data_bytes": VALID_PNG_BYTES,
    }, "CASE-A")

    reused = resident_media_domain.resident_input_media(
        [{"kind": "image", "path": image["path"]}],
        actor="CASE-A",
        resident_id=child_id,
        content=f"[image: {image['path']}]",
    )
    assert reused[0]["source"] == image["path"]
    def reject_root_conversation(resident_id: str, _actor: str) -> dict[str, str]:
        if resident_id == root_id:
            raise PermissionError("hidden Conversation")
        return {"resident_id": resident_id}

    monkeypatch.setattr(
        workspace_attachments.resident_domain_port,
        "resolve_resident_identity",
        reject_root_conversation,
    )
    with pytest.raises(PermissionError, match="Conversation"):
        resident_media_domain.resident_input_media(
            [{"kind": "image", "path": image["path"]}],
            actor="CASE-A",
            resident_id=child_id,
            content=f"[image: {image['path']}]",
        )


def test_exiting_continuation_rewakes_job_persisted_during_shutdown(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """续接退出时重新唤醒关闭期间持久化的任务"""
    resident_id = str(resident_record["resident_id"])
    notifications: list[str] = []
    resident_execution_work.track_current_thread(resident_record)
    resident_execution_work.write(
        resident_record,
        {
            'execution_id': "job_shutdown_rewake",
            "actor": "CASE-A",
            "status": "queued",
            "queue_reason": "starting",
            "queue": [],
        },
    )
    monkeypatch.setattr(
        lifecycle,
        "notify_execution_binding",
        lambda target_id: notifications.append(str(target_id)),
    )

    resident_intake_domain.finish_resident_continuation(resident_id)

    assert resident_execution_work.thread(resident_record) is None
    assert notifications == [resident_id]


def test_exiting_continuation_wakes_the_next_job(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """续接退出清除线程并唤醒下一任务"""
    resident_id = str(resident_record["resident_id"])
    notifications: list[str] = []
    resident_execution_work.track_current_thread(resident_record)
    resident_execution_work.write(
        resident_record,
        {
            'execution_id': "job_release_after_dev",
            "actor": "CASE-A",
            "status": "queued",
            "queue_reason": "starting",
            "queue": [],
        },
    )
    monkeypatch.setattr(
        lifecycle,
        "notify_execution_binding",
        lambda target_id: notifications.append(str(target_id)),
    )

    resident_intake_domain.finish_resident_continuation(resident_id)

    assert resident_execution_work.thread(resident_record) is None
    assert notifications == [resident_id]


@pytest.mark.parametrize("head_phase", ["queued", "running"])
def test_clear_resident_queue_preserves_admitted_execution(
    resident_record, monkeypatch, head_phase,
):
    """清空 Resident 等待队列保留已准入执行和接收状态"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    initial_ids = ["msg_bulk_head", "msg_bulk_local", "msg_bulk_remote"]
    for message_id in initial_ids:
        repository.append_message(resident_id, {
            "message_id": message_id, "role": "user", "author_id": "sample_admin",
            "content_parts": [{"type": "text", "text": message_id}],
        })
        repository.set_input_state(resident_id, message_id, "queued")
    resident_execution_work.write(resident_record, {
         "status": head_phase, "actor": "sample_admin",
        "user_message_id": initial_ids[0], "execution_id": "execution-bulk-head",
        "queue": [
            { "actor": "CASE-A", "user_message_id": initial_ids[1]},
            { "actor": "sample_admin", "user_message_id": initial_ids[2]},
        ],
     "submitted_at": "2026-10-14T00:00:00Z"})
    original_intake = resident_protocol.get_resident_state(resident_id, "sample_admin")["state"]["intake_state"]

    service = mock.Mock(states=lambda executions: {key: {"status": head_phase} for key in executions})
    service.cancel.side_effect = AssertionError("Waiting inputs have no submitted Execution")
    monkeypatch.setattr(execution_port, "service", lambda: service)
    result = resident_protocol.clear_resident_queue(resident_id, "sample_admin")
    assert result["cleared_count"] == 2
    assert result["errors"] == []
    current = resident_execution_work.read(resident_record)
    assert current["queue"] == []
    assert current["execution_id"] == "execution-bulk-head"
    assert not current.get("stop_requested_by")
    assert all(repository.input_state(resident_id, mid)["state"] == "cancelled" for mid in initial_ids[1:])
    assert resident_protocol.get_resident_state(resident_id, "sample_admin")["state"]["intake_state"] == original_intake
    service.cancel.assert_not_called()


def test_clear_resident_queue_uses_isa_authority_and_empty_queue_is_idempotent(
    resident_record, monkeypatch,
):
    """清空空队列保持幂等，只读取额度且不提交或取消执行"""
    resident_id = str(resident_record["resident_id"])
    quota = mock.Mock(side_effect=lambda bindings: {
        key: {"state": "open", "reason": ""} for key in bindings})
    service = SimpleNamespace(binding_quota_states=quota)
    monkeypatch.setattr(execution_port, "service", lambda: service)
    for _attempt in range(2):
        result = resident_protocol.clear_resident_queue(resident_id, "sample_admin")
        assert result["cleared_count"] == 0
        assert result["errors"] == []
    monkeypatch.setattr(resident_access, "can_manage", lambda record, actor: False)
    assert resident_protocol.clear_resident_queue(resident_id, "sample_admin")["cleared_count"] == 0
    assert quota.call_count == 3
    assert all(set(call.args[0]) == {f"resident:{resident_id}"} for call in quota.call_args_list)


def test_clear_resident_queue_cancels_held_inputs_without_reopening_intake(resident_record):
    """清空队列取消暂存输入并保持接收关闭"""
    resident_id = str(resident_record["resident_id"])
    resident_protocol.change_resident_state(
        resident_id, "CASE-A", state_domain="intake", transition="close",
    )
    with (
        mock.patch.object(resident_session_backend_domain, "handle_resident_native_command", return_value={}),
        mock.patch.object(slash_commands, "handle_slash_command", return_value={"handled": False}),
        mock.patch.object(resident_record_domain, "resolve_context_refs", return_value={}),
        mock.patch.object(resident_registration_domain, "build_resident_prompt", return_value="held prompt"),
    ):
        backend.deliver_resident_message_content(resident_id, "CASE-A", "held first message")
        backend.deliver_resident_message_content(resident_id, "CASE-A", "held following message")
    held = resident_execution_work.read(resident_record)
    assert resident_execution_work.is_execution_held(held)
    message_ids = [held["user_message_id"], *[item["user_message_id"] for item in held["queue"]]]
    with mock.patch.object(execution_port, "service", side_effect=AssertionError("No execution was admitted")):
        result = resident_protocol.clear_resident_queue(resident_id, "CASE-A")
        repeated = resident_protocol.clear_resident_queue(resident_id, "CASE-A")
    assert result["cleared_count"] == 2
    assert result["errors"] == []
    assert repeated["cleared_count"] == 0
    assert not resident_execution_work.is_active(resident_execution_work.read(resident_record))
    repository = resident_conversation_stream.get_repository()
    assert all(repository.input_state(resident_id, message_id)["state"] == "cancelled" for message_id in message_ids)
    assert resident_protocol.get_resident_state(resident_id, "CASE-A")["state"]["intake_state"] == "closed"
    # CASE-A 10-06: withdrawn messages never reached the Resident; the Conversation says nothing about them.
    assert not [item for item in repository.agent_data.read_chat_messages(resident_id)
                if item.get("reply_to_message_id") in message_ids or item.get("role") == "system"]




def test_clear_resident_queue_is_independent_of_execution_service(resident_record, monkeypatch):
    """清空等待队列独立于执行服务可用性"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    head_id, remote_id, local_id = "msg_clear_partial_head", "msg_clear_partial_remote", "msg_clear_partial_local"
    for message_id in (head_id, remote_id, local_id):
        repository.append_message(resident_id, {
            "message_id": message_id, "role": "user", "author_id": "sample_admin",
            "content_parts": [{"type": "text", "text": message_id}],
        })
        repository.set_input_state(resident_id, message_id, "queued")
    resident_execution_work.write(resident_record, {
        'execution_id': "job_clear_partial_head", "status": "running", "actor": "sample_admin", "user_message_id": head_id,
        "queue": [
            { "actor": "sample_admin", "user_message_id": remote_id},
            { "actor": "sample_admin", "user_message_id": local_id},
        ],
    })
    service = mock.Mock()
    service.cancel.side_effect = RuntimeError("Execution temporarily unavailable")
    monkeypatch.setattr(execution_port, "service", lambda: service)
    result = resident_protocol.clear_resident_queue(resident_id, "sample_admin")
    assert result["cleared_message_ids"] == [remote_id, local_id]
    assert result["errors"] == []
    service.cancel.assert_not_called()
    current = resident_execution_work.read(resident_record)
    assert [item["user_message_id"] for item in current["queue"]] == []
    assert not current.get("stop_requested_by")
    assert repository.input_state(resident_id, local_id)["state"] == "cancelled"
    assert repository.input_state(resident_id, remote_id)["state"] == "cancelled"






def test_cancel_queue_retry_rolls_back_interrupted_resident_commit(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取消队列提交中断时回滚输入状态且重试可完成"""
    resident_id = str(resident_record["resident_id"])
    message_id = "msg_cancel_interrupted_bbbbbbbbb"
    repository = resident_conversation_stream.get_repository()
    repository.append_message(
        resident_id,
        {
            "message_id": message_id,
            "role": "user",
            "content_parts": [{"type": "text", "text": "cancel me"}],
            "author_id": "sample_admin",
        },
    )
    repository.set_input_state(resident_id, message_id, "queued")
    resident_execution_work.write(
        resident_record,
        {
            'execution_id': "job_current",
            "actor": "CASE-A",
            "status": "running",
            "queue": [{

                "user_message_id": message_id,
                "actor": "sample_admin",
}],
        },
    )
    cancel_calls = []

    class Service:
        @staticmethod
        def binding_quota_states(bindings):
            return {key: {"state": "open", "reason": ""} for key in bindings}

        @staticmethod
        def cancel(execution_id, **_kwargs):
            cancel_calls.append(execution_id)
            return {"status": "cancelled", "cancel_requested": True}

    monkeypatch.setattr(execution_port, "service", lambda: Service())
    original_write = resident_execution_work.write
    interrupted = True

    def interrupt_first_commit(*args, **kwargs):
        nonlocal interrupted
        if interrupted:
            interrupted = False
            monkeypatch.setattr(resident_domain_common, "now_iso", lambda: "2026-09-07T00:00:00Z")
            raise OSError("simulated Resident state commit interruption")
        return original_write(*args, **kwargs)

    monkeypatch.setattr(resident_execution_work, "write", interrupt_first_commit)

    with pytest.raises(OSError, match="commit interruption"):
        resident_protocol.cancel_resident_input(
            resident_id,
            "sample_admin",
            message_id,
        )

    assert repository.input_state(resident_id, message_id)["state"] == "queued"
    assert len(resident_execution_work.normalized_queue(resident_execution_work.read(resident_record))) == 1

    result = resident_protocol.cancel_resident_input(
        resident_id,
        "sample_admin",
        message_id,
    )

    assert result["input_state"]["state"] == "cancelled"
    assert resident_execution_work.normalized_queue(resident_execution_work.read(resident_record)) == []
    assert cancel_calls == []
    # CASE-A 10-06: the page tells the one who withdrew it; the Conversation gets no notice.
    assert [item["message_id"] for item in repository.agent_data.read_chat_messages(resident_id)] == [message_id]


def test_inject_queue_message_does_not_stop_current_execution(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """队列消息注入当前执行后沿用消息身份且仅投递一次"""
    resident_id = str(resident_record["resident_id"])
    queued_message_id = "msg_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    queued_input = {
        "message_id": queued_message_id,
        "role": "user",
        "content_parts": [{"type": "text", "text": "补充检查移动端"}],
        "created_at": "2026-08-23T00:00:00Z",
        "author_id": "CASE-A",
    }
    resident_execution_work.write(
        resident_record,
        {

            "execution_id": "execution-111111111111111111111111",
            "actor": "CASE-A",
            "status": "running",
            "queue": [{
                'execution_id': "job_queued_input",
                "user_message_id": queued_message_id,
                "input_message": queued_input,
                "actor": "CASE-A",
                "raw_message": "补充检查移动端",
                "actual_sent_prompt": "补充检查移动端",
            }],
         "submitted_at": "2026-10-14T00:00:00Z"},
    )
    calls: list[tuple[str, str, list[dict[str, object]]]] = []

    class Service:
        def status(self, *_args, **_kwargs):
            return {"status": "running"}

        def append_input(self, execution_id, *, actor, input, **_kwargs):
            from management_portal.society_service.invariant_locks import lock_held
            assert not lock_held(), "Execution RPC under Resident state lock"
            calls.append((execution_id, actor, input))
            return {
                "control_id": "control-222222222222222222222222",
                "status": "delivered",
                "expected_turn_id": "turn-active",
            }

        def input_control(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(execution_port, "service", lambda: Service())
    assert resident_conversation_stream.get_repository().read_messages(
        resident_id
    ) == []
    result = _published_delivery(resident_protocol.inject_queued_input_into_active_turn(
        resident_id,
        "CASE-A",
        queued_message_id,
    ))

    stored = resident_execution_work.read(resident_record)
    assert stored['execution_id'] == 'execution-111111111111111111111111'
    assert "status" not in stored
    assert "queue_reason" not in stored
    assert stored["execution_id"] == "execution-111111111111111111111111"
    assert stored["queue"] == []
    assert calls == [(
        "execution-111111111111111111111111",
        "CASE-A",
        [{"type": "text", "text": "<user-message>\n补充检查移动端\n</user-message>"}],
    )]
    conversation = resident_conversation_stream.get_repository().read_messages(
        resident_id
    )
    queued_message = next(
        item for item in conversation if item["message_id"] == queued_message_id
    )
    assert _message_text(queued_message) == "补充检查移动端"
    assert conversation == [queued_message]
    assert queued_message["created_at"] == queued_input["created_at"]
    assert queued_message["activated_at"] != queued_message["created_at"]
    assert result["resident_id"] == resident_id
    assert result["message_id"] == queued_message_id
    assert result["accepted"] is True
    assert result["message"]["message_id"] == queued_message_id
    assert result["input_state"]["state"] == "completed"
    assert result["queue"]["items"] == []
    assert isinstance(result["state"], dict)
    assert isinstance(result["cursor"], int)
    assert resident_conversation_projection_domain.conversation_family_summaries(
        [resident_id], "CASE-A",
    )[resident_id]["unread_signature"]["completion_id"] == ""

    retried = _published_delivery(resident_protocol.inject_queued_input_into_active_turn(
        resident_id,
        "CASE-A",
        queued_message_id,
    ))
    assert retried["already_injected"] is True
    assert calls == [(
        "execution-111111111111111111111111",
        "CASE-A",
        [{"type": "text", "text": "<user-message>\n补充检查移动端\n</user-message>"}],
    )]



def test_send_now_into_a_forks_first_turn_does_not_repeat_its_inherited_context(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """立即发送进 Fork 首轮时不重复继承的上下文

    Fork 首轮运行期间排队的消息按当时状态带着继承的历史构建；立即发送进正在运行的这一轮时，只发送消息本身，不再附带继承的历史，也不会因此超过输入长度上限。

    An input queued while a Fork's first turn runs was built with the
    inherited context; Send now gives the running turn only the message.

    :module: resident.fork-handoff
    :importance: P1
    :design: decision_0f57656a2acd
    """
    from management_portal.society_service.resident_record_domain import (
        save_resident_record as _save_resident_record,
    )

    store = get_agent_data_store()
    sid = store.put_segment(segment_type="model", content="source question", created_by_agent_id="resident_bbbbbbbbbbbb")
    store.append_agent_segments(resident_record["resident_id"], [sid], actor_ref="CASE-A")
    resident_id = str(resident_record["resident_id"])
    resident_record.update({
        "fork_mode": "recent-5",
        "source_resident_id": "resident_bbbbbbbbbbbb",
    })
    _save_resident_record(resident_record)
    queued_message_id = "msg_cccccccccccccccccccccccccccccccc"
    queued_prompt = resident_registration_domain.build_resident_prompt(
        resident_record, {"actor": "CASE-A", "message": "补充检查移动端"},
    )
    prepared = resident_execution_domain._portable_context_execution(
        resident_record, {"actor": "CASE-A", "raw_message": "补充检查移动端"})
    assert "source question" in prepared["fork_context_input"]["portable_prompt"]
    resident_execution_work.write(resident_record, {

        "execution_id": "execution-333333333333333333333333",
        "actor": "CASE-A",
        "queue": [{
            'execution_id': "job_queued_input",
            "user_message_id": queued_message_id,
            "input_message": {
                "message_id": queued_message_id, "role": "user", "author_id": "CASE-A",
                "content_parts": [{"type": "text", "text": "补充检查移动端"}],
                "created_at": "2026-10-10T00:00:00Z",
            },
            "actor": "CASE-A",
            "raw_message": "补充检查移动端",
            "effective_message": "补充检查移动端",
            "message": queued_prompt,
            "actual_sent_prompt": queued_prompt,
            "fork_context_input": prepared["fork_context_input"],
        }],
     "submitted_at": "2026-10-14T00:00:00Z"})
    sent: list[list[dict[str, object]]] = []

    class Service:
        def status(self, *_args, **_kwargs):
            return {"status": "running"}

        def append_input(self, _execution_id, *, input, **_kwargs):
            sent.append(input)
            return {"control_id": "control-333333333333333333333333", "status": "delivered",
                    "expected_turn_id": "turn-active"}

        def input_control(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(execution_port, "service", lambda: Service())
    _published_delivery(resident_protocol.inject_queued_input_into_active_turn(resident_id, "CASE-A", queued_message_id))

    (text_part,) = sent[0]
    assert "补充检查移动端" in text_part["text"]
    assert "source question" not in text_part["text"]
    assert "<resident_trace_context>" not in text_part["text"]



def test_a_send_now_input_the_execution_refuses_waits_for_the_next_turn(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """执行层拒收的立即发送输入改在下一轮处理

    立即发送的输入被执行层拒收（没有活动轮次、不支持追加或输入不合法）时，消息转入下一轮等待，不会滞留在队列之外，也不会在每次轮次结束时反复重试而阻塞后续消息；早先被拒收而滞留的输入在轮次结算时同样转入下一轮。

    A refused input is neither stranded outside the queue nor retried forever.

    :module: resident.conversation
    :importance: P1
    :design: decision_b5d349190743
    """
    resident_id = str(resident_record["resident_id"])
    message_id = "msg_dddddddddddddddddddddddddddddddd"
    item = {
        'execution_id': "job_queued_input",
        "user_message_id": message_id,
        "input_message": {
            "message_id": message_id, "role": "user", "author_id": "CASE-A",
            "content_parts": [{"type": "text", "text": "补充检查移动端"}],
            "created_at": "2026-10-10T00:00:00Z",
        },
        "actor": "CASE-A",
        "raw_message": "补充检查移动端",
        "actual_sent_prompt": "补充检查移动端",
    }
    running = { "execution_id": "execution-444444444444444444444444", "actor": "CASE-A", "submitted_at": "2026-10-14T00:00:00Z"}

    class Service:
        def status(self, *_args, **_kwargs):
            return {"status": "running"}

        def append_input(self, *_args, **_kwargs):
            raise ValueError("execution input control text is too long")

        def input_control(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(execution_port, "service", lambda: Service())
    resident_execution_work.write(resident_record, {**running, "queue": [dict(item)]})
    _published_delivery(resident_protocol.inject_queued_input_into_active_turn(resident_id, "CASE-A", message_id))

    job = resident_execution_work.read(resident_record)
    assert not resident_execution_work.send_now_inputs(job)
    assert [entry["user_message_id"] for entry in job["queue"]] == [message_id]

    # An input an earlier refusal left outside the queue is settled the same way.
    stranded = {**item, "active_turn_input": {"control_id": "control-stranded", "actor": "CASE-A"}}
    resident_execution_work.write(resident_record, {**running, "send_now_inputs": [stranded]})
    assert resident_intake_domain.reconcile_queued_turn_inputs(resident_id, 'execution-444444444444444444444444')
    job = resident_execution_work.read(resident_record)
    assert not resident_execution_work.send_now_inputs(job)
    assert [entry["user_message_id"] for entry in job["queue"]] == [message_id]

def test_runtime_settings_save_updates_only_speed_on_running_turn(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """运行中保存设置仅即时更新速度且其他设置下轮生效"""
    resident_id = str(resident_record["resident_id"])
    resident_execution_work.write(
        resident_record,
        {

            "execution_id": "execution-runtime-settings",
            "actor": "CASE-A",
            "status": "running",
            "queue": [],
         "submitted_at": "2026-10-14T00:00:00Z"},
    )
    calls: list[dict[str, object]] = []

    class Service:
        @staticmethod
        def update_turn_settings(execution_id, *, actor, service_tier, control_id):
            from management_portal.society_service.invariant_locks import lock_held
            assert not lock_held(), "Execution RPC under Resident state lock"
            calls.append({
                "execution_id": execution_id,
                "actor": actor,
                "service_tier": service_tier,
                "control_id": control_id,
            })
            return {"control_id": control_id, "status": "delivered"}

    monkeypatch.setattr(execution_port, "service", lambda: Service())

    result = resident_protocol.update_resident_runtime_settings(
        resident_id,
        "CASE-A",
        {"runtime_settings": {"service_tier": "priority", "collaboration_mode": "plan"}},
    )

    assert result["runtime_settings"]["service_tier"] == "priority"
    assert result["active_turn_settings_update"]["status"] == "delivered"
    assert result["active_turn_fields"] == ["service_tier"]
    assert result["next_turn_fields"] == [
        "collaboration_mode", "model", "reasoning_effort",
    ]
    assert result["runtime_settings"]["collaboration_mode"] == "plan"
    assert calls[0]["execution_id"] == "execution-runtime-settings"
    assert calls[0]["actor"] == "CASE-A"
    assert calls[0]["service_tier"] == "priority"
    stored = resident_execution_work.read(resident_record)
    assert stored['execution_id'] == 'execution-runtime-settings'
    assert stored["execution_id"] == "execution-runtime-settings"


@pytest.mark.parametrize("running", [False, True])
@pytest.mark.parametrize("mode", ["default", "plan"])
def test_runtime_mode_persists_for_next_turn_without_steering(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    running: bool,
    mode: str,
) -> None:
    """协作模式保存供下一轮使用且不干预当前执行"""
    resident_id = str(resident_record["resident_id"])
    resident_runtime_domain.write_resident_runtime_settings(
        resident_record, {"collaboration_mode": "default" if mode == "plan" else "plan"},
    )
    if running:
        resident_execution_work.write(resident_record, {
             "execution_id": "execution-mode",
            "actor": "CASE-A", "status": "running", "queue": [],
         "submitted_at": "2026-10-14T00:00:00Z"})

    def reject_injection(*_args, **_kwargs):
        pytest.fail("mode changes must not steer or interrupt the active turn")

    monkeypatch.setattr(resident_intake_domain, "_submit_active_turn_input", reject_injection)
    result = resident_protocol.update_resident_runtime_settings(
        resident_id, "CASE-A", {"runtime_settings": {"collaboration_mode": mode}},
    )
    assert result["next_turn_fields"] == ["collaboration_mode"]
    assert result["active_turn_settings_update"] == {"requested": False, "status": "next_turn"}
    assert result["active_turn_fields"] == []
    persisted = resident_runtime_domain.read_resident_runtime_settings(resident_record)
    assert persisted["collaboration_mode"] == mode
    assert codex_runtime_settings.codex_cli_overrides(persisted)["collaboration_mode"] == mode
    unchanged = resident_protocol.update_resident_runtime_settings(
        resident_id, "CASE-A", {"runtime_settings": {"collaboration_mode": mode}},
    )
    assert unchanged["next_turn_fields"] == []
    assert unchanged["active_turn_settings_update"]["status"] == "unchanged"
    assert resident_runtime_domain.read_resident_runtime_settings(resident_record) == persisted
    if running:
        assert resident_execution_work.read(resident_record)["execution_id"] == "execution-mode"


@pytest.mark.parametrize("mode", [None, "", "auto", True, ["plan"]])
def test_runtime_mode_rejects_invalid_values_without_writing(
    resident_record: dict[str, object], mode: object,
) -> None:
    """无效协作模式被拒绝且不写入设置"""
    previous = resident_runtime_domain.read_resident_runtime_settings(resident_record)
    with pytest.raises(ValueError, match="collaboration_mode"):
        resident_protocol.update_resident_runtime_settings(
            str(resident_record["resident_id"]), "CASE-A",
            {"runtime_settings": {"collaboration_mode": mode}},
        )
    assert resident_runtime_domain.read_resident_runtime_settings(resident_record) == previous


@pytest.mark.parametrize("provider", ["claude", "kimi", "portal"])
def test_runtime_mode_rejects_non_codex_provider(
    resident_record: dict[str, object], provider: str,
) -> None:
    """非 codex Provider 拒绝协作模式设置"""
    record = {**resident_record, "backend": provider}
    with pytest.raises(ValueError, match="collaboration_mode|不支持运行设置"):
        resident_runtime_domain.validate_backend_runtime_updates(record, {}, {"collaboration_mode": "plan"})


def test_runtime_mode_is_preserved_in_creation_settings(resident_record: dict[str, object]) -> None:
    """Resident 创建设置保留协作模式"""
    settings = resident_runtime_domain.resident_runtime_settings_from_register_payload(
        resident_record, {"runtime_settings": {"collaboration_mode": "plan"}},
        "CASE-A", "2026-09-13T00:00:00Z",
    )
    assert settings["collaboration_mode"] == "plan"


def test_codex_registration_resolves_defaults_against_the_selected_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """codex 注册按选定账号解析默认模型和推理强度"""
    monkeypatch.setattr(
        resident_backend,
        "session_record",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        codex_runtime_settings,
        "codex_runtime_catalog",
        lambda *, account_id: {
            "models": ["account-model"],
            "default_model": "account-model",
            "efforts_by_model": {"account-model": ["low"]},
            "default_efforts_by_model": {"account-model": "low"},
            "service_tiers_by_model": {"account-model": []},
        },
    )

    settings = resident_runtime_domain.resident_runtime_settings_from_register_payload(
        {
            "resident_id": "resident_bbbbbbbbbbbb",
            "backend": "codex",
            "backend_route_id": "zero",
        },
        {},
        "CASE-A",
        "2026-09-21T00:00:00Z",
    )

    assert {
        field: settings[field]
        for field in ("model", "reasoning_effort", "service_tier")
    } == {
        "model": "account-model",
        "reasoning_effort": "low",
        "service_tier": "",
    }


def test_runtime_settings_save_while_idle_only_persists_settings(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空闲时保存运行设置仅持久化并按账号能力归一化"""
    class Service:
        @staticmethod
        def update_turn_settings(*_args, **_kwargs):
            pytest.fail("idle runtime settings save must not submit active-turn settings")

    monkeypatch.setattr(execution_port, "service", lambda: Service())

    result = resident_protocol.update_resident_runtime_settings(
        str(resident_record["resident_id"]),
        "CASE-A",
        {"runtime_settings": {"service_tier": "priority"}},
    )

    assert result["runtime_settings"]["service_tier"] == "priority"
    assert result["active_turn_settings_update"] == {
        "requested": False,
        "status": "inactive",
        "resident_id": str(resident_record["resident_id"]),
    }
    assert result["active_turn_fields"] == []
    assert result["next_turn_fields"] == [
        "model", "reasoning_effort", "service_tier",
    ]
    catalog_accounts = []

    def catalog(*, account_id):
        catalog_accounts.append(account_id)
        return {
            "models": ["catalog-model", "limited-model"],
            "efforts_by_model": {"catalog-model": ["xhigh"], "limited-model": ["low"]},
        }

    resident_record["backend_route_id"] = "four"
    resident_record_domain.save_resident_record(resident_record, commit_configuration=True)
    monkeypatch.setattr(codex_runtime_settings, "codex_runtime_catalog", catalog)
    result = resident_protocol.update_resident_runtime_settings(
        str(resident_record["resident_id"]), "CASE-A",
        {"runtime_settings": {"model": "catalog-model"}},
    )
    assert result["runtime_settings"]["model"] == "catalog-model"
    assert codex_runtime_settings.effective_codex_runtime_settings(result["runtime_settings"])["reasoning_effort"] == "xhigh"
    assert set(catalog_accounts) == {"zero"}
    result = resident_protocol.update_resident_runtime_settings(
        str(resident_record["resident_id"]), "CASE-A",
        {"runtime_settings": {"model": "limited-model"}},
    )
    assert result["runtime_settings"]["model"] == "limited-model"
    assert result["runtime_settings"]["reasoning_effort"] == "low"


def test_runtime_settings_provider_catalog_validation_runs_outside_resident_lock(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider 运行目录校验在 Resident 锁外执行"""
    lock_states = []

    def validate(_record, _current, updates):
        lock_states.append(invariant_locks.lock_held())
        return dict(updates)

    monkeypatch.setattr(resident_runtime_domain, "validate_backend_runtime_updates", validate)

    result = resident_protocol.update_resident_runtime_settings(
        str(resident_record["resident_id"]),
        "CASE-A",
        {"runtime_settings": {"service_tier": "priority"}},
    )

    assert lock_states == [False]
    assert result["runtime_settings"]["service_tier"] == "priority"


def test_fast_command_updates_running_turn_with_native_settings_control(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """快速指令通过原生设置控制更新当前轮速度"""
    resident_id = str(resident_record["resident_id"])
    resident_runtime_domain.write_resident_runtime_settings(
        resident_record,
        {"service_tier": ""},
    )
    resident_record_domain.save_resident_record(resident_record)
    resident_execution_work.write(
        resident_record,
        {

            "execution_id": "execution-fast-command",
            "actor": "CASE-A",
            "status": "running",
            "queue": [],
         "submitted_at": "2026-10-14T00:00:00Z"},
    )
    calls: list[tuple[str, str, str, str]] = []

    class Service:
        @staticmethod
        def update_turn_settings(execution_id, *, actor, service_tier, control_id):
            calls.append((execution_id, actor, service_tier, control_id))
            return {"control_id": control_id, "status": "pending"}

    monkeypatch.setattr(execution_port, "service", lambda: Service())
    protocol_input = {
        "message_id": "msg_ffffffffffffffffffffffffffffffff",
        "role": "user",
        "content_parts": [{"type": "text", "text": "/fast"}],
    }
    result = backend.deliver_resident_message_content(
        resident_id,
        "CASE-A",
        "/fast",
        message_id=protocol_input["message_id"],
        protocol_input=protocol_input,
    )

    assert calls and calls[0][:3] == (
        "execution-fast-command", "CASE-A", "priority",
    )
    settings = resident_runtime_domain.read_resident_runtime_settings(resident_record)
    assert settings["service_tier"] == "priority"
    stored = resident_execution_work.read(resident_record)
    assert stored['execution_id'] == 'execution-fast-command'
    assert stored["execution_id"] == "execution-fast-command"
    assert resident_conversation_projection_domain.conversation_family_summaries(
        [resident_id], "CASE-A",
    )[resident_id]["unread_signature"]["completion_id"] == ""


@pytest.mark.parametrize("failed_commit", [1, 2], ids=["intent", "delivery"])
def test_inject_queue_message_recovers_commit_interruption(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    failed_commit: int,
) -> None:
    """队列注入提交中断后恢复且输入仅追加一次"""
    resident_id = str(resident_record["resident_id"])
    message_id = "msg_dddddddddddddddddddddddddddddddd"
    resident_execution_work.write(
        resident_record,
        {

            "execution_id": "execution-444444444444444444444444",
            "actor": "CASE-A",
            "status": "running",
            "queue": [{
                'execution_id': "job_queued_input",
                "user_message_id": message_id,
                "input_message": {
                    "message_id": message_id,
                    "role": "user",
                    "created_at": "2026-09-08T00:00:00Z",
                    "content_parts": [{"type": "text", "text": "提交中断恢复"}],
                    "author_id": "CASE-A",
                },
                "actor": "CASE-A",
                "actual_sent_prompt": "提交中断恢复",
            }],
         "submitted_at": "2026-10-14T00:00:00Z"},
    )
    append_calls = []

    class Service:
        @staticmethod
        def append_input(execution_id, *, actor, input, control_id, source_segment_id=""):
            append_calls.append((execution_id, actor, input, control_id))
            return {"control_id": control_id, "status": "delivered"}

        @staticmethod
        def input_control(*_args, **_kwargs):
            return {"status": "delivered"} if append_calls else None

    monkeypatch.setattr(execution_port, "service", lambda: Service())
    original_write = resident_execution_work.write
    commit_count = 0

    def interrupt_first_commit(*args, **kwargs):
        nonlocal commit_count
        commit_count += 1
        if commit_count == failed_commit:
            raise OSError("simulated Resident state commit interruption")
        return original_write(*args, **kwargs)

    monkeypatch.setattr(resident_execution_work, "write", interrupt_first_commit)

    with pytest.raises(OSError, match="commit interruption"):
        _published_delivery(resident_protocol.inject_queued_input_into_active_turn(
            resident_id,
            "CASE-A",
            message_id,
        ))

    repository = resident_conversation_stream.get_repository()
    assert repository.input_state(resident_id, message_id)["state"] == (
        "queued" if failed_commit == 1 else "completed"
    )
    current = resident_execution_work.read(resident_record)
    assert len(resident_execution_work.normalized_queue(current)) == (1 if failed_commit == 1 else 0)
    assert len(resident_execution_work.send_now_inputs(current)) == (0 if failed_commit == 1 else 1)

    retried = _published_delivery(resident_protocol.inject_queued_input_into_active_turn(
        resident_id,
        "CASE-A",
        message_id,
    ))

    assert retried.get("already_injected", False) is (failed_commit == 2)
    assert retried["accepted"] is True
    assert resident_execution_work.normalized_queue(resident_execution_work.read(resident_record)) == []
    assert len(append_calls) == 1


def test_cancel_resident_input_reconciles_a_concurrent_state_change(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取消输入遇到并发终态时返回已终结状态"""
    states = iter([
        {"message_id": "message-1", "state": "queued"},
        {"message_id": "message-1", "state": "completed"},
        {"message_id": "message-1", "state": "completed"},
        {"message_id": "message-1", "state": "completed"},
    ])
    queue_cancel_calls = []

    monkeypatch.setattr(
        resident_protocol,
        "_stored_resident_input_state",
        lambda *_args, **_kwargs: next(states),
    )

    def queue_item_already_moved(*_args, **_kwargs):
        queue_cancel_calls.append(True)
        raise KeyError("queue item not found")

    monkeypatch.setattr(
        resident_protocol,
        "cancel_resident_queue_item",
        queue_item_already_moved,
    )
    monkeypatch.setattr(
        resident_protocol,
        "get_resident_state",
        lambda *_args, **_kwargs: {
            "state": {"work_state": "idle"},
            "cursor": 9,
        },
    )
    result = resident_protocol.cancel_resident_input(
        str(resident_record["resident_id"]),
        "CASE-A",
        "message-1",
    )

    assert queue_cancel_calls == [True]
    assert result["already_terminal"] is True
    assert result["input_state"]["state"] == "completed"


def test_cancel_resident_turn_rejects_queue_items_without_command_rerouting(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取消当前轮拒绝排队输入且不转发队列取消命令"""
    resident_id = str(resident_record["resident_id"])
    message_id = "msg_" + "7" * 32
    monkeypatch.setattr(
        resident_protocol,
        "_stored_resident_input_state",
        lambda *_args, **_kwargs: {"message_id": message_id, "state": "queued"},
    )
    monkeypatch.setattr(
        resident_protocol,
        "get_resident_state",
        lambda *_args, **_kwargs: {
            "state": {"work_state": "running", "active_message_id": "msg_" + "8" * 32},
        },
    )
    monkeypatch.setattr(
        resident_protocol,
        "cancel_resident_queue_item",
        lambda *_args, **_kwargs: pytest.fail("CancelResidentTurn rerouted to a queue command"),
    )

    with pytest.raises(ValueError, match="message is not the active Resident input"):
        resident_protocol.cancel_resident_execution(resident_id, "CASE-A", message_id)


def test_inject_queue_message_waits_durably_for_execution_binding(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """队列注入持久等待执行绑定后再追加消息"""
    resident_id = str(resident_record["resident_id"])
    queued_message_id = "msg_cccccccccccccccccccccccccccccccc"
    queued_input = {
        "message_id": queued_message_id,
        "role": "user",
        "content_parts": [
            {"type": "text", "text": "追加到尚未绑定的 turn"}
        ],
        "created_at": "2026-08-23T00:00:00Z",
        "author_id": "CASE-A",
    }
    resident_execution_work.write(
        resident_record,
        {
            'execution_id': "execution-333333333333333333333333",
            "user_message_id": "message-active-binding",
            "actor": "CASE-A",
            "status": "running",
            "queue": [{
                'execution_id': "job_queued_before_binding",
                "user_message_id": queued_message_id,
                "input_message": queued_input,
                "actor": "CASE-A",
                "raw_message": "追加到尚未绑定的 turn",
                "effective_message": "追加到尚未绑定的 turn",
                "actual_sent_prompt": "追加到尚未绑定的 turn",
            }],
        },
    )
    calls: list[tuple[str, str, list[dict[str, object]]]] = []

    class Service:
        def append_input(self, execution_id, *, actor, input, **_kwargs):
            calls.append((execution_id, actor, input))
            return {"control_id": "control-pending", "status": "pending"}

        def input_control(self, *_args, **_kwargs):
            return {"status": "delivered"} if calls else None

    service = Service()
    monkeypatch.setattr(execution_port, "service", lambda: service)
    repository = resident_conversation_stream.get_repository()
    assert repository.read_messages(resident_id) == []

    result = _published_delivery(resident_protocol.inject_queued_input_into_active_turn(
        resident_id,
        "CASE-A",
        queued_message_id,
    ))

    assert result["accepted"] is True
    assert result["queue"]["items"] == []
    assert calls == []
    pending_job = resident_execution_work.read(resident_record)
    assert pending_job["queue"] == []
    assert resident_execution_work.send_now_inputs(pending_job)[0]["user_message_id"] == queued_message_id
    assert resident_execution_work.send_now_inputs(pending_job)[0]["active_turn_input"]["control_id"]
    assert repository.read_messages(resident_id) == []

    bind_execution(
        pending_job,
        execution_id="execution-333333333333333333333333",


        now="2026-08-28T00:00:00Z",
    )
    resident_execution_work.write(resident_record, pending_job)
    resident_intake_domain.reconcile_queued_turn_inputs(
        resident_id,
        "execution-333333333333333333333333",
    )

    assert calls == [(
        "execution-333333333333333333333333",
        "CASE-A",
        [{"type": "text", "text": "<user-message>\n追加到尚未绑定的 turn\n</user-message>"}],
    )]
    assert "pending_active_turn_inputs" not in resident_execution_work.read(resident_record)
    assert resident_intake_domain.reconcile_queued_turn_inputs(resident_id, "execution-333333333333333333333333")
    assert resident_execution_work.read(resident_record)["queue"] == []
    assert [item["message_id"] for item in repository.read_messages(resident_id)] == [queued_message_id]



def test_pending_active_turn_flush_retries_one_idempotent_append_after_commit_failure(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """待注入控制提交失败后使用稳定控制标识重试"""
    resident_id = str(resident_record["resident_id"])
    execution_id = "execution-555555555555555555555555"
    message_id = "msg_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    resident_execution_work.write(
        resident_record,
        {

            "execution_id": execution_id,
            "actor": "CASE-A",
            "pending_active_turn_inputs": [{
                "message_id": message_id,
                "actor": "CASE-A",
                "input": [{"type": "text", "text": "只注入一次"}],
                "control_id": "resident-input:stable-control",
            }],
         "submitted_at": "2026-10-14T00:00:00Z"},
    )
    append_attempts: list[str] = []
    delivered_controls: set[str] = set()

    class Service:
        @staticmethod
        def append_input(_execution_id, *, control_id, **_kwargs):
            from management_portal.society_service.invariant_locks import lock_held
            assert not lock_held(), "Execution RPC under Resident state lock"
            append_attempts.append(control_id)
            delivered_controls.add(control_id)
            return {"control_id": control_id, "status": "delivered"}

    original_write = resident_execution_work.write
    interrupted = True

    def interrupt_first_commit(*args, **kwargs):
        nonlocal interrupted
        if interrupted:
            interrupted = False
            raise OSError("simulated pending-input commit interruption")
        return original_write(*args, **kwargs)

    monkeypatch.setattr(resident_execution_work, "write", interrupt_first_commit)

    with pytest.raises(OSError, match="commit interruption"):
        resident_execution_domain._flush_pending_active_turn_controls(
            Service(),
            resident_id,
            execution_id,
        )

    assert resident_execution_work.read(resident_record)["pending_active_turn_inputs"]

    resident_execution_domain._flush_pending_active_turn_controls(
        Service(),
        resident_id,
        execution_id,
    )

    assert append_attempts == [
        "resident-input:stable-control",
        "resident-input:stable-control",
    ]
    assert delivered_controls == {"resident-input:stable-control"}
    assert "pending_active_turn_inputs" not in resident_execution_work.read(resident_record)


def test_pending_active_turn_flush_does_not_append_after_stop_requested(
    resident_record: dict[str, object],
) -> None:
    """停止请求发出后不再追加待注入输入"""
    resident_id = str(resident_record["resident_id"])
    execution_id = "execution-666666666666666666666666"
    resident_execution_work.write(
        resident_record,
        {

            "execution_id": execution_id,
            "actor": "CASE-A",
            "stop_requested_at": "2026-08-31T08:00:00Z",
            "pending_active_turn_inputs": [{
                "message_id": "msg_ffffffffffffffffffffffffffffffff",
                "actor": "CASE-A",
                "input": [{"type": "text", "text": "不要注入旧执行"}],
            }],
         "submitted_at": "2026-10-14T00:00:00Z"},
    )
    service = mock.Mock()

    resident_execution_domain._flush_pending_active_turn_controls(
        service,
        resident_id,
        execution_id,
    )

    service.append_input.assert_not_called()
    assert resident_execution_work.read(resident_record)["pending_active_turn_inputs"]


@pytest.mark.parametrize(
    "scheduler_state",
    ["held", "submitting", "ready", "queued", "claimed", "dispatching", "running"],
)
def test_api_session_state_never_exposes_scheduler_state(
    resident_record: dict[str, object],
    scheduler_state: str,
) -> None:
    """会话状态接口将调度器阶段归一为活跃状态"""
    resident_record["session_state"] = scheduler_state

    assert resident_record_domain.record_backend_session_state(resident_record) == "active"


@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled"])
def test_terminal_job_projects_resident_idle(
    resident_record: dict[str, object],
    outcome: str,
) -> None:
    """执行任务终结后将 Resident 投影为空闲"""
    resident_execution_work.publish_conversation_state(
        resident_record,
        {

            "execution_id": "execution-" + "d" * 24,
            "outcome": outcome,
            "finished_at": "2026-08-27T00:00:00Z",
         "submitted_at": "2026-10-14T00:00:00Z"},
    )
    state = resident_conversation_stream.get_repository().current_projection(
        str(resident_record["resident_id"])
    )["state"]
    assert state == {
        "work_state": "idle",
        "active_message_id": "",
        "queued_count": 0,
        "intake_state": "open",
    }


def test_terminal_job_clears_resident_activity(
    resident_record: dict[str, object],
) -> None:
    """执行任务终结后清空 Resident 活动"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    repository.set_activities(resident_id, [_activity(resident_id)])

    resident_execution_work.publish_conversation_state(
        resident_record,
        {

            "execution_id": "execution-" + "c" * 24,
            "outcome": "cancelled",
            "finished_at": "2026-08-27T00:00:00Z",
         "submitted_at": "2026-10-14T00:00:00Z"},
    )

    assert repository.current_projection(resident_id)["activities"] == []


def test_execution_cancellation_request_projects_resident_stopping(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """执行取消请求将 Resident 投影为停止中"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    monkeypatch.setattr(
        repository,
        "current_projection",
        lambda *_args, **_kwargs: pytest.fail(
            "runtime state publication loaded the complete Conversation projection"
        ),
    )

    resident_execution_work.publish_conversation_state(
        resident_record,
        {

            "execution_id": "execution-" + "a" * 24,
            "user_message_id": "message_stopping",
         "submitted_at": "2026-10-14T00:00:00Z"},
        snapshot={
            "status": "running",
            "scheduler": {"cancel_requested": True},
        },
    )

    assert repository.published_state(resident_id) == {
        "work_state": "stopping",
        "active_message_id": "message_stopping",
        "queued_count": 0,
        "intake_state": "open",
    }


def test_next_queued_message_does_not_inherit_previous_turn_activity(
    resident_record: dict[str, object],
) -> None:
    """下一排队消息激活时清空上一轮活动"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    repository.set_state(resident_id, {
        "work_state": "running",
        "active_message_id": "message_previous",
        "queued_count": 1,
        "intake_state": "open",
    })
    repository.set_activities(resident_id, [_activity(resident_id)])

    resident_execution_work.publish_conversation_state(
        resident_record,
        {
            'execution_id': "job_next",
            "user_message_id": "message_next",
            "started_at": "2026-08-27T00:00:01Z",
        },
    )

    projection = repository.current_projection(resident_id)
    assert projection["state"]["work_state"] == "queued"
    assert projection["state"]["active_message_id"] == "message_next"
    assert projection["activities"] == []


@pytest.mark.parametrize(
    ("snapshot", "expected_state", "expected_phase"),
    [
        ({}, "queued", "starting_session"),
        ({"status": "claimed"}, "running", "starting_session"),
        (
            {"status": "queued", "scheduler": {"queue_reason": "capacity"}},
            "queued",
            "waiting_capacity",
        ),
        (
            {
                "status": "queued",
                "scheduler": {"queue_reason": "dispatch_interval"},
            },
            "queued",
            "waiting_dispatch",
        ),
        (
            {"status": "queued", "scheduler": {"queue_reason": "ready"}},
            "queued",
            "starting_session",
        ),
        (
            {"status": "queued", "scheduler": {"queue_reason": "session_binding"}},
            "queued",
            "",
        ),
    ],
)
def test_execution_lifecycle_projects_transient_chat_phase(
    resident_record: dict[str, object],
    snapshot: dict[str, object],
    expected_state: str,
    expected_phase: str,
) -> None:
    """执行生命周期将调度原因映射为聊天等待阶段"""
    job = {
        'execution_id': "job_phase",
        "user_message_id": "message_phase",
    }
    if snapshot:
        job["execution_id"] = "execution-" + "b" * 24
        job["submitted_at"] = "2026-10-14T00:00:00Z"
    state = resident_execution_work.conversation_state(
        job,
        intake_state="open",
        snapshot=snapshot,
    )

    assert state["work_state"] == expected_state
    assert state.get("work_phase", "") == expected_phase


def test_native_fork_keeps_session_phase_without_creating_an_input(resident_record):
    """原生分叉保留会话启动阶段且不创建输入消息"""
    job = { 'operation': "context_fork_create",
           "execution_id": "execution-" + "b" * 24, "submitted_at": "2026-10-14T00:00:00Z"}
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    resident_execution_work.publish_conversation_state(resident_record, job, snapshot={"status": "running"})
    state = repository.published_state(resident_id)
    assert state["work_state"] == "running"
    assert state["work_phase"] == "starting_session"
    assert state["active_message_id"] == ""
    assert state["queued_count"] == 0
    assert resident_execution_work.conversation_state(
        {**job, "user_message_id": "real-input"}, intake_state="open", snapshot={"status": "running"}
    ).get("work_phase") is None
    resident_execution_work.publish_conversation_state(
        resident_record, {**job, "outcome": "completed", "finished_at": "2026-10-13T10:00:00Z"},
        snapshot={"status": "completed"},
    )
    assert repository.published_state(resident_id) == {
        "work_state": "idle", "active_message_id": "", "queued_count": 0, "intake_state": "open",
    }


def test_applied_completion_projects_completed_input_and_idle_resident(
    resident_record: dict[str, object],
) -> None:
    """完成结果应用后将输入置为完成并将 Resident 置为空闲"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    message_id = "msg_11111111111111111111111111111111"
    repository.append_message(
        resident_id,
        {
            "message_id": message_id,
            "role": "user",
            "content_parts": [{"type": "text", "text": "question"}],
            "created_at": "2026-08-27T00:00:00Z",
        },
    )
    execution_id = "execution-" + "e" * 24
    job = {

        "execution_id": execution_id,
        "user_message_id": message_id,
        "completion_applied_at": "2026-08-27T00:00:01Z",
        "completion_applied_execution_id": execution_id,
     "submitted_at": "2026-10-14T00:00:00Z"}

    resident_execution_work.publish_conversation_state(resident_record, job)

    projection = repository.current_projection(resident_id)
    assert projection["state"]["work_state"] == "idle"
    assert repository.input_state(resident_id, message_id)["state"] == "completed"


@pytest.mark.parametrize("terminal_status", ["completed", "failed", "cancelled"])
def test_terminal_execution_snapshot_waits_for_resident_delivery(
    resident_record: dict[str, object],
    terminal_status: str,
) -> None:
    """执行快照已终结时仍等待 Resident 完成结果投递"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    message_id = "msg_22222222222222222222222222222222"
    repository.append_message(
        resident_id,
        {
            "message_id": message_id,
            "role": "user",
            "content_parts": [{"type": "text", "text": "question"}],
            "created_at": "2026-08-27T00:00:00Z",
        },
    )
    job = {

        "execution_id": "execution-" + "f" * 24,
        "user_message_id": message_id,
     "submitted_at": "2026-10-14T00:00:00Z"}

    resident_execution_work.publish_conversation_state(
        resident_record,
        job,
        snapshot={"status": terminal_status},
    )
    cursor = repository.snapshot(resident_id)["cursor"]
    resident_execution_work.publish_conversation_state(
        resident_record,
        job,
        snapshot={"status": terminal_status},
    )

    projection = repository.current_projection(resident_id)
    assert projection["state"]["work_state"] == "running"
    assert repository.input_state(
        resident_id,
        message_id,
    )["state"] == "running"
    assert repository.snapshot(resident_id)["cursor"] == cursor


def test_conversation_replay_does_not_acquire_writer_locks(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已提交对话消息重放无需获取写锁"""
    repository = resident_conversation_stream.get_repository()
    resident_id = str(resident_record["resident_id"])
    message = resident_intake_domain.build_message("agent", "durable update")
    persisted = repository.append_message(resident_id, message)

    def forbidden_write(*args, **kwargs):
        raise AssertionError("Committed replay must not acquire a writer lock")

    monkeypatch.setattr(repository, "_protocol_scope", forbidden_write)
    monkeypatch.setattr(repository.state_store.database, "write", forbidden_write)
    monkeypatch.setattr(repository.agent_data.database, "write", forbidden_write)
    assert repository.append_message(resident_id, message) == persisted
    with pytest.raises(ValueError, match="immutable"):
        repository.append_message(resident_id, {**message, "content_parts": [
            {"type": "text", "text": "different content"},
        ]})


@pytest.mark.parametrize("gate", [
    {"state": "open", "reason": ""},
    {"state": "closed", "reason": "quota_used_up"},
    {"state": "unknown", "reason": "execution_unavailable"},
])
def test_conversation_native_controls_follow_snapshot_and_events(resident_record, monkeypatch, gate):
    """对话快照和事件中的原生控制状态保持一致"""
    from copy import deepcopy

    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    quota_calls = []

    def quota_states(_self, bindings):
        quota_calls.append(dict(bindings))
        return {key: dict(gate) for key in bindings}

    monkeypatch.setattr(ExecutionService, "binding_quota_states", quota_states)
    for intake, active_message_id in [("closed", ""), ("open", "active-input"), ("open", "")]:
        state = {
            "intake_state": intake,
            "work_state": "running" if active_message_id else "idle",
            "active_message_id": active_message_id,
            "queued_count": 0,
        }
        repository.state_store.database.write(lambda c: c.execute('UPDATE residents SET intake_open=%s WHERE resident_id=%s',(intake == "open",resident_id)))
        if active_message_id:
            resident_execution_work.write(resident_record, { 'actor':'CASE-A', 'execution_id':"controls", 'user_message_id':active_message_id, "submitted_at": "2026-10-14T00:00:00Z"})
        else:
            resident_execution_work.clear(resident_record)
        event = repository.set_state(resident_id, state)
        if event is None:
            event = repository.events_after(resident_id,0)['events'][-1]
        original_projection = deepcopy(repository.current_projection(resident_id))
        original_events = deepcopy(repository.events_after(resident_id, event["cursor"] - 1))
        snapshot = resident_protocol.get_resident_conversation_snapshot(resident_id, "CASE-A")
        events = resident_protocol.get_resident_events(resident_id, "CASE-A", event["cursor"] - 1)["events"]
        expected_gate = {"state": "closed", "reason": "manual"} if intake == "closed" else gate
        assert snapshot["state"]["execution_gate"] == expected_gate
        assert events[0]["payload"]["state"]["execution_gate"] == expected_gate
        assert bool(snapshot["controls"]["send_hint"]) is bool(expected_gate["reason"])
        assert snapshot["controls"] == events[0]["payload"]["controls"]
        assert snapshot["controls"]["can_send"] is True
        assert snapshot["controls"]["can_stop"] is bool(active_message_id)
        assert resident_protocol.get_resident_state(resident_id, "CASE-A")["controls"] == snapshot["controls"]
        if active_message_id:
            with monkeypatch.context() as cancellation:
                cancellation.setattr(resident_protocol, "_stored_resident_input_state", lambda *_: {"state": "running"})
                for with_state in (False, True):
                    raw_receipt = {"resident_id": resident_id, "cancellation_requested": True, "backend_cancelled": False}
                    if with_state:
                        raw_receipt["state"] = deepcopy(state)
                    before_receipt = deepcopy(raw_receipt)
                    cancellation.setattr(resident_intake_domain, "request_resident_execution_cancellation",
                                         lambda *_args, **_kwargs: raw_receipt)
                    before_calls = len(quota_calls)
                    receipt = resident_protocol.cancel_resident_execution(
                        resident_id, "CASE-A", active_message_id, continue_queue=False)
                    assert receipt["message_id"] == active_message_id
                    assert receipt["cancellation_requested"] is True
                    assert receipt["backend_cancelled"] is False
                    if with_state:
                        assert receipt["state"]["execution_gate"] == expected_gate
                        assert receipt["controls"] == snapshot["controls"]
                    else:
                        assert receipt == {**raw_receipt, "message_id": active_message_id}
                        assert len(quota_calls) == before_calls
                    assert raw_receipt == before_receipt
        assert repository.current_projection(resident_id) == original_projection
        assert repository.events_after(resident_id, event["cursor"] - 1) == original_events
        notification = resident_conversation_stream.conversation_event_notification(event)
        assert notification["payload_omitted"] is True
        assert "payload" not in notification
        assert "controls" not in repository.events_after(resident_id, event["cursor"] - 1)["events"][0]["payload"]
    with pytest.raises(PermissionError):
        resident_protocol.get_resident_conversation_snapshot(resident_id, "")


def test_conversation_replay_repairs_missing_event(
    resident_record: dict[str, object],
) -> None:
    """对话消息重放补齐缺失事件且仅发布一次"""
    repository = resident_conversation_stream.get_repository()
    resident_id = str(resident_record["resident_id"])
    message = resident_intake_domain.build_message("agent", "committed before event failure")
    repository.agent_data.project_chat_message(resident_id, message)
    before = repository.current_projection(resident_id)["cursor"]
    repository.append_message(resident_id, message)
    repository.append_message(resident_id, message)
    events = repository.events_after(resident_id, before)["events"]
    assert len(events) == 1
    assert events[0]["event_type"] == "conversation.message.appended"
    assert events[0]["payload"]["message"]["message_id"] == message["message_id"]


def test_concurrent_conversation_replays_commit_one_message_and_event(
    resident_record: dict[str, object],
) -> None:
    """并发对话重放仅提交一条消息和一个事件"""
    repository = resident_conversation_stream.get_repository()
    resident_id = str(resident_record["resident_id"])
    message = resident_intake_domain.build_message("agent", "concurrent durable update")
    before = repository.current_projection(resident_id)["cursor"]
    ready = threading.Barrier(8)

    def replay(_index):
        ready.wait(timeout=5)
        return repository.append_message(resident_id, message)

    with ThreadPoolExecutor(max_workers=8) as pool:
        messages = list(pool.map(replay, range(8)))
    assert all(item == messages[0] for item in messages)
    assert len(repository.events_after(resident_id, before)["events"]) == 1
    assert len(repository.read_messages(resident_id)) == 1


def test_recovery_attaches_only_the_completion_waiter(
    resident_record: dict[str, object], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """恢复仅挂接完成等待者且不查询或投影远端执行"""
    resident_id = str(resident_record["resident_id"])
    attached = []
    def forbidden(*args, **kwargs):
        raise AssertionError("Recovery must not query or project remote execution")
    monkeypatch.setattr(resident_execution_work, "execution_snapshot", forbidden)
    monkeypatch.setattr(resident_execution_domain, "_project_resident_execution_snapshot", forbidden)
    # An active Job with no attached waiter is what recovery attaches one to.
    resident_execution_work.write(resident_record, {"execution_id": "execution-" + "a" * 24, "actor": "CASE-A", "queue": []})
    monkeypatch.setattr(resident_execution_domain, "_prepare_resident_continuation", lambda _record, **_kwargs: (resident_id, "waiter"))
    monkeypatch.setattr(resident_intake_domain, "start_resident_continuation", lambda record, key: attached.append((record["resident_id"], key)))
    assert backend.resume_resident_execution(resident_id)["residents"] == 1
    assert attached == [(resident_id, "waiter")]


def _record_harness_execution(
    resident_id: str,
    execution_id: str,
    *,
    accepted: bool = True,
) -> None:
    store = get_agent_data_store()
    store.begin_execution(
        execution_id=execution_id,
        agent_id=resident_id,
        actor_ref="CASE-A",
        provider="codex",
        started_at="2026-09-07T07:00:00Z",
    )
    store.finish_execution(
        execution_id,
        status="failed",
        provider_turn_id=f"turn-{execution_id}" if accepted else "",
    )


@pytest.mark.parametrize('mode', ['auto', 'manual'])
@pytest.mark.parametrize('enabled,first_status,error,input_accepted', [pytest.param(True, 'failed', 'Selected model is at capacity', True, id='capacity'), pytest.param(True, 'failed', 'Codex context compaction did not finish within 300 seconds', True, id='compact-timeout'), pytest.param(True, 'failed', "name 'unknown_symbol' is not defined", True, id='unexpected-error'), pytest.param(True, 'failed', 'provider unavailable', True, id='retry-also-fails'), pytest.param(False, 'failed', 'provider unavailable', True, id='disabled'), pytest.param(True, 'cancelled', 'cancelled by owner', True, id='cancelled'), pytest.param(True, 'completed', '', True, id='success'), pytest.param(True, 'failed', 'Codex app-server turn/start timed out', False, id='start-not-accepted'), pytest.param(True, 'failed', 'ExecutionService unavailable: timed out', False, id='dispatch-not-accepted'), pytest.param(True, 'failed', 'invalid model configuration', False, id='configuration-not-accepted')])
def test_lineage_projects_terminal_execution_without_harness_recovery(resident_record: dict[str, object], monkeypatch: pytest.MonkeyPatch, mode: str, enabled: bool, first_status: str, error: str, input_accepted: bool) -> None:
    """Lineage 投影执行终态且不合成技术重试输入

    Lineage projects final Execution results without synthesizing technical retries."""
    resident_id = str(resident_record['resident_id'])
    repository = resident_conversation_stream.get_repository()
    requests = []
    snapshots = {}
    acknowledgements = []
    wakeups = []
    exit_wakeups = []
    outcomes = iter([first_status])

    class ExecutionBoundary:

        def submit(self, request):
            assert request.execution_id not in snapshots
            requests.append(request)
            snapshots[request.execution_id] = {'execution_id': request.execution_id, 'status': 'queued', 'events': [], 'result': {}}
            return snapshots[request.execution_id]

        def status(self, execution_id, **_kwargs):
            return snapshots[execution_id]

        def wait(self, execution_id, **kwargs):
            assert kwargs['cancellation_requested']() is False
            status = next(outcomes)
            _record_harness_execution(resident_id, execution_id, accepted=input_accepted)
            result = {'status': status, 'error': error if status != 'completed' else '', 'output': {'message': 'isolated work completed'} if status == 'completed' else {}}
            snapshot = {**snapshots[execution_id], 'status': status, 'result': result, 'scheduler': {'status': status, 'updated_at': resident_domain_common.now_iso()}}
            snapshots[execution_id] = snapshot
            kwargs['on_snapshot'](snapshot)
            kwargs['on_snapshot'](snapshot)
            return snapshot

        def acknowledge(self, execution_id, **_kwargs):
            acknowledgements.append(execution_id)
    service = ExecutionBoundary()
    monkeypatch.setattr(execution_port, 'service', lambda: service)
    monkeypatch.setattr(resident_hatg_credentials, 'ensure_resident_credential', mock.Mock())
    monkeypatch.setattr(resident_execution_domain, '_notify_lineage_message_terminal', mock.Mock())
    monkeypatch.setattr(resident_execution_domain, '_notify_lineage_message_completed', mock.Mock())
    monkeypatch.setattr(lifecycle, 'notify_execution_binding', wakeups.append)
    finish_continuation = resident_intake_domain.finish_resident_continuation

    def observe_finish(target_id):
        before = len(wakeups)
        finish_continuation(target_id)
        exit_wakeups.extend(wakeups[before:])
    monkeypatch.setattr(resident_intake_domain, 'finish_resident_continuation', observe_finish)
    backend.change_resident_runtime_settings(resident_id, 'CASE-A', {'agent_harness': {'mode': mode, 'features': {'provider_error_continue': {'enabled': enabled}}}})
    saved = resident_runtime_domain.read_resident_runtime_settings(resident_record)['agent_harness']
    assert saved['mode'] == mode
    assert saved['features']['provider_error_continue']['enabled'] is enabled
    message_id = 'msg_123456789abcdef0123456789abcdef0'
    user_item = resident_intake_domain._resident_user_message(resident_id, 'isolated original task', 'CASE-A', message_id=message_id)
    repository.commit_user_message(resident_id, user_item)
    job = resident_execution_work.new_item(user_item, 'isolated original task', 'isolated original task', resident_domain_common.now_iso(), prompt_cwd=str(resident_record['default_cwd']))
    job.update({'actor': 'CASE-A', 'queue': []})
    original_job_id = job['execution_id']
    resident_execution_work.write(resident_record, job)
    prepared = resident_execution_domain._prepare_resident_continuation(resident_record)
    assert prepared is not None
    current_harness = resident_runtime_domain.read_resident_runtime_settings(resident_record)['agent_harness']
    assert current_harness['mode'] == mode
    assert current_harness['features']['provider_error_continue']['enabled'] is enabled
    resident_execution_work.track_current_thread(resident_record)
    resident_execution_domain.run_resident_execution(*prepared)
    assert len(requests) == 1
    assert repository.input_state(resident_id, message_id)['state'] == first_status
    assert exit_wakeups == []
    messages = repository.read_messages(resident_id)
    assert not any((_message_text(m) == 'continue' for m in messages)), [(m['role'], _message_text(m)) for m in messages]
    record = resident_record_domain.find_resident(resident_id)
    assert record is not None
    final_job = resident_execution_work.read(record)
    assert not resident_execution_work.is_active(final_job)
    assert not resident_execution_work.normalized_queue(final_job)
    assert resident_execution_domain._prepare_resident_continuation(record) is None
    assert len(exit_wakeups) == 0
    assert set(acknowledgements) == set(snapshots), {'error': record.get('last_error'), 'job_outcome': final_job.get('outcome'), 'events': [(m['role'], _message_text(m)) for m in repository.read_messages(resident_id)]}
    assert all((not s.get('projection_errors') for s in snapshots.values()))
    assert repository.current_projection(resident_id)['state']['work_state'] == 'idle'
    replies = [m for m in repository.read_messages(resident_id) if m['role'] == 'agent']
    if first_status == 'completed':
        assert len(replies) == 1
        assert _message_text(replies[0]) == 'isolated work completed'
        assert not record.get('last_error')
        assert final_job == {}
    else:
        assert replies == []
    for request in requests:
        harness = request.runtime_options['agent_harness']
        assert harness['mode'] == mode
        assert harness['features']['provider_error_continue']['enabled'] is enabled


def test_restart_recovery_isolates_one_broken_resident(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重启恢复隔离故障 Resident 并继续恢复健康任务"""
    monkeypatch.setattr(execution_client, "health", lambda: {"scheduler": {"capacity_partitions": {}}})
    broken_id = "resident_000000000091"
    healthy_id = "resident_000000000092"
    records = {
        broken_id: {
            "resident_id": broken_id,
            "registration_state": "ready",
        },
        healthy_id: {
            "resident_id": healthy_id,
            "registration_state": "ready",
        },
    }
    started: list[tuple[str, str]] = []
    monkeypatch.setattr(resident_execution_work, "pending_records", lambda: list(records.values()))
    monkeypatch.setattr(
        resident_lifecycle_domain,
        "resident_aggregate_lineage_id",
        lambda _resident_id: "lineage_000000000090",
    )

    monkeypatch.setattr(
        resident_lineage,
        "all",
        lambda: pytest.fail("Recovery must not enumerate idle Residents"),
    )
    monkeypatch.setattr(
        resident_lineage,
        "snapshot",
        lambda _record: {"residents": []},
    )
    monkeypatch.setattr(
        resident_execution_domain,
        "_active_resident_continuations",
        lambda: 0,
    )

    read_many_calls: list[list[str]] = []

    def read_many(items):
        resident_ids = [str(item.get("resident_id") or "") for item in items]
        read_many_calls.append(resident_ids)
        return {
            resident_id: {
                'execution_id': f"job_{resident_id}",
                "status": "running",
                "started_at": "2026-08-16T00:00:00Z",
            }
            for resident_id in resident_ids
        }

    monkeypatch.setattr(resident_execution_work, "read_many", read_many)
    monkeypatch.setattr(
        resident_execution_work,
        "read",
        lambda *_args, **_kwargs: pytest.fail(
            "global recovery must not read Resident jobs one at a time"
        ),
    )

    def continuation_needed(record, job):
        if str(record.get("resident_id") or "") == broken_id:
            raise KeyError("execution-expired")
        return bool(job)

    monkeypatch.setattr(
        resident_execution_work,
        "continuation_needed",
        continuation_needed,
    )
    monkeypatch.setattr(
        resident_record_domain,
        "find_resident",
        lambda resident_id: records.get(resident_id),
    )
    monkeypatch.setattr(
        resident_execution_domain,
        "_prepare_resident_continuation",
        lambda record, **_kwargs: (
            str(record.get("resident_id") or ""),
            "resident-continuation:healthy",
        ),
    )
    monkeypatch.setattr(
        resident_intake_domain,
        "start_resident_continuation",
        lambda record, continuation_id: started.append((
            str(record.get("resident_id") or ""),
            continuation_id,
        )),
    )

    result = backend.resume_pending_resident_executions()

    assert result == {
        "residents": 1,
        "active": 0,
        "errors": 1,
        "released_executions": 0,
        "steward_reserve_waits": 0,
        "failed_work_recoveries": 0,
    }
    assert read_many_calls == [[broken_id, healthy_id]]
    assert started == [(healthy_id, "resident-continuation:healthy")]


def test_compact_persists_context_usage_from_app_server_contract_contracts(subtests, isolated_portal_case):
    """压缩使用规范上下文用量并保留额度与中断状态"""
    with subtests.test(contract='test_compact_persists_context_usage_from_app_server_contract'), isolated_portal_case() as case_path:
        updated = resident_execution_domain._with_compacted_context_usage({}, {'last': {'totalTokens': 120}, 'total': {'totalTokens': 900}, 'context': {'inputTokens': 80, 'outputTokens': 20, 'totalTokens': 100}, 'modelContextWindow': 258400})
        assert updated['last_token_usage'] == {'input_tokens': 80, 'output_tokens': 20, 'total_tokens': 100}
        assert updated['last_model_context_window'] == 258400
    with subtests.test(contract='test_noncanonical_cumulative_usage_cannot_exceed_context_window'), isolated_portal_case() as case_path:
        pressure = resident_directory_domain._resident_scope_context_pressure({'last_token_usage': {'last': {'inputTokens': 12296210, 'cachedInputTokens': 12058368, 'outputTokens': 21299, 'reasoningOutputTokens': 9713, 'totalTokens': 12317509}, 'total': {'totalTokens': 61272965}, 'modelContextWindow': 258400}, 'last_model_context_window': ''})
        assert pressure['context_tokens'] == 0
        assert pressure['used_percent'] is None
        assert pressure['source'] == 'invalid_context_usage'
    with subtests.test(contract='test_resident_session_prompt_controls_honor_both_customer_switches'), isolated_portal_case() as case_path:
        settings = {'prompt_injection': {'preset': 'full'}, 'allow_subagents': True}
        assert resident_directory_domain.resident_session_prompt_controls(settings) == {'allow_subagents': True}
    with subtests.test(contract='test_quota_wait_is_projected_without_closing_resident'), isolated_portal_case() as case_path:
        state = resident_execution_work.conversation_state({ 'user_message_id': 'message_quota', 'execution_id': 'execution-' + '2' * 24, "submitted_at": "2026-10-14T00:00:00Z"}, snapshot={'status': 'queued', 'scheduler': {'queue_reason': 'quota_reserve'}}, intake_state='open')
        assert state['work_state'] == 'queued'
        assert state['work_phase'] == 'waiting_quota'
        assert state['intake_state'] == 'open'
    with subtests.test(contract='test_execution_failure_preserves_structured_provider_interruption'), isolated_portal_case() as case_path:
        error = backend.ResidentExecutionFailed({'status': 'failed', 'result': {'error': 'Codex turn interrupted without a provider reason', 'error_code': 'provider_turn_interrupted', 'error_details': {'provider_status': 'interrupted', 'thread_id': 'thread-1'}}}, 'failed')
        runtime_error = resident_record_domain.runtime_error_payload(str(error), code=error.error_code, details=error.error_details)
        assert runtime_error['code'] == 'provider_turn_interrupted'
        assert runtime_error['details']['thread_id'] == 'thread-1'
        assert resident_record_domain.runtime_failure_summary(str(error), runtime_error) == '模型后端中断了本轮执行'
    with subtests.test(contract='test_unknown_provider_code_is_normalized_from_usage_limit_message'), isolated_portal_case() as case_path:
        runtime_error = resident_record_domain.runtime_error_payload(
            "Codex turn failed: You’ve hit your usage limit. Purchase more credits.",
            code="unknown",
        )
        assert runtime_error['code'] == 'provider_usage_limit'
        assert resident_record_domain.runtime_failure_summary(runtime_error['message'], runtime_error) == '当前模型账号额度不足'


def test_resident_timeline_keeps_canonical_messages_without_attached_session(
    resident_record: dict[str, object],
) -> None:
    """Resident 未绑定会话时仍保留规范对话时间线"""
    resident_backend.delete_session_identity(resident_record)
    resident_lifecycle_domain.append_conversation_message(str(resident_record["resident_id"]), {
        "message_id": "msg_33333333333333333333333333333333",
        "role": "user",
        "content_parts": [{"type": "text", "text": "start"}],
    })
    resident_lifecycle_domain.append_conversation_message(str(resident_record["resident_id"]), {
        "message_id": "msg_44444444444444444444444444444444",
        "role": "agent",
        "content_parts": [
            {"type": "text", "text": "reply survived restart"}
        ],
    })

    messages = resident_directory_domain.resident_conversation_messages(
        resident_record,
    )

    assert [resident_domain_common.protocol_message_text(item) for item in messages] == [
        "start",
        "reply survived restart",
    ]


def test_session_account_uses_the_active_execution_route(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """会话账号以当前执行路由为准"""
    monkeypatch.setattr(
        resident_execution_work,
        "execution_snapshot",
        lambda _job: {
            "request": {
                "session": {
                    "backend_route_id": "zero",
                },
            },
        },
    )

    assert resident_runtime_domain.resident_session_account_id(
        resident_record,
        job={"execution_id": "execution_1234567890abcdef"},
    ) == "zero"


@pytest.mark.parametrize("project_id", ["", "RoboLab", "robotworker", "HardwareExplorer"])
def test_new_resident_session_account_is_independent_of_project(
    resident_record: dict[str, object],
    project_id: str,
) -> None:
    """新 Resident 会话账号不随所属项目变化"""
    lineage_id = str(resident_record["lineage_id"])
    store = resident_catalog.get_catalog().state_store
    definition = store.lineage_definition(lineage_id)
    store.put_lineage_definition(lineage_id, {**definition, "project_id": project_id}, {})
    record = resident_record_domain.find_resident(str(resident_record["resident_id"]))
    assert record["project_id"] == project_id
    resident_backend.delete_session_identity(record)
    resident_record_domain.save_resident_record(record)

    request = resident_execution_domain._build_resident_execution_request(
        record,
        {
            "actor": "CASE-A",
            'execution_id': f"job_{project_id or 'none'}",
            "user_message_id": "message_new_session",
            "message": "开始工作",
        },
    )

    assert request.session.backend_route_id == "one"
    assert request.runtime_options["allow_backend_route_fallback"] is False
    assert request.runtime_options["allow_subagents"] is False
    assert "agent_kind" not in request.metadata
    assert request.metadata["project_id"] == project_id


def test_execution_reconciles_a_stale_provider_tuple_before_submission(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """执行提交前按当前账号修正过期模型设置"""
    stale = resident_runtime_settings.merge_settings(
        resident_runtime_domain.read_resident_runtime_settings(resident_record),
        {
            "model": "retired-model",
            "reasoning_effort": "xhigh",
            "service_tier": "priority",
        },
    )
    resident_runtime_domain.write_resident_runtime_settings(resident_record, stale)
    resident_record_domain.save_resident_record(resident_record)
    catalog_accounts: list[str] = []

    def catalog(*, account_id: str) -> dict[str, object]:
        catalog_accounts.append(account_id)
        return {
            "models": ["replacement-model"],
            "default_model": "replacement-model",
            "efforts_by_model": {"replacement-model": ["medium"]},
            "default_efforts_by_model": {"replacement-model": "medium"},
            "service_tiers_by_model": {"replacement-model": []},
        }

    monkeypatch.setattr(
        codex_runtime_settings,
        "codex_runtime_catalog",
        catalog,
    )

    request = resident_execution_domain._build_resident_execution_request(
        resident_record,
        {
            "actor": "CASE-A",
            'execution_id': "job_provider_catalog_drift",
            "user_message_id": "message_provider_catalog_drift",
            "message": "continue after catalog change",
        },
    )

    persisted = resident_runtime_domain.read_resident_runtime_settings(resident_record)
    assert {
        field: persisted[field]
        for field in ("model", "reasoning_effort", "service_tier")
    } == {
        "model": "replacement-model",
        "reasoning_effort": "medium",
        "service_tier": "",
    }
    canonical = resident_record_domain.find_resident(str(resident_record["resident_id"]))
    assert request.runtime_options["runtime_settings"]["model"] == "replacement-model"
    assert request.runtime_options["runtime_settings"]["reasoning_effort"] == "medium"
    assert "service_tier" not in request.runtime_options
    assert set(catalog_accounts) == {"zero"}


def test_resident_goal_uses_structured_runtime_option(
    resident_record: dict[str, object],
) -> None:
    """Resident 目标使用结构化运行选项并随任务状态投影"""
    request = resident_execution_domain._build_resident_execution_request(
        resident_record,
        {
            "actor": "CASE-A",
            'execution_id': "job_native_goal",
            "user_message_id": "message_native_goal",
            "message": "/goal finish the Resident integration",
        },
    )

    assert request.runtime_options["goal"] == {
        "action": "set",
        "objective": "finish the Resident integration",
    }
    running_goal = resident_record_domain.resident_goal_payload(
        resident_record,
        job={
            'execution_id': "job_native_goal",
            "message": "/goal finish the Resident integration",
        },
    )
    paused_goal = resident_record_domain.resident_goal_payload(
        resident_record,
        job={
            'execution_id': "job_native_goal",
            "outcome": "cancelled",
            "message": "/goal finish the Resident integration",
        },
    )
    assert running_goal == {
        "objective": "finish the Resident integration",
        "status": "active",
    }
    assert paused_goal == {
        "objective": "finish the Resident integration",
        "status": "paused",
    }
    fallback = resident_record_domain.resident_goal_payload(resident_record)
    assert fallback["current_job"] == ""
    assert "objective" not in fallback


def test_resident_request_carries_only_the_authoritative_binding(
    resident_record: dict[str, object],
) -> None:
    """Resident 执行请求仅携带权威会话绑定"""
    resident_id = str(resident_record["resident_id"])

    request = resident_execution_domain._build_resident_execution_request(
        resident_record,
        {
            "actor": "CASE-A",
            'execution_id': "job_recover_binding",
            "user_message_id": "message_recover_binding",
            "message": "继续已有会话",
        },
    )

    assert request.session.mode == "persistent"
    assert request.session.binding_id == f"resident:{resident_id}"
    assert not hasattr(request.session, "backend_session_id")


def test_resident_execution_forwards_explicit_subagent_permission(
    resident_record: dict[str, object],
) -> None:
    """Resident 执行传递显式启用的子 agent 权限"""
    settings = resident_runtime_domain.read_resident_runtime_settings(resident_record)
    resident_runtime_domain.write_resident_runtime_settings(
        resident_record,
        {**settings, "allow_subagents": True},
    )

    request = resident_execution_domain._build_resident_execution_request(
        resident_record,
        {
            "actor": "CASE-A",
            'execution_id': "job_subagent_permission",
            "user_message_id": "message_subagent_permission",
            "message": "并行检查",
        },
    )

    assert request.runtime_options["allow_subagents"] is True


def test_resident_execution_forwards_agent_harness_settings(
    resident_record: dict[str, object],
) -> None:
    """Resident 执行传递归一化的 agent_harness 设置"""
    settings = resident_runtime_domain.read_resident_runtime_settings(resident_record)
    resident_runtime_domain.write_resident_runtime_settings(
        resident_record,
        {
            **settings,
            "agent_harness": {
                "features": {
                    "inline_media_history": {"enabled": True},
                },
            },
        },
    )

    request = resident_execution_domain._build_resident_execution_request(
        resident_record,
        {
            "actor": "CASE-A",
            'execution_id': "job_agent_harness",
            "user_message_id": "message_agent_harness",
            "message": "继续处理长上下文",
        },
    )

    harness = request.runtime_options["agent_harness"]
    assert "enabled" not in harness
    assert harness["mode"] == "auto"
    assert harness["features"]["inline_media_history"]["enabled"] is True


def test_derived_resident_execution_provisions_its_own_hatg_credential(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """派生 Resident 执行前为自身配置 HATG 命令行登录凭据"""
    derived_resident_id = "resident_bbbbbbbbbbbb"
    ensure = mock.Mock(return_value=None)
    monkeypatch.setattr(
        resident_hatg_credentials,
        "ensure_resident_credential",
        ensure,
    )
    derived_resident = {
        **resident_record,
        "resident_id": derived_resident_id,
        "source_resident_id": resident_record["resident_id"],
        "lineage_id": resident_record["lineage_id"],
        "created_by": "sample_reader",
        "backend": "codex",
        "default_cwd": resident_record["default_cwd"],
        "workspace_bindings": resident_record["workspace_bindings"],
    }
    resource_client.sync_resident(
        derived_resident,
        updated_by="sample_reader",
    )
    # Credential provisioning runs only after the Fork has its own session.
    register_session(
        derived_resident,
        "019f0000-0000-7000-8000-000000000002",
        backend_route_id="zero",
    )

    owned_workspace = resident_record_domain.resident_owned_workspace_path(derived_resident, str(derived_resident["resident_id"]))
    owned_workspace.mkdir(parents=True, exist_ok=True)
    derived_resident["owned_workspace_id"] = "workspace_bbbbbbbbbbbb"
    derived_resident["default_cwd"] = str(owned_workspace)
    derived_resident["owned_workspace"] = {
        "workspace_id": derived_resident["owned_workspace_id"],
        "path": str(owned_workspace),
    }
    resident_record_domain.save_resident_record(derived_resident)
    resident_execution_domain._build_resident_execution_request(
        derived_resident,
        {
            "actor": "sample_reader",
            'execution_id': "job_personal_resident",
            "user_message_id": "message_personal_resident",
            "message": "Inspect resources",
        },
    )

    ensure.assert_called_once_with(
        derived_resident_id,
        resident_record["lineage_id"],
    )


def _resident_test_codex_registry(tmp_path: Path, monkeypatch) -> tuple[CodexRoutes, Path, Path]:
    from tests.cases.account_registration_test_support import register_codex
    registry = register_codex(monkeypatch, tmp_path)
    zero_home, one_home = registry.get("zero").codex_home, registry.get("one").codex_home
    zero_home.mkdir(parents=True, exist_ok=True)
    one_home.mkdir(parents=True, exist_ok=True)
    return registry, zero_home, one_home


def test_fork_account_selection_defaults_to_source_session_account(
    resident_record: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """分叉账号默认使用来源会话账号且可显式选择目标账号"""
    registry, _zero_home, _one_home = _resident_test_codex_registry(
        tmp_path, monkeypatch,
    )
    source_session_id = resident_backend.record_session_id(
        resident_record
    )
    register_session(
        resident_record,
        source_session_id,
        backend_route_id="zero",
    )
    monkeypatch.setattr(
        resident_runtime_domain, "default_execution_accounts",
        lambda: default_execution_accounts(account_registry=registry),
    )

    selection = backend.account_selection_payload(
        resident_record,
        resident_runtime_domain.resident_session_account_id(resident_record),
    )
    derived_resident = {
        "resident_id": "resident_bbbbbbbbbbbb",
        "backend": "codex",
        "source_resident_id": str(resident_record["resident_id"]),
    }
    selected = resident_runtime_domain.assign_derived_resident_account_route(
        resident_record,
        derived_resident,
        resident_record,
        requested_account_id="one",
    )

    assert selection == {
        "enabled": True,
        "account_ids": ["codex_zero", "codex_one"],
        "source_account_id": "codex_zero",
        "default_account_id": "codex_zero",
        "copy_context_when_changed": True,
    }
    assert selected == "one"
    assert derived_resident["backend_route_id"] == "one"
    assert "source_backend_route_id" not in derived_resident
    assert "codex_context_copy_required" not in derived_resident


def test_fork_account_selection_reconciles_the_inherited_provider_tuple(
    resident_record: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """分叉切换账号时按目标能力修正继承的运行设置"""
    registry, _zero_home, _one_home = _resident_test_codex_registry(tmp_path, monkeypatch)
    register_session(
        resident_record,
        resident_backend.record_session_id(resident_record),
        backend_route_id="zero",
    )
    monkeypatch.setattr(
        resident_runtime_domain, "default_execution_accounts",
        lambda: default_execution_accounts(account_registry=registry),
    )
    monkeypatch.setattr(
        codex_runtime_settings,
        "codex_runtime_catalog",
        lambda *, account_id: {
            "models": ["target-model"],
            "default_model": "target-model",
            "efforts_by_model": {"target-model": ["low"]},
            "default_efforts_by_model": {"target-model": "low"},
            "service_tiers_by_model": {"target-model": []},
        },
    )
    derived_resident = {
        "resident_id": "resident_bbbbbbbbbbbb",
        "source_resident_id": str(resident_record["resident_id"]),
        "backend": "codex",
        "execution_settings": {
            "model": "source-only-model",
            "reasoning_effort": "xhigh",
            "service_tier": "priority",
        },
        "harness_config": {},
    }

    selected = resident_runtime_domain.assign_derived_resident_account_route(
        resident_record,
        derived_resident,
        resident_record,
        requested_account_id="one",
    )

    assert selected == "one"
    assert {
        field: derived_resident["execution_settings"][field]
        for field in ("model", "reasoning_effort", "service_tier")
    } == {
        "model": "target-model",
        "reasoning_effort": "low",
        "service_tier": "",
    }


def test_full_context_fork_stays_on_source_account(
    resident_record: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """完整上下文分叉默认在来源账号创建目标会话

    :module: resident.fork-handoff"""
    registry, zero_home, _one_home = _resident_test_codex_registry(
        tmp_path, monkeypatch,
    )
    source_session_id = resident_backend.record_session_id(
        resident_record
    )
    created_session_id = "019f0000-0000-7000-8000-000000000099"
    source_rollout = (
        zero_home
        / "sessions"
        / "2026"
        / "07"
        / "28"
        / f"rollout-2026-07-28T00-00-00-{source_session_id}.jsonl"
    )
    source_rollout.parent.mkdir(parents=True)
    source_rollout.write_text('{"type":"session_meta"}\n', encoding="utf-8")
    monkeypatch.setattr(resident_runtime_domain, "default_execution_accounts",
                        lambda: default_execution_accounts(account_registry=registry))
    from tests.cases.fork_context_test_support import record_session_turn
    record_session_turn(resident_record)
    created = backend.create_resident_full_context_fork(
        str(resident_record["resident_id"]),
        "CASE-A",
        defer_backend_start=True,
    )
    target_id = str(created["resident_id"])
    target = resident_record_domain.find_resident(target_id)
    assert target is not None

    def run_operation(**kwargs):
        assert kwargs["backend_route_id"] == "zero"
        assert kwargs['operation'] == 'create'
        assert kwargs["binding_id"] == f"resident:{target_id}"
        return {
            "backend_session_id": created_session_id,
            "backend_route_id": "zero",
        }

    monkeypatch.setattr(
        resident_session_backend_domain,
        "run_backend_session_operation",
        run_operation,
    )
    creation_job = {
        'execution_id': "job_native_source_account",
        'operation': "context_fork_create",
        "actor": "CASE-A",
    }
    resident_execution_work.write(target, creation_job)
    result = resident_session_backend_domain.create_exact_resident_session(target, creation_job)

    assert result['id'] == created_session_id
    assert result['backend_route_id'] == 'zero'
    assert get_agent_data_store().agent_context_snapshot(target_id)['segments']
    assert source_rollout.is_file()


def test_cross_account_codex_fork_uses_portable_context_seed(resident_record, monkeypatch):
    """跨账号 codex 分叉通过可迁移上下文传递选定历史"""
    from tests.cases.fork_context_test_support import record_session_turn
    selected_ids = record_session_turn(resident_record)
    created = backend.create_resident_full_context_fork(
        resident_record["resident_id"], "CASE-A", backend_route_id="one", defer_backend_start=True,
    )
    child = resident_record_domain.find_resident(created["resident_id"])
    assert child["backend_route_id"] == "one"
    assert child["fork_mode"] == "full"
    snapshot = get_agent_data_store().agent_context_snapshot(child["resident_id"])
    assert [item["segment_id"] for item in snapshot["segments"]] == selected_ids
    prepared = resident_execution_domain._portable_context_execution(child, {"actor": "CASE-A", "raw_message": "continue"})
    assert 'source question' in prepared['fork_context_input']['portable_prompt']
    assert 'source answer' in prepared['fork_context_input']['portable_prompt']
    assert 'source question' not in prepared['actual_sent_prompt']



@pytest.mark.parametrize("fork_mode", ["full", "message", "recent-5", "recent-10"])
def test_portal_fork_context_is_injected_until_provider_accepts_input(resident_record, fork_mode):
    """Portal 分叉上下文在 Provider 接受输入后停止注入

    :module: resident.conversation
    :aspects: functional, performance
    """
    from tests.cases.test_resident_fork_context_delivery import _accept_child_context
    record = dict(resident_record, fork_mode=fork_mode)
    store = get_agent_data_store()
    sid = store.put_segment(segment_type="model", content="source answer", created_by_agent_id="resident_bbbbbbbbbbbb")
    store.append_agent_segments(record["resident_id"], [sid], actor_ref="CASE-A")
    queued = {"actor": "CASE-A", "raw_message": "continue"}
    first = resident_execution_domain._portable_context_execution(record, queued)
    assert 'source answer' in first['fork_context_input']['portable_prompt']
    assert 'continue' in first['actual_sent_prompt']
    _accept_child_context(record)
    later = resident_execution_domain._portable_context_execution(record, first)
    assert 'fork_context_input' not in later
    assert 'source answer' not in later['actual_sent_prompt']



def test_session_token_usage_payload_reads_only_current_scope_projection(
    resident_record: dict[str, object],
) -> None:
    """会话用量载荷只读取当前作用域的用量投影"""
    resident_record["last_turn_usage"] = {"total_tokens": 120}
    resident_record["last_turn_usage_at"] = "2026-08-03T12:00:00Z"
    derived_resident = {
        "last_turn_usage": {"total_tokens": 45},
        "last_turn_usage_at": "2026-08-03T12:01:00Z",
    }

    resident_usage = resident_record_domain.session_token_usage_payload(resident_record)
    derived_payload = resident_record_domain.session_token_usage_payload(derived_resident)

    assert resident_usage["summary"] == {
        "last_turn_usage": {"total_tokens": 120},
        "updated_at": "2026-08-03T12:00:00Z",
    }
    assert derived_payload["summary"] == {
        "last_turn_usage": {"total_tokens": 45},
        "updated_at": "2026-08-03T12:01:00Z",
    }


def test_execution_projection_failure_is_quarantined_without_terminalizing_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """执行投影失败被隔离记录且不改变执行终态"""
    recorded: list[tuple[str, int]] = []
    advanced: list[int] = []

    class Service:
        @staticmethod
        def wait(_execution_id, **kwargs):
            assert kwargs["after_sequence"] == 11
            kwargs["on_snapshot"]({"status": "running"})
            kwargs["on_events"]([{
                "sequence": 17,
                "type": "provider_event",
                "details": {},
            }])
            return {"status": "completed", "result": {"status": "completed"}}

    monkeypatch.setattr(
        resident_execution_domain,
        "_project_resident_execution_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("Conversation message identity refers to different immutable content")
        ),
    )
    monkeypatch.setattr(
        resident_execution_domain,
        "_record_resident_projection_error",
        lambda *_args, event_sequence=0, **_kwargs: (
            recorded.append((str(_args[1]), event_sequence))
            or {"message": "projection failed", "event_sequence": event_sequence}
        ),
    )
    monkeypatch.setattr(
        resident_execution_domain,
        "_resident_execution_event_cursor",
        lambda *_args: 11,
    )
    monkeypatch.setattr(
        resident_execution_domain,
        "_advance_resident_execution_event_cursor",
        lambda *_args: advanced.append(int(_args[-1])),
    )

    snapshot = resident_execution_domain.wait_for_resident_execution(
        Service(),
        execution_id="execution-000000000000000000000706",
        actor="sample_admin",
        resident_id="resident_000000000706",

    )

    assert snapshot["status"] == "completed"
    assert snapshot["projection_errors"] == [{
        "message": "projection failed",
        "event_sequence": 0,
    }]
    assert recorded == [("execution-000000000000000000000706", 0)]
    assert advanced == [17]


_TURN_510K = {
    "input_tokens": 510_000, "cached_input_tokens": 470_000, "output_tokens": 3_000,
    "reasoning_output_tokens": 900, "total_tokens": 513_000,
}
_LAST_REQUEST_224K = {
    "inputTokens": 224_000, "cachedInputTokens": 210_000, "outputTokens": 650,
    "reasoningOutputTokens": 180, "totalTokens": 224_650,
}


@pytest.mark.parametrize("result_usage", [
    # The usage contract of the native Providers: ``last`` is the turn, ``context`` its last request.
    {"usage": {
        "last": {"inputTokens": 510_000, "cachedInputTokens": 470_000, "outputTokens": 3_000,
                 "reasoningOutputTokens": 900, "totalTokens": 513_000},
        "context": _LAST_REQUEST_224K, "total": {"totalTokens": 900_000}, "modelContextWindow": 258_400,
    }},
    # Codex reports its last request and its thread total; the turn is what the execution counted.
    {"usage": {"last": _LAST_REQUEST_224K, "total": {"totalTokens": 9_000_000}, "modelContextWindow": 258_400},
     "accounted_usage": _TURN_510K},
], ids=["usage-contract", "codex-counted"])
def test_completed_codex_turn_separates_accounting_and_context_usage(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    result_usage: dict[str, object],
) -> None:
    """codex 完成轮分别保存计费用量和当前上下文用量"""
    get_agent_data_store().display_chat_message(resident_record["resident_id"], {
        "message_id": "message_708", "role": "user", "author_id": "CASE-A",
        "content_parts": [{"type": "text", "text": "continue"}],
    })
    from management_portal.execution.contracts import ExecutionRequest, SessionOptions
    request = ExecutionRequest.new(
        actor="CASE-A", prompt="continue", provider="codex", service_type="agent_session", operation="turn",
        session=SessionOptions(mode="persistent", binding_id="resident:main"),
    )

    class Service:
        def __init__(self) -> None:
            pass

    monkeypatch.setattr(
        "management_portal.execution.service.ExecutionService",
        Service,
    )
    monkeypatch.setattr(
        resident_execution_domain,
        "_build_resident_execution_request",
        lambda *_args: request,
    )
    monkeypatch.setattr(
        resident_execution_domain,
        "durable_resident_execution_request",
        lambda _record, _job, factory: factory(),
    )
    monkeypatch.setattr(
        resident_execution_domain,
        "bind_or_resume_resident_execution",
        lambda *_args: "execution-" + "8" * 24,
    )
    monkeypatch.setattr(
        resident_execution_domain,
        "wait_for_resident_execution",
        lambda *_args, **_kwargs: {
            "status": "completed",
            "scheduler": {"updated_at": "2026-08-17T00:00:00Z"},
            "result": {
                "status": "completed",
                "backend_session_id": (
                    "019f0000-0000-7000-8000-000000000708"
                ),
                "output": {
                    "message": "完成",
                    "turn_id": (
                        "019f0000-0000-7000-8000-000000000709"
                    ),
                },
                **result_usage,
            },
        },
    )
    outcome = _deliver_session(
        resident_record,
        {
            'execution_id': "job_708",
            "user_message_id": "message_708",
        },
        persist_messages=False,
    )
    updated = dict(outcome.resident_updates)

    assert updated["last_turn_usage"] == _TURN_510K
    assert updated["last_token_usage"]["input_tokens"] == 224_000
    assert updated["last_model_context_window"] == 258_400
    assert updated["last_token_usage_at"] == updated[
        "last_turn_usage_at"
    ]
    pressure = resident_directory_domain._resident_scope_context_pressure(
        updated,
    )
    assert pressure["used_percent"] == 86.7
    assert pressure["heat_level"] == "hot"


@pytest.mark.parametrize("status", ["cancelled", "failed"])
def test_a_stopped_or_failed_turn_keeps_the_usage_its_execution_counted(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    """停止或失败的轮次保留执行计入的用量

    Resident 轮次被停止或失败时，异常携带执行的 accounted_usage；失败通知指向本轮执行并显示其用量。

    A turn stopped after hours of work used those hours: its end notice shows that usage.

    :module: execution.persistence
    :importance: P1
    :design: decision_f1331d57be89
    """
    resident_id = str(resident_record["resident_id"])
    get_agent_data_store().display_chat_message(resident_id, {
        "message_id": "message_709", "role": "user", "author_id": "CASE-A",
        "content_parts": [{"type": "text", "text": "long work"}],
    })
    from management_portal.execution.contracts import ExecutionRequest, SessionOptions
    request = ExecutionRequest.new(
        actor="CASE-A", prompt="continue", provider="codex", service_type="agent_session", operation="turn",
        session=SessionOptions(mode="persistent", binding_id="resident:main"),
    )

    class Service:
        pass

    monkeypatch.setattr("management_portal.execution.service.ExecutionService", Service)
    monkeypatch.setattr(resident_execution_domain, "_build_resident_execution_request", lambda *_args: request)
    monkeypatch.setattr(
        resident_execution_domain, "durable_resident_execution_request", lambda _record, _job, factory: factory(),
    )
    monkeypatch.setattr(
        resident_execution_domain, "bind_or_resume_resident_execution", lambda *_args: "execution-" + "9" * 24,
    )
    monkeypatch.setattr(resident_execution_domain, "wait_for_resident_execution", lambda *_args, **_kwargs: {
        "status": status,
        "scheduler": {"updated_at": "2026-08-17T00:00:00Z"},
        "result": {
            "status": status, "error": "stopped",
            "usage": {"last": _LAST_REQUEST_224K}, "accounted_usage": _TURN_510K,
        },
    })
    ended = backend.ResidentTurnCancelled if status == "cancelled" else backend.ResidentExecutionFailed
    with pytest.raises(ended) as raised:
        _deliver_session(resident_record, {'execution_id': "job_709", "user_message_id": "message_709"}, persist_messages=False)
    assert raised.value.usage == _TURN_510K

    if status == "failed":
        # The failure notice names the turn's Execution and shows what the Scheduler counted for it.
        execution_id = "execution-" + "9" * 24
        _ended_execution(resident_id, execution_id, "failed", _TURN_510K)
        notice = resident_intake_domain.persist_failed_execution_message(
            resident_id, { "user_message_id": "message_709", "execution_id": execution_id, "submitted_at": "2026-10-14T00:00:00Z"},
            "stopped", {}, failed_at="2026-08-17T00:00:01Z",
        )
        shown = resident_conversation_stream.get_repository().public_message(resident_id, notice["message_id"])
        assert shown["usage"] == _TURN_510K


def _ended_execution(agent_id: str, execution_id: str, status: str, counted: dict[str, int]) -> None:
    store = get_agent_data_store()
    if store.execution(execution_id) is None:
        store.begin_execution(
        execution_id=execution_id, agent_id=agent_id, actor_ref="CASE-A", provider="codex",
            started_at="2026-08-17T00:00:00Z",
        )
    store.finish_execution(execution_id, status=status, finished_at="2026-08-17T00:00:01Z")
    assert store.record_accounted_usage(execution_id, counted)


def test_completed_resident_turn_reuses_its_stable_final_message(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resident 完成轮复用稳定最终消息并按顺序发布终态"""
    repository = resident_conversation_stream.get_repository()
    resident_id = str(resident_record["resident_id"])
    user_message = resident_intake_domain._resident_user_message(
        resident_id, "question", "CASE-A", message_id="msg_" + "9" * 32,
    )
    repository.commit_user_message(resident_id, user_message)
    execution_id = "execution-" + "9" * 24
    job = {

        "user_message_id": user_message["message_id"],
        "execution_id": execution_id,
        "status": "running",
        "actor": "CASE-A",
        "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.write(resident_record, job)
    from management_portal.execution.contracts import ExecutionRequest, SessionOptions
    request = ExecutionRequest.new(
        actor="CASE-A", prompt="continue", provider="codex", service_type="agent_session", operation="turn",
        session=SessionOptions(mode="persistent", binding_id="resident:main"),
    )

    def wait_for_result(*args, **kwargs):
        snapshot = {
            "status": "completed",
            "scheduler": {"updated_at": "2026-08-17T00:00:00Z"},
            "result": {
                "status": "completed",
                "backend_session_id": "019f0000-0000-7000-8000-000000000799",
                "output": {"message": "唯一最终回答", "turn_id": "turn_799"},
            },
        }
        # Real wait callbacks and repositories run; only the remote provider is
        # substituted. Repeated terminal notifications must not end delivery.
        kwargs["on_snapshot"](snapshot)
        kwargs["on_snapshot"](snapshot)
        assert repository.current_projection(resident_id)["state"]["work_state"] == "running"
        return snapshot

    monkeypatch.setattr(
        execution_port, "service", lambda: SimpleNamespace(wait=wait_for_result),
    )
    monkeypatch.setattr(
        resident_execution_domain,
        "_build_resident_execution_request",
        lambda *_args: request,
    )
    monkeypatch.setattr(
        resident_execution_domain,
        "durable_resident_execution_request",
        lambda _record, _job, factory: factory(),
    )
    monkeypatch.setattr(
        resident_execution_domain,
        "bind_or_resume_resident_execution",
        lambda *_args: execution_id,
    )
    before_cursor = repository.current_projection(resident_id)["cursor"]
    first_outcome = _deliver_session(
        resident_record,
        job,
    )
    second_outcome = _deliver_session(
        resident_record,
        job,
    )
    first = dict(first_outcome.assistant_message or {})
    second = dict(second_outcome.assistant_message or {})

    assert first["message_id"] == second["message_id"]
    appended = [
        event
        for event in repository.events_after(resident_id, before_cursor)["events"]
        if event["event_type"] == "conversation.message.appended"
        and event["payload"]["message"]["message_id"] == first["message_id"]
    ]
    assert len(appended) == 1
    stored = repository.read_messages(resident_id)
    assert [item["message_id"] for item in stored].count(first["message_id"]) == 1

    resident_execution_domain._mark_completion_applied(job, resident_domain_common.now_iso())
    resident_execution_work.write(resident_record, job)
    events = repository.events_after(resident_id, before_cursor)["events"]
    completed = [
        event for event in events
        if event["event_type"] == "resident.input.state.changed"
        and event["payload"]["input_state"]["state"] == "completed"
    ]
    idle = [
        event for event in events
        if event["event_type"] == "resident.state.changed"
        and event["payload"]["state"]["work_state"] == "idle"
    ]
    assert len(completed) == len(idle) == 1
    assert completed[0]["payload"]["input_state"]["reply_message_ids"] == [first["message_id"]]
    assert appended[0]["cursor"] < completed[0]["cursor"] < idle[0]["cursor"]


def test_worker_completion_notifies_exact_resident_identity(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """工作完成清空任务后通知准确的 Resident 身份"""
    resident_id = str(resident_record["resident_id"])
    continuation_id = "resident-continuation:test-main-identity"
    user_message_id = "msg_dddddddddddddddddddddddddddddddd"
    job = {
        'execution_id': "job_main_identity",
        "user_message_id": user_message_id,
        "actor": "CASE-A",
        "status": "running",
        "continuation_id": continuation_id,
        "queue": [],
    }
    resident_execution_work.write(resident_record, job)
    resident_lifecycle_domain.append_conversation_message(resident_id, {
        "message_id": user_message_id,
        "role": "user",
        "author_id": "CASE-A",
        "content_parts": [{"type": "text", "text": "完成主会话"}],
        "created_at": "2026-08-12T13:55:59Z",
    })
    completion_resident_ids: list[str] = []

    def record_completion(completed_resident_id, *_args, **_kwargs):
        assert resident_execution_work.read(resident_record) == {}
        completion_resident_ids.append(str(completed_resident_id))

    def send_resident(record, _job):
        assistant = {
            "message_id": "msg_0123456789abcdef",
            "role": "agent",
            "content_parts": [{"type": "text", "text": "完成"}],
            "created_at": "2026-08-12T13:56:00Z",
        }
        resident_lifecycle_domain.append_conversation_message(resident_id, assistant)
        return assistant, {**record, "last_error": ""}

    monkeypatch.setattr(resident_execution_domain, "send_to_resident_backend", send_resident)
    monkeypatch.setattr(
        resident_execution_domain,
        "_notify_lineage_message_completed",
        record_completion,
    )

    resident_execution_domain.run_resident_execution(resident_id, continuation_id)

    assert completion_resident_ids == [resident_id]
    assert resident_execution_work.read(resident_record) == {}
    messages = resident_conversation_stream.get_repository().read_messages(
        resident_id
    )
    assert [item["role"] for item in messages] == ["user", "agent"]
    assert _message_text(messages[-1]) == "完成"


def test_resident_submission_recovers_same_request_after_prebind_crash(
    resident_record: dict[str, object],
) -> None:
    """提交绑定前崩溃后复用同一持久请求恢复执行"""
    service = mock.Mock()
    service.submit.side_effect = lambda request: {
        "execution_id": request.execution_id,
    }
    job = {
        'execution_id': "execution-" + "1" * 24,
        "actor": "CASE-A",
        "message": "hello",
        "actual_sent_prompt": "hello",
        "prompt_cwd": str(resident_record["default_cwd"]),
        "queue": [],
    }
    resident_execution_work.write(resident_record, job)
    first = resident_execution_domain.durable_resident_execution_request(
        resident_record,
        job,
        lambda: resident_execution_domain._build_resident_execution_request(resident_record, job),
    )

    # Scheduler accepted the request, then SocietyService crashed before it
    # could persist the execution binding.
    service.submit(first)
    recovered_job = resident_execution_work.read(resident_record)
    assert recovered_job["execution_id"] == first.execution_id
    assert not recovered_job.get("submitted_at")
    recovered = resident_execution_domain.durable_resident_execution_request(
        resident_record,
        recovered_job,
        mock.Mock(side_effect=PermissionError("Authorization configuration is unavailable")),
    )
    assert recovered.to_payload() == first.to_payload()

    execution_id = resident_execution_domain.bind_or_resume_resident_execution(
        service,
        recovered,
        resident_record,
        recovered_job,
    )

    assert execution_id == first.execution_id
    assert [call.args[0].execution_id for call in service.submit.call_args_list] == [
        first.execution_id,
        first.execution_id,
    ]
    stored = resident_execution_work.read(resident_record)
    assert stored["execution_id"] == execution_id
    assert "execution_layer" not in stored
    with (
        mock.patch.object(execution_port, "service", return_value=service),
        mock.patch.object(resident_execution_domain, "_build_resident_execution_request", side_effect=PermissionError("Authorization configuration is unavailable")) as build_turn,
        mock.patch.object(resident_session_backend_domain, "build_backend_session_operation_request", side_effect=PermissionError("Authorization configuration is unavailable")) as build_operation,
        mock.patch.object(resident_execution_domain, "_flush_pending_active_turn_controls"),
        mock.patch.object(resident_execution_domain, "wait_for_resident_execution", side_effect=RuntimeError("existing execution reached")) as wait,
    ):
        for operation in ("turn", "compact"):
            with pytest.raises(RuntimeError, match="^existing execution reached$"):
                if operation == "turn":
                    resident_execution_domain._send_resident_execution(resident_record, stored)
                else:
                    resident_execution_domain._run_resident_session_operation(resident_record, stored, operation)
        build_turn.assert_not_called()
        build_operation.assert_not_called()
        assert wait.call_count == 2
        assert service.resume.call_count == 2
        assert service.submit.call_count == 2
        assert all(call.kwargs["execution_id"] == first.execution_id for call in wait.call_args_list)


def test_resident_recovery_uses_canonical_message_already_accepted_at_intake(
    resident_record: dict[str, object],
) -> None:
    """Resident 恢复复用接收时已保存的规范消息"""
    resident_id = str(resident_record["resident_id"])
    resident_record["last_error"] = "previous terminal error"
    message = {
        "message_id": "msg_fedcba9876543210fedcba9876543210",
        "role": "user",
        "content_parts": [
            {"type": "text", "text": "recover this exact input"}
        ],
        "author_id": "CASE-A",
        "created_at": "2026-08-24T12:00:00Z",
    }
    job = {
        'execution_id': "job_durable_intake",
        "user_message_id": message["message_id"],
        "raw_message": _message_text(message),
        "message": _message_text(message),
        "actual_sent_prompt": _message_text(message),
        "prompt_cwd": str(resident_record["default_cwd"]),
        "actor": "CASE-A",
        "queue": [],
    }
    repository = resident_conversation_stream.get_repository()
    repository.commit_user_message(resident_id, message)
    repository.set_input_state(resident_id, str(message["message_id"]), "queued")
    resident_execution_work.write(resident_record, job)

    prepared = resident_execution_domain._prepare_resident_continuation(resident_record)

    assert prepared is not None
    assert resident_record["last_error"] == ""
    assert [item["message_id"] for item in repository.read_messages(resident_id)] == [
        message["message_id"]
    ]


def test_resident_recovery_preserves_delivered_input_while_finishing_job(
    resident_record: dict[str, object],
) -> None:
    """恢复收尾保留已完成输入状态直至结果处理结束"""
    resident_id = str(resident_record["resident_id"])
    message_id = "msg_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    execution_id = "execution-" + "1" * 24
    repository = resident_conversation_stream.get_repository()
    repository.append_message(
        resident_id,
        {
            "message_id": message_id,
            "role": "user",
            "content_parts": [{"type": "text", "text": "completed input"}],
            "created_at": "2026-08-28T00:00:00Z",
        },
    )
    repository.set_input_state(resident_id, message_id, "completed")
    resident_execution_work.write(
        resident_record,
        {

            "user_message_id": message_id,
            "actor": "CASE-A",
            "execution_id": execution_id,
            "queue": [],
         "submitted_at": "2026-10-14T00:00:00Z"},
        snapshot={"status": "completed"},
    )

    with mock.patch.object(
        resident_execution_work,
        "execution_snapshot",
        return_value={"status": "completed"},
    ):
        prepared = resident_execution_domain._prepare_resident_continuation(resident_record)

    assert prepared is not None
    assert repository.input_state(resident_id, message_id)["state"] == "completed"
    # Input delivery is durable, but the Resident job still needs finalization.
    assert repository.current_projection(resident_id)["state"]["work_state"] == "running"


def test_resident_recovery_clears_stale_error_for_active_continuation(
    resident_record: dict[str, object],
) -> None:
    """活跃续接恢复清除旧错误信息"""
    resident_record["last_error"] = "old provider interruption"
    resident_execution_work.write(
        resident_record,
        {'execution_id': "job_running", "actor": "CASE-A"},
    )

    with mock.patch.object(
        resident_execution_work,
        "continuation_needed",
        return_value=False,
    ):
        prepared = resident_execution_domain._prepare_resident_continuation(resident_record)

    assert prepared is None
    assert resident_record["last_error"] == ""


def test_resident_send_publishes_nothing_before_turn_persistence(
    resident_record: dict[str, object],
) -> None:
    """Resident 轮次持久化失败时不发布消息且可原键重试"""
    resident_id = str(resident_record["resident_id"])
    message_id = "msg_0123456789abcdef0123456789abcdef"
    with (
        mock.patch.object(
            resident_session_backend_domain,
            "handle_resident_native_command",
            return_value={},
        ),
        mock.patch.object(
            slash_commands,
            "handle_slash_command",
            return_value={"handled": False},
        ),
        mock.patch.object(resident_record_domain, "resolve_context_refs", return_value={}),
        mock.patch.object(
            resident_execution_work,
            "write",
            side_effect=RuntimeError("process interrupted"),
        ),
        mock.patch.object(
            resident_record_domain,
            "last_user_message_text",
            side_effect=AssertionError("ordinary messages must not read history"),
        ),
    ):
        with pytest.raises(RuntimeError, match="process interrupted"):
            _published_delivery(backend.deliver_resident_message_content(
                resident_id,
                "CASE-A",
                "survive restart",
                message_id=message_id,
            ))

    messages = resident_conversation_stream.get_repository().read_messages(
        resident_id
    )
    assert messages == []

    recovered = resident_execution_work.read(resident_record)
    assert recovered == {}

    with (
        mock.patch.object(
            resident_session_backend_domain,
            "handle_resident_native_command",
            return_value={},
        ),
        mock.patch.object(
            slash_commands,
            "handle_slash_command",
            return_value={"handled": False},
        ),
        mock.patch.object(resident_record_domain, "resolve_context_refs", return_value={}),
        mock.patch.object(
            resident_record_domain,
            "last_user_message_text",
            side_effect=AssertionError("ordinary messages must not read history"),
        ),
    ):
        retried = _published_delivery(backend.deliver_resident_message_content(
            resident_id,
            "CASE-A",
            "survive restart",
            message_id=message_id,
        ))

    assert retried["message"]["message_id"] == message_id
    assert retried["input_state"]["state"] == "running"
    assert retried["state"]["work_phase"] == "starting_session"
    assert resident_execution_work.read(resident_record)["user_message_id"] == message_id


@pytest.mark.parametrize("resident_backend", ("claude", "kimi"))
def test_non_codex_resident_instructions_use_runtime_lifecycle(
    resident_record: dict[str, object],
    resident_backend: str,
) -> None:
    """非 codex Resident 系统指令按会话生命周期注入"""
    resident_record["backend"] = resident_backend

    runtime_options = resident_execution_domain.resident_execution_runtime_options(
        resident_record,
        {"actor": "CASE-A"},
        is_owner=True,
    )
    system_instructions = resident_execution_domain.resident_system_instructions(
        resident_record,
        {"actor": "CASE-A"},
    )

    assert "system_prompt" not in runtime_options
    assert "system_prompt_append" not in runtime_options
    assert system_instructions.parts[0].timing == "session_start_and_after_compaction"


@pytest.mark.parametrize(
    ("provider_backend", "has_session"),
    (
        ("codex", False),
        ("claude", False),
        ("kimi", False),
        ("codex", True),
        ("claude", True),
        ("kimi", True),
    ),
)
def test_resident_turn_prompt_avoids_provider_system_prompt_duplicates(
    resident_record: dict[str, object],
    provider_backend: str,
    has_session: bool,
) -> None:
    """Resident 单轮提示避免重复包含 Provider 系统规则"""
    resident_record["backend"] = provider_backend
    if provider_backend != "codex":
        resident_backend.delete_session_identity(resident_record)
        register_session(
            resident_record,
            "provider-session-existing",
        )
    if not has_session:
        resident_backend.delete_session_identity(resident_record)

    job = {
        "message": "continue",
        "context_refs": {},
    }
    prompt = resident_registration_domain.build_resident_prompt(resident_record, job)
    system_instructions = resident_execution_domain.resident_system_instructions(
        resident_record,
        job,
    )

    assert "<portal-system-prompt" not in prompt
    core = {part.name: part for part in system_instructions.parts}["core_rules"]
    assert core.placement == "system"
    assert core.timing == "session_start_and_after_compaction"


def test_started_event_projects_only_the_bound_execution_work(
    resident_record: dict[str, object],
) -> None:
    """执行启动快照只投影当前绑定的执行工作"""
    execution_id = "execution-" + "1" * 24
    job = {
        'execution_id': execution_id,
        "actor": "CASE-A",
        "conversation_projection_error": {"message": "old projection error"},
        **resident_execution_work.continuation_fields(resident_record),
    }
    bind_execution(
        job,
        execution_id=execution_id,


        now="2026-07-27T00:00:00Z",
    )
    resident_execution_work.write(resident_record, job)

    resident_execution_domain._project_resident_execution_snapshot(
        "resident_aaaaaaaaaaaa",

        "execution-stale",
        {"status": "running"},
    )
    stale = resident_execution_work.read(resident_record)
    assert "status" not in stale
    assert "conversation_projection_error" in stale

    resident_execution_domain._project_resident_execution_snapshot(
        "resident_aaaaaaaaaaaa",

        execution_id,
        {"status": "running"},
    )
    stored = resident_execution_work.read(resident_record)
    assert "status" not in stored
    assert "queue_reason" not in stored
    assert "worker_heartbeat_at" not in stored
    assert "conversation_projection_error" not in stored
    assert resident_execution_work.public_execution_state(
        stored,
        snapshot={"status": "running"},
    ) == {"status": "running", "queue_reason": ""}


def test_late_execution_snapshot_cannot_restore_terminal_activity(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """迟到的运行快照不能恢复已终结任务的活动"""
    resident_id = str(resident_record["resident_id"])
    execution_id = "execution-" + "4" * 24
    terminal_job = {

        "actor": "CASE-A",
        "execution_id": execution_id,
        "outcome": "cancelled",
        "finished_at": "2026-08-27T00:00:00Z",
        **resident_execution_work.continuation_fields(resident_record),
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.write(resident_record, terminal_job)
    repository = resident_conversation_stream.get_repository()
    repository.set_activities(resident_id, [_activity(resident_id, "stale")])
    monkeypatch.setattr(
        resident_directory_domain,
        "runtime_activity_for_execution",
        lambda _resident_id, _job: {"details": [_activity(resident_id, "late")]},
    )

    resident_execution_domain._project_resident_execution_snapshot(
        resident_id,

        execution_id,
        {"status": "running"},
    )

    assert repository.current_projection(resident_id)["activities"] == []


def test_terminal_execution_snapshot_latches_provider_status_until_delivery(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """执行终态状态锁存至结果投递且迟到快照不恢复活动"""
    resident_id = str(resident_record["resident_id"])
    execution_id = "execution-" + "5" * 24
    job = {
        'execution_id': execution_id,
        "actor": "CASE-A",
        "user_message_id": "message_terminal_snapshot",
        **resident_execution_work.continuation_fields(resident_record),
    }
    bind_execution(
        job,
        execution_id=execution_id,


        now="2026-08-30T09:00:00Z",
    )
    resident_execution_work.write(resident_record, job)
    repository = resident_conversation_stream.get_repository()
    repository.append_message(
        resident_id,
        {
            "message_id": "message_terminal_snapshot",
            "resident_id": resident_id,
            "role": "user",
            "author_id": "CASE-A",
            "content_parts": [{"type": "text", "text": "Run"}],
            "created_at": "2026-08-30T09:00:00Z",
        },
    )
    monkeypatch.setattr(
        resident_directory_domain,
        "runtime_activity_for_execution",
        lambda _resident_id, _job: {
            "details": [_activity(resident_id, "stale")],
        },
    )

    resident_execution_domain._project_resident_execution_snapshot(
        resident_id,

        execution_id,
        {"status": "completed"},
    )
    resident_execution_domain._project_resident_execution_snapshot(
        resident_id,

        execution_id,
        {"status": "running"},
    )

    projection = repository.current_projection(resident_id)
    assert projection["state"]["work_state"] == "running"
    assert projection["activities"] == []
    assert repository.input_state(
        resident_id,
        "message_terminal_snapshot",
    )["state"] == "running"
    assert resident_execution_work.read(resident_record)["execution_terminal_status"] == "completed"


@pytest.mark.parametrize("transition", ["completed", "next_job"])
def test_snapshot_cannot_overwrite_a_concurrent_resident_transition(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    transition: str,
) -> None:
    """执行快照不能覆盖并发完成或新任务切换"""
    resident_id = str(resident_record["resident_id"])
    execution_id = "execution-" + "6" * 24
    job = {"actor": "CASE-A", "execution_id": execution_id, "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.write(resident_record, job)
    repository = resident_conversation_stream.get_repository()
    expected = {}

    def concurrent_transition(*args, **kwargs):
        replacement = (
            {**job, "outcome": "completed", "finished_at": resident_domain_common.now_iso()}
            if transition == "completed" else {'actor': 'CASE-A', 'execution_id': "job_next"}
        )
        resident_execution_work.write(resident_record, replacement)
        expected.update(repository.current_projection(resident_id))
        return {"outputs": [], "runtime_details": []}

    monkeypatch.setattr(
        execution_events,
        "execution_projection_from_execution",
        concurrent_transition,
    )
    resident_execution_domain._project_resident_execution_snapshot(
        resident_id,  execution_id, {"status": "running"},
    )
    assert repository.current_projection(resident_id) == expected


def test_live_waiter_is_not_reprojected_or_queried_by_recovery(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """存在活跃等待者时恢复不重复查询或投影"""
    resident_id = str(resident_record["resident_id"])
    job = {"actor": "CASE-A",
         "execution_id": "execution-" + "7" * 24,
        **resident_execution_work.continuation_fields(resident_record),
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.write(resident_record, job)
    monkeypatch.setattr(resident_execution_work, "thread", lambda _record: threading.current_thread())

    def forbidden_work(*args, **kwargs):
        raise AssertionError("A live waiter already owns the execution projection")

    monkeypatch.setattr(resident_execution_work, "publish_conversation_state", forbidden_work)
    monkeypatch.setattr(resident_execution_work, "execution_snapshot", forbidden_work)
    assert resident_intake_domain._active_resident_execution(resident_record)['execution_id'] == job['execution_id']
    assert backend.resume_resident_execution(resident_id)["residents"] == 0


def test_preparing_a_waiter_never_queries_execution_service(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """准备续接等待者只使用本地状态"""
    resident_execution_work.write(resident_record, {
         "execution_id": "execution-" + "8" * 24,
        "actor": "CASE-A",
     "submitted_at": "2026-10-14T00:00:00Z"})
    query = mock.Mock(side_effect=AssertionError("Preparation must be local"))
    monkeypatch.setattr(resident_execution_work, "execution_snapshot", query)
    assert resident_execution_domain._prepare_resident_continuation(resident_record) is not None
    query.assert_not_called()




def test_account_details_reads_current_states_without_reopening(resident_record, monkeypatch):
    """账号详情读取当前状态且不自动重新打开接收"""
    from management_portal.society_service.server import LocalSocietyService

    resident_id = resident_record["resident_id"]
    quota = {"status": "reserved", "remaining_percent": 1}
    monkeypatch.setattr(execution_client, "health", lambda: {"scheduler": {"capacity_partitions": {"codex:zero": {"quota_reserve": quota}}}})
    resident_protocol.change_resident_state(resident_id, "CASE-A", state_domain="intake", transition="close")
    repository = resident_conversation_stream.get_repository()
    resident_execution_work.write(resident_record, {"queue": [{"user_message_id": f"queued-{i}"} for i in range(5)]})
    before = resident_record_domain.find_resident(resident_id)
    quota.update(status="available", remaining_percent=80)
    execution_client.configure_binding_quota_overrides.reset_mock()

    result = LocalSocietyService().resident.get_account_resident_summary("codex_zero", actor="CASE-A")

    assert result["scope"] == "visible"
    assert result["total"] == 1
    assert result["intake"] == {"open": 0, "closed": 1, "unknown": 0}
    assert result["work"]["queued"] == 1
    assert result["queued_messages"] == 5
    assert result["without_session"] == 0
    assert result["quota_exempt"] == 0
    assert result["closed_resident_ids"] == [resident_id]
    assert resident_record_domain.find_resident(resident_id) == before
    assert resident_lifecycle_domain.resident_intake_state(before) == "closed"
    execution_client.configure_binding_quota_overrides.assert_not_called()


def test_account_details_preserves_counts_when_quota_unavailable(resident_record, monkeypatch):
    """额度服务不可用时账号详情仍保留 Resident 计数"""
    def unavailable():
        raise RuntimeError("quota service unavailable")

    monkeypatch.setattr(execution_client, "health", unavailable)
    resident_protocol.change_resident_state(resident_record["resident_id"], "CASE-A", state_domain="intake", transition="close")
    result = backend.get_account_resident_summary("codex_zero", "CASE-A")
    assert result["total"] == 1
    assert result["intake"]["closed"] == 1
    assert result["quota"]["status"] == "unknown"
    assert result["closed_resident_ids"] == [resident_record["resident_id"]]


def test_account_details_count_only_visible_residents(resident_record, monkeypatch):
    """账号汇总只统计调用者看得到的 Resident

    计数与供 OpenIntake（open=true/false）resident_ids 使用的 open_resident_ids、closed_resident_ids 都只含调用者看得到（GetResident 通过）的 Resident。

    :design: decision_4dea0db1f45a
    """
    monkeypatch.setattr(execution_client, "health", lambda: {})
    monkeypatch.setattr(resident_intake_domain, "_resident_visible_to_actor", lambda record, actor: actor == "viewer")
    hidden = backend.get_account_resident_summary("codex_zero", "visitor")
    assert hidden["scope"] == "visible"
    assert hidden["total"] == 0
    assert hidden["open_resident_ids"] == hidden["closed_resident_ids"] == []
    assert resident_record["resident_id"] not in str(hidden)
    visible = backend.get_account_resident_summary("codex_zero", "viewer")
    assert visible["total"] == 1
    assert visible["open_resident_ids"] == [resident_record["resident_id"]]
    assert visible["closed_resident_ids"] == []
    with pytest.raises(PermissionError):
        backend.get_account_resident_summary("codex_zero", "")


@pytest.fixture
def quota_codex_registry(tmp_path, monkeypatch):
    registry, _, _ = _resident_test_codex_registry(tmp_path, monkeypatch)
    monkeypatch.setattr(resident_runtime_domain, "default_execution_accounts",
                        lambda: default_execution_accounts(account_registry=registry))
    return registry


def test_account_details_follows_binding_and_no_session_default(quota_codex_registry, resident_record, monkeypatch):
    """账号汇总按当前会话绑定或无会话默认账号归属"""
    monkeypatch.setattr(execution_client, "health", lambda: {})
    assert backend.get_account_resident_summary("codex_zero", "CASE-A")["total"] == 1
    assert backend.get_account_resident_summary("codex_one", "CASE-A")["total"] == 0
    monkeypatch.setattr(resident_backend, "session_routes_for_bindings", lambda _: {})
    assert backend.get_account_resident_summary("codex_zero", "CASE-A")["total"] == 0
    result = backend.get_account_resident_summary("codex_one", "CASE-A")
    assert result["total"] == result["without_session"] == 1


def test_unbound_account_resolution_is_cached_by_route_selector(
    quota_codex_registry,
    resident_record,
    monkeypatch,
):
    """未绑定账号解析按路由选择器缓存结果"""
    accounts = ExecutionService().accounts()["accounts"]
    resolve = mock.Mock(return_value={
        "account_id": "codex_one",
        "backend_route_id": "one",
    })
    monkeypatch.setattr(ExecutionService, "resolve_account", resolve)
    account_for = resident_intake_domain._resident_quota_account_resolver(accounts)

    assert account_for(resident_record, {}) == "codex_one"
    assert account_for({**resident_record}, {}) == "codex_one"
    resolve.assert_called_once()


def test_released_model_api_without_provider_does_not_break_account_details(
    resident_record, monkeypatch,
):
    """A sanitized API route is unassigned until the new site configures it."""
    accounts = ExecutionService().accounts()["accounts"]
    resolve = mock.Mock(side_effect=AssertionError("Unconfigured Resident must not resolve an account"))
    monkeypatch.setattr(ExecutionService, "resolve_account", resolve)
    account_for = resident_intake_domain._resident_quota_account_resolver(accounts)
    released = {**resident_record, "backend": "provider_api", "backend_route_id": ""}
    assert account_for(released, {}) == ""
    assert account_for(released, {}) == ""
    resolve.assert_not_called()


def test_unbound_provider_without_execution_account_is_not_quota_managed(
    resident_record,
    monkeypatch,
):
    """无执行账号的未绑定 Provider 不参与额度管理"""
    accounts = ExecutionService().accounts()["accounts"]
    resolve = mock.Mock(return_value={})
    monkeypatch.setattr(ExecutionService, "resolve_account", resolve)
    account_for = resident_intake_domain._resident_quota_account_resolver(accounts)
    unaccounted = {
        **resident_record,
        "backend": "portal",
        "provider": "portal",
    }

    assert account_for(unaccounted, {}) == ""
    assert account_for({**unaccounted}, {}) == ""
    resolve.assert_called_once()


def test_provider_api_resident_belongs_to_its_api_execution_account(
    resident_record,
    monkeypatch,
):
    """Provider API Resident 直接归属其 API 执行账号

    :module: resident.conversation
    :aspects: functional, performance
    """
    # Shared API accounts are execution accounts (82359ba4): a Provider API
    # Resident maps to its account by provider ID, without route resolution.
    accounts = [{"account_id": "kimi_primary", "provider": "kimi_primary", "backend_route_id": ""}]
    resolve = mock.Mock(side_effect=AssertionError("an API account needs no route resolution"))
    monkeypatch.setattr(ExecutionService, "resolve_account", resolve)
    account_for = resident_intake_domain._resident_quota_account_resolver(accounts)
    provider_resident = {
        **resident_record,
        "backend": "provider_api",
        "backend_route_id": "kimi_primary",
    }

    assert account_for(provider_resident, {}) == "kimi_primary"
    resolve.assert_not_called()








def test_quota_override_survives_stale_record_saves(resident_record, monkeypatch):
    """过期 Resident 记录保存不覆盖最新额度覆盖策略"""
    resident_id = resident_record["resident_id"]
    stale = resident_record_domain.find_resident(resident_id)
    resident_protocol.change_resident_state(resident_id, "CASE-A", state_domain="intake", transition="open")
    opened = resident_record_domain.find_resident(resident_id)
    resident_record_domain.save_resident_record(stale)
    assert resident_record_domain.find_resident(resident_id)["quota_reserve_override"] == opened["quota_reserve_override"]
    resident_protocol.change_resident_state(resident_id, "CASE-A", state_domain="intake", transition="close")
    closed = resident_record_domain.find_resident(resident_id)
    resident_record_domain.save_resident_record(opened)
    assert resident_record_domain.find_resident(resident_id)["quota_reserve_override"] == closed["quota_reserve_override"]
    assert not closed["quota_reserve_override"]["enabled"]


def test_quota_override_repairs_failed_sync_and_releases_existing_execution(resident_record, monkeypatch, tmp_path):
    """额度覆盖重试同步后释放既有执行且关闭时请求取消"""
    import time

    from management_portal.execution.codex_quota import quota_sample
    from management_portal.execution.contracts import ExecutionRequest, SessionOptions
    from management_portal.execution.scheduler import Scheduler

    resident_id = resident_record["resident_id"]
    binding_id = resident_backend.execution_binding_id(resident_record)
    scheduler = Scheduler(quota_reserve_enabled=True, dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    scheduler.register_worker(worker_id="quota-test", build_id="test", capabilities=["agent_session:codex"])
    scheduler.update_account_quota("zero", quota_sample({"rateLimits": {"primary": {"usedPercent": 100}}}, observed_at=time.time()))
    request = ExecutionRequest.new(
        actor="CASE-A", prompt="test", service_type="agent_session", provider="codex", operation="turn",
        session=SessionOptions(mode="persistent", binding_id=binding_id, backend_route_id="zero"),
    )
    scheduler.submit(request)
    assert scheduler.snapshot(request.execution_id)["scheduler"]["queue_reason"] == "quota_reserve"
    sync = mock.Mock(side_effect=RuntimeError("offline"))
    monkeypatch.setattr(execution_client, "configure_binding_quota_overrides", sync)
    monkeypatch.setattr(execution_client, "health", lambda: {"scheduler": scheduler.health()})
    result = resident_protocol.change_resident_state(resident_id, "CASE-A", state_domain="intake", transition="open")
    assert result["quota_override_pending"]
    opened = resident_record_domain.find_resident(resident_id)
    assert opened["quota_reserve_override"]["enabled"]
    assert resident_lifecycle_domain.resident_intake_state(opened) == "open"
    sync.side_effect = scheduler.configure_binding_quota_overrides
    resident_intake_domain.sync_resident_quota_overrides([opened])
    claimed = scheduler.claim(worker_id="quota-test", capabilities=["agent_session:codex"])
    assert claimed["execution"]["execution_id"] == request.execution_id
    scheduler.update_account_quota("zero", quota_sample({"rateLimits": {"primary": {"usedPercent": 100}}}, observed_at=time.time()))
    assert not scheduler.snapshot(request.execution_id)["scheduler"]["cancel_requested"]
    resident_protocol.change_resident_state(resident_id, "CASE-A", state_domain="intake", transition="close")
    assert scheduler.snapshot(request.execution_id)["scheduler"]["cancel_requested"]




def test_quota_override_does_not_inherit_to_another_resident(resident_record):
    """额度覆盖策略不能复制继承给其他 Resident"""
    resident_protocol.change_resident_state(resident_record["resident_id"], "CASE-A", state_domain="intake", transition="open")
    copied = resident_record_domain.find_resident(resident_record["resident_id"])
    copied["resident_id"] = "resident_bbbbbbbbbbbb"
    assert resident_record_domain.resident_quota_override(copied) == {}








def test_capacity_queue_snapshot_projects_visible_queue_reason(
    resident_record: dict[str, object],
) -> None:
    """容量不足的执行快照保留可见排队原因"""
    execution_id = "execution-" + "2" * 24
    job = {
        'execution_id': execution_id,
        "actor": "CASE-A",
        **resident_execution_work.continuation_fields(resident_record),
    }
    bind_execution(
        job,
        execution_id=execution_id,


        now="2026-07-27T00:00:00Z",
    )
    resident_execution_work.write(resident_record, job)

    resident_execution_domain._project_resident_execution_snapshot(
        "resident_aaaaaaaaaaaa",

        execution_id,
        {
            "status": "queued",
            "scheduler": {"queue_reason": "capacity"},
        },
    )
    stored = resident_execution_work.read(resident_record)
    queued = resident_execution_work.public_execution_state(
        stored,
        snapshot={
            "status": "queued",
            "scheduler": {"queue_reason": "capacity"},
        },
    )
    assert queued["status"] == "queued"
    assert queued["queue_reason"] == "capacity"
    assert "status" not in stored
    assert "queue_reason" not in stored

    resident_execution_domain._project_resident_execution_snapshot(
        "resident_aaaaaaaaaaaa",

        execution_id,
        {
            "status": "claimed",
            "scheduler": {"queue_reason": ""},
        },
    )
    claimed = resident_execution_work.public_execution_state(
        stored,
        snapshot={"status": "claimed"},
    )
    assert claimed["status"] == "claimed"
    assert claimed["queue_reason"] == ""


def test_session_binding_queue_snapshot_remains_visibly_queued(
    resident_record: dict[str, object],
) -> None:
    """等待会话绑定的执行快照保持可见排队状态"""
    execution_id = "execution-" + "3" * 24
    job = {
        'execution_id': execution_id,
        "actor": "CASE-A",
        **resident_execution_work.continuation_fields(resident_record),
    }
    bind_execution(
        job,
        execution_id=execution_id,


        now="2026-08-10T07:48:33Z",
    )
    resident_execution_work.write(resident_record, job)

    resident_execution_domain._project_resident_execution_snapshot(
        "resident_aaaaaaaaaaaa",

        execution_id,
        {
            "status": "queued",
            "scheduler": {"queue_reason": "session_binding"},
        },
    )

    stored = resident_execution_work.read(resident_record)
    queued = resident_execution_work.public_execution_state(
        stored,
        snapshot={
            "status": "queued",
            "scheduler": {"queue_reason": "session_binding"},
        },
    )
    assert queued["status"] == "queued"
    assert queued["queue_reason"] == "session_binding"
    assert "status" not in stored
    assert "queue_reason" not in stored


def test_queue_promotion_keeps_local_continuation(
    resident_record: dict[str, object],
) -> None:
    """队列提升保留本地续接身份"""
    job = {
        'execution_id': "job_dev",
        "continuation_id": "resident-continuation:dev",
        "continuation_started_at": "2026-07-27T00:00:00Z",
        "queue": [],
    }
    next_job = {
        'execution_id': "job_next_dev",
        "user_message_id": "message_next_dev",
        "message": "continue in dev",
        "actor": "CASE-A",
    }

    resident_execution_work.promote_queue_item(
        job,
        next_job,
        [],
        "2026-07-27T00:01:00Z",
        retain_continuation=True,
    )

    assert "status" not in job
    assert "queue_reason" not in job
    assert job["continuation_id"] == "resident-continuation:dev"


def test_conversation_snapshot_reads_canonical_running_state(
    resident_record: dict[str, object],
) -> None:
    """对话快照读取规范运行状态和活跃消息标识"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    repository.set_state(resident_id, {
        "work_state": "running",
        "active_message_id": "message_running",
        "queued_count": 0,
        "intake_state": "open",
    })
    snapshot = resident_protocol.get_resident_conversation_snapshot(
        resident_id,
        "CASE-A",
    )
    assert snapshot["resident"]["resident_id"] == resident_id
    assert "resident_id" not in snapshot
    assert snapshot["state"]["work_state"] == "running"
    assert snapshot["state"]["active_message_id"] == "message_running"


def test_conversation_snapshot_defaults_to_idle_without_runtime_projection(
    resident_record: dict[str, object],
) -> None:
    """缺少运行投影时对话快照默认空闲且队列为空"""
    snapshot = resident_protocol.get_resident_conversation_snapshot(
        str(resident_record["resident_id"]), "CASE-A"
    )
    assert snapshot["state"]["work_state"] == "idle"
    assert snapshot["activities"] == []
    assert snapshot["queue"] == {
        "resident_id": str(resident_record["resident_id"]),
        "items": [],
    }


def test_conversation_snapshot_reads_nonempty_canonical_activity_and_queue(
    resident_record: dict[str, object],
) -> None:
    """对话快照读取已有规范活动和队列内容"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    activity = _activity(resident_id, "command")
    queue = {
        "resident_id": resident_id,
        "items": [
            {
                "message_id": "message_queued",
                "author_id": "sample_admin",
                "content_preview": "queued follow-up",
                "enqueued_at": "2026-08-29T00:00:00Z",
            }
        ],
    }
    repository.set_activities(resident_id, [activity])
    # The queue is read from the Mailbox: nothing is set on the Conversation.
    repository.state_store.put_resident_execution_record(resident_id, {"queue": [{
        "user_message_id": "message_queued",
        "enqueued_at": "2026-08-29T00:00:00Z",
        "input_message": {
            "message_id": "message_queued", "role": "user", "author_id": "sample_admin",
            "created_at": "2026-08-29T00:00:00Z",
            "content_parts": [{"type": "text", "text": "queued follow-up"}],
        },
    }]})
    queue["items"][0]["content_parts"] = [{"type": "text", "text": "queued follow-up"}]

    snapshot = resident_protocol.get_resident_conversation_snapshot(
        resident_id,
        "CASE-A",
    )

    assert snapshot["activities"] == [activity]
    assert snapshot["queue"] == queue


def test_conversation_snapshot_includes_persisted_runtime_settings(
    resident_record: dict[str, object],
) -> None:
    """对话快照包含持久化运行设置"""
    resident_runtime_domain.write_resident_runtime_settings(
        resident_record,
        {
            "model": "gpt-5.6-sol",
            "reasoning_effort": "xhigh",
        },
    )

    snapshot = resident_protocol.get_resident_conversation_snapshot(
        str(resident_record["resident_id"]),
        "CASE-A",
    )

    assert snapshot["runtime_settings"]["reasoning_effort"] == "xhigh"


def test_conversation_snapshot_is_a_side_effect_free_read(
    resident_record: dict[str, object],
) -> None:
    """读取对话快照不推进游标或产生副作用"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    resident_runtime_domain.write_resident_runtime_settings(
        resident_record,
        {"model": "gpt-5.6-sol", "reasoning_effort": "high"},
    )
    cursor = repository.current_projection(resident_id)["cursor"]

    snapshot = resident_protocol.get_resident_conversation_snapshot(
        resident_id,
        "CASE-A",
    )

    assert snapshot["cursor"] == cursor
    assert repository.current_projection(resident_id)["cursor"] == cursor


def test_resident_session_projection_skips_lineage_lock_and_family_scan(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resident 会话投影跳过 Lineage 锁与 Family 对话扫描"""
    monkeypatch.setattr(
        resident_record_domain,
        "state_lock",
        lambda *_args, **_kwargs: pytest.fail("projection entered lineage lock"),
    )
    monkeypatch.setattr(
        resident_conversation_projection_domain,
        "conversation_family_summaries",
        lambda *_args, **_kwargs: pytest.fail("projection scanned family messages"),
    )
    monkeypatch.setattr(
        resident_directory_domain,
        "resident_conversation_messages",
        lambda *_args, **_kwargs: pytest.fail("projection read the conversation"),
    )

    detail = backend.get_resident_session_projection(
        str(resident_record["resident_id"]),
        "CASE-A",
    )

    assert detail["schema"] == "tfi.resident-session-projection.v1"
    assert detail["resident_id"] == resident_record["resident_id"]
    assert "family_residents" not in detail
    assert "messages" not in detail
    assert detail["provider_session"] == {
        "provider": "codex",
        "id": "019f0000-0000-7000-8000-000000000001",
    }
    assert "chat_workspace" not in detail
    assert detail["account_selection"]["source_account_id"] == "codex_zero"
    assert "codex_account_selection" not in detail
    assert detail["agent_harness_defaults"]["native_tool_loop_governor"] == (
        resident_runtime_settings.normalize_settings({})["agent_harness"]["features"]["native_tool_loop_governor"]
    )


def test_lineage_node_metadata_does_not_build_chat_or_workspace_projection(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lineage 节点元数据读取不构建会话或 Workspace 投影"""
    monkeypatch.setattr(
        resident_directory_domain,
        "_resident_session_projection_from_record",
        lambda *_args, **_kwargs: pytest.fail("node metadata built session projection"),
    )
    monkeypatch.setattr(
        resident_runtime_domain,
        "read_resident_runtime_settings",
        lambda *_args, **_kwargs: pytest.fail("node metadata read runtime settings"),
    )

    detail = backend.get_lineage_node_metadata(
        str(resident_record["resident_id"]),
        "CASE-A",
    )

    assert detail["schema"] == "tfi.lineage-node-metadata.v1"
    assert set(detail) == {
        "schema",
        "revision",
        "resident_id",
        "resident",
        "account_selection",
    }


def test_resident_session_projection_excludes_execution_storage_fields(
    resident_record: dict[str, object],
) -> None:
    """Resident 会话投影排除执行存储内部字段"""
    stored = dict(resident_record)
    execution = {
        "execution_id": "execution_internal",

        "runtime_error": {"message": "internal"},
        "actor": "CASE-A",
     "submitted_at": "2026-10-14T00:00:00Z"}
    stored["session_state"] = "running"
    resident_record_domain.save_resident_record(stored)
    resident_execution_work.write(stored, execution)
    assert resident_execution_work.read(stored)["execution_id"] == execution["execution_id"]

    detail = backend.get_resident_session_projection(
        str(resident_record["resident_id"]),
        "CASE-A",
    )

    public = detail["resident"]
    for field in (
        "execution_id",
        'execution_id',
        "runner_id",
        "session_state",
        "runtime_error",
        "backend_binding",
    ):
        assert field not in public
    assert "backend_binding" not in detail
    assert public["resident_id"] == resident_record["resident_id"]
    assert public["backend"] == "codex"


def test_queue_promotion_preserves_native_input_media(
    resident_record: dict[str, object],
) -> None:
    """队列提升保留原生输入媒体"""
    job = {
        'execution_id': "job_current",
        "input_media": [],
        "queue": [],
    }
    media = [{
        "media_type": "image",
        "source_type": "local_path",
        "source": "/tmp/queued-probe.png",
        "mime_type": "image/png",
        "name": "queued-probe.png",
    }]
    next_job = {
        'execution_id': "job_image",
        "user_message_id": "message_image",
        "message": "inspect queued image",
        "actor": "CASE-A",
        "input_media": media,
    }

    resident_execution_work.promote_queue_item(
        job,
        next_job,
        [],
        "2026-07-27T00:01:00Z",
        retain_continuation=True,
    )

    assert job['execution_id'].startswith('execution-')
    assert job["input_media"] == media


def test_message_queued_behind_active_job_stays_in_mailbox(
    resident_record: dict[str, object],
) -> None:
    """活跃任务后的排队消息保留在 Mailbox 且暂不进入聊天"""
    foreign_job = {

        "user_message_id": "message_dev",
        "status": "running",
        "execution_id": "execution-" + "4" * 24,
        "actor": "CASE-A",
        "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.write(resident_record, foreign_job)
    with (
        mock.patch.object(
            resident_execution_work,
            "execution_snapshot",
            return_value={
                "execution_id": foreign_job["execution_id"],
                "status": "running",
            },
        ),
        mock.patch.object(
            resident_session_backend_domain,
            "handle_resident_native_command",
            return_value={},
        ),
        mock.patch.object(
            slash_commands,
            "handle_slash_command",
            return_value={"handled": False},
        ),
        mock.patch.object(resident_record_domain, "resolve_context_refs", return_value={}),
    ):
        result = _published_delivery(backend.deliver_resident_message_content(
            "resident_aaaaaaaaaaaa",
            "CASE-A",
            "queued in release",
        ))

    assert result["resident_id"] == "resident_aaaaaaaaaaaa"
    assert result["input_state"]["state"] == "queued"
    stored = resident_execution_work.read(resident_record)
    assert stored['execution_id'] == 'execution-' + '4' * 24
    assert len(stored["queue"]) == 1
    queued = stored["queue"][0]
    assert "execution_id" not in queued
    assert queued["input_message"]["content_parts"][0]["text"] == "queued in release"
    repository = resident_conversation_stream.get_repository()
    assert repository.read_messages("resident_aaaaaaaaaaaa") == []
    accepted = repository.message(
        "resident_aaaaaaaaaaaa",
        queued["input_message"]["message_id"],
    )
    assert accepted is not None
    assert _message_text(accepted) == "queued in release"
    assert "activated_at" not in accepted
    assert repository.current_projection("resident_aaaaaaaaaaaa")["queue"][
        "items"
    ][0]["content_preview"] == "queued in release"


def test_starting_session_materializes_message_at_the_execution_tail(
    resident_record: dict[str, object],
) -> None:
    """会话启动时在执行尾部激活消息并保留原创建时间"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    prior_reply = resident_intake_domain.build_message(
        "agent",
        "previous turn completed",
        message_id="msg_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        created_at="2026-08-31T13:49:00Z",
    )
    repository.append_message(resident_id, prior_reply)
    queued_message = resident_intake_domain._resident_user_message(
        resident_id,
        "queued while the previous turn was running",
        "CASE-A",
        message_id="msg_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    )
    queued_message["created_at"] = "2026-08-31T13:46:00Z"
    resident_lifecycle_domain.mark_resident_input_queued(resident_id, queued_message)

    assert [
        item["message_id"] for item in repository.read_messages(resident_id)
    ] == [prior_reply["message_id"]]
    before_cursor = repository.current_projection(resident_id)["cursor"]

    resident_execution_work.publish_conversation_state(
        resident_record,
        {
            'execution_id': "job_starting_session",
            "user_message_id": queued_message["message_id"],
            "input_message": queued_message,
            "started_at": "2026-08-31T13:50:00Z",
        },
    )

    projection = repository.current_projection(resident_id)
    assert projection["state"]["work_phase"] == "starting_session"
    assert [
        item["message_id"] for item in repository.read_messages(resident_id)
    ] == [prior_reply["message_id"], queued_message["message_id"]]
    dispatched = repository.message(resident_id, queued_message["message_id"])
    assert dispatched is not None
    assert dispatched["created_at"] == "2026-08-31T13:46:00Z"
    assert dispatched["activated_at"] == "2026-08-31T13:50:00Z"
    assert repository.input_state(
        resident_id,
        queued_message["message_id"],
    )["state"] == "running"
    event_types = [
        event["event_type"]
        for event in repository.events_after(resident_id, before_cursor)["events"]
    ]
    assert event_types[0] == "conversation.message.appended"
    assert event_types.index("conversation.message.appended") < event_types.index(
        "resident.state.changed"
    )


@pytest.mark.parametrize("initial_snapshot", [None, {"status": "running"}])
@pytest.mark.parametrize("transition", ["metadata", "running", "stopping"])
def test_observed_active_input_is_not_rematerialized_for_state_updates(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    initial_snapshot: dict[str, object] | None,
    transition: str,
) -> None:
    """活跃输入状态更新不重复物化已观察的消息"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    message = resident_intake_domain._resident_user_message(
        resident_id, "one accepted input", "CASE-A",
        message_id="msg_abababababababababababababababab",
    )
    job = {

        "user_message_id": message["message_id"],
        "input_message": message,
        "execution_id": "execution-" + "a" * 24,
        "started_at": "2026-09-08T15:00:00Z",
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.publish_conversation_state(
        resident_record, job, snapshot=initial_snapshot,
    )
    original = repository.message(resident_id, message["message_id"])
    assert original is not None

    def forbidden(*_args, **_kwargs):
        raise AssertionError("State-only updates must not rematerialize an active input")

    monkeypatch.setattr(resident_execution_work, "materialize_conversation_input", forbidden)
    snapshot = {"status": "running"} if transition == "running" else None
    if transition == "stopping":
        job["stop_requested_at"] = "2026-09-08T15:00:01Z"
    resident_execution_work.publish_conversation_state(resident_record, job, snapshot=snapshot)

    assert repository.message(resident_id, message["message_id"]) == original
    assert len(repository.read_messages(resident_id)) == 1
    state = repository.current_projection(resident_id)["state"]
    assert state["active_message_id"] == message["message_id"]
    if transition == "stopping":
        assert state["work_state"] == "stopping"


def test_input_activation_failure_does_not_advance_public_checkpoint(
    resident_record: dict[str, object], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """输入激活失败时不推进公开状态且重试可完成"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    message = resident_intake_domain._resident_user_message(
        resident_id, "retry activation", "CASE-A",
        message_id="msg_acacacacacacacacacacacacacacacac",
    )
    job = {
        'execution_id': "job_activation_retry", "user_message_id": message["message_id"],
        "input_message": message, "started_at": "2026-09-08T15:00:00Z",
    }
    before = repository.current_projection(resident_id)["state"]
    with monkeypatch.context() as patch:
        def fail(*_args, **_kwargs):
            raise RuntimeError("input commit failed")
        patch.setattr(resident_execution_work, "materialize_conversation_input", fail)
        with pytest.raises(RuntimeError, match="input commit failed"):
            resident_execution_work.publish_conversation_state(resident_record, job)
    assert repository.current_projection(resident_id)["state"] == before
    resident_execution_work.publish_conversation_state(resident_record, job)
    assert repository.message(resident_id, message["message_id"]) is not None
    assert repository.current_projection(resident_id)["state"]["active_message_id"] == message["message_id"]


def test_new_active_input_has_its_own_materialization_checkpoint(
    resident_record: dict[str, object],
) -> None:
    """新活跃输入各自拥有独立的物化检查点"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    expected_ids = []
    for marker in ("d", "e"):
        message = resident_intake_domain._resident_user_message(
            resident_id, "next input " + marker, "CASE-A", message_id="msg_" + marker * 32,
        )
        job = {
            'execution_id': "job_new_input_" + marker,
            "user_message_id": message["message_id"], "input_message": message,
            "started_at": "2026-09-08T15:00:00Z",
        }
        resident_execution_work.publish_conversation_state(resident_record, job)
        expected_ids.append(message["message_id"])
    assert [message["message_id"] for message in repository.read_messages(resident_id)] == expected_ids
    assert repository.current_projection(resident_id)["state"]["active_message_id"] == expected_ids[-1]


def test_waiting_capacity_keeps_message_out_of_chat_until_starting_session(
    resident_record: dict[str, object],
) -> None:
    """等待容量的消息在会话启动前不进入聊天记录"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    queued_message = resident_intake_domain._resident_user_message(
        resident_id,
        "wait for scheduler capacity",
        "CASE-A",
        message_id="msg_cccccccccccccccccccccccccccccccc",
    )
    job = {

        "user_message_id": queued_message["message_id"],
        "input_message": queued_message,
        "execution_id": "execution-" + "7" * 24,
        "started_at": "2026-08-31T14:00:00Z",
     "submitted_at": "2026-10-14T00:00:00Z"}

    resident_execution_work.publish_conversation_state(
        resident_record,
        job,
        snapshot={
            "status": "queued",
            "scheduler": {"queue_reason": "capacity"},
        },
    )

    assert repository.read_messages(resident_id) == []
    assert repository.current_projection(resident_id)["state"][
        "work_phase"
    ] == "waiting_capacity"

    resident_execution_work.publish_conversation_state(
        resident_record,
        job,
        snapshot={
            "status": "queued",
            "scheduler": {"queue_reason": "ready"},
        },
    )

    assert [
        item["message_id"] for item in repository.read_messages(resident_id)
    ] == [queued_message["message_id"]]
    assert repository.current_projection(resident_id)["state"][
        "work_phase"
    ] == "starting_session"


def test_bound_execution_without_snapshot_does_not_invent_running(
    resident_record: dict[str, object],
) -> None:
    """已绑定执行缺少快照时保持排队及会话启动阶段"""
    repository = resident_conversation_stream.get_repository()
    job = {

        "user_message_id": "msg_12341234123412341234123412341234",
        "execution_id": "execution-" + "9" * 24,
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.publish_conversation_state(resident_record, job)
    state = repository.current_projection(str(resident_record["resident_id"]))["state"]
    assert state["work_state"] == "queued"
    assert state["work_phase"] == "starting_session"


@pytest.mark.parametrize("snapshot", [
    {"status": "claimed"},
    {"status": "running"},
    {"status": "queued", "scheduler": {"queue_reason": "ready"}},
    {"status": "queued", "scheduler": {"queue_reason": "session_binding"}},
    {"status": "queued", "scheduler": {"queue_reason": "dispatch_interval"}},
    {"status": "queued", "scheduler": {"queue_reason": "capacity"}},
])
def test_snapshotless_update_preserves_observed_dispatch_state(
    resident_record: dict[str, object], snapshot: dict[str, object],
) -> None:
    """无快照更新保留已观察的调度状态并允许停止"""
    repository = resident_conversation_stream.get_repository()
    resident_id = str(resident_record["resident_id"])
    job = {

        "user_message_id": "msg_56785678567856785678567856785678",
        "execution_id": "execution-" + "9" * 24,
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.publish_conversation_state(resident_record, job, snapshot=snapshot)
    expected = dict(repository.current_projection(resident_id)["state"])
    resident_execution_work.publish_conversation_state(resident_record, job)
    assert repository.current_projection(resident_id)["state"] == expected
    job["stop_requested_at"] = "2026-09-07T08:00:00Z"
    resident_execution_work.publish_conversation_state(resident_record, job)
    assert repository.current_projection(resident_id)["state"]["work_state"] == "stopping"


def test_dispatch_interval_has_its_own_wait_phase(
    resident_record: dict[str, object],
) -> None:
    """调度间隔等待投影为独立等待阶段"""
    repository = resident_conversation_stream.get_repository()
    resident_execution_work.publish_conversation_state(resident_record, {

        "user_message_id": "msg_98769876987698769876987698769876",
        "execution_id": "execution-" + "9" * 24,
     "submitted_at": "2026-10-14T00:00:00Z"}, snapshot={"status": "queued", "scheduler": {"queue_reason": "dispatch_interval"}})
    state = repository.current_projection(str(resident_record["resident_id"]))["state"]
    assert state["work_state"] == "queued"
    assert state["work_phase"] == "waiting_dispatch"


def test_recovery_snapshot_can_requeue_the_same_running_input(
    resident_record: dict[str, object],
) -> None:
    """恢复快照可将同一运行输入重新置为容量等待"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    message = resident_intake_domain._resident_user_message(
        resident_id, "recover the accepted input", "CASE-A",
        message_id="msg_76547654765476547654765476547654",
    )
    job = {
         "user_message_id": message["message_id"],
        "input_message": message, "execution_id": "execution-" + "9" * 24,
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.publish_conversation_state(resident_record, job, snapshot={"status": "running"})
    resident_execution_work.publish_conversation_state(resident_record, job, snapshot={
        "status": "queued", "scheduler": {"queue_reason": "capacity"},
    })
    state = repository.current_projection(resident_id)["state"]
    assert state["work_state"] == "queued"
    assert state["work_phase"] == "waiting_capacity"
    assert state["active_message_id"] == message["message_id"]
    assert [item["message_id"] for item in repository.read_messages(resident_id)] == [message["message_id"]]


def test_waiting_capacity_is_not_overwritten_by_snapshotless_job_write(
    resident_record: dict[str, object],
) -> None:
    """无快照任务写入保留容量等待且不提前激活消息"""
    resident_id = str(resident_record["resident_id"])
    repository = resident_conversation_stream.get_repository()
    queued_message = resident_intake_domain._resident_user_message(
        resident_id,
        "wait for scheduler capacity",
        "CASE-A",
        message_id="msg_dddddddddddddddddddddddddddddddd",
    )
    job = {

        "user_message_id": queued_message["message_id"],
        "input_message": queued_message,
        "execution_id": "execution-" + "8" * 24,
        "started_at": "2026-08-31T14:00:00Z",
        "queue": [
            {
                'execution_id': "job_next",
                "user_message_id": "msg_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
            }
        ],
     "submitted_at": "2026-10-14T00:00:00Z"}

    resident_execution_work.publish_conversation_state(
        resident_record,
        job,
        snapshot={
            "status": "queued",
            "scheduler": {"queue_reason": "capacity"},
        },
    )
    resident_execution_work.publish_conversation_state(resident_record, job)

    projection = repository.current_projection(resident_id)
    assert projection["state"] == {
        "work_state": "queued",
        "active_message_id": queued_message["message_id"],
        "queued_count": 1,
        "intake_state": "open",
        "work_phase": "waiting_capacity",
    }
    assert repository.read_messages(resident_id) == []
    assert repository.input_state(
        resident_id,
        queued_message["message_id"],
    )["state"] == "queued"


def test_lazy_resident_first_message_creates_session_job(
    resident_record: dict[str, object],
) -> None:
    """延迟创建的 Resident 首条消息建立会话任务且延后组装提示"""
    resident_backend.delete_session_identity(resident_record)
    resident_record.update({
        "session_state": "pending_create",
    })
    resident_record_domain.save_resident_record(resident_record)
    with (
        mock.patch.object(
            slash_commands,
            "handle_slash_command",
            return_value={"handled": False},
        ),
        mock.patch.object(resident_record_domain, "resolve_context_refs", return_value={}),
        mock.patch.object(resident_registration_domain, "build_resident_prompt", side_effect=AssertionError("prompt built during admission")),
    ):
        result = _published_delivery(backend.deliver_resident_message_content(
            "resident_aaaaaaaaaaaa",
            "CASE-A",
            "开始第一轮。",
            message_id="msg_0123456789abcdef",
    ))

    assert result["resident_id"] == "resident_aaaaaaaaaaaa"
    assert result["message"]["message_id"] == "msg_0123456789abcdef"
    assert result["input_state"]["state"] == "running"
    assert result["state"]["work_phase"] == "starting_session"
    submitted_job = resident_execution_work.read(resident_record)
    assert submitted_job["bootstrap_session"] is True
    assert submitted_job["raw_message"] == "开始第一轮。"
    assert "prompt_builder_version" not in submitted_job
    assert "actual_sent_prompt" not in submitted_job
    assert resident_conversation_stream.get_repository().message(
        "resident_aaaaaaaaaaaa", "msg_0123456789abcdef"
    ) is not None
    stored = resident_execution_work.read(resident_record)
    assert "status" not in stored
    assert "queue_reason" not in stored
    assert stored["user_message_id"] == "msg_0123456789abcdef"


@pytest.fixture
def input_delivery(resident_record, monkeypatch):
    monkeypatch.setattr(resident_session_backend_domain, "handle_resident_native_command", lambda *a, **kw: {})
    monkeypatch.setattr(slash_commands, "handle_slash_command", lambda *a, **kw: {"handled": False})
    monkeypatch.setattr(resident_record_domain, "resolve_context_refs", lambda *a, **kw: {})
    return resident_record


@pytest.mark.parametrize("policy", ["notify_only", "enqueue_without_wake", "unknown"])
def test_removed_delivery_modes_are_rejected_without_persistence(input_delivery, policy):
    """已移除的投递模式在持久化前被拒绝"""
    with pytest.raises(ValueError, match="activation_policy must be"):
        backend.deliver_resident_message_content(
            input_delivery["resident_id"], "CASE-A", "Do not silently start this", activation_policy=policy,
        )
    assert resident_execution_work.read(input_delivery) == {}
    assert resident_conversation_stream.get_repository().read_messages(input_delivery["resident_id"]) == []


def test_delivery_instructions_share_the_two_activation_modes():
    """投递与转发指令共用两种激活模式"""
    from management_portal.hatg.catalog import instruction_catalog
    from management_portal.society_service import resident_message_contract

    declared = {
        name: tuple(parameter.enum)
        for name, declaration in instruction_catalog().items()
        for parameter in declaration.parameters
        if parameter.name == "activation_policy"
    }
    assert set(declared) == {"DeliverMessage", "ForwardMessages"}
    assert set(declared.values()) == {resident_message_contract.ACTIVATION_POLICIES}


def test_normal_delivery_starts_idle_and_advances_busy_queue(input_delivery, monkeypatch):
    """普通投递启动空闲任务且忙碌时按序排队提升"""
    resident_id = input_delivery["resident_id"]
    prompt_builder = resident_registration_domain.build_resident_prompt
    monkeypatch.setattr(resident_registration_domain, "build_resident_prompt", lambda *_: (_ for _ in ()).throw(AssertionError("prompt built during admission")))
    first = _published_delivery(backend.deliver_resident_message_content(resident_id, "CASE-A", "First"))
    job = resident_execution_work.read(input_delivery)
    assert "prompt_builder_version" not in job
    assert "actual_sent_prompt" not in job
    assert resident_conversation_stream.get_repository().message(resident_id, first["message_id"]) is not None
    monkeypatch.setattr(resident_registration_domain, "build_resident_prompt", prompt_builder)
    prepared = resident_execution_domain._portable_context_execution(input_delivery, job)
    assert prepared["prompt_builder_version"] == resident_execution_work.PROMPT_BUILDER_VERSION
    assert "<user-message>\nFirst\n</user-message>" in prepared["actual_sent_prompt"]
    assert resident_execution_work.execution_id(job)
    assert resident_execution_work.continuation_needed(input_delivery, job)
    assert not resident_execution_work.is_execution_held(job)
    second = _published_delivery(backend.deliver_resident_message_content(resident_id, "CASE-A", "Second"))
    third = _published_delivery(backend.deliver_resident_message_content(resident_id, "CASE-A", "Third"))
    job = resident_execution_work.read(input_delivery)
    assert job["user_message_id"] == first["message_id"]
    queue = resident_execution_work.normalized_queue(job)
    assert [item["user_message_id"] for item in queue] == [second["message_id"], third["message_id"]]
    assert [message["message_id"] for message in resident_conversation_stream.get_repository().read_messages(resident_id)] == [first["message_id"]]
    assert resident_conversation_stream.get_repository().input_state(resident_id, second["message_id"])["state"] == "queued"
    assert resident_conversation_stream.get_repository().input_state(resident_id, third["message_id"])["state"] == "queued"
    promoted = resident_execution_work.promote_queue_item(job, queue[0], queue[1:], resident_domain_common.now_iso())
    assert promoted["user_message_id"] == second["message_id"]
    assert "actual_sent_prompt" not in promoted
    assert "prompt_built_at" not in promoted
    assert not resident_execution_work.is_execution_held(promoted)
    assert resident_execution_work.continuation_needed(input_delivery, promoted)


@pytest.mark.parametrize("start", ["idle", "reopened", "promoted"])
def test_closing_intake_during_session_start_keeps_active_input_out_of_queue(input_delivery, start):
    """关闭接单不将正在启动 Session 的输入退回队列

    验证首次启动、重新开接单和队列提升后，关闭接单仍保留当前输入，后续输入继续等待，完整与轻量状态读取保持一致。

    :module: resident.conversation
    :importance: P1
    """
    resident_id = input_delivery["resident_id"]
    repository = resident_conversation_stream.get_repository()

    def intake(transition):
        resident_protocol.change_resident_state(resident_id, "CASE-A", state_domain="intake", transition=transition)

    if start == "reopened":
        intake("close")
    first = _published_delivery(backend.deliver_resident_message_content(resident_id, "CASE-A", "Start this session"))
    if start == "reopened":
        intake("open")
        assert resident_execution_domain._prepare_resident_continuation(
            resident_record_domain.find_resident(resident_id),
        )
    elif start == "promoted":
        first = _published_delivery(backend.deliver_resident_message_content(resident_id, "CASE-A", "Next session"))
        job = resident_execution_work.read(input_delivery)
        queue = resident_execution_work.normalized_queue(job)
        resident_execution_work.promote_queue_item(job, queue[0], [], resident_domain_common.now_iso())
        resident_execution_work.write(input_delivery, job)
    assert repository.published_state(resident_id)["work_phase"] == "starting_session"
    assert not resident_execution_work.submitted_execution_id(resident_execution_work.read(input_delivery))

    intake("close")
    snapshot = resident_protocol.get_resident_conversation_snapshot(resident_id, "CASE-A")
    assert snapshot["queue"]["items"] == []
    assert snapshot["state"]["active_message_id"] == first["message_id"]
    assert snapshot["state"]["queued_count"] == 0
    for job in (resident_execution_work.read(input_delivery),
                resident_execution_work.read_control(resident_id)["state"]):
        assert job["intake_state"] == "closed"
        assert not resident_execution_work.is_execution_held(job)
        assert resident_execution_work.continuation_needed(input_delivery, job)

    second = _published_delivery(backend.deliver_resident_message_content(resident_id, "CASE-A", "Wait until intake reopens"))
    snapshot = resident_protocol.get_resident_conversation_snapshot(resident_id, "CASE-A")
    assert [item["message_id"] for item in snapshot["queue"]["items"]] == [second["message_id"]]
    assert snapshot["state"]["queued_count"] == 1
    assert snapshot["state"]["work_phase"] == "starting_session"
    assert repository.input_state(resident_id, first["message_id"])["state"] == "running"
    assert repository.input_state(resident_id, second["message_id"])["state"] == "queued"
    assert sum(message["message_id"] == first["message_id"] for message in repository.read_messages(resident_id)) == 1

    # A new head must still obey the closed gate after the current turn ends.
    job = resident_execution_work.read(input_delivery)
    resident_execution_work.promote_queue_item(job, job["queue"][0], [], resident_domain_common.now_iso())
    resident_execution_work.write(input_delivery, job)
    assert resident_execution_work.is_execution_held(resident_execution_work.read(input_delivery))
    assert second["message_id"] not in [message["message_id"] for message in repository.read_messages(resident_id)]
    assert repository.published_state(resident_id)["active_message_id"] == ""


@pytest.mark.parametrize("policy", ["notify_only", "enqueue_without_wake", "unsupported_future_policy"])
def test_unknown_persisted_policy_cannot_replay_work(input_delivery, policy):
    """未知持久化激活策略阻止工作重放并显示原因"""
    message_id = "msg_ffffffffffffffffffffffffffffffff"
    job = {
        'execution_id': "job_old", "actor": "CASE-A", "activation_policy": policy,
        "user_message_id": message_id,
        "input_message": {"message_id": message_id, "role": "user", "author_id": "CASE-A",
                          "content_parts": [{"type": "text", "text": "Legacy pending input"}]},
    }
    resident_execution_work.write(input_delivery, job)
    persisted = resident_execution_work.read(input_delivery)
    assert resident_execution_work.is_execution_held(persisted)
    assert not resident_execution_work.continuation_needed(input_delivery, persisted)
    assert resident_execution_work.public_execution_state(persisted)["queue_reason"] == resident_execution_work.INVALID_ACTIVATION_REASON


@pytest.mark.parametrize("status", ["delivered", "pending", "next_turn"])
def test_direct_injection_reuses_queue_delivery_and_identity(input_delivery, monkeypatch, status):
    """直接注入复用队列投递与消息身份且区分投递状态"""
    resident_id = input_delivery["resident_id"]
    resident_execution_work.write(input_delivery, {
         "actor": "CASE-A",
        "execution_id": "execution-111111111111111111111111", "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"})
    service = mock.Mock()
    service.status.return_value = {"status": "running"}
    service.input_control.return_value = None
    if status == "next_turn":
        service.append_input.side_effect = ValueError("execution has no active turn")
    else:
        service.append_input.return_value = {"status": status, "control_id": "control-test"}
    monkeypatch.setattr(execution_port, "service", lambda: service)
    message_id = "msg_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    payload = {
        "resident_id": resident_id, "activation_policy": "inject_active_turn",
        "message": {"message_id": message_id, "role": "user",
                    "content_parts": [{"type": "text", "text": "Additional input"}]},
    }
    result = _published_delivery(resident_protocol.deliver_resident_input(payload, "CASE-A"))
    assert result["message_id"] == message_id
    service.append_input.assert_called_once()
    job = resident_execution_work.read(input_delivery)
    assert job['execution_id'] == 'execution-111111111111111111111111'
    assert job["execution_id"] == "execution-111111111111111111111111"
    service.submit.assert_not_called()
    service.cancel.assert_not_called()
    repository = resident_conversation_stream.get_repository()
    if status == "delivered":
        assert result["accepted"]
        assert job["queue"] == []
        assert repository.input_state(resident_id, message_id)["state"] == "completed"
        assert len(repository.read_messages(resident_id)) == 1
        _published_delivery(resident_protocol.deliver_resident_input(payload, "CASE-A"))
        service.append_input.assert_called_once()
    else:
        assert result["accepted"]
        assert [item["user_message_id"] for item in job["queue"]] == ([message_id] if status == "next_turn" else [])
        assert repository.input_state(resident_id, message_id)["state"] == "queued"
        if status == "pending":
            assert resident_execution_work.send_now_inputs(job)[0]["user_message_id"] == message_id
            assert not job.get("send_now_next_turn")
        else:
            assert job["queue"][0]["user_message_id"] == message_id
            assert not resident_execution_work.send_now_inputs(job)


def test_direct_injection_requires_active_work_before_acceptance(input_delivery):
    """无活跃任务时直接注入在接收输入前被拒绝"""
    with pytest.raises(ValueError, match="没有可注入"):
        _published_delivery(backend.deliver_resident_message_content(
            input_delivery["resident_id"], "CASE-A", "Extra", activation_policy="inject_active_turn",
        ))
    assert resident_execution_work.read(input_delivery) == {}
    assert resident_conversation_stream.get_repository().user_message_counts(
        [input_delivery["resident_id"]],
    ).get(input_delivery["resident_id"], 0) == 0


def test_direct_injection_rejects_unsupported_provider(input_delivery, monkeypatch):
    """Provider 不支持当前轮输入时直接注入被拒绝"""
    from dataclasses import replace

    provider = resident_execution_provider.provider_for(input_delivery)
    monkeypatch.setattr(provider, "capabilities", replace(provider.capabilities, supports_active_turn_input=False))
    monkeypatch.setattr(resident_execution_provider, "provider_for", lambda record: provider)
    with pytest.raises(ValueError, match="不支持注入"):
        _published_delivery(backend.deliver_resident_message_content(
            input_delivery["resident_id"], "CASE-A", "Extra", activation_policy="inject_active_turn",
        ))
    assert resident_execution_work.read(input_delivery) == {}


def test_direct_injection_turn_completion_race_keeps_one_queued_input(input_delivery, monkeypatch):
    """直接注入与轮次完成竞态只保留一个待运行输入"""
    resident_execution_work.write(input_delivery, {
         "actor": "CASE-A",
        "execution_id": "execution-111111111111111111111111", "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"})
    def finish_while_preparing(*args, **kwargs):
        job = resident_execution_work.read(input_delivery)
        job["outcome"] = "completed"
        resident_execution_work.write(input_delivery, job)
        return {}
    monkeypatch.setattr(resident_record_domain, "resolve_context_refs", finish_while_preparing)
    result = _published_delivery(backend.deliver_resident_message_content(
        input_delivery["resident_id"], "CASE-A", "Extra", activation_policy="inject_active_turn",
    ))
    assert result["injected"] is False
    assert result["delivery_status"] == "next_turn"
    job = resident_execution_work.read(input_delivery)
    assert job["user_message_id"] == result["message_id"]
    assert job["queue"] == []
    assert not resident_execution_work.is_execution_held(job)
    assert resident_execution_work.continuation_needed(input_delivery, job)


@pytest.mark.parametrize("generic", [False, True])
@pytest.mark.parametrize("with_queue", [False, True])
def test_cancel_closed_intake_first_input_needs_no_execution(resident_record, generic, with_queue):
    """接收关闭时取消首条输入无需执行服务且保留后续队列

    :importance: P1"""
    resident_id = str(resident_record["resident_id"])
    resident_protocol.change_resident_state(resident_id, "CASE-A", state_domain="intake", transition="close")
    with (
        mock.patch.object(resident_session_backend_domain, "handle_resident_native_command", return_value={}),
        mock.patch.object(slash_commands, "handle_slash_command", return_value={"handled": False}),
        mock.patch.object(resident_record_domain, "resolve_context_refs", return_value={}),
        mock.patch.object(resident_registration_domain, "build_resident_prompt", return_value="held prompt"),
    ):
        delivery = backend.deliver_resident_message_content(resident_id, "CASE-A", "hold this message")
        following = backend.deliver_resident_message_content(resident_id, "CASE-A", "keep this message") if with_queue else None
    assert resident_execution_work.is_execution_held(resident_execution_work.read(resident_record))
    cancel = resident_protocol.cancel_resident_input if generic else resident_protocol.cancel_resident_execution
    with mock.patch.object(execution_port, "service", side_effect=AssertionError("No execution was admitted")):
        cancel(resident_id, "CASE-A", delivery["message_id"])
    assert resident_protocol.get_resident_input_state(resident_id, "CASE-A", delivery["message_id"])["state"] == "cancelled"
    assert resident_execution_work.is_active(resident_execution_work.read(resident_record)) is with_queue
    if following:
        assert resident_protocol.get_resident_input_state(resident_id, "CASE-A", following["message_id"])["state"] == "queued"
        assert resident_execution_work.read(resident_record)["user_message_id"] == following["message_id"]
    assert resident_protocol.get_resident_state(resident_id, "CASE-A")["state"]["intake_state"] == "closed"


def test_closed_resident_intake_holds_message_until_reopened(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resident 接收关闭时暂存消息直至重新打开"""
    monkeypatch.setattr(ExecutionService, "binding_quota_states",
                        lambda _self, bindings: {key: {"state": "open", "reason": ""} for key in bindings})
    resident_protocol.change_resident_state(
        "resident_aaaaaaaaaaaa",
        "CASE-A",
        state_domain="intake",
        transition="close",
    )
    with (
        mock.patch.object(
            resident_session_backend_domain,
            "handle_resident_native_command",
            return_value={},
        ),
        mock.patch.object(
            slash_commands,
            "handle_slash_command",
            return_value={"handled": False},
        ),
        mock.patch.object(resident_record_domain, "resolve_context_refs", return_value={}),
        mock.patch.object(resident_registration_domain, "build_resident_prompt", return_value="held prompt"),
    ):
        result = _published_delivery(resident_protocol.deliver_resident_input({
            "resident_id": "resident_aaaaaaaaaaaa",
            "message": {
                "message_id": "msg_22222222222222222222222222222222",
                "role": "user",
                "content_parts": [{"type": "text", "text": "hold this message"}],
            },
        }, "CASE-A"))

    assert result["resident_id"] == "resident_aaaaaaaaaaaa"
    assert result["input_state"]["state"] == "queued"

    held = resident_execution_work.read(resident_record)
    state_view = resident_protocol.get_resident_state(
        "resident_aaaaaaaaaaaa",
        "CASE-A",
    )
    assert state_view["resident_id"] == "resident_aaaaaaaaaaaa"
    assert state_view["controls"]["can_send"] is True
    assert state_view["controls"]["send_hint"]
    assert isinstance(state_view["cursor"], int)
    assert state_view["state"]["intake_state"] == "closed"
    assert state_view["controls"]["can_send"] is True
    assert resident_execution_work.public_execution_state(held) == {
        "status": "held",
        "queue_reason": resident_state.INTAKE_CLOSED_QUEUE_REASON,
    }
    assert held["intake_state"] == "closed"
    assert held.get("execution_id")
    assert not resident_execution_work.submitted_execution_id(held)
    assert resident_execution_work.continuation_needed(
        resident_record,
        held,
    ) is False

    response = resident_protocol.change_resident_state(
        "resident_aaaaaaaaaaaa",
        "CASE-A",
        state_domain="intake",
        transition="open",
    )
    reopened = resident_execution_work.read(resident_record)
    assert response["intake_state"] == "open"
    assert response["state_domain"] == "intake"
    assert response["field"] == "intake_state"
    assert response["previous_state"] == "closed"
    assert response["state"] == "open"
    assert response["changed"] is True
    assert "status" not in reopened
    assert "queue_reason" not in reopened
    assert reopened["intake_state"] == "open"
    assert resident_execution_work.continuation_needed(resident_record, reopened)
    # Opening changes the manual gate; the execution owner reserves the
    # request identity when it prepares the resumed work.
    current_record = resident_record_domain.find_resident("resident_aaaaaaaaaaaa")
    assert resident_execution_domain._prepare_resident_continuation(current_record)
    assert resident_execution_work.execution_id(resident_execution_work.read(current_record))
    reopened_view = resident_protocol.get_resident_state("resident_aaaaaaaaaaaa", "CASE-A")
    assert reopened_view["state"]["intake_state"] == "open"
    assert reopened_view["controls"]["can_send"] is True
    assert reopened_view["controls"]["send_hint"] == ""


def test_fork_intake_state_uses_protocol_state_and_holds_promoted_queue_item(
    resident_record: dict[str, object],
) -> None:
    """分叉 Resident 的接收状态以协议状态为准"""
    created_resident_id = "resident_bbbbbbbbbbbb"
    registry = resident_lineage.snapshot(resident_record)
    registry["residents"].append({
        "resident_id": created_resident_id,
        "source_resident_id": resident_record["resident_id"],
        "lineage_id": resident_record["lineage_id"],
        "created_by": "CASE-A",
        "backend": "codex",
    })
    resident_fork_domain._persist_derived_resident(resident_record, registry["residents"][-1])

    response = resident_protocol.change_resident_state(
        created_resident_id,
        "CASE-A",
        state_domain="intake",
        transition="close",
    )

    updated_registry = resident_lineage.snapshot(resident_record)
    derived_resident = resident_lineage.find_in_snapshot(updated_registry, created_resident_id)
    assert response["intake_state"] == "closed"
    assert derived_resident is not None
    assert "intake_state" not in derived_resident
    assert resident_protocol.get_resident_state(created_resident_id, "CASE-A")["state"][
        "intake_state"
    ] == "closed"
    exact_resident = resident_record_domain.find_resident(created_resident_id)
    assert exact_resident is not None


def test_similar_failed_message_accepts_a_new_retry_job_generation(
    resident_record: dict[str, object],
) -> None:
    """失败后收到相同内容时创建新执行并清除旧轮状态"""
    failed = {

        "user_message_id": "message_failed_previous_generation",
        "raw_message": "repeat this exact role prompt",
        "effective_message": "repeat this exact role prompt",
        "message": "repeat this exact role prompt",
        "actor": "CASE-A",
        "outcome": "failed",
        "error": "previous attempt failed",
        "finished_at": "2026-07-29T12:00:00Z",
        "updated_at": "2026-07-29T12:00:00Z",
        "execution_id": "execution-previous-attempt",
        'execution_id': "execution-previous-attempt",
        "execution_request": {"execution_id": "execution-previous-attempt"},
        "failure_message_id": "runtime-error-job_failed_previous_generation",
        "failure_message_persisted_at": "2026-07-29T12:00:00Z",
        "continuation_id": "resident-continuation:previous-attempt",
        "continuation_started_at": "2026-07-29T11:59:00Z",
        "completion_applied_at": "2026-07-29T12:00:00Z",
        "completion_applied_execution_id": "execution-previous-attempt",
        "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.write(resident_record, failed)
    submit = mock.Mock()

    with (
        mock.patch.object(
            resident_session_backend_domain,
            "handle_resident_native_command",
            return_value={},
        ),
        mock.patch.object(
            slash_commands,
            "handle_slash_command",
            return_value={"handled": False},
        ),
        mock.patch.object(resident_record_domain, "resolve_context_refs", return_value={}),
    ):
        result = _published_delivery(backend.deliver_resident_message_content(
            "resident_aaaaaaaaaaaa",
            "CASE-A",
            "repeat this exact role prompt",
        ))

    message_id = str(resident_execution_work.read(resident_record)['execution_id'])
    assert result["resident_id"] == "resident_aaaaaaaaaaaa"
    assert result["message"]["message_id"] == result["message_id"]
    assert result["input_state"]["state"] == "running"
    assert result["state"]["work_phase"] == "starting_session"
    assert message_id.startswith("execution-")
    assert message_id != failed['execution_id']
    stored = resident_execution_work.read(resident_record)
    assert stored['execution_id'] == message_id
    assert stored["execution_id"] == message_id
    assert not stored.get("submitted_at")
    assert stored['execution_id'] != "execution-previous-attempt"
    assert stored.get("execution_request", {}).get("execution_id") != (
        "execution-previous-attempt"
    )
    assert "failure_message_id" not in stored
    assert "failure_message_persisted_at" not in stored
    assert "continuation_id" not in stored
    assert "continuation_started_at" not in stored
    assert "completion_applied_at" not in stored
    assert "completion_applied_execution_id" not in stored
    submit.assert_not_called()
    submitted = stored
    assert submitted['execution_id'] == message_id
    assert stored["raw_message"] == "repeat this exact role prompt"


def test_stop_job_is_accepted_before_execution_binding(
    resident_record: dict[str, object],
) -> None:
    """执行绑定前接受停止请求且阻止后续提交"""
    resident_id = str(resident_record["resident_id"])
    message_id = "msg_55555555555555555555555555555555"
    repository = resident_conversation_stream.get_repository()
    repository.append_message(
        resident_id,
        {
            "message_id": message_id,
            "role": "user",
            "content_parts": [{"type": "text", "text": "stop before binding"}],
            "author_id": "CASE-A",
            "created_at": "2026-08-28T00:00:00Z",
        },
    )
    resident_execution_work.write(
        resident_record,
        {
            'execution_id': "execution-" + "2" * 24,
            "user_message_id": message_id,
            "actor": "CASE-A",
            "message": "stop before binding",
            "actual_sent_prompt": "stop before binding",
            "prompt_cwd": str(resident_record["default_cwd"]),
            "queue": [],
        },
    )
    pending = resident_execution_work.read(resident_record)
    pending_request = resident_execution_domain._build_resident_execution_request(
        resident_record,
        pending,
    )

    with (
        mock.patch.object(execution_port, "service") as service,
        mock.patch.object(resident_record_domain, "save_resident_record") as save_resident,
    ):
        result = resident_protocol.cancel_resident_execution(
            resident_id,
            "CASE-A",
            message_id,
        )

    assert result["cancelled"] is False
    assert result["cancellation_requested"] is True
    assert result["backend_cancelled"] is False
    service.assert_not_called()
    save_resident.assert_not_called()
    stored = resident_execution_work.read(resident_record)
    assert stored["stop_requested_by"] == "CASE-A"
    assert stored["stop_requested_at"]
    from management_portal.society_service import resident_delivery_work
    resident_delivery_work.drain(str(resident_record["resident_id"]))
    assert repository.current_projection(resident_id)["state"]["work_state"] == "stopping"
    with pytest.raises(backend.ResidentTurnCancelled):
        resident_execution_domain.durable_resident_execution_request(
            resident_record,
            stored,
            lambda: pending_request,
        )
    with (
        mock.patch.object(resident_execution_domain, "_build_resident_execution_request") as build,
        pytest.raises(backend.ResidentTurnCancelled),
    ):
        resident_execution_domain._send_resident_execution(
            resident_record,
            stored,
        )
    build.assert_not_called()
    prepared = resident_execution_domain._prepare_resident_continuation(resident_record)
    assert prepared is not None
    _, continuation_id = prepared
    with (
        mock.patch.object(resident_execution_domain, "_build_resident_execution_request") as build,
        mock.patch.object(resident_execution_domain, "_notify_lineage_message_terminal"),
    ):
        resident_execution_domain.run_resident_execution(resident_id, continuation_id)
    build.assert_not_called()
    assert resident_execution_work.read(resident_record) == {}
    assert repository.input_state(resident_id, message_id)["state"] == "cancelled"
    assert repository.current_projection(resident_id)["state"]["work_state"] == "idle"


def test_stop_racing_with_execution_submission_cancels_bound_execution(
    resident_record: dict[str, object],
) -> None:
    """停止与执行提交竞态取消最终绑定的执行"""
    resident_id = str(resident_record["resident_id"])
    message_id = "msg_56565656565656565656565656565656"
    repository = resident_conversation_stream.get_repository()
    repository.append_message(
        resident_id,
        {
            "message_id": message_id,
            "role": "user",
            "content_parts": [{"type": "text", "text": "stop during submit"}],
            "author_id": "CASE-A",
            "created_at": "2026-08-28T00:00:00Z",
        },
    )
    job = {
        'execution_id': "execution-" + "3" * 24,
        "user_message_id": message_id,
        "actor": "CASE-A",
        "message": "stop during submit",
        "actual_sent_prompt": "stop during submit",
        "prompt_cwd": str(resident_record["default_cwd"]),
        "queue": [],
    }
    resident_execution_work.write(resident_record, job)
    service = mock.Mock()

    def submit(request: object) -> dict[str, object]:
        current = resident_execution_work.read(resident_record)
        current.update({
            'execution_id': "execution-" + "3" * 24,
            "stop_requested_by": "CASE-A",
            "stop_requested_at": "2026-08-28T00:00:01Z",
        })
        resident_execution_work.write(resident_record, current)
        return {"execution_id": str(request.execution_id)}

    def wait(execution_id: str, **kwargs: object) -> dict[str, object]:
        assert kwargs["cancellation_requested"] is None
        return {
            "execution_id": execution_id,
            "status": "cancelled",
            "scheduler": {"cancel_requested": True},
            "result": {"status": "cancelled", "error": "cancelled"},
        }

    service.submit.side_effect = submit
    service.wait.side_effect = wait

    with (
        mock.patch.object(execution_port, "service", return_value=service),
        pytest.raises(backend.ResidentTurnCancelled),
    ):
        resident_execution_domain._send_resident_execution(
            resident_record,
            resident_execution_work.read(resident_record),
        )

    service.submit.assert_called_once()
    stored = resident_execution_work.read(resident_record)
    assert stored['execution_id'] == "execution-" + "3" * 24
    assert stored["execution_id"]
    service.cancel.assert_called_once_with(
        resident_execution_work.submitted_execution_id(stored),
        actor="CASE-A",
        reason="execution cancelled by caller",
    )


def test_stop_after_request_construction_prevents_execution_submission(
    resident_record: dict[str, object],
) -> None:
    """构建请求后收到停止意图时阻止执行提交"""
    resident_id = str(resident_record["resident_id"])
    message_id = "msg_57575757575757575757575757575757"
    job = {
        'execution_id': "job_stop_after_request_construction",
        "user_message_id": message_id,
        "actor": "CASE-A",
        "message": "stop before submit",
        "actual_sent_prompt": "stop before submit",
        "prompt_cwd": str(resident_record["default_cwd"]),
        "queue": [],
    }
    resident_execution_work.write(resident_record, job)
    stored = resident_execution_work.read(resident_record)
    request = resident_execution_domain._build_resident_execution_request(
        resident_record,
        stored,
    )
    service = mock.Mock()

    def finish_request_construction(*_args: object) -> object:
        current = resident_execution_work.read(resident_record)
        current.update({
            'execution_id': "job_admitted_after_request_construction",
            "stop_requested_by": "CASE-A",
            "stop_requested_at": "2026-08-28T00:00:01Z",
        })
        resident_execution_work.write(resident_record, current)
        return request

    with (
        mock.patch.object(
            resident_execution_domain,
            "durable_resident_execution_request",
            side_effect=finish_request_construction,
        ),
        mock.patch.object(execution_port, "service", return_value=service),
        pytest.raises(backend.ResidentTurnCancelled),
    ):
        resident_execution_domain._send_resident_execution(
            resident_record,
            stored,
        )

    service.submit.assert_not_called()
    service.resume.assert_not_called()
    service.wait.assert_not_called()


def test_stopped_job_cannot_authorize_final_projection(
    resident_record: dict[str, object],
) -> None:
    """已请求停止的任务不能提交最终回答投影"""
    resident_id = str(resident_record["resident_id"])
    job_id = "job_stopped_final_projection"
    execution_id = "execution-" + "7" * 24
    resident_execution_work.write(resident_record, {

        "user_message_id": "msg_58585858585858585858585858585858",
        "execution_id": execution_id,
        "actor": "CASE-A",
        "stop_requested_by": "CASE-A",
        "stop_requested_at": "2026-08-28T00:00:01Z",
        "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"})
    final = resident_intake_domain.build_message(
        "agent",
        "must not be visible",
        message_id="msg_59595959595959595959595959595959",
    )

    assert resident_execution_domain._persist_execution_final_message(
        resident_id,

        execution_id,
        final,
    ) is None
    assert resident_conversation_stream.get_repository().read_messages(
        resident_id
    ) == []


def test_stopped_bound_execution_waits_for_scheduler_terminal_state(
    resident_record: dict[str, object],
) -> None:
    """停止已绑定执行后等待调度器终态"""
    execution_id = "execution-" + "8" * 24
    job = {

        "user_message_id": "msg_89898989898989898989898989898989",
        "execution_id": execution_id,
        "actor": "CASE-A",
        "message": "wait for physical cancellation",
        "actual_sent_prompt": "wait for physical cancellation",
        "prompt_cwd": str(resident_record["default_cwd"]),
        "stop_requested_by": "CASE-A",
        "stop_requested_at": "2026-08-28T00:00:01Z",
        "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.write(resident_record, job)
    service = mock.Mock()
    service.wait.return_value = {
        "execution_id": execution_id,
        "status": "cancelled",
        "scheduler": {"cancel_requested": True},
        "result": {"status": "cancelled", "error": "cancelled"},
    }

    with (
        mock.patch.object(execution_port, "service", return_value=service),
        pytest.raises(backend.ResidentTurnCancelled),
    ):
        resident_execution_domain._send_resident_execution(
            resident_record,
            resident_execution_work.read(resident_record),
        )

    service.submit.assert_not_called()
    service.resume.assert_called_once_with(
        execution_id,
        actor="CASE-A",
        correlation_id=job['execution_id'],
        service_type="agent_session",
        provider="codex",
    )
    service.wait.assert_called_once()


def test_stop_job_cancels_its_execution(
    resident_record: dict[str, object],
) -> None:
    """停止任务先持久化意图并保留原请求者及当前活动"""
    message_id = "msg_66666666666666666666666666666666"
    foreign_job = {

        "user_message_id": message_id,
        "status": "running",
        "execution_id": "execution-" + "5" * 24,
        "actor": "sample_admin",
        "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"}
    repository = resident_conversation_stream.get_repository()
    repository.append_message(
        str(resident_record["resident_id"]),
        {
            "message_id": foreign_job["user_message_id"],
            "role": "user",
            "content_parts": [{"type": "text", "text": "stop this run"}],
            "created_at": "2026-08-28T00:00:00Z",
        },
    )
    resident_execution_work.write(resident_record, foreign_job)
    repository.set_activities(
        str(resident_record["resident_id"]),
        [_activity(str(resident_record["resident_id"]))],
    )
    service = mock.Mock()
    service.cancel.return_value = {
        "execution_id": foreign_job["execution_id"],
        "status": "running",
        "scheduler": {"cancel_requested": True},
    }

    with (
        mock.patch(
            "management_portal.execution.service.ExecutionService",
            return_value=service,
        ) as service_factory,
    ):
        result = resident_protocol.cancel_resident_execution(
            "resident_aaaaaaaaaaaa",
            "CASE-A",
            message_id,
        )

    assert result["cancelled"] is False
    assert result["cancellation_requested"] is True
    assert result["backend_cancelled"] is False
    assert "state" not in result and "cursor" not in result
    service_factory.assert_not_called()
    service.cancel.assert_not_called()
    assert resident_execution_work.read(resident_record)["actor"] == "sample_admin"
    from management_portal.society_service import resident_delivery_work
    resident_delivery_work.drain(str(resident_record["resident_id"]))
    projection = repository.current_projection(
        str(resident_record["resident_id"]),
    )
    assert projection["state"]["work_state"] == "stopping"
    assert projection["activities"] == [
        _activity(str(resident_record["resident_id"]))
    ]
    assert repository.input_state(
        str(resident_record["resident_id"]),
        foreign_job["user_message_id"],
    )["state"] == "running"


def test_stop_identity_is_rechecked_when_current_turn_changes(
    resident_record: dict[str, object],
) -> None:
    """停止时重新核对输入身份避免取消已切换的新轮

    :design: decision_c524c775e0be
    :design-point:
    """
    resident_id = str(resident_record["resident_id"])
    original_message_id = "msg_" + "8" * 32
    next_message_id = "msg_" + "9" * 32
    repository = resident_conversation_stream.get_repository()
    repository.append_message(resident_id, {
        "message_id": original_message_id,
        "role": "user",
        "content_parts": [{"type": "text", "text": "stop only this input"}],
        "created_at": "2026-09-07T00:00:00Z",
    })
    resident_execution_work.write(resident_record, {

        "user_message_id": original_message_id,
        "execution_id": "execution-" + "8" * 24,
        "status": "running",
        "actor": "sample_admin",
        "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"}, snapshot={"status": "running"})
    cancel = backend.request_resident_execution_cancellation

    def advance_turn_before_cancellation(*args, **kwargs):
        repository.set_input_state(resident_id, original_message_id, "completed")
        resident_execution_work.write(resident_record, {

            "user_message_id": next_message_id,
            "execution_id": "execution-" + "9" * 24,
            "status": "running",
            "actor": "sample_admin",
            "queue": [],
         "submitted_at": "2026-10-14T00:00:00Z"}, snapshot={"status": "running"})
        return cancel(*args, **kwargs)

    with (
        mock.patch.object(resident_intake_domain, "request_resident_execution_cancellation", side_effect=advance_turn_before_cancellation),
        pytest.raises(ValueError, match="message is not the active Resident input"),
    ):
        resident_protocol.cancel_resident_execution(resident_id, "sample_admin", original_message_id)

    stored = resident_execution_work.read(resident_record)
    assert stored["user_message_id"] == next_message_id
    assert not stored.get("stop_requested_at")
    assert repository.current_projection(resident_id)["state"]["work_state"] == "running"


def test_stop_notifies_exact_execution_after_outer_state_unlock(resident_record):
    """停止通知在外层状态锁释放后发送到准确执行"""
    from management_portal.society_service import invariant_locks, lifecycle

    resident_id = str(resident_record["resident_id"])
    message_id = "msg_99999999999999999999999999999999"
    execution_id = "execution-" + "7" * 24
    resident_execution_work.write(resident_record, {
         "status": "running",
        "user_message_id": message_id, "execution_id": execution_id,
        "actor": "sample_admin", "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"})
    notifications = []

    def notified(target_execution_id, actor):
        assert not invariant_locks.lock_held()
        assert resident_execution_work.read(resident_record)["stop_requested_at"]
        notifications.append((target_execution_id, actor))

    lifecycle.set_execution_cancellation_handler(notified)
    try:
        with resident_record_domain.state_lock(resident_id):
            backend.request_resident_execution_cancellation(resident_id, "CASE-A", message_id)
            assert notifications == []
        assert notifications == [(execution_id, "sample_admin")]
    finally:
        lifecycle.set_execution_cancellation_handler(None)


@pytest.mark.parametrize("fail_commit", [False, True])
def test_stop_notification_waits_for_execution_binding_and_successful_commit(resident_record, fail_commit):
    """停止通知等待执行绑定且仅在事务提交成功后发送"""
    from management_portal.society_service import lifecycle

    resident_id = str(resident_record["resident_id"])
    job = {
        'execution_id': "execution-" + "8" * 24,
        "user_message_id": "msg_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "actor": "sample_admin", "queue": [], "stop_requested_at": "2026-09-07T00:00:00Z",
    }
    notifications = []
    lifecycle.set_execution_cancellation_handler(lambda *target: notifications.append(target))
    try:
        resident_execution_work.write(resident_record, job)
        assert notifications == []
        try:
            with resident_record_domain.state_lock(resident_id):
                job["submitted_at"] = "2026-10-14T00:00:00Z"
                resident_execution_work.write(resident_record, job)
                assert notifications == []
                if fail_commit:
                    raise RuntimeError("state transition failed")
        except RuntimeError:
            if not fail_commit:
                raise
        expected = [] if fail_commit else [(job["execution_id"], "sample_admin")]
        assert notifications == expected
    finally:
        lifecycle.set_execution_cancellation_handler(None)


def test_stale_execution_binding_cannot_erase_committed_stop_intent(
    resident_record,
):
    """过期执行绑定写入保留已提交的停止意图"""
    from management_portal.society_service import lifecycle

    resident_id = str(resident_record["resident_id"])
    message_id = "msg_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    execution_id = "execution-" + "9" * 24
    initial = {
        'execution_id': execution_id,
        "user_message_id": message_id,
        "actor": "sample_admin",
        "queue": [],
    }
    resident_execution_work.write(resident_record, initial)
    stale_submission = dict(initial)
    notifications = []
    lifecycle.set_execution_cancellation_handler(
        lambda *target: notifications.append(target)
    )
    try:
        backend.request_resident_execution_cancellation(
            resident_id,
            "CASE-A",
            message_id,
        )
        assert notifications == []

        stale_submission["submitted_at"] = "2026-10-14T00:00:00Z"
        resident_execution_work.write(resident_record, stale_submission)

        stored = resident_execution_work.read(resident_record)
        assert stored["stop_requested_at"]
        assert stored["stop_requested_by"] == "CASE-A"
        assert stored["execution_id"] == execution_id
        assert notifications == [(execution_id, "sample_admin")]
        assert resident_conversation_stream.get_repository().current_projection(
            resident_id
        )["state"]["work_state"] == "stopping"
    finally:
        lifecycle.set_execution_cancellation_handler(None)


def test_execution_admission_replacement_preserves_committed_input_stop(
    resident_record,
):
    """执行准入替换临时身份时保留同一输入的停止意图"""
    from management_portal.society_service import lifecycle

    resident_id = str(resident_record["resident_id"])
    message_id = "msg_cccccccccccccccccccccccccccccccc"
    execution_id = "execution-" + "a" * 24
    provisional = {
        'execution_id': "job_provisional_immediate_stop",
        "user_message_id": message_id,
        "actor": "sample_admin",
        "queue": [],
    }
    resident_execution_work.write(resident_record, provisional)
    notifications = []
    lifecycle.set_execution_cancellation_handler(
        lambda *target: notifications.append(target)
    )
    try:
        backend.request_resident_execution_cancellation(
            resident_id,
            "CASE-A",
            message_id,
        )

        admitted = {
            **provisional,

            "execution_id": execution_id,
         "submitted_at": "2026-10-14T00:00:00Z"}
        resident_execution_work.write(resident_record, admitted)

        stored = resident_execution_work.read(resident_record)
        assert stored['execution_id'] == admitted['execution_id']
        assert stored["user_message_id"] == message_id
        assert stored["stop_requested_at"]
        assert stored["stop_requested_by"] == "CASE-A"
        assert notifications == [(execution_id, "sample_admin")]
        assert resident_conversation_stream.get_repository().current_projection(
            resident_id
        )["state"]["work_state"] == "stopping"
    finally:
        lifecycle.set_execution_cancellation_handler(None)


def test_execution_store_preserves_stop_for_stale_admission_write(
    resident_record,
):
    """执行存储层保留过期准入写入之前已提交的停止标记"""
    resident_id = str(resident_record["resident_id"])
    message_id = "msg_dddddddddddddddddddddddddddddddd"
    store = resident_execution_work.get_lineage_data_store()
    provisional = {
        'execution_id': "job_storage_provisional",
        "user_message_id": message_id,
        "actor": "sample_admin",
        "queue": [],
    }
    store.put_resident_execution_record(resident_id, provisional)
    store.put_resident_execution_record(resident_id, {
        **provisional,
        "stop_requested_at": "2026-09-28T00:00:00Z",
        "stop_requested_by": "CASE-A",
    })

    revision, committed = store.put_resident_execution_record(
        resident_id,
        {
            **provisional,

            "execution_id": "execution-" + "d" * 24,
         "submitted_at": "2026-10-14T00:00:00Z"},
        return_state=True,
    )

    assert revision >= 3
    assert committed['execution_id'] == 'execution-' + 'd' * 24
    assert committed["stop_requested_at"] == "2026-09-28T00:00:00Z"
    assert committed["stop_requested_by"] == "CASE-A"
    assert all(resident_execution_work.read(resident_record).get(key) == value for key,value in committed.items())


def test_stop_intent_is_durable_without_waiting_for_execution_service(
    resident_record: dict[str, object],
) -> None:
    """停止意图持久保存且无需等待执行服务"""
    message_id = "msg_77777777777777777777777777777777"
    job = {

        "user_message_id": message_id,
        "execution_id": "execution-" + "6" * 24,
        "actor": "sample_admin",
        "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"}
    repository = resident_conversation_stream.get_repository()
    repository.append_message(
        str(resident_record["resident_id"]),
        {
            "message_id": job["user_message_id"],
            "role": "user",
            "content_parts": [
                {"type": "text", "text": "keep running if cancellation fails"}
            ],
            "created_at": "2026-08-28T00:00:00Z",
        },
    )
    resident_execution_work.write(resident_record, job)
    service = mock.Mock()
    service.cancel.side_effect = RuntimeError("control plane unavailable")

    with (
        mock.patch(
            "management_portal.execution.service.ExecutionService",
            return_value=service,
        ),
    ):
        result = resident_protocol.cancel_resident_execution(
            str(resident_record["resident_id"]),
            "sample_admin",
            str(job["user_message_id"]),
        )

    stored = resident_execution_work.read(resident_record)
    assert resident_execution_work.is_active(stored)
    assert stored["stop_requested_at"]
    assert result["cancellation_requested"] is True
    service.cancel.assert_not_called()
    from management_portal.society_service import resident_delivery_work
    resident_delivery_work.drain(str(resident_record["resident_id"]))
    assert repository.current_projection(
        str(resident_record["resident_id"])
    )["state"]["work_state"] == "stopping"


def test_confirmed_cancellation_terminalizes_message_and_clears_job(
    resident_record: dict[str, object],
) -> None:
    """确认取消后终结输入、保存用量并清空任务"""
    resident_id = str(resident_record["resident_id"])
    message_id = "msg_88888888888888888888888888888888"
    continuation_id = "continuation_cancelled_terminal"
    repository = resident_conversation_stream.get_repository()
    repository.append_message(
        resident_id,
        {
            "message_id": message_id,
            "role": "user",
            "content_parts": [
                {"type": "text", "text": "cancel after backend confirmation"}
            ],
            "created_at": "2026-08-28T00:00:00Z",
        },
    )
    job = {

        "user_message_id": message_id,
        "execution_id": "execution-" + "7" * 24,
        "actor": "sample_admin",
        "continuation_id": continuation_id,
        "continuation_started_at": "2026-08-28T00:00:01Z",
        "stop_requested_by": "CASE-A",
        "stop_requested_at": "2026-08-28T00:00:02Z",
        "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"}
    resident_execution_work.write(resident_record, job)
    _ended_execution(resident_id, job["execution_id"], "cancelled", _TURN_510K)

    with (
        mock.patch.object(
            resident_intake_domain,
            "acknowledge_execution",
            return_value=True,
        ),
        mock.patch.object(
            resident_execution_domain,
            "_notify_lineage_message_terminal",
        ) as notify,
    ):
        advanced = resident_execution_domain.apply_cancelled_continuation(
            resident_id,
            continuation_id,
            usage=_TURN_510K,
        )

    assert advanced is False
    assert resident_execution_work.read(resident_record) == {}
    input_state = repository.input_state(resident_id, message_id)
    assert input_state["state"] == "cancelled"
    assert len(input_state["reply_message_ids"]) == 1
    # The stop notice shows what the stopped turn used.
    assert repository.public_message(resident_id, input_state["reply_message_ids"][0])["usage"] == _TURN_510K
    assert resident_record_domain.find_resident(resident_id)["last_turn_usage"] == _TURN_510K
    assert repository.current_projection(resident_id)["state"]["work_state"] == "idle"
    notify.assert_called_once()


def test_compact_persists_runnable_intent_before_execution_submission(
    resident_record: dict[str, object],
) -> None:
    """压缩操作先持久保存可运行意图再由续接提交执行"""
    execution_id = "execution-" + "7" * 24
    service = mock.Mock()
    service.submit.return_value = {
        "execution_id": execution_id,
        "status": "queued",
        "events": [],
        "result": {},
    }

    with mock.patch(
        "management_portal.execution.service.ExecutionService",
        return_value=service,
    ):
        resident_intake_domain._queue_resident_compaction(
            resident_record, "CASE-A", "msg_00000000000000000000000000000001"
        )

    stored = resident_execution_work.read(resident_record)
    assert stored["execution_id"].startswith("execution-")
    assert not stored.get("submitted_at")
    assert resident_execution_work.execution_id(stored)
    assert resident_execution_work.is_compact(stored)
    assert resident_execution_work.continuation_needed(resident_record, stored)
    service.submit.assert_not_called()


@pytest.mark.parametrize("terminal", [False, True])
def test_recovery_preparation_does_not_hydrate_resources(resident_record, monkeypatch, terminal) -> None:
    """执行恢复准备不在状态锁内读取资源权限

    验证恢复只准备本地 continuation，保留 actor 与 job 绑定校验及终态不重启，避免资源服务延迟阻塞 Resident。

    :importance: P1
    :aspects: functional, performance, architecture-conformance
    :design: decision_edc470e09f5a, decision_1b2bdf5c33d6
    """
    resident_id = str(resident_record["resident_id"])
    job = {
        'execution_id': "job_control_recovery", "user_message_id": "msg_control_recovery",
        "actor": "CASE-A", "message": "continue", "actual_sent_prompt": "continue",
        "prompt_cwd": str(resident_record["default_cwd"]), "queue": [],
    }
    if terminal:
        job.update(outcome="completed", finished_at="2026-09-06T12:00:00Z")
    resident_execution_work.write(resident_record, job)

    with mock.patch.object(resident_intake_domain, "start_resident_continuation") as start:
        result = backend.resume_resident_execution(resident_id)
        assert result["residents"] == (0 if terminal else 1)
        assert start.call_count == (0 if terminal else 1)
        if not terminal:
            assert start.call_args.args[0]["resident_id"] == resident_id
            assert start.call_args.args[1] == resident_execution_work.read(resident_record)["continuation_id"]


def test_recovery_preserves_existing_continuation_identity(
    resident_record: dict[str, object],
) -> None:
    """重复准备恢复保留已有续接身份"""
    job = {
        'execution_id': "job_existing_continuation",
        "user_message_id": "msg_existing_continuation",
        "actor": "CASE-A",
        "message": "continue the same durable job",
        "actual_sent_prompt": "continue the same durable job",
        "prompt_cwd": str(resident_record["default_cwd"]),
        "queue": [],
    }
    resident_execution_work.write(resident_record, job)

    first = resident_execution_domain._prepare_resident_continuation(resident_record)
    second = resident_execution_domain._prepare_resident_continuation(resident_record)

    assert first is not None
    assert second == first


def test_job_projection_and_binding_run_after_the_resident_lock(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """任务投影和绑定通知在 Resident 状态锁释放后执行"""
    from management_portal.society_service import invariant_locks

    resident_id = str(resident_record["resident_id"])
    observed: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        resident_execution_work,
        "publish_conversation_state",
        lambda *_args, **_kwargs: observed.append(
            ("projection", invariant_locks.lock_held())
        ),
    )
    monkeypatch.setattr(resident_execution_work, "notify_changed", lambda *_args: None)
    monkeypatch.setattr(
        lifecycle,
        "notify_execution_binding",
        lambda _resident_id: observed.append(
            ("binding", invariant_locks.lock_held())
        ),
    )

    with resident_record_domain.state_lock(resident_id):
        resident_execution_work.write(resident_record, {

            "execution_id": "execution-" + "a" * 24,
            "actor": "CASE-A",
         "submitted_at": "2026-10-14T00:00:00Z"})
        assert observed == []

    assert observed == [("projection", False), ("binding", False)]


def test_execution_message_append_runs_outside_the_execution_work_lock(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """执行消息追加在执行工作锁外进行并清除投影意图"""
    from management_portal.society_service import invariant_locks

    resident_id = str(resident_record["resident_id"])
    job_id = "job_message_after_unlock"
    execution_id = "execution-" + "b" * 24
    resident_execution_work.write(resident_record, {

        "execution_id": execution_id,
        "actor": "CASE-A",
     "submitted_at": "2026-10-14T00:00:00Z"})
    append = resident_lifecycle_domain.append_conversation_message
    observed: list[bool] = []

    def append_after_unlock(*args, **kwargs):
        observed.append(invariant_locks.lock_held())
        return append(*args, **kwargs)

    monkeypatch.setattr(
        resident_lifecycle_domain,
        "append_conversation_message",
        append_after_unlock,
    )
    persisted = resident_execution_domain._persist_execution_display_messages(
        resident_id,

        execution_id,
        [{
            "output_id": "msg_" + "e" * 32,
            "phase": "commentary",
            "text": "project after the Job commit",
        }],
    )

    assert len(persisted) == 1
    assert observed == [False]
    assert not resident_execution_work.read(resident_record).get(
        resident_execution_work.CONVERSATION_PROJECTION_INTENTS
    )


def test_failed_job_projection_does_not_suppress_change_or_binding(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """任务投影失败仍发送变化与绑定通知"""
    resident_id = str(resident_record["resident_id"])
    observed: list[str] = []

    def projection_failure(*_args, **_kwargs):
        observed.append("projection")
        raise RuntimeError("projection unavailable")

    monkeypatch.setattr(
        resident_execution_work,
        "publish_conversation_state",
        projection_failure,
    )
    monkeypatch.setattr(
        resident_execution_work,
        "notify_changed",
        lambda *_args: observed.append("change"),
    )
    monkeypatch.setattr(
        lifecycle,
        "notify_execution_binding",
        lambda *_args: observed.append("binding"),
    )

    with pytest.raises(RuntimeError, match="projection unavailable"):
        with resident_record_domain.state_lock(resident_id):
            resident_execution_work.write(resident_record, {

                "execution_id": "execution-" + "c" * 24,
                "actor": "CASE-A",
             "submitted_at": "2026-10-14T00:00:00Z"})

    assert observed == ["projection", "change", "binding"]


def test_general_job_writes_cannot_drop_or_resurrect_projection_intents(
    resident_record: dict[str, object],
) -> None:
    """普通任务写入不能丢弃或复活对话投影意图"""
    resident_id = str(resident_record["resident_id"])
    job_id = "job_projection_intent_owner"
    execution_id = "execution-" + "d" * 24
    resident_execution_work.write(resident_record, {

        "execution_id": execution_id,
        "actor": "CASE-A",
     "submitted_at": "2026-10-14T00:00:00Z"})
    projected_message = resident_intake_domain.build_message(
        "agent",
        "authorized projection",
        message_id="msg_" + "f" * 32,
    )
    with resident_record_domain.state_lock(resident_id):
        assert resident_execution_work.authorize_conversation_projection(
            resident_record,

            execution_id=execution_id,
            message=projected_message,
        )
        stale_without_intent = {

            "execution_id": execution_id,
            "actor": "CASE-A",
         "submitted_at": "2026-10-14T00:00:00Z"}
        resident_execution_work.write(resident_record, stale_without_intent)
    authorized = resident_execution_work.read(resident_record)
    assert projected_message["message_id"] in authorized[
        resident_execution_work.CONVERSATION_PROJECTION_INTENTS
    ]
    stale_with_intent = dict(authorized)
    with resident_record_domain.state_lock(resident_id):
        resident_execution_work.complete_conversation_projection(
            resident_record,

            execution_id=execution_id,
            message_id=projected_message["message_id"],
        )
        resident_execution_work.write(resident_record, stale_with_intent)
    assert not resident_execution_work.read(resident_record).get(
        resident_execution_work.CONVERSATION_PROJECTION_INTENTS
    )


def test_recovery_finishes_durable_terminal_projection_then_releases_turn(resident_record, monkeypatch):
    """恢复先完成持久终态消息投影再释放轮次

    :module: resident.conversation
    :aspects: functional, performance
    :design: decision_edc470e09f5a
    :design-point:
    """
    resident_id = str(resident_record['resident_id'])
    job_id, execution_id = 'job_projection_recovery', 'execution-' + 'a' * 24
    message = resident_intake_domain.build_message('agent', 'completed reply', message_id='msg_' + 'a' * 32)
    store = resident_execution_work.get_lineage_data_store()
    store.put_resident_execution_record(resident_id, {
         'execution_id': execution_id, 'actor': 'CASE-A',
        'outcome': 'completed', 'execution_acknowledged': True,
        'conversation_projection_intents': {message['message_id']: {
             'execution_id': execution_id, 'message': message,
         "submitted_at": "2026-10-14T00:00:00Z"}},
     "submitted_at": "2026-10-14T00:00:00Z"})
    observed = []
    monkeypatch.setattr(resident_lifecycle_domain, 'append_conversation_message',
                        lambda rid, value: observed.append((rid, value)))
    resident_execution_domain.resume_pending_resident_executions()
    assert observed == [(resident_id, message)]
    assert not resident_execution_work.read(resident_record)
    resident_execution_domain.resume_pending_resident_executions()
    assert len(observed) == 1


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_continued_turn_usage_follows_its_terminal_reply_in_pages_forks_and_events(resident_record, status):
    """续接轮用量绑定终态回复并在分页、分叉和事件中一致呈现"""
    from management_portal.society_service.resident_conversation_stream import ResidentConversationRepository
    from tests.cases.test_canonical_message_trace import message
    store = AgentDataStore()
    repository = ResidentConversationRepository(LineageDataStore(), store)
    agent, execution = str(resident_record["resident_id"]), "execution-continued-footer"
    repository.append_message(agent, message("msg_work", "continue work"))
    repository.set_input_state(agent, "msg_work", "running")
    store.begin_execution(execution_id=execution, agent_id=agent, actor_ref="sample_admin", provider="codex",
                          started_at="2026-10-12T00:00:00Z")
    counted = {"input_tokens": 1200, "output_tokens": 90, "total_tokens": 1290}
    store.record_accounted_usage(execution, counted)

    def output(identity, kind):
        segment = store.put_segment(segment_type="model", content=identity,
            execution_id=execution, created_by_agent_id=agent,
            metadata={"kind": kind, "message": {
                "message_id": identity, "role": "agent", "execution_id": execution,
                "reply_to_message_id": "msg_work", "parts": [{"type": "text"}],
            }})
        store.append_agent_segments(agent, [segment], actor_ref="sample_admin")
        return segment

    first = output("msg_early_final", "final_answer")
    store.fork_agent(source_agent_id=agent, target_agent_id="resident_partial",
        actor_ref="sample_admin", segment_ids=store.sequence_segment_ids(store.trace_sequence_id(agent)))
    last = output("msg_after_continue", "commentary")
    before = store.segment(last)
    assert all(not m.get("usage") for m in store.read_chat_messages(agent))
    # Artifacts and Harness inputs follow the reply but cannot become its footer.
    notice = {**message("msg_harness", "continue"), "role": "system",
              "execution_id": execution, "reply_to_message_id": "msg_work"}
    for card_type in ("native_turn", "resource_changes", "subagent_run"):
        part = {"type": card_type, "segment_id": "segment_" + card_type}
        store.put_operation_record(segment_id=part["segment_id"], created_by_agent_id=agent,
            segment_type="model" if card_type == "native_turn" else "artifact", content=part)
        store.display_chat_message(agent, {**notice, "message_id": "msg_" + card_type,
            "role": "agent", "content_parts": [part]})
    final_id = "msg_after_continue"
    if status != "completed":
        final_id = "msg_terminal_notice"
        store.display_chat_message(agent, {**notice, "message_id": final_id})
    store.display_chat_message(agent, {**notice, "harness_input": {"source": "recovery"}})
    store.finish_execution(execution, status=status, usage={"input_tokens": 9})
    store.record_accounted_usage(execution, counted)  # Scheduler settles after the Provider result.
    start_cursor = repository.events_after(agent, 0)["cursor"]
    terminal = repository.set_input_state(agent, "msg_work", status)
    assert terminal["reply_message_ids"] == [final_id]
    updates = [e for e in repository.events_after(agent, start_cursor)["events"]
               if e["event_type"] == "conversation.message.updated"]
    assert len(updates) == 1
    assert updates[0]["payload"]["message"]["message_id"] == final_id
    assert updates[0]["payload"]["message"]["usage"] == counted

    def footer(messages):
        return {m["message_id"]: m["usage"] for m in messages if m.get("usage")}

    expected = {final_id: counted}
    assert footer(store.read_chat_messages(agent)) == expected
    assert store.chat_message(agent, final_id)["usage"] == counted
    assert not store.chat_message(agent, "msg_early_final").get("usage")
    pages, boundary = [], None
    while True:
        page = store.read_chat_message_page(agent, before_ordinal=boundary, limit=1)
        pages.extend(page["messages"])
        boundary = page["next_before_ordinal"]
        if boundary is None:
            break
    assert footer(pages) == expected
    assert footer(store.read_chat_messages_many([agent])[agent]) == expected
    assert not footer(store.read_chat_messages("resident_partial"))
    store.fork_agent(source_agent_id=agent, target_agent_id="resident_complete",
        actor_ref="sample_admin", segment_ids=store.sequence_segment_ids(store.trace_sequence_id(agent)))
    if status == "completed":
        assert footer(store.read_chat_messages("resident_complete")) == expected
    assert store.segment(last) == before  # Projection never writes counters into content.
    assert "usage" not in store.segment(first)["metadata"]["message"]

