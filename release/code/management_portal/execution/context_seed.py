"""Provider-neutral contract for restoring a frozen effective context window.

Portal owns the seed schema and its lifecycle.  A backend adapter owns only the
last-mile representation used by its provider.  This keeps Resident/Execution
orchestration independent from provider names and native fork APIs.
"""
from __future__ import annotations

from management_common.format_tags import schema_matches

from dataclasses import replace
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
    if not schema_matches(schema, "tfi.provider-context-seed.v1"):
        raise ValueError("Unsupported Provider context seed schema")
    references = value.get("segments")
    if not isinstance(references, list):
        raise ValueError("Provider context seed segments must be an array")
    for reference in references:
        if not isinstance(reference, Mapping) or not str(reference.get("segment_id") or "").strip():
            raise ValueError("Provider context seed contains an invalid Segment reference")
    prompt = value.get("prompt", "")
    portable_prompt = value.get("portable_prompt", "")
    if prompt is not None and not isinstance(prompt, str):
        raise ValueError("Provider context seed prompt must be text")
    if portable_prompt is not None and not isinstance(portable_prompt, str):
        raise ValueError("Provider context seed portable prompt must be text")
    return {
        **dict(value),
        "schema": schema,
        "segments": [dict(item) for item in references],
        "prompt": str(prompt or ""),
        "portable_prompt": str(portable_prompt or ""),
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
    return bool(
        str(checkpoint.get("turn_id") or "").strip()
        and str(checkpoint.get("backend_session_id") or "").strip()
    )


def realize_portable_seed(invocation: InvocationT) -> InvocationT:
    """Realize the same seed through its provider-neutral transcript envelope.

    The portable prompt is the inherited transcript followed by the seed's
    prompt.  The transcript goes in front of this turn's own prompt, which
    keeps what Portal put around the message for the turn: the Fork target's
    own runtime identity and authorization context.
    """

    seed = context_seed(invocation.request)
    if not seed or seed_reached_provider(invocation):
        return invocation
    portable_prompt, prompt = seed["portable_prompt"], seed["prompt"]
    if not portable_prompt or not portable_prompt.endswith(prompt):
        raise ValueError(
            "This backend requires a portable Provider context seed representation"
        )
    transcript = portable_prompt[: len(portable_prompt) - len(prompt)]
    return replace(invocation, user_prompt=transcript + invocation.user_prompt)


def prepare_backend_context_seed(backend: object, invocation: InvocationT) -> InvocationT:
    """Invoke the stable adapter contract without branching on provider identity."""

    seed = context_seed(invocation.request)
    if not seed:
        return invocation
    prepare = getattr(backend, "prepare_context_seed", None)
    if not callable(prepare):
        raise ValueError("Agent backend does not implement Provider context seed delivery")
    return prepare(invocation)
