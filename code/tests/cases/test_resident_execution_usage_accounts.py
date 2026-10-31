"""Token usage of a Resident's turns, account details and quota overrides.

:module: resident.identity
:importance: P2
"""
from __future__ import annotations
from management_portal.society_service import resident_backend
from management_portal.society_service import resident_conversation_stream
from management_portal.execution.accounts import default_execution_accounts

from unittest import mock

import pytest

pytestmark = pytest.mark.usefixtures("registered_execution_defaults")

from management_portal.society_service import (
    resident_directory_domain,
    resident_execution_domain,
    resident_intake_domain,
    resident_execution_work,
    resident_protocol,
    resident_record_domain,
    resident_runtime_domain,
    resident_input_queue_domain,
    resident_execution_engine_domain,
)
from management_portal.society_service import resident_domain as backend
from management_portal.society_service.state_store import LineageDataStore
from management_portal.execution.agent_data_store import AgentDataStore, get_agent_data_store
from management_portal.execution.service import ExecutionService
from management_portal.society_service import execution_client
from tests.cases.resident_execution_test_support import (
    _deliver_session,
    _resident_test_codex_registry,
    _TURN_510K,
    _ended_execution,
)
from tests.cases.resident_execution_test_support import resident_record  # noqa: F401 - fixture


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
        assert resident_runtime_domain.resident_session_prompt_controls(settings) == {'allow_subagents': True}
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
        resident_execution_engine_domain,
        "durable_resident_execution_request",
        lambda _record, _job, factory: factory(),
    )
    monkeypatch.setattr(
        resident_execution_engine_domain,
        "bind_or_resume_resident_execution",
        lambda *_args: "execution-" + "8" * 24,
    )
    monkeypatch.setattr(
        resident_execution_engine_domain,
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
        resident_execution_engine_domain, "durable_resident_execution_request", lambda _record, _job, factory: factory(),
    )
    monkeypatch.setattr(
        resident_execution_engine_domain, "bind_or_resume_resident_execution", lambda *_args: "execution-" + "9" * 24,
    )
    monkeypatch.setattr(resident_execution_engine_domain, "wait_for_resident_execution", lambda *_args, **_kwargs: {
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
        notice = resident_input_queue_domain.persist_failed_execution_message(
            resident_id, { "user_message_id": "message_709", "execution_id": execution_id, "submitted_at": "2026-10-14T00:00:00Z"},
            "stopped", {}, failed_at="2026-08-17T00:00:01Z",
        )
        shown = resident_conversation_stream.get_repository().public_message(resident_id, notice["message_id"])
        assert shown["usage"] == _TURN_510K


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
    assert resident_record_domain.resident_intake_state(before) == "closed"
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
    """未绑定路由且解析不到执行账号的 Resident 不参与额度管理，同一 Provider 只解析一次默认账号"""
    accounts = ExecutionService().accounts()["accounts"]
    resolve = mock.Mock(return_value={})
    monkeypatch.setattr(ExecutionService, "resolve_account", resolve)
    account_for = resident_intake_domain._resident_quota_account_resolver(accounts)
    unaccounted = {
        **resident_record,
        "backend": "opencode",
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
    assert resident_record_domain.resident_intake_state(opened) == "open"
    sync.side_effect = scheduler.configure_binding_quota_overrides
    resident_input_queue_domain.sync_resident_quota_overrides([opened])
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


def test_api_details_include_all_visible_harness_nodes(monkeypatch):
    """按计费 API 汇总跨 Harness 节点，批量操作名单仍受读取权限过滤。"""
    from types import SimpleNamespace
    domain = resident_intake_domain
    accounts = [dict(account_id=name, provider=name, backend_route_id='', billing_account_id=api)
                for name, api in [('direct', 'shared'), ('dsh', 'shared'), ('claude', 'shared'), ('other', 'separate')]]
    residents = [dict(resident_id=name, backend=name) for name in ('direct', 'dsh', 'claude', 'other')]
    monkeypatch.setattr(domain.execution_port, 'service', lambda: SimpleNamespace(accounts=lambda: {'accounts': accounts}))
    monkeypatch.setattr(domain.resident_lineage, 'all', lambda: residents)
    monkeypatch.setattr(domain, '_resident_visible_to_actor', lambda record, actor: record['resident_id'] != 'claude')
    monkeypatch.setattr(domain.resident_backend, 'execution_binding_id', lambda record: record['resident_id'])
    monkeypatch.setattr(domain.resident_backend, 'session_routes_for_bindings', lambda ids: {})
    monkeypatch.setattr(domain.resident_backend, 'execution_provider', lambda record: record['backend'])
    monkeypatch.setattr(domain.resident_backend, 'execution_account_route', lambda record, binding: '')
    monkeypatch.setattr(domain.resident_conversation_stream, 'get_repository', lambda: SimpleNamespace(
        state_snapshots=lambda ids: {name: {'state': {'intake_state': 'open', 'work_state': 'idle'}} for name in ids}))
    result = backend.get_account_resident_summary('shared', 'CASE-A', account_scope='model_api')
    assert result['total'] == 2
    assert result['account_scope'] == 'model_api'
    assert result['open_resident_ids'] == ['direct', 'dsh']
    assert backend.get_account_resident_summary('dsh', 'CASE-A')['total'] == 1
    with pytest.raises(ValueError):
        backend.get_account_resident_summary('absent', 'CASE-A', account_scope='model_api')
