"""One provider turn through the AgentHarness, from its feature plan to its completion report.

``HarnessTurn`` runs the plan's before-turn features, attaches the
after-tool-batch features as a ``ToolBatchPipeline`` the Provider adapter
calls with each tool batch, and builds the prepared and completed reports.
Only local optimization failures are fail-open; cancellation and execution
control signals always propagate.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from typing import Any

from .contracts import ExecutionControlSignal
from .harness import (
    HARNESS_FEATURES,
    HARNESS_TIMING_AFTER_TOOL_BATCH,
    HARNESS_TIMING_BEFORE_TURN,
    AgentLoopProviderEventProcessor,
    AgentLoopToolBatch,
    AgentLoopToolBatchProcessor,
    AgentLoopToolBatchTransform,
    HarnessFeature,
    capabilities_of,
    feature_supports_operation,
    harness_timing_includes,
)
from .harness_effectiveness import classify_failure
from .harness_observer import ContextSavingsObserver


# A feature of the plan: its ID, the feature and its configuration.
PlannedFeature = tuple[str, HarnessFeature, dict[str, Any]]

_RESULT_COUNT_KEYS = {
    "plain_text": "plain_text_result_count",
    "image_or_multimodal": "image_or_multimodal_result_count",
    "failed": "failed_result_count",
}


@dataclass(frozen=True)
class TurnPlan:
    before_turn: list[PlannedFeature]
    after_tool_batch: list[PlannedFeature]


class HarnessTurn:
    """The feature lifecycles and reports of one provider turn."""

    def __init__(self, *, provider: Any, invocation: Any, context: Any) -> None:
        self.provider = provider
        self.context = context
        self.started_ns = time.monotonic_ns()
        # The invocation as the features have prepared it so far.
        self.invocation = invocation
        self.reports: list[dict[str, Any]] = []
        self.observer = ContextSavingsObserver(
            provider=provider,
            invocation=invocation,
        )
        self.feature_configs: dict[str, Any] = {}
        self.decision: Any = None
        self._controller: Any = None
        self._adaptive_runtime: Any = None
        self._tool_batches = ToolBatchPipeline([], self)

    def plan(
        self,
        features: dict[str, HarnessFeature],
        config: dict[str, Any],
        *,
        decision: Any,
        controller: Any,
    ) -> TurnPlan:
        """Split the enabled features by lifecycle; report the ones left out."""

        self.decision = decision
        self._controller = controller
        if decision is not None:
            begin_turn = getattr(controller, "begin_turn", None)
            if callable(begin_turn):
                try:
                    self._adaptive_runtime = begin_turn(decision)
                except Exception:
                    self._adaptive_runtime = None
        raw_feature_configs = config.get("features")
        self.feature_configs = (
            raw_feature_configs
            if isinstance(raw_feature_configs, dict)
            else {}
        )
        operation = str(
            getattr(getattr(self.invocation, "request", None), "operation", "")
            or ""
        )
        enabled: list[PlannedFeature] = []
        for feature_id, feature in features.items():
            raw_feature_config = self.feature_configs.get(feature_id)
            feature_config = (
                raw_feature_config
                if isinstance(raw_feature_config, dict)
                else {}
            )
            if (
                feature_config.get("enabled") is not True
                or not feature_supports_operation(feature, operation)
            ):
                continue
            enabled.append((feature_id, feature, dict(feature_config)))
        # Features the plan asked for but this Adapter cannot run: the report
        # keeps the missing capability instead of silently dropping them.
        for feature_id, raw_feature_config in self.feature_configs.items():
            if (
                feature_id in features
                and isinstance(raw_feature_config, dict)
                and raw_feature_config.get("unavailable")
            ):
                self.reports.append(_skipped_report(
                    feature_id,
                    "plan",
                    str(raw_feature_config["unavailable"]),
                ))
        before_turn = [
            item for item in enabled if _runs_before_turn(item[1], item[2])
        ]
        if getattr(self.invocation, "recovery_checkpoint", {}).get("turn_id"):
            before_turn = self._without_input_rewrites(before_turn)
        return TurnPlan(
            before_turn=before_turn,
            after_tool_batch=[
                item for item in enabled
                if _runs_after_tool_batch(item[1], item[2])
            ],
        )

    def run_before_turn(self, planned: list[PlannedFeature]) -> None:
        for feature_id, feature, feature_config in planned:
            self._apply_before_turn(feature_id, feature, feature_config)

    def attach_tool_batches(self, planned: list[PlannedFeature]) -> None:
        self._tool_batches = ToolBatchPipeline(planned, self)
        if not planned:
            return
        self.invocation = replace(
            self.invocation,
            tool_batch_processor=AgentLoopToolBatchProcessor(
                process=self._tool_batches.process,
                output_bytes_cap=self._tool_batches.output_bytes_cap(),
            ),
        )

    def feature_config(
        self,
        feature_id: str,
        feature_config: dict[str, Any],
    ) -> dict[str, Any]:
        """A feature's configuration as the adaptive turn policy hands it over."""

        if self._adaptive_runtime is None:
            return feature_config
        try:
            return self._adaptive_runtime.feature_config(
                feature_id,
                feature_config,
            )
        except Exception:
            return feature_config

    def observe_tool_batch(self, batch: AgentLoopToolBatch) -> None:
        if self._adaptive_runtime is None:
            return
        try:
            self._adaptive_runtime.observe_tool_batch(
                batch=batch,
                context_pressure_percent=(
                    self.observer.context_pressure_percent
                ),
            )
        except Exception:
            pass

    def record_feature_result(
        self,
        feature_id: str,
        transformed: AgentLoopToolBatchTransform,
    ) -> None:
        if self._adaptive_runtime is None:
            return
        try:
            self._adaptive_runtime.record_feature_result(
                feature_id,
                applied=bool(transformed.applied),
                metrics=transformed.metrics,
            )
        except Exception:
            pass

    def prepared_details(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """The prepared event and the feature reports it carries."""

        prepared_reports = [
            {
                **report,
                "metrics": dict(report.get("metrics") or {}),
            }
            for report in self.reports
        ]
        details: dict[str, Any] = {
            "lifecycle": "before_provider",
            "status": "prepared",
            "provider": str(getattr(self.provider, "provider_id", "") or ""),
            "capabilities": sorted(capabilities_of(self.invocation, self.provider)),
            "features": prepared_reports,
            "applied_feature_count": _applied_feature_count(prepared_reports),
            "applied_lifecycle_count": _applied_lifecycle_count(prepared_reports),
            "prepare_wall_ms": _elapsed_ms(self.started_ns, time.monotonic_ns()),
            "measurement_model_calls": 0,
        }
        if self.decision is not None:
            details["adaptive_policy"] = self._adaptive_report()
        return details, prepared_reports

    def completed_details(
        self,
        *,
        status: str,
        result: Any,
        error: BaseException | None,
        provider_started_ns: int,
    ) -> dict[str, Any]:
        """The completion event; the adaptive policy observes it first."""

        finished_ns = time.monotonic_ns()
        step_reports = self._tool_batches.final_reports()
        self.reports.extend(step_reports)
        for report in step_reports:
            self.observer.record_feature(
                f"{report['feature_id']}:{HARNESS_TIMING_AFTER_TOOL_BATCH}",
                status=report["status"],
                metrics=report["metrics"],
            )
        provider_event_loop = self._provider_event_loop()
        provider_usage = _numeric_observation(getattr(result, "usage", None))
        details: dict[str, Any] = {
            "lifecycle": "after_turn",
            "status": status,
            "provider": str(getattr(self.provider, "provider_id", "") or ""),
            "capabilities": sorted(capabilities_of(self.invocation, self.provider)),
            "features": self.reports,
            "applied_feature_count": _applied_feature_count(self.reports),
            "applied_lifecycle_count": _applied_lifecycle_count(self.reports),
            "provider_wall_ms": _elapsed_ms(provider_started_ns, finished_ns),
            "total_wall_ms": _elapsed_ms(self.started_ns, finished_ns),
            "measurement_model_calls": 0,
            "after_tool_batch_count": max(
                (
                    int(report["metrics"].get("tool_batch_count") or 0)
                    for report in step_reports
                ),
                default=0,
            ),
            "after_tool_batch": self._tool_batches.lifecycle_metrics(),
            "provider_event_loop": provider_event_loop,
            "context_savings": self.observer.report(invocation=self.invocation),
        }
        if provider_usage:
            details["provider_usage"] = provider_usage
        if error is not None:
            details["error_type"] = type(error).__name__
            details["failure_kind"] = classify_failure("failed", error)
        if self.decision is not None:
            self._observe_adaptive(details, result)
        else:
            self._observe_manual(details, result)
        return details

    def _without_input_rewrites(
        self,
        planned: list[PlannedFeature],
    ) -> list[PlannedFeature]:
        """A recovered turn carries on; features that rewrite its input sit it out."""

        recovering = {
            name for name, definition in HARNESS_FEATURES.items()
            if definition.rewrites_model_input
        }
        for feature_id, _feature, _config in planned:
            if feature_id in recovering:
                self.reports.append(_skipped_report(
                    feature_id,
                    "before_turn",
                    "turn_recovery_in_progress",
                ))
        return [item for item in planned if item[0] not in recovering]

    def _apply_before_turn(
        self,
        feature_id: str,
        feature: HarnessFeature,
        feature_config: dict[str, Any],
    ) -> None:
        effective_config = self.feature_config(feature_id, feature_config)
        started_ns = time.monotonic_ns()
        try:
            outcome = feature.apply(
                observer=self.observer,
                provider=self.provider,
                invocation=self.invocation,
                context=self.context,
                config=effective_config,
            )
        except (InterruptedError, ExecutionControlSignal):
            raise
        except Exception as exc:
            report = {
                "feature_id": feature_id,
                "lifecycle": HARNESS_TIMING_BEFORE_TURN,
                "status": "failed_open",
                "action_applied": False,
                "reason": type(exc).__name__,
                "prepare_ms": _elapsed_ms(started_ns, time.monotonic_ns()),
                "metrics": {},
            }
        else:
            self.invocation = outcome.invocation
            status = str(outcome.status or "skipped")
            report = {
                "feature_id": feature_id,
                "lifecycle": HARNESS_TIMING_BEFORE_TURN,
                "status": status,
                "action_applied": (
                    bool(outcome.action_applied)
                    if outcome.action_applied is not None
                    else status == "applied"
                ),
                "reason": str(outcome.reason or ""),
                "prepare_ms": _elapsed_ms(started_ns, time.monotonic_ns()),
                "metrics": dict(outcome.metrics),
            }
        self.reports.append(report)
        self.observer.record_feature(
            feature_id,
            status=report["status"],
            metrics=report["metrics"],
        )

    def _provider_event_loop(self) -> dict[str, Any]:
        """The native event processor's report, merged into its feature's report."""

        event_processor = getattr(self.invocation, "provider_event_processor", None)
        if not isinstance(event_processor, AgentLoopProviderEventProcessor):
            return {}
        try:
            provider_event_loop = dict(event_processor.report())
        except Exception as exc:
            provider_event_loop = {"report_error": type(exc).__name__}
        for report in self.reports:
            if report.get("feature_id") != event_processor.feature_id:
                continue
            report["metrics"] = {
                **dict(report.get("metrics") or {}),
                **provider_event_loop,
            }
            self.observer.record_feature(
                event_processor.feature_id,
                status=str(report.get("status") or "skipped"),
                metrics=report["metrics"],
            )
            break
        return provider_event_loop

    def _adaptive_report(self) -> dict[str, Any]:
        report = self.decision.report()
        if self._adaptive_runtime is not None:
            try:
                report["turn_runtime"] = self._adaptive_runtime.report()
            except Exception as exc:
                report["turn_runtime_error"] = type(exc).__name__
        return report

    def _observe_adaptive(self, details: dict[str, Any], result: Any) -> None:
        details["adaptive_policy"] = self._adaptive_report()
        try:
            details["adaptive_policy"]["state_updated"] = bool(
                self._controller.observe(
                    decision=self.decision,
                    details=details,
                    result=result,
                )
            )
        except Exception as exc:
            details["adaptive_policy"]["state_updated"] = False
            details["adaptive_policy"]["state_error"] = type(exc).__name__

    def _observe_manual(self, details: dict[str, Any], result: Any) -> None:
        # A hand-set plan's component reports are kept as well; the
        # completion event itself is not stored anywhere.
        observe_manual = getattr(self._controller, "observe_manual", None)
        if not callable(observe_manual):
            return
        try:
            observe_manual(
                invocation=self.invocation,
                features=self.feature_configs,
                details=details,
                result=result,
            )
        except Exception:
            pass


class ToolBatchPipeline:
    """The after-tool-batch features of one turn, run on each Provider tool batch."""

    def __init__(self, planned: list[PlannedFeature], turn: HarnessTurn) -> None:
        self._planned = planned
        self._turn = turn
        self._lock = threading.Lock()
        self._states: dict[str, dict[str, Any]] = {
            feature_id: {} for feature_id, _feature, _config in planned
        }
        self._reports: dict[str, dict[str, Any]] = {
            feature_id: {
                "feature_id": feature_id,
                "lifecycle": HARNESS_TIMING_AFTER_TOOL_BATCH,
                "status": "skipped",
                "reason": "no_tool_batches",
                "prepare_ms": 0.0,
                "metrics": {
                    "tool_batch_count": 0,
                    "tool_result_count": 0,
                    "applied_batch_count": 0,
                    "failed_open_batch_count": 0,
                },
            }
            for feature_id, _feature, _config in planned
        }
        self._metrics: dict[str, Any] = {
            "tool_batch_count": 0,
            "tool_result_count": 0,
            "plain_text_result_count": 0,
            "image_or_multimodal_result_count": 0,
            "structured_result_count": 0,
            "failed_result_count": 0,
            "source_counts": {},
        }

    def output_bytes_cap(self) -> int:
        """How much of each tool output the adapter must capture for the features."""

        output_bytes_cap = 64 * 1024
        for _feature_id, feature, feature_config in self._planned:
            capture_bytes = getattr(feature, "tool_output_capture_bytes", None)
            if not callable(capture_bytes):
                continue
            try:
                output_bytes_cap = max(
                    output_bytes_cap,
                    int(capture_bytes(feature_config)),
                )
            except (TypeError, ValueError):
                continue
        return output_bytes_cap

    def process(self, batch: AgentLoopToolBatch) -> AgentLoopToolBatch:
        if not isinstance(batch, AgentLoopToolBatch):
            raise TypeError("AgentHarness tool batch contract required")
        observer = self._turn.observer
        with self._lock:
            before_tokens = observer.observe_tool_batch_before(
                tuple(item.result for item in batch.items)
            )
            self._count_results(batch)
            self._turn.observe_tool_batch(batch)
            current_batch = batch
            for feature_id, feature, feature_config in self._planned:
                current_batch = self._apply(
                    feature_id,
                    feature,
                    feature_config,
                    current_batch,
                )
            observer.observe_tool_batch_after(
                tuple(item.result for item in current_batch.items),
                before_tokens=before_tokens,
            )
            return current_batch

    def final_reports(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    **report,
                    "metrics": dict(report["metrics"]),
                }
                for report in self._reports.values()
            ]

    def lifecycle_metrics(self) -> dict[str, Any]:
        return {
            **self._metrics,
            "source_counts": dict(self._metrics["source_counts"]),
        }

    def _count_results(self, batch: AgentLoopToolBatch) -> None:
        self._metrics["tool_batch_count"] += 1
        self._metrics["tool_result_count"] += len(batch.items)
        source_counts = self._metrics["source_counts"]
        for item in batch.items:
            source = str(item.source or "unknown")
            source_counts[source] = int(source_counts.get(source) or 0) + 1
            classification = _tool_result_classification(item.result)
            self._metrics[
                _RESULT_COUNT_KEYS.get(classification, "structured_result_count")
            ] += 1

    def _apply(
        self,
        feature_id: str,
        feature: HarnessFeature,
        feature_config: dict[str, Any],
        batch: AgentLoopToolBatch,
    ) -> AgentLoopToolBatch:
        turn = self._turn
        report = self._reports[feature_id]
        metrics = report["metrics"]
        metrics["tool_batch_count"] += 1
        metrics["tool_result_count"] += len(batch.items)
        effective_config = turn.feature_config(feature_id, feature_config)
        started_ns = time.monotonic_ns()
        try:
            transformed = feature.after_tool_batch(
                observer=turn.observer,
                batch=batch,
                provider=turn.provider,
                invocation=turn.invocation,
                context=turn.context,
                config=effective_config,
                state=self._states[feature_id],
            )
            if not isinstance(transformed, AgentLoopToolBatchTransform):
                raise TypeError(
                    "after_tool_batch must return "
                    "AgentLoopToolBatchTransform"
                )
        except (InterruptedError, ExecutionControlSignal):
            raise
        except Exception as exc:
            metrics["failed_open_batch_count"] += 1
            if report["status"] != "applied":
                report["status"] = "failed_open"
                report["reason"] = type(exc).__name__
            else:
                report["reason"] = (
                    "applied_with_failed_open_batches:"
                    f"{type(exc).__name__}"
                )
        else:
            batch = transformed.batch
            if transformed.applied:
                metrics["applied_batch_count"] += 1
                report["status"] = "applied"
            elif report["status"] != "applied":
                report["status"] = "skipped"
            if transformed.applied or report["status"] != "applied":
                report["reason"] = str(transformed.reason or report["reason"])
            _accumulate_numeric_metrics(metrics, transformed.metrics)
            turn.record_feature_result(feature_id, transformed)
        report["prepare_ms"] = round(
            float(report["prepare_ms"])
            + (time.monotonic_ns() - started_ns) / 1_000_000,
            3,
        )
        return batch


