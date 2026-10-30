"""What an Effect activation reports: its dispatch result and the latest finished activation."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from management_common.database_time import iso_time
from management_common.json_columns import json_value
from .lineage_program_runtime import _iso_now, _item_id
from .state_store import get_lineage_data_store


EFFECT_CALL_SUMMARY_FIELDS = ("position", "index", "line", "instruction", "instruction_id", "status", "error_code", "script_run_id")


def _effect_call_summary(raw: object) -> list[dict[str, Any]]:
    """The calls of an Effect's latest activation, bounded to the script call limit."""

    from .lineage_effect_script import MAX_CALLS

    result: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        entry = {
            name: (int(item.get(name) or 0) if name in {"index", "line"} else str(item.get(name) or "")[:200])
            for name in EFFECT_CALL_SUMMARY_FIELDS
            if name in item
        }
        result.append(entry)
        if len(result) >= MAX_CALLS:
            break
    return result


def effect_result_projection(
    lineage_id: str,
    effect_id: str,
    *,
    status: str,
    outputs: dict[str, Any] | None = None,
    instruction_id: str = "",
    instruction: str = "",
    error: str = "",
    calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the dispatch result; the Effect journal owns terminal persistence."""

    clean_effect_id = _item_id(effect_id, field="Program Effect id")
    clean_status = str(status or "").strip().lower()
    if clean_status not in {"executed", "failed"}:
        raise ValueError("Program Effect execution status is invalid")
    projection = {
        "status": clean_status,
        "at": _iso_now(),
        "instruction_id": str(instruction_id or "").strip(),
        "instruction": str(instruction or "").strip(),
        "outputs": deepcopy(outputs) if isinstance(outputs, dict) else {},
        "calls": _effect_call_summary(calls),
    }
    diagnostic = str(error or "").strip()
    if diagnostic:
        projection["error"] = diagnostic[:1000]
    return deepcopy(projection)


def effect_runtime_view(
    lineage_id: str,
    effect_ids: list[str] | tuple[str, ...] | set[str],
) -> dict[str, dict[str, Any]]:
    """Read durable controls and the latest completed Effect activation."""
    ids = [_item_id(value, field="Effect id") for value in effect_ids]
    if not ids:
        return {}
    with get_lineage_data_store().database.read_current() as connection:
        # Each Effect's newest finished step, not a sort of its whole history.
        rows = connection.execute(
            "SELECT effect.node_id,s.step_id,s.trigger_path_id,s.status,s.finished_at,s.result_json "
            "FROM unnest(%s::text[]) AS effect(node_id) CROSS JOIN LATERAL ("
            "SELECT s.* FROM trigger_path_steps s JOIN trigger_paths p USING(trigger_path_id) "
            "WHERE s.node_id=effect.node_id AND s.kind='effect' AND s.status IN ('completed','failed','cancelled') "
            "AND p.lineage_id=%s ORDER BY s.finished_at DESC,s.step_id DESC LIMIT 1) s", (ids, lineage_id),
        ).fetchall()
        controls = connection.execute(
            "SELECT operator_id,checkpoint_json FROM operator_work WHERE lineage_id=%s AND operator_id=ANY(%s)",
            (lineage_id, ids),
        ).fetchall()
    result = {}
    for row in rows:
        record = json_value(row["result_json"])
        calls = list((record.get("calls") or {}).values())
        last = calls[-1] if calls else {}
        failed = row["status"] == "failed"
        result[row["node_id"]] = {
            "status": "failed" if failed else "cancelled" if row["status"] == "cancelled" else "executed",
            "step_id": row["step_id"],
            "trigger_path_id": row["trigger_path_id"],
            "at": iso_time(row["finished_at"]),
            "instruction_id": last.get("instruction_id", ""),
            "instruction": last.get("instruction", ""),
            "outputs": record.get("outputs") or {},
            "calls": _effect_call_summary(calls),
        }
        if failed:
            result[row["node_id"]]["error"] = str((record.get("outputs") or {}).get("diagnostic") or record.get("failure") or "")[:1000]
    states = {row["operator_id"]: json_value(row["checkpoint_json"]) for row in controls}
    for effect_id in ids:
        state = states.get(effect_id, {})
        paused = bool(state.get("paused"))
        result.setdefault(effect_id, {}).update(
            paused=paused, state="paused" if paused else "active", paused_at=str(state.get("paused_at") or ""),
        )
    return result
