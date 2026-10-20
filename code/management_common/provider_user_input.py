from __future__ import annotations

import copy
import hashlib
from typing import Any


REQUEST_ITEM = "userInputRequest"
RESULT_ITEM = "userInputResponse"
RESPONSE_INPUT = "user_input_response"


def normalize_request(params: dict[str, Any]) -> dict[str, Any]:
    if not all(isinstance(params.get(key), str) and params[key] for key in ("threadId", "turnId", "itemId")):
        raise ValueError("user input request requires thread, turn and item identities")
    if not isinstance(params.get("isBlocking"), bool):
        raise ValueError("user input request requires isBlocking")
    questions = params.get("questions")
    if not isinstance(questions, list) or not 1 <= len(questions) <= 3:
        raise ValueError("user input request requires one to three questions")
    identifiers = set()
    for question in questions:
        if not isinstance(question, dict):
            raise ValueError("invalid user input question")
        for field in ("id", "header", "question"):
            if not isinstance(question.get(field), str) or not question[field] or len(question[field]) > 8000:
                raise ValueError("invalid user input question field")
        if question["id"] in identifiers:
            raise ValueError("duplicate user input question identity")
        identifiers.add(question["id"])
        options = question.get("options") or []
        if not isinstance(options, list) or len(options) > 20:
            raise ValueError("invalid user input options")
        for option in options:
            if not isinstance(option, dict) or not isinstance(option.get("label"), str) or not option["label"]:
                raise ValueError("invalid user input option")
        if not isinstance(question.get("isSecret", False), bool):
            raise ValueError("invalid secret input flag")
        if question.get("isSecret"):
            raise ValueError("secret user input requires a protected input channel")
    return copy.deepcopy(params)


def validate_response(value: dict[str, Any], questions: list[dict[str, Any]] | None = None,
                      interaction: dict[str, Any] | None = None) -> dict[str, Any]:
    request_id = value.get("request_id")
    if not isinstance(request_id, str) or not request_id or len(request_id) > 160:
        raise ValueError("user input response requires request identity")
    if interaction is not None or value.get("interaction_response") is True:
        from .provider_interactions import interaction_response

        if interaction is None and questions is not None:
            raise ValueError("this request requires question answers")
        return interaction_response(value, interaction)
    action = value.get("action", "answer")
    if action not in {"answer", "cancel"}:
        raise ValueError("unsupported user input action")
    answers = value.get("answers", {})
    if not isinstance(answers, dict) or len(answers) > 3:
        raise ValueError("invalid user input answers")
    if action == "answer":
        if not answers or (questions is not None and set(answers) != {question["id"] for question in questions}):
            raise ValueError("answer every question before submitting")
        for key, answer in answers.items():
            values = answer.get("answers") if isinstance(answer, dict) else None
            if not isinstance(key, str) or not isinstance(values, list) or not 1 <= len(values) <= 20:
                raise ValueError("invalid user input answer")
            if any(not isinstance(text, str) or not text.strip() or len(text) > 16000 for text in values):
                raise ValueError("invalid user input answer text")
    elif answers:
        raise ValueError("cancelled user input cannot include answers")
    return {"type": RESPONSE_INPUT, "request_id": request_id, "action": action, "answers": copy.deepcopy(answers)}


def segment_id(execution_id: str, request_id: str, *, result: bool = False) -> str:
    value = f"provider-user-input:{execution_id}:{request_id}:{'result' if result else 'request'}"
    return "segment_" + hashlib.sha256(value.encode()).hexdigest()[:24]


def content_part(item: dict[str, Any], execution_id: str) -> dict[str, Any]:
    payload = copy.deepcopy(item["user_input"])
    is_result = item["type"] == RESULT_ITEM
    request_id = payload["request_id"]
    return {
        "type": "tool_result" if is_result else "tool_call",
        "tool_name": ("mcp_elicitation" if payload.get("interaction", {}).get("kind") == "mcp"
                      else "request_approval" if payload.get("interaction") else "request_user_input"),
        "execution_id": execution_id,
        "segment_id": segment_id(execution_id, request_id, result=is_result),
        "result_segment_id": segment_id(execution_id, request_id, result=True),
        **payload,
    }
