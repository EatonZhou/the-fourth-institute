"""What a Resident's execution request carries: input identity, runtime settings and modes, session
account, binding, permissions, credentials and prompt.

:module: resident.identity
:importance: P2
"""
from __future__ import annotations
from tests.cases.agent_data_test_support import register_session
from management_portal.execution import event_projection as execution_events
from management_portal.society_service import invariant_locks
from management_portal.society_service import resident_backend
from management_portal.society_service import resident_catalog
from management_portal.society_service.domain_ports import execution_port
from management_portal.execution import resident_runtime_settings, codex_runtime_settings

from pathlib import Path
from unittest import mock

import pytest

pytestmark = pytest.mark.usefixtures("registered_execution_defaults")

from management_portal.society_service import (
    resident_hatg_credentials,
    resident_conversation_projection_domain,
    resident_directory_domain,
    resident_execution_domain,
    resident_intake_domain,
    resident_execution_work,
    resident_protocol,
    resident_record_domain,
    resident_runtime_domain,
    resident_session_backend_domain,
    resource_client,
    resident_prompt_domain,
    resident_input_queue_domain,
    resident_execution_engine_domain,
)
from management_portal.society_service import resident_domain as backend
from management_portal.society_service import resident_conversation_records
from tests.cases.resident_execution_test_support import resident_record  # noqa: F401 - fixture


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
            activity = resident_execution_engine_domain.runtime_activity_for_execution('resident_444444444444', {'execution_id': execution_id, 'status': 'running', 'submitted_at': '2026-10-14T00:00:00Z'})
            assert activity == {'live': True, 'execution_id': execution_id, 'details': [{'activity_id': 'runtime:2:tool', 'resident_id': 'resident_444444444444', 'message_id': '', 'kind': 'tool', 'status': 'completed', 'title': 'Shell', 'content': 'pytest -q', 'started_at': '', 'updated_at': ''}]}
    with subtests.test(contract='test_a_running_subagent_activity_carries_its_card'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            execution_id = 'execution-' + '5' * 24
            card = {'tool_use_id': 'toolu_a', 'agent_type': 'Explore', 'status': 'running', 'steps': [{'kind': 'tool', 'tool_name': 'Bash', 'text': 'ls', 'error': False, 'running': True}]}
            subagent_detail = {'id': 'runtime-update:subagent:toolu_a', 'kind': 'subagent', 'title': '子 agent', 'content': 'Survey', 'status': 'running', 'subagent': card}
            monkeypatch.setattr(execution_events, 'execution_projection_from_execution', lambda *args, **kwargs: {'execution_id': execution_id, 'outputs': [], 'runtime_details': [subagent_detail]})
            detail, = resident_execution_engine_domain.runtime_activity_for_execution('resident_444444444444', {'execution_id': execution_id, 'status': 'running', 'submitted_at': '2026-10-14T00:00:00Z'})['details']
            assert (detail['kind'], detail['status'], detail['subagent']) == ('subagent', 'running', card)
            published, = resident_conversation_records.protocol_activities('resident_444444444444', [detail])
            assert published['subagent'] == card
            # Only a subagent activity carries a card.
            other, = resident_conversation_records.protocol_activities('resident_444444444444', [{**detail, 'kind': 'tool'}])
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
    # The delivery's own receipt: a sender that cannot read the recipient gets only these fields.
    receipt = {"resident_id": "resident_0123456789ab", "message_id": "msg_0123456789abcdef",
               "accepted": True, "activation_policy": "enqueue_and_trigger"}

    def deliver(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return dict(receipt)

    monkeypatch.setattr(
        resident_intake_domain,
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

    assert result == receipt
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
        resident_execution_engine_domain,
        "durable_resident_execution_request",
        lambda _record, _job, factory: factory(),
    )
    monkeypatch.setattr(
        resident_execution_engine_domain,
        "bind_or_resume_resident_execution",
        lambda *_args: "execution-managed",
    )
    monkeypatch.setattr(
        resident_execution_engine_domain,
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
        resident_execution_engine_domain._resident_execution_still_current(resident_id, "control-job"),
        resident_execution_engine_domain.resident_execution_cancellation_requested(resident_id, "control-job"),
    ) == expected
    # No cache: a newly committed Stop must be seen by the next poll.
    resident_execution_work.get_lineage_data_store().put_resident_execution_record(
        resident_id, {'execution_id': 'control-stop', 'actor': 'CASE-A', 'stop_requested_at': 'now'},
    )
    assert resident_execution_engine_domain.resident_execution_cancellation_requested(resident_id, "control-stop")
    assert not resident_execution_engine_domain._resident_execution_still_current(resident_id, "control-stop")


def test_execution_control_poll_rejects_missing_resident(control_record) -> None:
    """执行控制轮询对缺失 Resident 返回无效状态"""
    missing = "resident_ffffffffffff"
    assert not resident_execution_engine_domain._resident_execution_still_current(missing, "control-job")
    assert not resident_execution_engine_domain.resident_execution_cancellation_requested(missing, "control-job")


def test_runtime_settings_save_updates_only_speed_on_running_turn(
    resident_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """运行中保存设置不改动当前轮，速度和其他设置都从下一轮生效

    正在执行的一轮保持已提交快照；保存参数不向它发送速度更新。

    :design: decision_ccbb1cac24aa
    """
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

    class Service:
        @staticmethod
        def update_turn_settings(*_args, **_kwargs):
            pytest.fail("saving settings must not change the running turn")

    monkeypatch.setattr(execution_port, "service", lambda: Service())

    result = resident_protocol.update_resident_runtime_settings(
        resident_id,
        "CASE-A",
        {"runtime_settings": {"service_tier": "priority", "collaboration_mode": "plan"}},
    )

    assert result["runtime_settings"]["service_tier"] == "priority"
    assert result["runtime_settings"]["collaboration_mode"] == "plan"
    assert result["active_turn_settings_update"] == {"requested": False, "status": "next_turn"}
    assert result["active_turn_fields"] == []
    assert result["next_turn_fields"] == [
        "collaboration_mode", "model", "reasoning_effort", "service_tier",
    ]
    stored = resident_execution_work.read(resident_record)
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

    monkeypatch.setattr(resident_input_queue_domain, "_submit_active_turn_input", reject_injection)
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
    """codex 注册按选定账号解析默认模型和推理强度

    注册没有选择模型时只保存空值（继承默认），不读取模型目录；执行前按 Resident 选定的账号解析模型和推理强度。

    :design: decision_8b2fa224616b
    """
    from management_portal.execution.provider_catalog import AGENT_PROVIDERS

    for suffix in ("MODEL", "EFFORT", "SERVICE_TIER"):
        monkeypatch.delenv(f"RESIDENT_AGENT_CODEX_{suffix}", raising=False)
    monkeypatch.setattr(
        resident_backend,
        "session_record",
        lambda *_args, **_kwargs: {},
    )
    catalog_accounts: list[str] = []

    def catalog(*, account_id):
        catalog_accounts.append(account_id)
        return {
            "models": ["account-model"],
            "default_model": "account-model",
            "efforts_by_model": {"account-model": ["low"]},
            "default_efforts_by_model": {"account-model": "low"},
            "service_tiers_by_model": {"account-model": []},
        }

    monkeypatch.setattr(codex_runtime_settings, "codex_runtime_catalog", catalog)
    record = {
        "resident_id": "resident_bbbbbbbbbbbb",
        "backend": "codex",
        "backend_route_id": "zero",
    }

    settings = resident_runtime_domain.resident_runtime_settings_from_register_payload(
        record,
        {},
        "CASE-A",
        "2026-09-21T00:00:00Z",
    )

    fields = ("model", "reasoning_effort", "service_tier")
    # Registration needs no Codex account: API-only installations register Codex Residents too.
    assert catalog_accounts == []
    assert {field: settings[field] for field in fields} == dict.fromkeys(fields, "")

    # The Resident's first execution reconciles its settings with the selected account.
    definition = AGENT_PROVIDERS["codex"]
    resolved = definition.runtime_adapter().reconcile(definition, record["backend_route_id"], settings)
    assert catalog_accounts == ["zero"]
    assert {field: resolved[field] for field in fields} == {
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
    # Saving only configures later turns (decision_ccbb1cac24aa), idle or not.
    assert result["active_turn_settings_update"] == {"requested": False, "status": "next_turn"}
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


@pytest.mark.parametrize("resident_backend", ("claude", "kimi"))
def test_non_codex_resident_instructions_use_runtime_lifecycle(
    resident_record: dict[str, object],
    resident_backend: str,
) -> None:
    """非 codex Resident 系统指令按会话生命周期注入"""
    resident_record["backend"] = resident_backend

    runtime_options = resident_execution_engine_domain.resident_execution_runtime_options(
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
    prompt = resident_prompt_domain.build_resident_prompt(resident_record, job)
    system_instructions = resident_execution_domain.resident_system_instructions(
        resident_record,
        job,
    )

    assert "<portal-system-prompt" not in prompt
    core = {part.name: part for part in system_instructions.parts}["core_rules"]
    assert core.placement == "system"
    assert core.timing == "session_start_and_after_compaction"
