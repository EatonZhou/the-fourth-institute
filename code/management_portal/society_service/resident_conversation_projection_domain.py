"""Resident domain responsibilities: conversation projection."""
from __future__ import annotations

from management_common.principal import is_user_or_resident

from . import resident_backend, resident_conversation_stream
from .domain_ports import execution_port
from datetime import datetime, timezone
from difflib import SequenceMatcher
from management_common.actor_identity import canonical_actor_id
import re
from pathlib import Path
from typing import Any, Mapping


MAX_MESSAGE_CHARS = 12000

GENERATED_DERIVED_RESIDENT_TITLE_RE = re.compile(
    r"^(?:派生 Resident \d+|Fork \d+|Fork-\d+ \d+|Fork from here \d+|Fork selected \d+)"
    r" · \d{4}-\d{2}-\d{2}$"
)


def _parse_iso(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


_EARLIEST = datetime.min.replace(tzinfo=timezone.utc)


def date_part(value: str) -> str:
    parsed = _parse_iso(value)
    if parsed:
        return parsed.date().isoformat()
    return str(value or "").split("T", 1)[0] or "unknown date"


def is_generated_derived_resident_title(value: object) -> bool:
    """Whether a derived Resident still has its automatic creation label."""

    return bool(
        GENERATED_DERIVED_RESIDENT_TITLE_RE.fullmatch(
            str(value or "").strip()
        )
    )


def derived_resident_title_is_generated(resident: Mapping[str, Any]) -> bool:
    origin = str(resident.get("title_origin") or "").strip().lower()
    if origin:
        return origin == "generated"
    return is_generated_derived_resident_title(resident.get("title"))


def truncate_text(text: str, limit: int = MAX_MESSAGE_CHARS) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "\n\n[content truncated for portal preview]"


def _terminal_input_unread_signature(
    actor: str,
    request: object,
    input_state: object,
) -> dict[str, str | int]:
    """Build one unread version from a canonical notifiable Input outcome."""

    actor_id = canonical_actor_id(actor)
    signature: dict[str, str | int] = {
        "schema_version": 1,
        "request_actor": actor_id,
        "completion_id": "",
        "input_message_id": "",
        "completed_at": "",
    }
    request_value = request if isinstance(request, dict) else {}
    state_value = input_state if isinstance(input_state, dict) else {}
    message_id = str(request_value.get("message_id") or "")
    state_message_id = str(state_value.get("message_id") or "")
    terminal_state = str(state_value.get("state") or "").strip().lower()
    if (
        not actor_id
        or not message_id
        or state_message_id != message_id
        or terminal_state not in resident_conversation_stream.TERMINAL_INPUT_STATES
    ):
        return signature
    reply_ids = [
        str(item or "").strip()
        for item in state_value.get("reply_message_ids") or []
        if str(item or "").strip()
    ]
    if terminal_state == "completed" and not reply_ids:
        return signature
    signature.update({
        "completion_id": (
            reply_ids[-1]
            if reply_ids
            else f"resident-input-terminal:{message_id}:{terminal_state}"
        ),
        "input_message_id": message_id,
        "completed_at": str(state_value.get("updated_at") or ""),
    })
    return signature


def conversation_family_summaries(
    resident_ids: list[str],
    actor: str,
) -> dict[str, dict[str, Any]]:
    """Read bounded conversation activity facts from AgentData.

    A sender's conversation with a Family is its facade (decision_44cf587c1721):
    the copies the facade relays to members are not the sender's requests
    there, and a facade request is answered when its member finishes it.
    """

    from . import family_facade

    requested = list(dict.fromkeys(item for item in resident_ids if item))
    actor_id = canonical_actor_id(actor)
    repository = resident_conversation_stream.get_repository()
    summaries = repository.conversation_summaries(
        requested, actor_id, excluded_business_ref=family_facade.RELAY_REF.pattern,
    )
    requests = {
        resident_id: summary["actor_request_ids"]
        for resident_id, summary in summaries.items()
        if summary["actor_request_ids"]
    }
    input_states = repository.latest_notifiable_input_states_for_messages(requests)
    outcomes = family_facade.relayed_outcomes(requests)
    for facade_id, outcome in outcomes.items():
        own = input_states.get(facade_id) or {}
        # A routing failure is the facade's own outcome; a member's answer comes later.
        if outcome and (str(outcome.get("updated_at") or ""), str(outcome.get("message_id") or "")) > (
            str(own.get("updated_at") or ""), str(own.get("message_id") or ""),
        ):
            input_states[facade_id] = outcome
    for resident_id, summary in summaries.items():
        summary.pop("actor_request_ids")
        input_state = input_states.get(resident_id) or {}
        signature = _terminal_input_unread_signature(
            actor_id,
            {"message_id": input_state.get("message_id")},
            input_state,
        )
        # A message sent to the actor is itself worth reading (decision_b4e5ea4a47ec).
        addressed_id = str(summary.get("actor_addressed_message_id") or "")
        addressed_at = str(summary.get("actor_addressed_at") or "")
        if addressed_id and (_parse_iso(addressed_at) or _EARLIEST, addressed_id) > (
            _parse_iso(str(signature["completed_at"])) or _EARLIEST, str(signature["completion_id"]),
        ):
            signature.update(completion_id=addressed_id, input_message_id="", completed_at=addressed_at)
        summary["unread_signature"] = signature
    return summaries


def path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def can_view_actual_sent_prompts(actor: str, resident_id: str) -> bool:
    return is_user_or_resident(actor)


def _compact_match_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _read_provider_actual_sent_prompts(
    provider: str,
    session_id: str,
) -> list[dict[str, Any]]:
    return actual_sent_prompt_candidates(execution_port.provider_user_prompts(
        provider=provider,
        backend_session_id=session_id,
    ))


def actual_sent_prompt_candidates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The prompts a Provider session's transcript holds, as candidates for its user messages."""
    prompts: list[dict[str, Any]] = []
    for item in items:
        prompt = str(
            item.get("actual_sent_prompt") or item.get("content") or ""
        )
        if not prompt:
            continue
        prompts.append({
            "actual_sent_prompt": prompt,
            "created_at": str(item.get("created_at") or ""),
            "source": "provider_transcript",
            "event_path": str(item.get("event_path") or ""),
            "event_line": int(
                item.get("event_line")
                or item.get("_transcript_ordinal")
                or 0
            ),
        })
    return prompts


def _actual_sent_prompt_matches(item: dict[str, Any], candidate: dict[str, Any]) -> bool:
    from . import resident_workspace_domain
    item_id = str(item.get("message_id") or "")
    candidate_id = str(candidate.get("user_message_id") or "")
    if item_id and candidate_id and item_id == candidate_id:
        return True
    item_at = _parse_iso(str(item.get("created_at") or ""))
    candidate_at = _parse_iso(str(candidate.get("created_at") or ""))
    if item_at and candidate_at and abs((candidate_at - item_at).total_seconds()) > 120:
        return False
    content = resident_workspace_domain.protocol_message_text(item)
    if not content:
        return False
    raw = str(candidate.get("raw_message") or "").strip()
    if raw and raw == content:
        return True
    actual_sent_prompt = str(candidate.get("actual_sent_prompt") or "")
    if content and content in actual_sent_prompt:
        return True
    content_norm = _compact_match_text(content)
    if not content_norm:
        return False
    if content_norm in _compact_match_text(raw) or content_norm in _compact_match_text(actual_sent_prompt):
        return True
    return bool(raw and SequenceMatcher(None, content_norm, _compact_match_text(raw)).ratio() >= 0.92)


def attach_actual_sent_prompts(
    messages: list[dict],
    *,
    session_ids: list[str] | None = None,
    provider: str = "codex",
    candidates: list[dict[str, Any]] | None = None,
) -> None:
    if not messages:
        return
    seen_sessions: set[str] = set()
    candidates = list(candidates or [])
    for session_id in session_ids or []:
        safe_id = resident_backend.session_id(session_id)
        if not safe_id or safe_id in seen_sessions:
            continue
        seen_sessions.add(safe_id)
        candidates.extend(
            _read_provider_actual_sent_prompts(provider, safe_id)
        )
    if not candidates:
        return
    used_candidates: set[int] = set()
    for item in messages:
        if str(item.get("role") or "").strip().lower() != "user":
            continue
        if item.get("actual_sent_prompt"):
            continue
        item_at = _parse_iso(str(item.get("created_at") or ""))
        matches: list[tuple[tuple[int, float, int], int, dict[str, Any]]] = []
        for index, candidate in enumerate(candidates):
            if index in used_candidates or not _actual_sent_prompt_matches(item, candidate):
                continue
            candidate_at = _parse_iso(str(candidate.get("created_at") or ""))
            time_distance = (
                abs((candidate_at - item_at).total_seconds())
                if item_at and candidate_at
                else float("inf")
            )
            item_id = str(item.get("message_id") or "")
            exact_id = bool(
                item_id
                and item_id == str(candidate.get("user_message_id") or "")
            )
            matches.append(((0 if exact_id else 1, time_distance, index), index, candidate))
        if not matches:
            continue
        _score, match_index, match = min(matches, key=lambda entry: entry[0])
        used_candidates.add(match_index)
        actual_sent_prompt = str(match.get("actual_sent_prompt") or "")
        item["actual_sent_prompt"] = actual_sent_prompt
        item["actual_sent_prompt_length"] = len(actual_sent_prompt)
        item["actual_sent_prompt_source"] = str(match.get("source") or "")
        if match.get("actual_sent_prompt_path"):
            item["actual_sent_prompt_path"] = str(match.get("actual_sent_prompt_path") or "")
        if match.get("event_path"):
            item["actual_sent_prompt_event_path"] = str(match.get("event_path") or "")
            item["actual_sent_prompt_event_line"] = match.get("event_line")
