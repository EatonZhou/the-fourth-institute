from __future__ import annotations
from management_portal.society_service import resident_execution_domain
from management_portal.society_service import resident_execution_provider


def test_provider_capabilities_and_backend_mode_pairs_contracts(subtests, isolated_portal_case):
    """Provider 以 capabilities 声明能力，非规范或非法的 backend/mode 组合被拒绝"""
    with subtests.test(contract='test_session_provider_exposes_common_execution_contract'), isolated_portal_case() as case_path:
        provider = resident_execution_provider.provider_for({'backend': 'codex', 'mode': 'new_session'})
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
    with subtests.test(contract='test_registry_has_no_portal_backend'), isolated_portal_case() as case_path:
        try:
            resident_execution_provider.validate_registration('portal', 'deterministic')
        except ValueError as exc:
            assert 'portal/deterministic' in str(exc)
        else:
            raise AssertionError('the removed portal backend was accepted')


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
