from __future__ import annotations

import logging
from typing import Any

from .harness_history import (
    approximate_value_tokens,
    positive_int,
)

LOGGER = logging.getLogger(__name__)


class ContextSavingsObserver:
    """Turn-local, deterministic context accounting."""

    def __init__(self, *, provider: Any, invocation: Any) -> None:
        self.provider_id = str(getattr(provider, "provider_id", "") or "")
        self._prompt_tokens_before = self._invocation_prompt_tokens(invocation)
        self._last_input_tokens = 0
        self._context_window_tokens = 0
        self._tool_schema_tokens_avoided = 0
        self._artifact_bytes_retained = 0
        self._duplicate_bytes_removed = 0
        self._current_turn_tool_tokens_before = 0
        self._current_turn_tool_tokens_after = 0
        self._feature_metrics: dict[str, dict[str, Any]] = {}
        observe_window = getattr(provider, "observe_context_window", None)
        if callable(observe_window):
            try:
                observation = observe_window(invocation=invocation)
            except Exception:
                LOGGER.warning("%s context window unreadable; savings are counted without it",
                               self.provider_id, exc_info=True)
                observation = None
            self._context_window_tokens = positive_int(
                getattr(observation, "tokens", 0)
            )

    @staticmethod
    def _invocation_prompt_tokens(invocation: Any) -> int:
        tokens = approximate_value_tokens(
            str(getattr(invocation, "user_prompt", "") or "")
        )
        if bool(getattr(invocation, "attach_system_context", False)):
            tokens += approximate_value_tokens(
                str(getattr(invocation, "system_context", "") or "")
            )
        return tokens



    def observe_tool_batch_before(self, items: object) -> int:
        """Account for one tool batch before it returns to the model."""

        batch_tokens = approximate_value_tokens(items)
        self._current_turn_tool_tokens_before += batch_tokens
        self._current_turn_tool_tokens_after += batch_tokens
        return batch_tokens

    def observe_tool_batch_after(
        self,
        items: object,
        *,
        before_tokens: int,
    ) -> None:
        after_tokens = approximate_value_tokens(items)
        self._current_turn_tool_tokens_after += (
            after_tokens - max(0, int(before_tokens))
        )


    @property
    def context_pressure_percent(self) -> float:
        if not self._context_window_tokens:
            return 0.0
        input_tokens = self._last_input_tokens + self._current_turn_tool_tokens_after
        return round(
            min(100.0, (input_tokens / self._context_window_tokens) * 100),
            2,
        )






    def record_feature(
        self,
        feature_id: str,
        *,
        status: str,
        metrics: dict[str, Any],
    ) -> None:
        clean_metrics = dict(metrics or {})
        self._feature_metrics[str(feature_id or "")] = clean_metrics
        if status != "applied":
            return
        self._tool_schema_tokens_avoided += positive_int(
            clean_metrics.get("tool_schema_tokens_avoided")
        )
        self._artifact_bytes_retained += positive_int(
            clean_metrics.get("artifact_bytes_retained")
        )
        self._duplicate_bytes_removed += positive_int(
            clean_metrics.get("duplicate_output_bytes_removed")
        )

    def report(self, *, invocation: Any) -> dict[str, Any]:
        prompt_tokens_after = self._invocation_prompt_tokens(invocation)
        prompt_added = max(0, prompt_tokens_after - self._prompt_tokens_before)
        current_turn_tool_tokens_removed = max(
            0,
            self._current_turn_tool_tokens_before
            - self._current_turn_tool_tokens_after,
        )
        return {
            "measurement_model_calls": 0,
            "context_window_tokens": self._context_window_tokens,
            "observed_input_tokens": self._last_input_tokens,
            "context_pressure_percent": self.context_pressure_percent,
            "prompt_tokens_estimated_before": self._prompt_tokens_before,
            "prompt_tokens_estimated_after": prompt_tokens_after,
            "prompt_tokens_estimated_added": prompt_added,
            "current_turn_tool_tokens_estimated_before": (
                self._current_turn_tool_tokens_before
            ),
            "current_turn_tool_tokens_estimated_after": (
                self._current_turn_tool_tokens_after
            ),
            "current_turn_tool_tokens_estimated_removed": (
                current_turn_tool_tokens_removed
            ),
            "tool_schema_tokens_estimated_avoided": (
                self._tool_schema_tokens_avoided
            ),
            "net_visible_tokens_estimated_avoided": max(
                0,
                current_turn_tool_tokens_removed
                + self._tool_schema_tokens_avoided
                - prompt_added,
            ),
            "artifact_bytes_retained": self._artifact_bytes_retained,
            "duplicate_output_bytes_removed": self._duplicate_bytes_removed,
        }
