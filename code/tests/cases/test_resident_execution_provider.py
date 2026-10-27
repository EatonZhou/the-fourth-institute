from __future__ import annotations
from management_portal.society_service import resident_conversation_stream
from management_portal.society_service import resident_execution_domain

import contextlib
import threading

from management_portal.society_service import resident_execution_provider


from management_portal.society_service import (
    resident_access,
    resident_intake_domain,
    resident_lifecycle_domain,
    resident_record_domain,
    resident_runtime_domain,
)

from management_portal.society_service import resident_intake_domain

def test_deterministic_provider_exposes_behavior_as_capabilities_contracts(subtests, isolated_portal_case):
    """Provider 以 capabilities 声明能力，非规范或非法的 backend/mode 组合被拒绝"""
    with subtests.test(contract='test_deterministic_provider_exposes_behavior_as_capabilities'), isolated_portal_case() as case_path:
        provider = resident_execution_provider.provider_for({'backend': 'portal', 'mode': 'deterministic'})
        assert provider.capabilities.synchronous_delivery is True
        assert provider.capabilities.supports_context is False
        assert provider.capabilities.supports_media is False
        assert provider.capabilities.supports_skills is False
        assert provider.capabilities.supports_fork is False
        assert provider.capabilities.supports_session_restart is False
    with subtests.test(contract='test_session_provider_exposes_common_execution_contract'), isolated_portal_case() as case_path:
        provider = resident_execution_provider.provider_for({'backend': 'codex', 'mode': 'new_session'})
        assert provider.capabilities.synchronous_delivery is False
        assert provider.capabilities.supports_context is True
        assert provider.capabilities.supports_fork is True
        assert 'model' in provider.capabilities.runtime_settings
    with subtests.test(contract='test_runtime_and_registration_reject_noncanonical_session_mode'), isolated_portal_case() as case_path:
        try:
            resident_execution_provider.provider_for({'backend': 'codex', 'mode': 'existing_session'})
        except ValueError as exc:
            assert 'codex/existing_session' in str(exc)
        else:
            raise AssertionError('noncanonical provider mode was accepted at runtime')
        try:
            resident_execution_provider.validate_registration('codex', 'existing_session')
        except ValueError as exc:
            assert 'codex/existing_session' in str(exc)
        else:
            raise AssertionError('noncanonical provider mode was accepted for registration')
    with subtests.test(contract='test_provider_registry_rejects_invalid_backend_mode_pair'), isolated_portal_case() as case_path:
        try:
            resident_execution_provider.validate_registration('codex', 'deterministic')
        except ValueError as exc:
            assert 'codex/deterministic' in str(exc)
        else:
            raise AssertionError('invalid provider pair was accepted')


def test_deterministic_delivery_uses_the_lineage_host_port(monkeypatch) -> None:
    """deterministic 模式的投递经 Lineage host port 处理并返回回复"""
    calls: list[tuple[str, str, str, str]] = []

    def deliver(
        lineage_id: str,
        resident_id: str,
        message: str,
        actor_id: str,
    ) -> dict[str, str]:
        calls.append((lineage_id, resident_id, message, actor_id))
        return {"reply": "done"}

    monkeypatch.setattr(
        resident_execution_provider.resident_lineage_host_port,
        "handle_deterministic_resident_message",
        deliver,
    )
    provider = resident_execution_provider.provider_for({
        "backend": "portal",
        "mode": "deterministic",
    })

    outcome = provider.deliver(resident_execution_provider.DeliveryRequest(
        lineage_id="lineage_111111111111",
        resident_id="resident_222222222222",
        actor_id="sample_admin",
        message="/status",
    ))

    assert outcome.reply == "done"
    assert calls == [(
        "lineage_111111111111",
        "resident_222222222222",
        "/status",
        "sample_admin",
    )]


def test_session_delivery_uses_the_common_provider_interface(monkeypatch) -> None:
    """会话型 Provider 经通用 DeliveryRequest 接口投递并返回回复与 Resident 更新"""
    calls: list[resident_execution_provider.DeliveryRequest] = []

    def deliver(
        request: resident_execution_provider.DeliveryRequest,
    ) -> resident_execution_provider.DeliveryResult:
        calls.append(request)
        return resident_execution_provider.DeliveryResult(
            assistant_message={"message_id": "message_123", "content": "done"},
            resident_updates={"session_state": "active"},
        )

    monkeypatch.setattr(
        resident_execution_provider,
        "_session_delivery",
        deliver,
    )
    provider = resident_execution_provider.provider_for({
        "backend": "codex",
        "mode": "new_session",
    })
    request = resident_execution_provider.DeliveryRequest(
        lineage_id="lineage_111111111111",
        resident_id="resident_222222222222",
        actor_id="sample_admin",
        message="hello",
        resident={"backend": "codex"},
        delivery={"job_id": "job_123"},
    )

    outcome = provider.deliver(request)

    assert calls == [request]
    assert outcome.assistant_message == {
        "message_id": "message_123",
        "content": "done",
    }
    assert outcome.resident_updates == {"session_state": "active"}


