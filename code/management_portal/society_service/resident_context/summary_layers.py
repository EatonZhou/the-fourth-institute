"""Temporal memory with a shared budget and per-input consolidation."""
from __future__ import annotations

from .evidence import SUMMARY_FIELDS, character_count, check_budget, policy, utc_now, validate_summary


def project(layers: dict, resolved_threads=()) -> dict:
    result = {}
    for key in SUMMARY_FIELDS:
        values = []
        for layer in ("recent", "phase", "long_term"):
            value = layers.get(layer, {}).get(key)
            values.extend(value if isinstance(value, list) else ([value] if value else []))
        values = list(dict.fromkeys(values))
        if key == "active_threads":
            values = [value for value in values if value not in resolved_threads]
        # String fields are a current view; the older descriptions remain in
        # the temporal maintenance view, without creating another summary.
        result[key] = (values[0] if values else "") if key in {"current_focus", "branch_delta"} else values
    return result


def initial(summary: dict, updated_at: str = "") -> tuple[dict, dict]:
    timestamp = updated_at or utc_now()
    return ({"recent": summary, "phase": {}, "long_term": {}},
            {"phase_at": timestamp, "long_term_at": timestamp, "phase_rounds": 0, "long_term_rounds": 0})


def pending_rounds(state: dict, legacy_covered=()) -> dict:
    if "pending_rounds" in state:
        return state["pending_rounds"]
    # Historical counters did not identify individual inputs. Expose the
    # covered inputs conservatively for each level with an unsettled counter.
    return {level: list(legacy_covered) if state.get(f"{level}_rounds") else []
            for level in ("phase", "long_term")}


def with_pending_rounds(state: dict, pending: dict) -> dict:
    return {**state, "pending_rounds": pending,
            **{f"{level}_rounds": len(ids) for level, ids in pending.items()}}


def due(layers: dict, state: dict) -> dict:
    # Only new covered work or capacity makes a layer due. Elapsed time alone
    # never does: an idle member has nothing new to condense.
    limits = policy()
    has_recent = bool(character_count(layers.get("recent", {})))
    has_phase = bool(character_count(layers.get("phase", {})))
    long_due = (has_recent or has_phase) and state.get("long_term_rounds", 0) >= limits["long_term_rounds"]
    phase_due = has_recent and state.get("phase_rounds", 0) >= limits["phase_rounds"]
    total = character_count(layers)
    pressure = not state.get("budget_reviewed") and total >= limits["total_chars"] * .8
    return {"due": bool(long_due or phase_due or pressure),
            "level": "long_term" if long_due else "phase",
            "reason": "budget" if pressure else "rounds" if long_due or phase_due else "",
            "total_chars": total, "policy": limits}


def consolidate(layers: dict, state: dict, request: dict) -> tuple[dict, dict]:
    if not isinstance(request, dict) or set(request) - {"phase", "long_term", "resolved_threads", "consolidated_message_ids"}:
        raise ValueError("consolidation 只接受 phase、long_term、resolved_threads、consolidated_message_ids")
    if "phase" not in request:
        raise ValueError("consolidation 必须给出 phase；长期归纳可同时替换 long_term")
    ids = request.get("consolidated_message_ids")
    if not isinstance(ids, list) or any(not isinstance(mid, str) or not mid for mid in ids):
        raise ValueError("consolidated_message_ids 必须是有效输入 ID 的数组")
    submitted = set(ids)
    pending = pending_rounds(state)
    long_rounds = len(submitted.intersection(pending["long_term"]))
    if due(layers, {**state, "long_term_rounds": long_rounds})["level"] == "long_term" and "long_term" not in request:
        raise ValueError("已达到长期归纳条件，请同时提交 long_term")
    levels = [level for level in ("phase", "long_term") if level in request]
    clean = {**layers, **{level: validate_summary(request[level]) if request[level] else {} for level in levels}}
    resolved = request.get("resolved_threads", {})
    if not isinstance(resolved, dict) or any(not isinstance(v, str) or not v.strip() for v in resolved.values()):
        raise ValueError("resolved_threads 是原未决事项到解决、取消或交接说明的映射")
    closed = set(state.get("resolved_threads", []))
    old_threads = project(layers, closed)["active_threads"]
    if set(resolved) - (set(old_threads) | closed):
        raise ValueError("resolved_threads 只能关闭已有未决事项")
    closed.update(resolved)
    for level in levels:
        layer = clean[level]
        if layer:
            layer["active_threads"] = [item for item in layer.get("active_threads", []) if item not in closed]
    retained = project(clean, closed)["active_threads"]
    missing = [item for item in old_threads if item not in closed and item not in retained]
    if missing:
        clean["phase"].setdefault("active_threads", []).extend(missing)
    if not character_count(clean):
        raise ValueError("归纳结果不能为空")
    check_budget(clean)
    state = {**state, "resolved_threads": sorted(closed)}
    remaining = {**pending}
    for level in levels:
        remaining[level] = [mid for mid in pending[level] if mid not in submitted]
        if remaining[level] != pending[level]:
            state[f"{level}_at"] = utc_now()
    if not remaining["phase"]:
        state["budget_reviewed"] = True
    state = with_pending_rounds(state, remaining)
    return clean, state
