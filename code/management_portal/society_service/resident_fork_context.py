"""Fork snapshot admission and initial compaction state."""
from __future__ import annotations

from typing import Any, Mapping


def require_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Admit the canonical context shared by Fork and account switching."""
    if not snapshot or (not snapshot.get("segments") and not snapshot.get("empty_context")):
        raise ValueError("Fork 来源缺少 PostgreSQL 中的有效模型上下文；不能创建空白分支")
    return snapshot


def requires_initial_context_compaction(state: Mapping[str, Any]) -> bool:
    return (
        str(state.get("context_compaction_policy") or "").strip() == "before_first_turn"
    )


def initial_context_compaction_completed(state: Mapping[str, Any]) -> bool:
    return (
        requires_initial_context_compaction(state)
        and str(state.get("context_compaction_state") or "").strip() == "completed"
        and bool(str(state.get("context_compacted_at") or "").strip())
    )
