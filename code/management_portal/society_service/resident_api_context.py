"""Bounded history for one stateless Model API request, with no second transcript.

Only completed input/final-answer pairs are eligible. The current input is the
read fence, so retries cannot ingest their own previous output or queued inputs.
"""
from __future__ import annotations

from copy import deepcopy

HISTORY_MESSAGE_LIMIT = 200


def text_content(message):
    return "\n".join(str(part.get("text") or "") for part in message.get("content_parts", [])
                     if part.get("type") == "text").strip()


def select_history(messages, states, excluded, *, max_tokens):
    by_id = {message["message_id"]: message for message in messages}
    turns = []
    for message in messages:
        mid = message["message_id"]
        state = states.get(mid, {})
        if message.get("role") != "user" or mid in excluded or state.get("state") != "completed":
            continue
        replies = [by_id[rid] for rid in state.get("reply_message_ids", []) if rid in by_id
                   and by_id[rid].get("role") == "agent" and by_id[rid].get("reply_to_message_id") == mid]
        if not replies:
            continue
        user, answer = text_content(message), text_content(replies[-1])
        if user and answer:
            turns.append([{"role": "user", "content": user}, {"role": "assistant", "content": answer}])
    selected, used = [], 0
    for turn in reversed(turns):
        # A conservative cross-model estimate: one token per UTF-8 byte plus
        # message overhead. No tokenizer or extra model call is required.
        cost = sum(len(message["content"].encode("utf-8")) + 8 for message in turn)
        if used + cost > max_tokens:
            break
        selected.append(turn)
        used += cost
    return [message for turn in reversed(selected) for message in turn], {
        "included_turns": len(selected), "estimated_tokens": used,
        "truncated": len(selected) < len(turns) or len(messages) >= HISTORY_MESSAGE_LIMIT,
    }


def assemble_api_messages(resident_id, job, context, *, fresh_context=False):
    from . import resident_conversation_stream, resident_input_scope

    policy = context.get("history") or {}
    enabled = bool(policy.get("enabled", False)) and not fresh_context
    report = {"enabled": enabled, "max_tokens": policy.get("max_tokens", 8000),
              "included_turns": 0, "estimated_tokens": 0, "truncated": False}
    history = []
    message_id = str(job.get("user_message_id") or "")
    if enabled and message_id:
        repository = resident_conversation_stream.get_repository()
        messages = repository.messages_before_input(resident_id, message_id, limit=HISTORY_MESSAGE_LIMIT)
        ids = [message["message_id"] for message in messages if message.get("role") == "user"]
        states = repository.input_states_by_id([(resident_id, mid) for mid in ids])
        excluded = resident_input_scope.scoped_message_ids(resident_id, ids)
        history, selection = select_history(messages, {mid: states.get((resident_id, mid), {}) for mid in ids},
                                            excluded, max_tokens=report["max_tokens"])
        report.update(selection)
    presets = [] if fresh_context else deepcopy(context.get("messages") or [])
    return presets + history, report
