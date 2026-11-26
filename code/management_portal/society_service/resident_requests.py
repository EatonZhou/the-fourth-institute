"""Translate public answer and cancel operations into trusted request responses."""

from __future__ import annotations

from typing import Any

from management_portal.hatg.contracts import InstructionError, invalid

from . import resident_protocol


def answer(
    resident_id: str, actor: str, message_id: str, *, answers: dict[str, Any] | None = None,
    decision: str | None = None, content: Any = None,
) -> dict[str, Any]:
    if decision is None:
        if not answers or content is not None:
            raise invalid("请提供全部问题的 answers，或提供审批 decision")
        response = {"action": "answer", "answers": answers}
    else:
        if decision not in {"accept", "decline"} or answers is not None:
            raise invalid("审批 decision 只能是 accept 或 decline，并且不能同时给出 answers")
        if content is not None and decision != "accept":
            raise invalid("仅接受 MCP 请求时可以给出 content")
        response = {"action": decision}
        if content is not None:
            response["content"] = content
    return _respond(resident_id, actor, message_id, response)


def cancel(resident_id: str, actor: str, message_id: str) -> dict[str, Any]:
    return _respond(resident_id, actor, message_id, {"action": "cancel"})


def _respond(resident_id: str, actor: str, message_id: str, response: dict[str, Any]) -> dict[str, Any]:
    try:
        receipt = resident_protocol.respond_resident_user_input(resident_id, actor, {"message_id": message_id, **response})
    except ValueError as exc:
        if "已回答" in str(exc) or "失效" in str(exc):
            raise InstructionError("instruction.conflict", str(exc)) from exc
        raise
    return {**receipt, "status": str(receipt.get("status") or "")}
