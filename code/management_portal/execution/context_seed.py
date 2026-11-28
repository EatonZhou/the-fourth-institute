"""Provider-neutral contract for restoring a frozen effective context window.

Portal owns the seed schema and its lifecycle.  A backend adapter owns only the
last-mile representation used by its provider.  This keeps Resident/Execution
orchestration independent from provider names and native fork APIs.
"""
from __future__ import annotations

import re

from typing import Any, Mapping, Protocol, Sequence, TypeVar


CONTEXT_SEED_SCHEMA = "tfi.provider-context-seed.v1"


def is_context_user_input(item: Mapping[str, Any]) -> bool:
    """Whether the Segment represents user content, including attached info."""
    metadata = item.get("metadata") or {}
    return (
        item.get("segment_type") == "input"
        and str(metadata.get("role") or "user") == "user"
        or item.get("segment_type") == "harness"
        and metadata.get("kind") == "effective_provider_input"
        and metadata.get("role") == "user"
    )


def is_context_turn_input(item: Mapping[str, Any]) -> bool:
    """Held info belongs to its explicit carrier; it cannot start a new turn.

    released_with is written by held-info delivery. Neither missing Provider
    receipts nor an author's identity or message text classify an input.
    """
    metadata = item.get("metadata") or {}
    message = metadata.get("message") or {}
    return is_context_user_input(item) and not message.get("released_with")


def context_turn_starts(segments: Sequence[Mapping[str, Any]]) -> list[int]:
    """Trace boundaries, grouping only inputs with a recorded common turn."""
    starts = []
    previous_turn = ""
    for index, item in enumerate(segments):
        if not is_context_turn_input(item):
            continue
        turn = str(item.get("provider_turn_id") or "")
        if not turn or turn != previous_turn:
            starts.append(index)
        previous_turn = turn
    return starts


class ContextSeedInvocation(Protocol):
    request: Any
    user_prompt: str


InvocationT = TypeVar("InvocationT", bound=ContextSeedInvocation)


def context_seed(request: Any) -> dict[str, Any]:
    """Return and validate the optional provider-neutral context seed."""

    metadata = getattr(request, "metadata", {})
    value = metadata.get("fork_context_input") if isinstance(metadata, Mapping) else None
    if not value:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("Provider context seed must be an object")
    schema = str(value.get("schema") or CONTEXT_SEED_SCHEMA)
    if not re.fullmatch(r"tfi\.provider-context-seed\.v\d+", schema):
        raise ValueError("Unsupported Provider context seed schema")
    references = value.get("segments")
    if not isinstance(references, list):
        raise ValueError("Provider context seed segments must be an array")
    for reference in references:
        segment_id = reference.get("segment_id") if isinstance(reference, Mapping) else None
        if not isinstance(segment_id, str) or not segment_id.strip():
            raise ValueError("Provider context seed contains an invalid Segment reference")
    if len({ref["segment_id"] for ref in references}) != len(references):
        raise ValueError("Provider context seed contains duplicate Segment references")
    prompt = value.get("prompt", "")
    if prompt is not None and not isinstance(prompt, str):
        raise ValueError("Provider context seed prompt must be text")
    if not references and value.get("portable_prompt") not in (None, "", prompt):
        raise ValueError("Historical context requires database Segment references")
    return {
        "schema": schema,
        "segments": [dict(item) for item in references],
        "prompt": str(prompt or ""),
    }


def preserve_structured_seed(invocation: InvocationT) -> InvocationT:
    """Validate a seed for an adapter that imports canonical typed history."""

    context_seed(invocation.request)
    return invocation


def seed_reached_provider(invocation: object) -> bool:
    """Whether an earlier attempt of this turn already delivered its seed.

    A recovered Execution resumes the Provider session and turn that its
    earlier attempt started (both are in the recovery checkpoint).  That
    session already holds the seed, so the recovered attempt sends only the
    message; sending the seed again would add the whole inherited context once
    more on every recovery.
    """

    checkpoint = getattr(invocation, "recovery_checkpoint", None) or {}
    session_id = str(checkpoint.get("backend_session_id") or "").strip()
    turn_id = str(checkpoint.get("turn_id") or "").strip()
    active_id = getattr(getattr(invocation, "session", None), "backend_session_id", "")
    if turn_id and session_id and active_id and active_id != session_id:
        raise ValueError("Recovered turn belongs to a different Provider session")
    return bool(turn_id and session_id)


def require_native_history_import(invocation: InvocationT) -> InvocationT:
    """A prompt-only backend can resume an accepted turn or start with empty history."""
    seed = context_seed(invocation.request)
    if seed.get("segments") and not seed_reached_provider(invocation):
        raise ValueError(f"{invocation.request.provider} cannot import structured historical segments")
    return invocation


def prepare_backend_context_seed(backend: object, invocation: InvocationT) -> InvocationT:
    """Invoke the stable adapter contract without branching on provider identity."""

    seed = context_seed(invocation.request)
    if not seed:
        return invocation
    prepare = getattr(backend, "prepare_context_seed", None)
    if not callable(prepare):
        raise ValueError("Agent backend does not implement Provider context seed delivery")
    return prepare(invocation)
