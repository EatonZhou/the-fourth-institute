"""Bounded temporal memory. Policy is mechanical; agents choose the synthesis."""
from __future__ import annotations

import os
from datetime import datetime, timezone


def policy() -> dict:
    defaults = {"total_chars": 3000, "phase_rounds": 20, "phase_seconds": 86400,
                "long_term_rounds": 100, "long_term_seconds": 604800}
    result = {}
    for key, default in defaults.items():
        value = int(os.environ.get("PORTAL_SUMMARY_" + key.upper(), default))
        if value <= 0 or (key == "total_chars" and value < 400):
            raise ValueError("摘要策略必须为正数，total_chars 至少 400")
        result[key] = value
    result["higher_layer_chars"] = result["total_chars"] * 3 // 4
    return result


def character_count(value) -> int:
    """Count all text values, including repeated text across fields/layers."""
    if isinstance(value, str):
        return len(value)
    if isinstance(value, dict):
        return sum(character_count(item) for item in value.values())
    if isinstance(value, list):
        return sum(character_count(item) for item in value)
    return 0


def check_budget(layers: dict) -> None:
    limits = policy()
    size = character_count(layers)
    if size > limits["total_chars"]:
        raise ValueError(f"摘要各时间层、所有字段合计 {size} 字符，超过总预算 {limits['total_chars']}；请归纳重写，不能增量堆积")


def project(layers: dict) -> dict:
    from .evidence import SUMMARY_FIELDS
    result = {}
    for key in SUMMARY_FIELDS:
        values = []
        for layer in ("recent", "phase", "long_term"):
            value = layers.get(layer, {}).get(key)
            values.extend(value if isinstance(value, list) else ([value] if value else []))
        values = list(dict.fromkeys(values))
        # String fields are a current view; the older descriptions remain in
        # the temporal maintenance view, without creating another summary.
        result[key] = (values[0] if values else "") if key in {"current_focus", "branch_delta"} else values
    return result


def initial(summary: dict, updated_at: str = "") -> tuple[dict, dict]:
    from .evidence import utc_now
    timestamp = updated_at or utc_now()
    return ({"recent": summary, "phase": {}, "long_term": {}},
            {"phase_at": timestamp, "long_term_at": timestamp, "phase_rounds": 0, "long_term_rounds": 0})


def due(layers: dict, state: dict, *, now: datetime | None = None) -> dict:
    limits = policy()
    now = now or datetime.now(timezone.utc)
    def elapsed(key):
        stamp = state.get(key)
        return (now - datetime.fromisoformat(stamp.replace("Z", "+00:00"))).total_seconds() if stamp else 0
    has_recent = bool(character_count(layers.get("recent", {})))
    has_phase = bool(character_count(layers.get("phase", {})))
    long_due = (has_recent or has_phase) and (
        state.get("long_term_rounds", 0) >= limits["long_term_rounds"]
        or elapsed("long_term_at") >= limits["long_term_seconds"])
    phase_due = has_recent and (state.get("phase_rounds", 0) >= limits["phase_rounds"]
                               or elapsed("phase_at") >= limits["phase_seconds"])
    pressure = character_count(layers) >= limits["total_chars"] * .8
    return {"due": bool(long_due or phase_due or pressure),
            "level": "long_term" if long_due else "phase",
            "reason": "budget" if pressure else "interval" if long_due or phase_due else "",
            "total_chars": character_count(layers), "policy": limits}


def consolidate(layers: dict, state: dict, request: dict) -> tuple[dict, dict]:
    from .evidence import utc_now, validate_summary
    if not isinstance(request, dict) or set(request) - {"phase", "long_term", "resolved_threads"}:
        raise ValueError("consolidation 只接受 phase、long_term、resolved_threads")
    if "phase" not in request:
        raise ValueError("consolidation 必须给出 phase；长期归纳可同时替换 long_term")
    if due(layers, state)["level"] == "long_term" and "long_term" not in request:
        raise ValueError("已达到长期归纳条件，请同时提交 long_term")
    clean = {"recent": {}, "phase": validate_summary(request["phase"]) if request["phase"] else {},
             "long_term": layers.get("long_term", {})}
    if "long_term" in request:
        clean["long_term"] = validate_summary(request["long_term"]) if request["long_term"] else {}
    resolved = request.get("resolved_threads", {})
    if not isinstance(resolved, dict) or any(not isinstance(v, str) or not v.strip() for v in resolved.values()):
        raise ValueError("resolved_threads 是原未决事项到解决、取消或交接说明的映射")
    old_threads = project(layers).get("active_threads", [])
    if set(resolved) - set(old_threads):
        raise ValueError("resolved_threads 只能关闭已有未决事项")
    for name, layer in clean.items():
        clean[name] = {**layer, "active_threads": [item for item in layer.get("active_threads", []) if item not in resolved]} if layer else {}
    retained = project(clean).get("active_threads", [])
    missing = [item for item in old_threads if item not in resolved and item not in retained]
    if missing:
        clean["phase"].setdefault("active_threads", []).extend(missing)
    if not character_count(clean):
        raise ValueError("归纳结果不能为空")
    check_budget(clean)
    if character_count(clean) > policy()["higher_layer_chars"]:
        raise ValueError("阶段与长期层合计最多占总预算 75%，须为近期事实留出空间")
    now = utc_now()
    state = {**state, "phase_at": now, "phase_rounds": 0}
    if "long_term" in request:
        state.update(long_term_at=now, long_term_rounds=0)
    return clean, state
