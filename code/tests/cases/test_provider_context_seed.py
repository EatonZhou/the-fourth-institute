from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from management_portal.execution.backend_contracts import BackendInvocation
from management_portal.execution.backend_services import AgentSessionService
from management_portal.execution.context_seed import (
    CONTEXT_SEED_SCHEMA,
    context_seed,
    prepare_backend_context_seed,
)
from management_portal.execution.requests import build_agent_turn_request
from management_portal.execution.provider_catalog import AGENT_PROVIDERS
from management_portal.execution.session_manager import RuntimeSession
from management_portal.society_service import (
    resident_fork_domain,
    resident_record_domain,
    resident_session_backend_domain,
)

def _invocation(provider: str) -> BackendInvocation:
    request = build_agent_turn_request(
        actor="sample_admin",
        prompt="new question",
        provider=provider,
        correlation_id="provider-context-seed",
        cwd="/tmp/provider-context-seed",
        binding_id="resident:context-seed",
        metadata={
            "fork_context_input": {
                "schema": CONTEXT_SEED_SCHEMA,
                "segments": [{"segment_id": "segment-parent-window"}],
                "prompt": "new question",
            },
        },
    )
    return BackendInvocation(
        request=request,
        session=RuntimeSession(
            mode="persistent",
            binding_id="resident:context-seed",
            backend_session_id="",
            is_new_backend_session=True,
        ),
        user_prompt=request.prompt,
        system_context="",
        attach_system_context=False,
        is_new_backend_session=True,
    )


@pytest.mark.parametrize("provider", tuple(AGENT_PROVIDERS))
def test_every_agent_backend_implements_one_context_seed_contract(provider):
    """每个后端导入结构化历史或在开始本轮前报告不支持。"""
    backend = AgentSessionService().providers[provider]
    invocation = _invocation(provider)
    if provider == "codex":
        assert prepare_backend_context_seed(backend, invocation) is invocation
    else:
        with pytest.raises(ValueError, match="cannot import structured historical segments"):
            prepare_backend_context_seed(backend, invocation)


@pytest.mark.parametrize("provider", tuple(AGENT_PROVIDERS))
def test_empty_history_keeps_current_turn_envelope(provider):
    invocation = _invocation(provider)
    invocation = replace(invocation, user_prompt="current identity, authorization and message",
                         request=replace(invocation.request, metadata={"fork_context_input": {
                             "segments": [], "prompt": "new question"}}))
    assert prepare_backend_context_seed(AgentSessionService().providers[provider], invocation) is invocation


def test_context_seed_requires_adapter_support_without_provider_branching():
    """未实现上下文种子的后端在准备 Fork 输入时报错"""
    class FutureBackend:
        provider_id = "future"

    with pytest.raises(ValueError, match="does not implement"):
        prepare_backend_context_seed(FutureBackend(), _invocation("codex"))


def test_context_seed_schema_and_segment_references_are_validated():
    """种子按实际字段消费，数字标签不阻断；无关类型仍拒绝"""
    invocation = _invocation("codex")
    invalid = replace(
        invocation,
        request=replace(
            invocation.request,
            metadata={
                "fork_context_input": {
                    "schema": "tfi.provider-context-seed.v999",
                    "segments": [{"segment_id": "segment-parent-window"}],
                },
            },
        ),
    )
    assert context_seed(invalid.request)["schema"] == "tfi.provider-context-seed.v999"
    unrelated = replace(invalid.request, metadata={"fork_context_input":{
        "schema":"tfi.unrelated.v1","segments":[]}})
    with pytest.raises(ValueError, match="Unsupported"):
        context_seed(unrelated)


@pytest.mark.parametrize(
    "backend",
    tuple(AGENT_PROVIDERS),
)
@pytest.mark.parametrize(
    ("context_mode", "fork_mode"),
    (
        ("full", "full"),
        ("5", "recent-5"),
    ),
)
def test_registered_resident_derivation_uses_one_provider_neutral_seed(
    monkeypatch,
    backend,
    context_mode,
    fork_mode,
):
    """从源 Resident 初始化上下文时各 Provider 共用中立种子，不留原生 Fork 标记"""
    source = {"resident_id": "resident_111111111111", "backend": "codex"}
    target = {"resident_id": "resident_222222222222", "backend": backend}
    snapshot = {
        "provider_session_ref": "provider-session-source",
        "segments": [
            {
                "segment_id": "segment-source-input",
                "segment_type": "input",
                "provider_turn_id": "turn-source",
                "content": "inherited question",
                "metadata": {"role": "user"},
            },
            {
                "segment_id": "segment-source-model",
                "segment_type": "model",
                "provider_turn_id": "turn-source",
                "content": "inherited answer",
                "metadata": {"role": "assistant"},
            },
        ],
    }
    create_calls = []

    monkeypatch.setattr(
        resident_record_domain,
        "resolve_resident_runtime_target",
        lambda *_args, **_kwargs: SimpleNamespace(record=source),
    )
    monkeypatch.setattr(
        resident_fork_domain,
        "_fork_provider_context_snapshot",
        lambda *_args, **_kwargs: snapshot,
    )

    def create_session(**kwargs):
        create_calls.append(kwargs)
        return {"backend_session_id": "created-session"}

    monkeypatch.setattr(
        resident_session_backend_domain,
        "run_backend_session_operation",
        create_session,
    )

    resident_fork_domain.initialize_resident_context_from_source(
        target,
        source["resident_id"],
        context_mode,
        "sample_admin",
        preparation_id="preparation-source-registration",
    )

    assert target["source_resident_id"] == source["resident_id"]
    assert target["context_source_resident_id"] == source["resident_id"]
    assert target["fork_mode"] == fork_mode
    assert target["_pending_agent_data_fork"] == {
        "source_agent_id": source["resident_id"],
        "target_agent_id": target["resident_id"],
        "segment_ids": ["segment-source-input", "segment-source-model"],
    }
    assert {"native_backend_fork", "native_codex_fork", "disable_native_codex_fork",
            "codex_context_copy_required", "session_source", "fork_context"}.isdisjoint(target)
    # Registration only freezes the inherited context; the first accepted
    # input creates the Provider session and delivers the seed.
    assert create_calls == []
    assert target["session_state"] == "pending_create"
