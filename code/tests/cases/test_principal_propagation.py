from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from management_common.operation_identity import current_operation_identity, operation_identity_scope
from management_common.principal import OperationIdentity, authenticated_principal, message_origin
from management_portal.execution.contracts import ExecutionRequest, SessionOptions
from management_portal.execution.scheduler import Scheduler
from management_portal.hatg.contracts import InstructionContext, InstructionError
from management_portal.society_service import lineage_domain, lineage_primitives
from management_portal.society_service import resident_intake_domain
from management_portal.society_service import resident_lifecycle_domain
from management_portal.society_service import resident_session_backend_domain
from management_portal.society_service.resident_conversation_stream import protocol_message
from tests.cases.isa_test_support import isolated_instruction_store, ok, publish_test_members, run  # noqa: F401 - fixture
from tests.cases.model_api_test_support import DEEPSEEK_PRIMARY, register_apis
from tests.cases.test_resident_conversation_boundaries import repository


SENDER = "resident_aaaaaaaaaaaa"
RECEIVER = "resident_111111111111"


def test_authentication_preserves_principal_namespace(subtests, isolated_portal_case):
    """认证拒绝主体类型与标识命名空间不一致的身份"""
    for auth in [
    {"type": "human", "id": SENDER},
    {"type": "agent", "id": "alice"},
    {"type": "human", "id": "portal"},
    {"type": "unknown", "id": "alice"},
]:
        with subtests.test(auth=auth), isolated_portal_case() as case_path:
            with pytest.raises(PermissionError):
                authenticated_principal(auth)


def test_instruction_cannot_split_author_and_authenticated_principal_contracts(subtests, isolated_portal_case):
    """指令与执行请求拒绝拆分或伪造认证调用者身份"""
    with subtests.test(contract='test_an_instruction_context_has_exactly_one_authenticated_caller'), isolated_portal_case() as case_path:
        with pytest.raises(InstructionError):
            InstructionContext(caller='', entry='web')
        with pytest.raises(InstructionError):
            InstructionContext(caller='alice', entry='forged-entry')
    with subtests.test(contract='test_service_identity_cannot_act_as_an_automation_root'), isolated_portal_case() as case_path:
        denied = run('CreateLineage', {'title': 'forged', 'project_id': 'project_test'}, actor='portal', key='forged-root')
        assert denied.status == 'failed'
    with subtests.test(contract='test_execution_identity_roundtrip_rejects_forged_executor'), isolated_portal_case() as case_path:
        request = ExecutionRequest.new(actor='alice', agent_id=SENDER, prompt='hello', service_type='agent_session', provider='codex', operation='turn', cwd='/tmp')
        assert request.identity.principal.id == SENDER
        assert request.identity.initiated_by.id == 'alice'
        assert ExecutionRequest.from_payload(request.to_payload()) == request
        forged = request.to_payload()
        forged['identity']['principal']['id'] = RECEIVER
        with pytest.raises(ValueError, match='coordinates'):
            ExecutionRequest.from_payload(forged)
    with subtests.test(contract='test_legacy_messages_keep_unknown_origin_and_existing_author'), isolated_portal_case() as case_path:
        message = {'message_id': 'msg_' + 'b' * 32, 'role': 'user', 'author_id': 'alice', 'content_parts': [{'type': 'text', 'text': 'legacy'}], 'created_at': ''}
        assert protocol_message(message) == message
        forged = {**message, 'origin': message_origin(SENDER)}
        with pytest.raises(ValueError, match='disagrees'):
            protocol_message(forged)
    with subtests.test(contract='test_operation_context_is_isolated_and_reset_on_failure'), isolated_portal_case() as case_path:
        identity = OperationIdentity.for_actor(SENDER, initiated_by='alice')
        with operation_identity_scope(identity):
            with ThreadPoolExecutor(max_workers=1) as executor:
                assert executor.submit(current_operation_identity).result() is None
            with pytest.raises(RuntimeError):
                with operation_identity_scope(OperationIdentity.for_actor('bob')):
                    raise RuntimeError('test')
            assert current_operation_identity() == identity
        assert current_operation_identity() is None
    with subtests.test(contract='test_event_subject_does_not_supply_execution_authority'), isolated_portal_case() as case_path:
        identity = OperationIdentity.for_actor(SENDER).to_dict()
        event = lineage_primitives.make_event('manual.triggered', subject_resident_id=RECEIVER, identity=identity)
        assert lineage_primitives.normalize_event(event.to_dict()).identity == identity
        with pytest.raises(PermissionError, match='dispatch principal'):
            lineage_domain.dispatch_lineage_program_event('lineage_aaaaaaaaaaaa', 'event_source_test', event, 'alice', cursor='once')


