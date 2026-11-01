from __future__ import annotations

from typing import Any, Callable

PORTAL_BACKGROUND_ACTOR = "portal"


def _resolve_agent_account(account_id: str) -> dict[str, str]:
    from management_portal.execution.accounts import default_execution_accounts
    from management_portal.execution.provider_catalog import AGENT_PROVIDERS

    account = default_execution_accounts(tolerate_unavailable=True).get(str(account_id or "").strip())
    if account.provider not in AGENT_PROVIDERS:
        raise ValueError("Agent Session requires a native execution account")
    return {"provider": account.provider, "backend_route_id": account.route_id}


def _execution_actor(actor: str) -> str:
    clean_actor = str(actor or "").strip()
    if not clean_actor.startswith("resident_"):
        return PORTAL_BACKGROUND_ACTOR
    from management_common.principal import actor_principal

    return actor_principal(clean_actor).id


def _requester_actor(actor: str) -> str:
    """The User or Resident a Lineage-bound call is made as.

    A Resident bound to a Lineage is created and addressed by its requester;
    the Portal service name cannot act on a Lineage, so it is never used here.
    """

    from management_common.principal import actor_principal

    principal = actor_principal(str(actor or "").strip())
    if principal.kind not in {"user", "resident"}:
        raise ValueError("A Lineage-bound background call needs a User or Resident requester")
    return principal.id


def _invocation_metadata(
    actor: str,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        **dict(metadata or {}),
        "background_agent_actor": str(actor or "").strip(),
    }


def _load_protocol_functions():
    from management_portal.resident_invocation import (
        ensure_invocation_resident,
        resume_and_wait,
        submit_invocation,
    )

    return ensure_invocation_resident, submit_invocation, resume_and_wait


def _permission_preset(sandbox: str) -> str:
    """Resident settings carry a permission preset, not a sandbox mode.

    Only full access has a matching preset; other sandbox requests leave the
    Resident's configured preset unchanged.
    """
    return "full-access" if str(sandbox or "").strip() in {"danger-full-access", "full-access"} else ""


def ensure_agent_session(
    *,
    actor: str,
    account_id: str,
    cwd: str,
    resident_scope: str,
    lineage_scope: str = "",
    title: str = "Background Agent",
    lineage_title: str = "Background Agents",
    project_id: str = "",
    model: str = "",
    effort: str = "",
    sandbox: str = "read-only",
    metadata: dict[str, Any] | None = None,
    lineage_binding: str = "",
) -> dict[str, Any]:
    ensure_invocation_resident, _submit_invocation, _resume_and_wait = (
        _load_protocol_functions()
    )
    policy = _resolve_agent_account(account_id)
    provider = policy["provider"]
    backend_route_id = policy["backend_route_id"]
    return dict(ensure_invocation_resident(
        actor=_requester_actor(actor) if lineage_binding else _execution_actor(actor),
        resident_scope=resident_scope,
        lineage_scope=lineage_scope,
        lineage_binding=lineage_binding,
        title=title,
        lineage_title=lineage_title,
        project_id=project_id,
        backend=provider,
        backend_route_id=str(backend_route_id or "").strip(),
        cwd=cwd,
        runtime_settings={
            key: value
            for key, value in {
                "model": model,
                "reasoning_effort": effort,
                "permission_preset": _permission_preset(sandbox),
            }.items()
            if value
        },
        metadata=_invocation_metadata(actor, metadata),
    ))


def submit_agent_session(
    *,
    actor: str,
    prompt: str,
    display_message: str = "",
    account_id: str,
    cwd: str,
    resident_scope: str,
    lineage_scope: str = "",
    title: str = "Background Agent",
    lineage_title: str = "Background Agents",
    project_id: str = "",
    model: str = "",
    effort: str = "",
    sandbox: str = "read-only",
    business_ref: str = "",
    message_id: str = "",
    metadata: dict[str, Any] | None = None,
    additional_skills: tuple[str, ...] | list[str] = (),
    input_media: list[dict[str, Any]] | None = None,
    lineage_binding: str = "",
) -> dict[str, str]:
    _ensure_invocation_resident, submit_invocation, _resume_and_wait = (
        _load_protocol_functions()
    )
    policy = _resolve_agent_account(account_id)
    provider = policy["provider"]
    backend_route_id = policy["backend_route_id"]
    execution_actor = _requester_actor(actor) if lineage_binding else _execution_actor(actor)
    handle = dict(submit_invocation(
        actor=execution_actor,
        message=str(display_message or prompt),
        resident_scope=resident_scope,
        lineage_scope=lineage_scope,
        lineage_binding=lineage_binding,
        title=title,
        lineage_title=lineage_title,
        project_id=project_id,
        backend=provider,
        backend_route_id=str(backend_route_id or "").strip(),
        cwd=cwd,
        runtime_context=(str(prompt) if display_message else ""),
        runtime_settings={
            key: value
            for key, value in {
                "model": model,
                "reasoning_effort": effort,
                "permission_preset": _permission_preset(sandbox),
            }.items()
            if value
        },
        business_ref=business_ref,
        message_id=message_id,
        metadata=_invocation_metadata(actor, metadata),
        additional_skills=additional_skills,
        input_media=input_media,
    ))
    return {**handle, "actor": execution_actor}


def wait_agent_session(handle: dict[str, str]) -> dict[str, Any]:
    _ensure_invocation_resident, _submit_invocation, resume_and_wait = (
        _load_protocol_functions()
    )
    result = resume_and_wait(
        actor=_execution_actor(str(handle.get("actor") or PORTAL_BACKGROUND_ACTOR)),
        resident_id=str(handle.get("resident_id") or ""),
        lineage_id=str(handle.get("lineage_id") or ""),
        message_id=str(handle.get("message_id") or ""),
    )
    return {
        **result,
        "response": str(result.get("message") or ""),
    }


def run_agent_session(
    *,
    actor: str,
    prompt: str,
    display_message: str = "",
    account_id: str,
    cwd: str,
    resident_scope: str,
    lineage_scope: str = "",
    title: str = "Background Agent",
    lineage_title: str = "Background Agents",
    project_id: str = "",
    model: str = "",
    effort: str = "",
    sandbox: str = "read-only",
    business_ref: str = "",
    message_id: str = "",
    metadata: dict[str, Any] | None = None,
    additional_skills: tuple[str, ...] | list[str] = (),
    input_media: list[dict[str, Any]] | None = None,
    on_submitted: Callable[[dict[str, str]], None] | None = None,
) -> dict[str, Any]:
    """Submit one durable Portal Agent Session turn."""
    handle = submit_agent_session(
        actor=actor,
        prompt=prompt,
        display_message=display_message,
        account_id=account_id,
        cwd=cwd,
        resident_scope=resident_scope,
        lineage_scope=lineage_scope,
        title=title,
        lineage_title=lineage_title,
        project_id=project_id,
        model=model,
        effort=effort,
        sandbox=sandbox,
        business_ref=business_ref,
        message_id=message_id,
        metadata=metadata,
        additional_skills=additional_skills,
        input_media=input_media,
    )
    if on_submitted is not None:
        on_submitted(handle)
    return wait_agent_session(handle)
