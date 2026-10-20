from __future__ import annotations

from typing import Any

from management_portal.execution.session_manager import SessionManager




def service():
    # Resolve lazily so tests and service-process bootstraps can replace the
    # ExecutionService transport without importing it into domain modules.
    from management_portal.execution.service import ExecutionService

    return ExecutionService()


def is_unavailable(error: BaseException) -> bool:
    from management_portal.execution.transport import ExecutionServiceUnavailable

    return isinstance(error, ExecutionServiceUnavailable)


def sessions() -> SessionManager:
    return SessionManager()


def health() -> dict[str, Any]:
    return service().health()


def configure_binding_quota_overrides(policies: list[dict[str, Any]]) -> dict[str, Any]:
    return service().configure_binding_quota_overrides(policies)


def forget_binding_quota_overrides(binding_ids: list[str]) -> dict[str, Any]:
    return service().forget_binding_quota_overrides(binding_ids)


def token_usage(
    *,
    start_hour: str,
    end_hour: str,
    provider: str = "codex",
    include_accounts: bool = False,
) -> dict[str, Any]:
    return service().token_usage(
        start_hour=start_hour,
        end_hour=end_hour,
        provider=provider,
        include_accounts=include_accounts,
    )


def fork_agent_data(source_agent_id: str, target_agent_id: str, *, actor: str,
                    segment_ids: list[str]) -> dict[str, Any]:
    from management_portal.execution.agent_data_store import get_agent_data_store
    return get_agent_data_store().fork_agent(source_agent_id=source_agent_id,
        target_agent_id=target_agent_id, segment_ids=segment_ids, actor_ref=actor)


def fork_inheritance(agent_id: str, *, include_compaction: bool = False) -> dict[str, Any]:
    from management_portal.execution.agent_data_store import get_agent_data_store
    return get_agent_data_store().fork_inheritance(agent_id, include_compaction=include_compaction)


def agent_context_snapshot(agent_id: str, **kwargs: Any) -> dict[str, Any]:
    from management_portal.execution.agent_data_store import get_agent_data_store
    return get_agent_data_store().agent_context_snapshot(agent_id, **kwargs)


def context_evidence_snapshot(agent_id: str) -> dict[str, Any]:
    from management_portal.execution.agent_data_store import get_agent_data_store
    return get_agent_data_store().context_evidence_snapshot(agent_id)


def input_source_segment_id(agent_id: str, message_id: str) -> str:
    from management_portal.execution.agent_data_store import get_agent_data_store
    store = get_agent_data_store()
    message = store.chat_message(agent_id, message_id)
    if not message:
        from .resident_conversation_stream import get_repository
        accepted = get_repository().message(agent_id, message_id)
        if accepted:
            message = store.display_chat_message(agent_id, accepted)
    return str((message or {}).get("segment_id") or "")


def latest_provider_session_ref(agent_id: str, *, since: str = "") -> str:
    from management_portal.execution.agent_data_store import get_agent_data_store

    return get_agent_data_store().latest_provider_session_ref(agent_id, since=since)


def latest_context_compaction(agent_id: str, *, since: str = "") -> dict[str, Any] | None:
    from management_portal.execution.agent_data_store import get_agent_data_store

    return get_agent_data_store().latest_context_compaction(agent_id, since=since)


def initialization_context(agent_id: str, binding_id: str) -> dict[str, Any]:
    from management_portal.execution.agent_data_store import get_agent_data_store
    store = get_agent_data_store()
    return {**store.initialization_session_fact(agent_id, binding_id),
            'segment_ids': store.frozen_fork_segments(agent_id)}


def frozen_initialization_segments(agent_id: str) -> list[dict[str, str]]:
    from management_portal.execution.agent_data_store import get_agent_data_store
    return get_agent_data_store().frozen_fork_references(agent_id)


def promote_initialization_session(**kwargs: Any) -> dict[str, Any]:
    from management_portal.execution.agent_data_store import get_agent_data_store
    return get_agent_data_store().promote_initialization_session(**kwargs)


def initialization_execution(execution_id: str) -> dict[str, Any]:
    from management_portal.execution.agent_data_store import get_agent_data_store
    return get_agent_data_store().execution(execution_id) or {}


def initialization_session_fact(agent_id: str, binding_id: str) -> dict[str, Any]:
    from management_portal.execution.agent_data_store import get_agent_data_store
    return get_agent_data_store().initialization_session_fact(agent_id, binding_id)


def message_in_context(agent_id: str, message_id: str, *, since: str = "") -> bool:
    from management_portal.execution.agent_data_store import get_agent_data_store

    return get_agent_data_store().message_in_context(agent_id, message_id, since=since)



def provider_user_prompts(
    *,
    provider: str,
    backend_session_id: str,
) -> list[dict[str, Any]]:
    from management_portal.execution.agent_data_store import get_agent_data_store

    return get_agent_data_store().provider_user_prompts(
        provider=provider,
        backend_session_id=backend_session_id,
    )




def message_provider_prompts(agent_id: str, message_id: str) -> list[dict[str, Any]]:
    from management_portal.execution.agent_data_store import get_agent_data_store

    return get_agent_data_store().message_provider_prompts(agent_id, message_id)


def reset_provider_context(agent_id: str, *, reset_at: str, actor_ref: str) -> str:
    from management_portal.execution.agent_data_store import get_agent_data_store

    return get_agent_data_store().reset_provider_context(agent_id, reset_at=reset_at, actor_ref=actor_ref)