def test_disabled_user_loses_lineage_authority_contracts(monkeypatch, subtests, isolated_portal_case):
    """停用用户失去 Lineage 权限且旧投递需重新授权"""
    with subtests.test(contract='test_disabled_user_loses_lineage_authority'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            from types import SimpleNamespace
            monkeypatch.setattr('management_portal.portal_access_control.get_portal_access_store', lambda: SimpleNamespace(is_active=lambda _actor: False))
            assert not lineage_domain._actor_can_manage_lineage({'lineage_id': 'lineage_aaaaaaaaaaaa'}, 'alice')
    with subtests.test(contract='test_legacy_pending_delivery_is_not_consumed_before_identity_validation'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            event = lineage_primitives.make_event('manual.triggered')
            owner_checks = []
            monkeypatch.setattr(lineage_domain, '_ensure_manager', lambda *args: owner_checks.append(args))
            with pytest.raises(PermissionError, match='explicit reissue'):
                lineage_domain._resume_program_execution_principal('lineage_aaaaaaaaaaaa', event, SENDER)
            assert owner_checks == []


def test_execution_control_preserves_request_scope_and_actual_performer(tmp_path):
    """执行控制保留请求归属并记录实际操作者"""
    register_apis(DEEPSEEK_PRIMARY)  # Claude's default route.
    scheduler = Scheduler()
    request = ExecutionRequest.new(
        actor="alice", agent_id=RECEIVER, prompt="hello", service_type="agent_session",
        provider="claude", operation="turn", cwd="/tmp",
    )
    scheduler.submit(request)
    scheduler.set_priority(request.execution_id, actor="alice", performed_by=SENDER, priority=10)
    result = scheduler.cancel(request.execution_id, actor="alice", performed_by=SENDER, reason="peer cancelled")
    assert result["request"]["actor"] == "alice"
    controls = [event["details"] for event in result["events"] if event["type"] in {"priority_changed", "cancelled"}]
    assert len(controls) == 2
    assert all(item["actor"] == SENDER and item["request_actor"] == "alice" for item in controls)
    with pytest.raises(PermissionError):
        scheduler.cancel(request.execution_id, actor="bob", performed_by=SENDER)


def test_queued_codex_execution_can_change_account_without_changing_identity(tmp_path):
    """排队中的 codex 执行更换账号时保留请求身份"""
    scheduler = Scheduler()
    request = ExecutionRequest.new(
        actor="alice", agent_id=RECEIVER, prompt="hello", service_type="agent_session",
        provider="codex", operation="turn", cwd="/tmp",
        session=SessionOptions(mode="persistent", binding_id="resident:test", backend_route_id="one"),
    )
    scheduler.submit(request)
    result = scheduler.reassign_queued_route(
        request.execution_id, actor="alice", performed_by=SENDER, backend_route_id="three",
    )
    assert result["status"] == "queued"
    assert result["request"]["execution_id"] == request.execution_id
    assert result["request"]["session"]["backend_route_id"] == "three"
    event = next(item for item in result["events"] if item["type"] == "route_changed")
    assert event["details"]["actor"] == SENDER
    assert event["details"]["request_actor"] == "alice"
    assert event["details"]["source_backend_route_id"] == "one"
    with pytest.raises(KeyError):
        scheduler.reassign_queued_route(request.execution_id, actor="bob", backend_route_id="four")


def test_resident_delivery_signs_sender_independently_of_provider_role(repository):
    """Resident 投递独立记录发送者及发起用户"""
    with operation_identity_scope(OperationIdentity.for_actor(SENDER, initiated_by="alice")):
        message = resident_intake_domain._resident_user_message(
            RECEIVER, "hello", SENDER, message_id="msg_" + "a" * 32,
        )
    saved = repository.commit_user_message(RECEIVER, message)
    assert saved["role"] == "user"
    assert saved["author_id"] == SENDER
    assert saved["origin"]["sender"] == {"kind": "resident", "id": SENDER}
    assert saved["origin"]["initiated_by"]["id"] == "alice"
    assert repository.message(RECEIVER, saved["message_id"]) == saved
    assert repository.commit_user_message(RECEIVER, message) == saved
    forged = deepcopy(message)
    forged["origin"]["initiated_by"]["id"] = "bob"
    with pytest.raises(ValueError, match="immutable"):
        repository.commit_user_message(RECEIVER, forged)


def test_model_output_and_service_notice_have_distinct_senders(repository):
    """模型输出与服务通知分别记录各自发送者"""
    answer = resident_lifecycle_domain._append_conversation_message(
        RECEIVER, resident_intake_domain._message("agent", "answer"),
    )
    notice = resident_lifecycle_domain._append_conversation_message(
        RECEIVER, resident_intake_domain._message("system", "cancelled", "alice"),
    )
    assert answer["author_id"] == RECEIVER
    assert answer["origin"]["sender"]["kind"] == "resident"
    assert notice["author_id"] == "portal"
    assert notice["origin"]["sender"]["kind"] == "service"
    assert notice["origin"]["initiated_by"]["id"] == "alice"


def test_automation_without_user_does_not_borrow_a_leader():
    """自动观察允许省略 User 且不借用 Leader

    User 可选，自动观察保留 Portal 服务来源，不借用 Leader，也不允许外部伪装服务身份。

    :design: decision_ae422b92c6f3, decision_5ff0cb51ed84
    """
    from management_common.principal import authenticated_principal
    for leader in (SENDER, ""):
        assert lineage_domain._lineage_runtime_actor({"leader_resident_id": leader}) == "portal"
    with pytest.raises(PermissionError):
        authenticated_principal({"type": "human", "id": "portal"})
    with pytest.raises(PermissionError):
        authenticated_principal({"type": "agent", "id": "portal"})


def test_fork_recovery_does_not_borrow_the_resident_creator():
    """分叉恢复缺失身份时拒绝借用 Resident 创建者"""
    record = {"resident_id": RECEIVER, "created_by": "alice"}
    with pytest.raises(PermissionError, match="explicit reissue"):
        resident_session_backend_domain._materialize_queued_agent_data_fork(record, {"agent_data_fork": {}})
    with pytest.raises(PermissionError, match="explicit reissue"):
        resident_session_backend_domain._create_exact_resident_session(record, {"operation": "context_fork_create"})
