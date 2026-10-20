from __future__ import annotations
from management_portal.society_service import resident_backend

from dataclasses import replace
from types import SimpleNamespace

import pytest

from management_portal.execution.backends import (
    AgentSessionService,
    BackendInvocation,
)
from management_portal.execution.context_seed import (
    CONTEXT_SEED_SCHEMA,
    context_seed,
    prepare_backend_context_seed,
)
from management_portal.execution.requests import build_agent_turn_request
from management_portal.execution.provider_catalog import AGENT_PROVIDERS
from management_portal.execution.session_manager import RuntimeSession
from management_portal.society_service import resident_directory_domain, resident_execution_domain, resident_fork_domain, resident_runtime_domain, resident_session_backend_domain, resident_workspace_domain

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
                "portable_prompt": "<inherited>effective parent window</inherited>\n\nnew question",
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
    """每个 Provider 后端都实现上下文种子：codex 保留原提示，其余把继承记录放在提示前"""
    service = AgentSessionService()
    backend = service.providers[provider]
    invocation = _invocation(provider)

    prepared = prepare_backend_context_seed(backend, invocation)

    assert context_seed(prepared.request)["schema"] == CONTEXT_SEED_SCHEMA
    if provider == "codex":
        assert prepared.user_prompt == "new question"
    else:
        assert prepared.user_prompt.startswith("<inherited>effective parent window")
        assert prepared.request.prompt == "new question"


@pytest.mark.parametrize("provider", [name for name in AGENT_PROVIDERS if name != "codex"])
def test_a_portable_seed_keeps_what_portal_put_around_the_fork_targets_message(provider):
    """可移植上下文种子保留 Fork 目标本轮的身份与授权上下文

    继承记录放在本轮提示最前面，后面保留 Portal 为 Fork 目标放上的运行身份、消息和授权上下文。

    :module: execution.providers
    :importance: P1
    :aspects: functional, behavior
    :design: decision_276105b5d67b
    """
    # The Fork target's own identity and authorization context, as the injection plan placed them.
    planned = (
        "<resident-runtime-identity>resident_id: resident_child</resident-runtime-identity>\n\n"
        "new question\n\n<portal-authorization-context>本轮执行者：resident_child</portal-authorization-context>"
    )
    invocation = replace(_invocation(provider), user_prompt=planned)

    prepared = prepare_backend_context_seed(AgentSessionService().providers[provider], invocation)

    assert prepared.user_prompt == "<inherited>effective parent window</inherited>\n\n" + planned


def test_context_seed_requires_adapter_support_without_provider_branching():
    """未实现上下文种子的后端在准备 Fork 输入时报错"""
    class FutureBackend:
        provider_id = "future"

    with pytest.raises(ValueError, match="does not implement"):
        prepare_backend_context_seed(FutureBackend(), _invocation("codex"))


def test_context_seed_schema_and_segment_references_are_validated():
    """上下文种子的 schema 版本不受支持时被拒绝"""
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
    with pytest.raises(ValueError, match="Unsupported"):
        context_seed(invalid.request)


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
    created = False
    create_calls = []

    monkeypatch.setattr(
        resident_workspace_domain,
        "resolve_resident_runtime_target",
        lambda *_args, **_kwargs: SimpleNamespace(record=source),
    )
    monkeypatch.setattr(
        resident_fork_domain,
        "_fork_provider_context_snapshot",
        lambda *_args, **_kwargs: snapshot,
    )
    monkeypatch.setattr(
        resident_execution_domain,
        "_resident_execution_runtime_options",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        resident_directory_domain,
        "_has_resident_workspace_authority",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        resident_runtime_domain,
        "_resident_working_directory",
        lambda *_args, **_kwargs: "/tmp/provider-context-seed",
    )
    monkeypatch.setattr(
        resident_backend,
        "record_session_id",
        lambda record: "created-session" if record is target and created else "",
    )
    monkeypatch.setattr(resident_backend, "execution_account", lambda _record: {})

    def create_session(**kwargs):
        nonlocal created
        created = True
        create_calls.append(kwargs)
        return {"backend_session_id": "created-session"}

    monkeypatch.setattr(
        resident_session_backend_domain,
        "_run_backend_session_operation",
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
    definition = AGENT_PROVIDERS[backend]
    assert len(create_calls) == (1 if "create" in definition.operations else 0)
    if create_calls:
        assert create_calls[0]["operation"] == "create"
        assert create_calls[0]["backend"] == definition.provider
        assert create_calls[0]["preparation_id"] == "preparation-source-registration"