def _feature_default_timing(feature: HarnessFeature) -> str:
    return str(getattr(feature, "default_timing", HARNESS_TIMING_BEFORE_TURN))


def _runs_before_turn(feature: HarnessFeature, feature_config: dict[str, Any]) -> bool:
    return (
        not callable(getattr(feature, "after_tool_batch", None))
        or harness_timing_includes(
            feature_config,
            HARNESS_TIMING_BEFORE_TURN,
            default=_feature_default_timing(feature),
        )
        or (
            bool(getattr(
                feature,
                "requires_before_turn_setup_for_after_tool_batch",
                False,
            ))
            and harness_timing_includes(
                feature_config,
                HARNESS_TIMING_AFTER_TOOL_BATCH,
                default=_feature_default_timing(feature),
            )
        )
    )


def _runs_after_tool_batch(feature: HarnessFeature, feature_config: dict[str, Any]) -> bool:
    return callable(getattr(feature, "after_tool_batch", None)) and harness_timing_includes(
        feature_config,
        HARNESS_TIMING_AFTER_TOOL_BATCH,
        default=_feature_default_timing(feature),
    )


def _skipped_report(feature_id: str, lifecycle: str, reason: str) -> dict[str, Any]:
    return {
        "feature_id": feature_id,
        "lifecycle": lifecycle,
        "status": "skipped",
        "action_applied": False,
        "reason": reason,
        "prepare_ms": 0.0,
        "metrics": {},
    }