def test_execution_provider_owns_initial_instruction_transport() -> None:
    """各 Provider 决定 resident.md 随用户消息还是随系统指令送达"""
    # resident.md goes in the user message only where it stays in the session's
    # history; Claude Code (system prompt given at every process start), Kimi
    # Code (session system prompt) and the Provider API take it with Portal's
    # system instructions.
    cases = {
        ("codex", "new_session"): "user_prompt",
        ("dsh", "new_session"): "user_prompt",
        ("claude", "new_session"): "system_instructions",
        ("kimi", "new_session"): "system_instructions",
        ("provider_api", "new_session"): "system_instructions",
        ("portal", "deterministic"): "none",
    }

    for (provider_backend, mode), expected in cases.items():
        provider = resident_execution_provider.provider_for({
            "backend": provider_backend,
            "mode": mode,
        })
        assert provider.initial_instructions_transport == expected


def test_resident_delivery_routes_through_provider(monkeypatch) -> None:
    """Resident 投递交给 provider_for 选出的 Provider，并带上 Resident 记录与投递任务"""

    calls: list[resident_execution_provider.DeliveryRequest] = []

    class Provider:
        def deliver(
            self,
            request: resident_execution_provider.DeliveryRequest,
        ) -> resident_execution_provider.DeliveryResult:
            calls.append(request)
            return resident_execution_provider.DeliveryResult(
                assistant_message={"message_id": "message_reply"},
                resident_updates={"session_state": "active"},
            )

    monkeypatch.setattr(
        resident_execution_provider,
        "provider_for",
        lambda record: Provider(),
    )
    record = {
        "resident_id": "resident_222222222222",
        "lineage_id": "lineage_111111111111",
        "backend": "codex",
        "mode": "new_session",
    }
    job = {
        "actor": "sample_admin",
        "job_id": "job_123",
        "message": "hello",
    }

    assistant, updated = resident_execution_domain.send_to_resident_backend(
        record,
        job,
    )

    assert assistant == {"message_id": "message_reply"}
    assert updated == {"session_state": "active"}
    assert len(calls) == 1
    assert calls[0].resident is record
    assert calls[0].delivery is job


def test_concurrent_synchronous_duplicate_is_claimed_once(monkeypatch, tmp_path) -> None:
    """同一消息 ID 并发提交两次时同步 Provider 只投递一次"""

    resident_id = "resident_222222222222"
    message_id = "msg_3333333333333333"
    record = {
        "resident_id": resident_id,
        "lineage_id": "lineage_111111111111",
        "backend": "portal",
        "mode": "deterministic",
        "session_state": "active",
        "default_cwd": str(tmp_path),
    }
    claimed: set[str] = set()
    aggregate_lock = threading.RLock()
    delivery_started = threading.Event()
    release_delivery = threading.Event()
    delivery_calls: list[str] = []

    @contextlib.contextmanager
    def state_lock(_resident_id: str):
        with aggregate_lock:
            yield

    class Provider:
        capabilities = resident_execution_provider.ExecutionCapabilities(
            synchronous_delivery=True,
            supports_context=False,
            supports_media=False,
            supports_skills=False,
        )

    class Repository:
        def set_input_state(self, *args, **kwargs) -> None:
            return None

        def message(self, *args, **kwargs) -> None:
            return None

    def user_message(*args, **kwargs):
        return {"message_id": kwargs["message_id"], "content": args[1]}

    def existing(_resident_id, message):
        return str(message["message_id"]) in claimed

    def persist(_resident_id, message):
        claimed.add(str(message["message_id"]))
        return dict(message)

    def deliver(*args, **kwargs):
        delivery_calls.append(str(kwargs["message_id"]))
        delivery_started.set()
        assert release_delivery.wait(timeout=2)
        return {"resident_id": resident_id, "message_id": message_id}

    monkeypatch.setattr(resident_record_domain, "state_lock", state_lock)
    monkeypatch.setattr(resident_record_domain, "find_resident", lambda value: record)
    monkeypatch.setattr(resident_record_domain, "find_resident_for_admission", lambda value: record)
    monkeypatch.setattr(resident_runtime_domain, "resident_working_directory", lambda _record: tmp_path)
    monkeypatch.setattr(
        resident_intake_domain,
        "ensure_resident_visible_to_actor",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(resident_access, "can_manage", lambda *args: True)
    monkeypatch.setattr(resident_record_domain, "save_resident_record", lambda *args: None)
    monkeypatch.setattr(
        resident_execution_provider,
        "provider_for",
        lambda value: Provider(),
    )
    monkeypatch.setattr(resident_intake_domain, "_resident_user_message", user_message)
    monkeypatch.setattr(resident_intake_domain, "existing_resident_delivery", existing)
    monkeypatch.setattr(resident_lifecycle_domain, "persist_resident_user_message", persist)
    monkeypatch.setattr(
        resident_conversation_stream,
        "get_repository",
        lambda: Repository(),
    )
    monkeypatch.setattr(
        resident_intake_domain,
        "_resident_message_delivery_result",
        lambda **kwargs: dict(kwargs),
    )
    monkeypatch.setattr(
        resident_intake_domain,
        "_send_synchronous_provider_message",
        deliver,
    )

    errors: list[BaseException] = []

    def send() -> None:
        try:
            resident_intake_domain.persist_resident_input(
                resident_id,
                "sample_admin",
                "hello",
                message_id=message_id,
            )
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=send)
    second = threading.Thread(target=send)
    first.start()
    assert delivery_started.wait(timeout=2), errors
    second.start()
    second.join(timeout=2)
    release_delivery.set()
    first.join(timeout=2)

    assert errors == []
    assert not first.is_alive()
    assert not second.is_alive()
    assert delivery_calls == [message_id]
