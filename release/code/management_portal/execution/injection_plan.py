"""Which caller-materialized parts a turn carries, and where they go.

A part is due on the session's first injection; a part timed
``session_start_and_after_compaction`` is due again on the first turn after
the session's context was compacted since the last injection; an
``every_turn`` part is due on every turn.  ``system`` parts reach the
Provider's system location, prompt parts go before or after the turn's
prompt (decision_ef1d6c925955).
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from .contracts import InjectionPart


@dataclass(frozen=True)
class InjectionPlan:
    # The system parts due this turn, for Providers whose session keeps them.
    system_context: str
    # Every system part, for Providers given their system prompt anew at
    # every process start (Claude Code).
    session_system_context: str
    user_prompt: str
    attach_system_context: bool
    # Whether this turn carries any part: it counts as an injection.
    attached: bool
    attached_parts: tuple[str, ...]
    fingerprint: str


def _instant(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def compacted_since(compacted_at: str, injected_at: str) -> bool:
    """Whether the context was compacted after the last injection."""

    compacted = _instant(compacted_at)
    if compacted is None:
        return False
    injected = _instant(injected_at)
    return injected is None or compacted > injected


def _join(parts: Sequence[InjectionPart], separator: str) -> str:
    return separator.join(part.content.strip() for part in parts if part.content.strip())


def plan_injection(
    parts: Sequence[InjectionPart],
    prompt: str,
    *,
    generation: bool,
    first_injection: bool,
    injected_at: str = "",
    compacted_at: str = "",
    system_already_attached: bool = False,
    system_context_survives_compaction: bool = False,
) -> InjectionPlan:
    """Plan one execution's injected input.

    ``system_already_attached`` is a resumed turn whose system parts the
    Provider already received; its prompt parts are planned again exactly as
    in the first attempt, since the session state they depend on only changes
    when the turn completes.
    """

    system_parts = [part for part in parts if part.placement == "system"]
    session_system_context = _join(system_parts, "\n\n")
    fingerprint = (
        hashlib.sha256(session_system_context.encode("utf-8")).hexdigest()
        if session_system_context else ""
    )
    if not generation:
        return InjectionPlan("", session_system_context, prompt, False, False, (), fingerprint)
    recompact = compacted_since(compacted_at, injected_at)

    def due(part: InjectionPart) -> bool:
        return (
            part.timing == "every_turn"
            or first_injection
            or (part.timing == "session_start_and_after_compaction" and recompact
                and not (part.placement == "system" and system_context_survives_compaction))
        )

    due_parts = [part for part in parts if due(part)]
    due_system = [part for part in due_parts if part.placement == "system"]
    prefix = _join([part for part in due_parts if part.placement == "prompt_prefix"], "\n")
    suffix = _join([part for part in due_parts if part.placement == "prompt_suffix"], "\n\n")
    user_prompt = prompt
    if prefix:
        user_prompt = f"{prefix}\n\n{user_prompt}"
    if suffix:
        user_prompt = f"{user_prompt}\n\n{suffix}"
    attach_system = bool(due_system) and not system_already_attached
    return InjectionPlan(
        system_context=_join(due_system, "\n\n"),
        session_system_context=session_system_context,
        user_prompt=user_prompt,
        attach_system_context=attach_system,
        attached=bool(due_parts),
        attached_parts=tuple(part.name for part in due_parts),
        fingerprint=fingerprint,
    )