def _applied_feature_count(reports: list[dict[str, Any]]) -> int:
    return len({
        str(report.get("feature_id") or "")
        for report in reports
        if report.get("status") == "applied"
        and str(report.get("feature_id") or "")
    })


def _applied_lifecycle_count(reports: list[dict[str, Any]]) -> int:
    return sum(1 for report in reports if report.get("status") == "applied")


def _elapsed_ms(started_ns: int, finished_ns: int) -> float:
    return round((finished_ns - started_ns) / 1_000_000, 3)


def _tool_result_classification(result: object) -> str:
    """Classify a provider-neutral result without interpreting its content."""

    if not isinstance(result, dict) or result.get("success") is not True:
        return "failed"
    content_items = result.get("contentItems")
    if not isinstance(content_items, list) or not content_items:
        return "structured"
    item_types = {
        str(item.get("type") or "")
        for item in content_items
        if isinstance(item, dict)
    }
    if item_types == {"inputText"} and all(
        isinstance(item, dict) and isinstance(item.get("text"), str)
        for item in content_items
    ):
        return "plain_text"
    if any("image" in item_type.casefold() for item_type in item_types):
        return "image_or_multimodal"
    return "structured"


def _numeric_observation(value: object, *, depth: int = 0) -> dict[str, Any]:
    if depth > 3 or not isinstance(value, dict):
        return {}
    observed: dict[str, Any] = {}
    for raw_key, raw_value in list(value.items())[:100]:
        key = str(raw_key or "")[:80]
        if not key:
            continue
        if isinstance(raw_value, bool):
            continue
        if isinstance(raw_value, (int, float)):
            observed[key] = raw_value
            continue
        nested = _numeric_observation(raw_value, depth=depth + 1)
        if nested:
            observed[key] = nested
    return observed


def _accumulate_numeric_metrics(
    target: dict[str, Any],
    incoming: dict[str, Any],
) -> None:
    for raw_key, value in dict(incoming or {}).items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            target[key] = value
            continue
        target[key] = target.get(key, 0) + value
