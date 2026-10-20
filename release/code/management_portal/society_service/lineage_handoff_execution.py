"""Handoff attempt and delivery transitions; callers own aggregate commits."""

from __future__ import annotations

from typing import Any

from .lineage_values import _text


def begin_attempt(handoff: dict[str, Any], now: str) -> None:
    handoff["status"] = "preparing"
    handoff["error"] = ""
    for item in handoff.get("items") or []:
        attempts = item.get("attempts") if isinstance(item.get("attempts"), list) else []
        attempts.append({
            "number": len(attempts) + 1,
            "status": "preparing",
            "method": str((item.get("transfer") or {}).get("method") or "direct"),
            "started_at": now,
            "completed_at": "",
            "error": "",
            "result": {},
        })
        item["attempts"] = attempts
        item["result"] = {}


def fail_attempt(handoff: dict[str, Any], error: str, now: str) -> None:
    handoff["status"] = "failed"
    handoff["error"] = _text(error, 4000)
    for item in handoff.get("items") or []:
        attempts = item.get("attempts") if isinstance(item.get("attempts"), list) else []
        if attempts and attempts[-1].get("status") == "preparing":
            attempts[-1].update({
                "status": "failed", "completed_at": now, "error": _text(error, 4000),
            })


def complete_attempt(
    handoff: dict[str, Any], prepared: list[tuple[dict[str, Any], dict[str, Any]]], now: str,
) -> None:
    handoff["status"] = "ready"
    handoff["error"] = ""
    handoff["ready_at"] = now
    result_by_id = {str(item.get("item_id") or ""): result for item, result in prepared}
    for item in handoff.get("items") or []:
        result = result_by_id.get(str(item.get("item_id") or ""), {})
        item["result"] = result
        target = item.get("target") if isinstance(item.get("target"), dict) else {}
        target.update({
            "workspace_id": str(result.get("target_workspace_id") or ""),
            "path": str(result.get("target_path") or target.get("path") or ""),
            "scope": str(result.get("target_scope") or target.get("scope") or "."),
        })
        item["target"] = target
        attempts = item.get("attempts") if isinstance(item.get("attempts"), list) else []
        if attempts:
            attempts[-1].update({
                "status": "ready", "completed_at": now, "error": "", "result": result,
            })


