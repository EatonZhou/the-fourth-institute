"""The Agent Harness card of one turn.

When a turn ends the Harness reports which of its features took part
(``agent_harness_completed``).  The card names the features that applied or
had news, what they saved, and the turn's Provider time and input tokens.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .app_server_event_projection import token_usage
from .runtime_details import EventContext, mapping, nonnegative_int

# Reasons a feature skips most turns for: no news.
_ROUTINE_SKIPS = frozenset({
    "below_inline_media_trigger",
    "new_backend_session",
    "no_duplicate_read_outputs",
    "no_oversized_tool_outputs",
    "stable_artifact_scope_unavailable",
    "tool_output_offload_reclaim_zero",
    "turn_recovery_in_progress",
})


def _kib(value: object) -> int:
    return round(nonnegative_int(value, 0) / 1024)


def _inline_media_history(metrics: dict[str, Any]) -> str:
    removed = nonnegative_int(metrics.get("inline_image_count_removed"), 0)
    return f"历史图片减少 {removed} 张 / {_kib(metrics.get('inline_media_bytes_removed'))} KiB"


def _tool_output_offloading(metrics: dict[str, Any]) -> str:
    offloaded = nonnegative_int(metrics.get("tool_output_count_offloaded"), 0)
    reclaimed = nonnegative_int(metrics.get("estimated_tokens_reclaimed"), 0)
    return f"大型工具输出转存 {offloaded} 条 / {_kib(metrics.get('artifact_bytes_retained'))} KiB / 约 {reclaimed} tokens"


def _hash_aware_read_dedup(metrics: dict[str, Any]) -> str:
    removed = nonnegative_int(metrics.get("duplicate_output_count_removed"), 0)
    reclaimed = nonnegative_int(metrics.get("estimated_tokens_reclaimed"), 0)
    return f"重复只读结果去重 {removed} 条 / 约 {reclaimed} tokens"


# What an applied feature saved, by feature.
_APPLIED_SUMMARIES: dict[str, Callable[[dict[str, Any]], str]] = {
    "inline_media_history": _inline_media_history,
    "tool_output_offloading": _tool_output_offloading,
    "hash_aware_read_dedup": _hash_aware_read_dedup,
}


def _is_news(report: object) -> bool:
    if not isinstance(report, dict):
        return False
    reason = str(report.get("reason") or "")
    return str(report.get("status") or "") in {"applied", "failed_open"} or not (
        reason in _ROUTINE_SKIPS
        # The Adapter lacks a capability the feature needs: true for every
        # turn of this Backend, not news.
        or reason.startswith("requires_")
    )


def _feature_summary(report: dict[str, Any]) -> str:
    feature_id = str(report.get("feature_id") or "feature")
    status = str(report.get("status") or "skipped")
    summary = _APPLIED_SUMMARIES.get(feature_id) if status == "applied" else None
    if summary is not None:
        return summary(mapping(report.get("metrics")))
    reason = str(report.get("reason") or "")
    return f"{feature_id}: {status}" + (f" ({reason})" if reason else "")


def harness_activity_card(report: dict[str, Any], context: EventContext) -> dict[str, Any] | None:
    """The card of one ``agent_harness_completed`` report; ``None`` when no feature has news."""

    features = report.get("features")
    feature_reports = features if isinstance(features, list) else []
    news = [feature for feature in feature_reports if _is_news(feature)]
    if not news:
        return None
    summaries = [_feature_summary(feature) for feature in news]
    net_avoided = nonnegative_int(
        mapping(report.get("context_savings")).get("net_visible_tokens_estimated_avoided"), 0,
    )
    if net_avoided:
        summaries.append(f"本轮可见上下文估算净减少 {net_avoided} tokens")
    provider_wall_ms = report.get("provider_wall_ms")
    if isinstance(provider_wall_ms, (int, float)):
        summaries.append(f"Provider {float(provider_wall_ms) / 1000:.1f}s")
    usage = token_usage(mapping(report.get("provider_usage")))
    if usage.get("input_tokens"):
        cached = f"（cached {usage['cached_input_tokens']}）" if usage.get("cached_input_tokens") else ""
        summaries.append(f"input {usage['input_tokens']}{cached}")
    failed = any(
        isinstance(feature, dict) and str(feature.get("status") or "").casefold() == "failed"
        for feature in feature_reports
    )
    return context.detail(
        kind="optimization",
        title="Agent Harness",
        content=" · ".join(summaries),
        # This card reports Harness feature application, not the provider
        # turn outcome. Provider failures have their own runtime activity and
        # must not make successfully attached Harness controls appear to have
        # failed.
        status="failed" if failed else "completed",
    )
