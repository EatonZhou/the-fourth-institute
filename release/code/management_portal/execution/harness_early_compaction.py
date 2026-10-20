from __future__ import annotations

from typing import Any

from .harness import (
    HARNESS_TIMING_BEFORE_TURN,
    HarnessFeatureOutcome,
)
from .harness_capabilities import AUTO_COMPACT_THRESHOLD, requires


EARLY_CONTEXT_COMPACTION_FEATURE_ID = "early_context_compaction"
DEFAULT_CONTEXT_COMPACTION_PERCENT = 70


def _bounded(value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(float(str(value)))
    except (TypeError, ValueError, OverflowError):
        number = default
    return max(minimum, min(maximum, number))


class EarlyContextCompactionFeature:
    """Configure the provider's native automatic compactor."""

    feature_id = EARLY_CONTEXT_COMPACTION_FEATURE_ID
    # The Provider compacts by itself; this feature only moves its threshold.
    requires = requires({AUTO_COMPACT_THRESHOLD})
    supported_operations = frozenset({"create", "turn"})
    default_timing = HARNESS_TIMING_BEFORE_TURN
    # Native auto-compaction executes inside the Provider's agent loop, but its
    # threshold must be attached to the invocation before that loop starts.

    def apply(
        self,
        *,
        provider: Any,
        invocation: Any,
        context: Any,
        config: dict[str, Any],
        observer: Any = None,
    ) -> HarnessFeatureOutcome:
        del context, observer
        # The threshold is a policy parameter, not a user setting: a stale
        # persisted ``threshold_percent`` (an older 80%) must not silently
        # win.  The automatic policy passes its Backend's parameters under
        # ``policy_threshold_percent`` and ``max_context_tokens``.
        threshold_percent = _bounded(
            config.get("policy_threshold_percent"),
            default=DEFAULT_CONTEXT_COMPACTION_PERCENT,
            minimum=10,
            maximum=95,
        )
        max_context_tokens = _bounded(
            config.get("max_context_tokens"),
            default=0,
            minimum=0,
            maximum=10_000_000,
        )
        current = invocation
        applied = False
        reasons: list[str] = []
        metrics: dict[str, Any] = {
            "threshold_percent": threshold_percent,
            "max_context_tokens": max_context_tokens,
        }

        configure = getattr(provider, "configure_auto_compact_threshold", None)
        if callable(configure):
            transformed = configure(
                invocation=current,
                threshold_percent=threshold_percent,
                # Only a bounded policy passes the bound, so an Adapter
                # without one keeps its two-argument hook.
                **(
                    {"max_context_tokens": max_context_tokens}
                    if max_context_tokens
                    else {}
                ),
            )
            current = transformed.invocation
            applied = transformed.applied
            reasons.append(f"agent_loop:{transformed.reason}")
            metrics.update({
                f"agent_loop_{key}": value
                for key, value in dict(transformed.metrics).items()
            })
        else:
            reasons.append(f"requires_{AUTO_COMPACT_THRESHOLD}")

        reason = ";".join(reasons)
        if len(reasons) == 1 and reasons[0].startswith("agent_loop:"):
            reason = reasons[0].split(":", 1)[1]
        elif len(reasons) == 1:
            reason = reasons[0]
        # Moving the Provider's compaction point is this feature's action; the
        # compactions that follow are the Provider's and are counted as such.
        return HarnessFeatureOutcome(
            invocation=current,
            status="applied" if applied else "skipped",
            reason=reason or "compaction_timing_unavailable",
            metrics=metrics,
            action_applied=applied,
        )
