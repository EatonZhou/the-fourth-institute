from __future__ import annotations

import hashlib
import json
import math
import queue
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, ClassVar, Mapping

from psycopg.errors import DeadlockDetected, LockNotAvailable, SerializationFailure

from management_common.database import DatabaseError
from management_common.json_columns import json_text
from management_common.portal_database import portal_database
from management_common.database_time import instant



QUALITY_RESULTS = frozenset({"pass", "fail", "unknown"})
# A plan a person set: recorded under its own variant and load family, so its
# rows never count as evidence about the automatic policy.
MANUAL_PLAN_VARIANT = "manual"

HARNESS_TABLE_COLUMNS = {
    "harness_strategy_observations": {
        "execution_id", "scope", "provider", "load_family",
        "production_variant", "production_signature", "production_plan_json",
        "shadow_variant", "shadow_signature", "shadow_plan_json",
        "shadow_diff_json", "actual_actions_json", "action_outcomes_json",
        "latency_ms", "provider_latency_ms", "harness_latency_ms",
        "queue_wait_ms", "provider_latency_observed", "queue_wait_observed",
        "unattributed_latency_ms", "end_to_end_latency_ms", "input_tokens",
        "cached_input_tokens", "output_tokens", "status", "failure_kind",
        "signals_json", "recorded_at", "measurement_version",
    },
    "harness_quality_labels": {
        "execution_id", "quality_result", "quality_source", "load_family",
        "quality_score", "quality_reason", "updated_at",
    },
}
HARNESS_INDEXES = {
    "harness_strategy_load_idx",
    "harness_reasoning_quality_idx",
}


def create_observation_view(connection) -> None:
    """Quality is owned by labels, including labels received before a turn."""
    columns = sorted(HARNESS_TABLE_COLUMNS["harness_strategy_observations"] - {"load_family"})
    connection.execute(
        "CREATE OR REPLACE VIEW harness_observations AS SELECT "
        + ",".join("o." + name for name in columns)
        + ",COALESCE(NULLIF(q.load_family,''),o.load_family) AS load_family"
        + ",COALESCE(q.quality_result,'unknown') AS quality_result"
        + ",COALESCE(q.quality_source,'none') AS quality_source"
        + ",q.quality_score,COALESCE(q.quality_reason,'') AS quality_reason"
        + " FROM harness_strategy_observations o LEFT JOIN harness_quality_labels q USING(execution_id)"
    )


def plan_signature(features: Mapping[str, object]) -> str:
    enabled = {
        str(key): value
        for key, value in sorted(features.items())
        if isinstance(value, Mapping) and value.get("enabled") is True
    }
    payload = json.dumps(enabled, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def load_family(signals: Mapping[str, object]) -> str:
    """Classify generic load shape; never infer product/task semantics."""

    media = _int(signals.get("current_media_count"))
    large_media = _int(signals.get("current_media_large_image_count"))
    prompt = _int(signals.get("prompt_tokens_estimated"))
    tools = max(
        _int(signals.get("portal_tool_count")),
        _int(signals.get("previous_tool_calls")),
    )
    pressure = _float(signals.get("context_pressure_percent"))
    if media >= 2:
        return "multi_media_large" if large_media else "multi_media"
    if media:
        return "single_media_large" if large_media else "single_media"
    if pressure >= 72:
        return "context_pressure_severe"
    if prompt >= 24_000:
        return "long_prompt"
    if tools >= 80:
        return "tool_storm"
    if tools >= 32:
        return "tool_heavy"
    if signals.get("repo_task_signal") is True:
        return "repository_work"
    return "short_text"


@dataclass(frozen=True)
class ManualHarnessTurn:
    """A turn that ran a hand-set plan, in the shape ``record_turn`` takes."""

    execution_id: str
    scope: str
    provider: str
    features: dict[str, Any]
    capabilities: tuple[str, ...] = ()
    signals: dict[str, Any] = field(
        default_factory=lambda: {"policy_variant": MANUAL_PLAN_VARIANT}
    )
    load_family: str = MANUAL_PLAN_VARIANT

    def report(self) -> dict[str, Any]:
        return {
            "enabled_feature_ids": sorted(
                feature_id
                for feature_id, config in self.features.items()
                if isinstance(config, Mapping) and config.get("enabled") is True
            ),
            "capabilities": list(self.capabilities),
        }


class HarnessEffectivenessStore:
    """Owns Harness outcome evidence: one row per turn and its quality labels.

    Runtime writes are queued to a daemon so telemetry cannot extend the
    user-visible provider critical path. PostgreSQL is the durable audit/query
    surface; canonical Segments remain the owner of model-visible facts.
    """

    _instance: ClassVar["HarnessEffectivenessStore | None"] = None
    _instances_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self) -> None:
        self.database = portal_database(schema="harness_effectiveness")
        self._queue: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue()
        self._initialized = False
        self._init_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._health_lock = threading.Lock()
        self._write_failure_count = 0
        self._last_write_error_type = ""
        self._recording_settings = None
        self._closed = False
        self._worker = threading.Thread(
            target=self._run,
            name="harness-effectiveness-writer",
            daemon=True,
        )
        self._worker.start()

    @classmethod
    def shared(cls) -> "HarnessEffectivenessStore":
        with cls._instances_lock:
            instance = cls._instance
            if (
                instance is None
                or instance._closed
                or not instance._worker.is_alive()
            ):
                instance = cls()
                cls._instance = instance
            return instance

    def record_turn(
        self,
        *,
        decision: Any,
        details: Mapping[str, object],
        result: object,
    ) -> None:
        report = decision.report()
        signals = dict(getattr(decision, "signals", {}) or {})
        features = dict(getattr(decision, "features", {}) or {})
        feature_reports = details.get("features")
        feature_reports = feature_reports if isinstance(feature_reports, list) else []
        action_outcomes = collect_action_outcomes(feature_reports, details)
        actual_actions = {
            item["action"]
            for item in action_outcomes
            if item["status"] == "applied"
        }
        usage = details.get("provider_usage")
        usage = usage if isinstance(usage, Mapping) else {}
        status = str(details.get("status") or "unknown")
        provider_latency_observed = (
            "provider_wall_ms" in details
            and details.get("provider_wall_ms") is not None
        )
        queue_wait_observed = (
            ("queue_wait_ms" in details and details.get("queue_wait_ms") is not None)
            or signals.get("queue_wait_observed") is True
        )
        queue_wait_value = (
            details.get("queue_wait_ms")
            if "queue_wait_ms" in details and details.get("queue_wait_ms") is not None
            else signals.get("queue_wait_ms")
        )
        payload = {
            "execution_id": str(getattr(decision, "execution_id", "") or ""),
            "scope": str(getattr(decision, "scope", "") or ""),
            "provider": str(getattr(decision, "provider", "") or details.get("provider") or ""),
            "load_family": str(getattr(decision, "load_family", "") or "") or load_family(signals),
            "production_variant": str(signals.get("policy_variant") or ""),
            "production_signature": plan_signature(features),
            "production_plan": report.get("enabled_feature_ids") or [],
            # No shadow plan is computed any more; the columns stay empty.
            "shadow_variant": "",
            "shadow_signature": "",
            "shadow_plan": [],
            "shadow_diff": {},
            "actual_actions": sorted(actual_actions),
            "action_outcomes": action_outcomes,
            "latency_ms": _float(details.get("total_wall_ms")),
            "provider_latency_ms": _float(details.get("provider_wall_ms")),
            "provider_latency_observed": provider_latency_observed,
            "queue_wait_ms": _float(queue_wait_value),
            "queue_wait_observed": queue_wait_observed,
            # 7: action_outcomes holds every component's result, not only actions.
            "measurement_version": 7,
            "input_tokens": _usage_value(usage, "input_tokens", "inputTokens"),
            "cached_input_tokens": _usage_value(usage, "cached_input_tokens", "cachedInputTokens"),
            "output_tokens": _usage_value(usage, "output_tokens", "outputTokens"),
            "status": status,
            "failure_kind": str(details.get("failure_kind") or "")
            or classify_failure(status, result),
            "signals": signals,
            "recorded_at": time.time(),
        }
        if payload["execution_id"]:
            self._enqueue("turn", payload)

    def record_quality(
        self,
        execution_id: str,
        *,
        passed: bool | None = None,
        result: str = "",
        source: str,
        workload_family: str = "",
        score: float | None = None,
        reason: str = "",
    ) -> None:
        normalized_execution_id = str(execution_id or "").strip()
        if not normalized_execution_id:
            raise ValueError("execution_id required for Harness quality evidence")
        normalized_result = str(result or "").strip().casefold()
        if not normalized_result:
            if passed is None:
                raise ValueError("quality result or passed is required")
            normalized_result = "pass" if passed else "fail"
        if normalized_result not in QUALITY_RESULTS:
            raise ValueError("quality result must be pass, fail, or unknown")
        normalized_score = None if score is None else float(score)
        if normalized_score is not None and not 0.0 <= normalized_score <= 1.0:
            raise ValueError("quality score must be between 0 and 1")
        self._enqueue("quality", {
            "execution_id": normalized_execution_id,
            "quality_result": normalized_result,
            "quality_source": str(source or "explicit"),
            "load_family": str(workload_family or ""),
            "quality_score": normalized_score,
            "quality_reason": str(reason or "")[:512],
        })

    def reasoning_quality_signal(
        self,
        *,
        scope: str,
        provider: str,
        workload_family: str,
        limit: int = 32,
    ) -> dict[str, object]:
        """Return bounded historical quality evidence for effort protection.

        Unknown outcomes never influence execution. A guard requires at
        least three explicit labels and protects quality when failures are
        recurrent or scored quality is materially low.
        """

        empty = {
            "sample_count": 0,
            "failure_count": 0,
            "failure_rate": 0.0,
            "average_score": None,
            "guard": False,
        }
        normalized_scope = str(scope or "").strip()
        normalized_provider = str(provider or "").strip()
        normalized_family = str(workload_family or "").strip()
        if (
            not normalized_scope
            or not normalized_provider
            or not normalized_family
        ):
            return empty
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT quality_result,quality_score FROM harness_observations WHERE scope=%s AND provider=%s AND load_family=%s AND quality_result IN ('pass','fail') ORDER BY recorded_at DESC LIMIT %s",
                    (
                        normalized_scope,
                        normalized_provider,
                        normalized_family,
                        max(3, min(128, int(limit))),
                    ),
                ).fetchall()
        except (OSError, DatabaseError, TypeError, ValueError):
            return empty
        sample_count = len(rows)
        failure_count = sum(
            1 for result, _score in rows if str(result) == "fail"
        )
        scores = [
            float(score)
            for _result, score in rows
            if score is not None
        ]
        failure_rate = (
            failure_count / sample_count if sample_count else 0.0
        )
        average_score = sum(scores) / len(scores) if scores else None
        return {
            "sample_count": sample_count,
            "failure_count": failure_count,
            "failure_rate": round(failure_rate, 4),
            "average_score": (
                None if average_score is None else round(average_score, 4)
            ),
            "guard": bool(
                sample_count >= 3
                and (
                    failure_count >= 2
                    and failure_rate >= 0.25
                    or average_score is not None
                    and average_score < 0.7
                )
            ),
        }


    def flush(self, timeout: float = 5.0) -> None:
        barrier = threading.Event()
        with self._lifecycle_lock:
            self._ensure_open()
            self._queue.put(("barrier", {"event": barrier}))
        if not barrier.wait(max(0.01, float(timeout))):
            raise TimeoutError("Harness effectiveness writer did not flush")

    def health(self) -> dict[str, object]:
        with self._health_lock:
            return {
                "writer_alive": self._worker.is_alive(),
                "write_failure_count": self._write_failure_count,
                "last_write_error_type": self._last_write_error_type,
                "pending_writes": self._queue.qsize(),
            }

    def close(self, timeout: float = 5.0) -> None:
        stopped = threading.Event()
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            self._queue.put(("stop", {"event": stopped}))
        if not stopped.wait(max(0.01, float(timeout))):
            raise TimeoutError("Harness effectiveness writer did not stop")
        with self._instances_lock:
            if self._instance is self:
                self.__class__._instance = None

    def _enqueue(self, kind: str, payload: dict[str, Any]) -> None:
        with self._lifecycle_lock:
            self._ensure_open()
            self._queue.put((kind, payload))

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Harness effectiveness store is closed")

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                kind, payload = item
                if kind == "barrier":
                    payload["event"].set()
                    continue
                if kind == "stop":
                    payload["event"].set()
                    return
                try:
                    self._write_with_retry(kind, payload)
                except Exception as exc:
                    # A malformed row or transient PostgreSQL failure must not kill
                    # the only writer and deadlock all later flush barriers.
                    with self._health_lock:
                        self._write_failure_count += 1
                        self._last_write_error_type = type(exc).__name__
            finally:
                self._queue.task_done()

    def _write_with_retry(self, kind: str, payload: dict[str, Any]) -> None:
        delays = (0.01, 0.05, 0.20)
        for attempt in range(len(delays) + 1):
            try:
                self._write(kind, payload)
                return
            except (DeadlockDetected, LockNotAvailable, SerializationFailure):
                if attempt >= len(delays):
                    raise
                time.sleep(delays[attempt])

    def _write(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "turn":
            # Initialize/read policy on this daemon, never on the model's
            # critical path. Closing the Event drains this short admission;
            # queued observations are discarded while recording is disabled.
            if self._recording_settings is None:
                from .service_settings import ExecutionSettings
                self._recording_settings = ExecutionSettings()
            with self._recording_settings.admit_harness_recording(payload["recorded_at"]) as enabled:
                if enabled:
                    self._write_evidence(kind, payload)
            return
        # Human review labels for existing history remain editable; the
        # recording switch governs new automatic observations.
        self._write_evidence(kind, payload)

    def _write_evidence(self, kind: str, payload: dict[str, Any]) -> None:
        with self._connect() as connection:
            if kind == "turn":
                provider_observed = bool(payload["provider_latency_observed"])
                total_latency = payload["latency_ms"]
                values = {
                    name: payload[name]
                    for name in HARNESS_TABLE_COLUMNS["harness_strategy_observations"]
                    if name in payload
                }
                values.update(
                    production_plan_json=_json(payload["production_plan"]),
                    shadow_plan_json=_json(payload["shadow_plan"]),
                    shadow_diff_json=_json(payload["shadow_diff"]),
                    signals_json=_json(payload["signals"]),
                    actual_actions_json=_json(payload["actual_actions"]),
                    action_outcomes_json=_json(payload["action_outcomes"]),
                    harness_latency_ms=max(0.0, total_latency - payload["provider_latency_ms"])
                    if provider_observed else 0.0,
                    unattributed_latency_ms=0.0 if provider_observed else total_latency,
                    end_to_end_latency_ms=total_latency + payload["queue_wait_ms"]
                    if payload["queue_wait_observed"] else 0.0,
                    # '1'/'0' fit the BIGINT flags of older schemas and BOOLEAN alike.
                    provider_latency_observed="1" if provider_observed else "0",
                    queue_wait_observed="1" if payload["queue_wait_observed"] else "0",
                )
                columns = sorted(HARNESS_TABLE_COLUMNS["harness_strategy_observations"])
                values["recorded_at"] = instant(values["recorded_at"])
                connection.execute(
                    "INSERT INTO harness_strategy_observations (" + ",".join(columns)
                    + ") VALUES (" + ",".join(["%s"] * len(columns))
                    + ") ON CONFLICT(execution_id) DO UPDATE SET "
                    + ",".join(f"{name}=excluded.{name}" for name in columns if name != "execution_id"),
                    [values[name] for name in columns],
                )
            elif kind == "quality":
                connection.execute(
                    "INSERT INTO harness_quality_labels "
                    "(execution_id,quality_result,quality_source,load_family,quality_score,quality_reason,updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(execution_id) DO UPDATE SET "
                    "quality_result=excluded.quality_result,quality_source=excluded.quality_source,"
                    "load_family=excluded.load_family,quality_score=excluded.quality_score,"
                    "quality_reason=excluded.quality_reason,updated_at=excluded.updated_at",
                    (payload["execution_id"], payload["quality_result"], payload["quality_source"],
                     payload["load_family"], payload["quality_score"], payload["quality_reason"], instant(time.time())),
                )
            connection.commit()

    def prune(self, *, before: float, limit: int = 1000) -> int:
        with self._connect() as connection:
            deleted = connection.execute(
                "DELETE FROM harness_strategy_observations WHERE execution_id IN ("
                "SELECT execution_id FROM harness_strategy_observations WHERE recorded_at < %s "
                "ORDER BY recorded_at LIMIT %s FOR UPDATE SKIP LOCKED)",
                (instant(before), max(1, limit)),
            ).rowcount
            connection.commit()
            return deleted

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        with self.database.connect() as connection:
            if not self._initialized:
                with self._init_lock:
                    if not self._initialized:
                        existing_tables = {
                            str(row[0])
                            for row in connection.execute(
                                "SELECT table_name FROM information_schema.tables "
                                "WHERE table_schema=current_schema() AND table_type='BASE TABLE'"
                            )
                        }
                        if not existing_tables:
                            self._create_schema(connection)
                        _require_current_schema(connection)
                        connection.commit()
                        self._initialized = True
            yield connection

    @staticmethod
    def _create_schema(connection) -> None:
        connection.executescript("""
                    CREATE TABLE IF NOT EXISTS harness_strategy_observations (
                        execution_id TEXT PRIMARY KEY, scope TEXT NOT NULL, provider TEXT NOT NULL,
                        load_family TEXT NOT NULL, production_variant TEXT NOT NULL,
                        production_signature TEXT NOT NULL, production_plan_json JSONB NOT NULL,
                        shadow_variant TEXT NOT NULL, shadow_signature TEXT NOT NULL,
                        shadow_plan_json JSONB NOT NULL, shadow_diff_json JSONB NOT NULL,
                        actual_actions_json JSONB NOT NULL,
                        action_outcomes_json JSONB NOT NULL DEFAULT '[]',
                        latency_ms DOUBLE PRECISION NOT NULL,
                        provider_latency_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
                        harness_latency_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
                        queue_wait_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
                        provider_latency_observed BOOLEAN NOT NULL DEFAULT FALSE,
                        queue_wait_observed BOOLEAN NOT NULL DEFAULT FALSE,
                        unattributed_latency_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
                        end_to_end_latency_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
                        input_tokens BIGINT NOT NULL, cached_input_tokens BIGINT NOT NULL,
                        output_tokens BIGINT NOT NULL, status TEXT NOT NULL, failure_kind TEXT NOT NULL,
                        signals_json JSONB NOT NULL, recorded_at TIMESTAMPTZ NOT NULL,
                        measurement_version BIGINT NOT NULL DEFAULT 1
                    );
                    CREATE INDEX IF NOT EXISTS harness_strategy_load_idx
                    ON harness_strategy_observations(load_family, production_variant, recorded_at);
                    CREATE TABLE IF NOT EXISTS harness_quality_labels (
                        execution_id TEXT PRIMARY KEY, quality_result TEXT NOT NULL,
                        quality_source TEXT NOT NULL, load_family TEXT NOT NULL,
                        quality_score DOUBLE PRECISION, quality_reason TEXT NOT NULL DEFAULT '',
                        updated_at TIMESTAMPTZ NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS harness_reasoning_quality_idx ON
                    harness_strategy_observations(
                        scope,provider,load_family,recorded_at DESC
                    );
                        """)
        create_observation_view(connection)

def classify_failure(status: str, result: object) -> str:
    from management_portal.execution.provider_failures import is_usage_limit_failure

    if str(status).casefold() == "completed":
        return "none"
    if isinstance(result, Mapping):
        text = " ".join(
            str(result.get(key) or "")
            for key in ("error", "message", "status", "code", "result")
        ).casefold()
    else:
        text = str(result or "")[:4096].casefold()
    if isinstance(result, BaseException):
        # A worker that stops cancels its turns with an empty CancelledError.
        text = f"{type(result).__name__.casefold()} {text}"
    if "timeout" in text or str(status).casefold() == "timeout":
        return "timeout"
    if any(marker in text for marker in (
        "rate limit", "too many requests", "429",
    )):
        return "provider_rate_limit"
    if is_usage_limit_failure(result):
        return "provider_usage_limit"
    if any(marker in text for marker in (
        "auth", "unauthorized", "invalid api key", "permission", "401", "403",
    )):
        return "provider_authentication"
    if any(marker in text for marker in (
        "service unavailable", "backend unavailable", "connection refused",
        "overloaded", "internal server error", "bad gateway",
    )):
        return "provider_unavailable"
    if "worker lease expired" in text:
        return "infrastructure_worker_lease_expired"
    if any(marker in text for marker in (
        "prompt is too long", "context_length_exceeded", "context length",
        "context window",
    )):
        return "context_overflow"
    if any(marker in text for marker in (
        "bad request", "invalid_request_error", "invalid request", "http 400",
    )):
        return "provider_bad_request"
    if any(marker in text for marker in ("cancel", "interrupted", "aborted")):
        return "cancelled"
    return "execution_failure"


def _usage_value(usage: Mapping[str, object], *keys: str) -> int:
    # Codex adapter's `last` is accumulated consumption for this provider turn.
    # `context` is only the latest context sample; `total` is session lifetime.
    # Neither is a valid fallback for the cost of this observation.
    candidates: list[Mapping[str, object]] = [usage]
    if isinstance((last := usage.get("last")), Mapping):
        candidates.append(last)
    for candidate in candidates:
        for key in keys:
            if key in candidate:
                return _int(candidate[key])
    return 0


def _require_current_schema(connection: Any) -> None:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema=current_schema() AND table_type='BASE TABLE'"
        )
    }
    columns = {
        table: {
            str(row[0])
            for row in connection.execute(
                'SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s',
                (table,),
            )
        }
        for table in HARNESS_TABLE_COLUMNS
        if table in tables
    }
    indexes = {
        str(row[0])
        for row in connection.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname=current_schema()"
        )
        if not str(row[0]).endswith("_pkey")
    }
    if (
        tables != set(HARNESS_TABLE_COLUMNS)
        or columns != HARNESS_TABLE_COLUMNS
        or indexes != HARNESS_INDEXES
        or connection.execute("SELECT to_regclass('harness_observations')").fetchone()[0] is None
    ):
        raise RuntimeError(
            "Harness effectiveness store does not match its schema"
        )


def _json(value: object) -> str:
    return json_text(value, ensure_ascii=False, sort_keys=True, default=str)


def _int(value: object) -> int:
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _float(value: object) -> float:
    try:
        return max(0.0, float(value or 0))
    except (TypeError, ValueError):
        return 0.0


def collect_action_outcomes(
    feature_reports: list[object],
    details: Mapping[str, object],
) -> list[dict[str, Any]]:
    """Return bounded semantic outcomes without persisting user content.

    Every component of the turn leaves one outcome per lifecycle, also when
    it did nothing: ``applied`` (it acted), ``no_action`` (it ran and found
    nothing to do), ``skipped`` with the reason (no opportunity, a missing
    capability), or a failure.  Numeric metrics come along; text does not.
    The ledger is the only record of a turn's components that survives the
    turn, so "it never had a chance" stays distinguishable from "it did not
    work".
    """
    outcomes: list[dict[str, Any]] = []
    for item in feature_reports:
        if not isinstance(item, Mapping):
            continue
        feature_id = str(item.get("feature_id") or "").strip()[:160]
        if not feature_id:
            continue
        status = _action_status(item.get("status"))
        action_applied = item.get("action_applied")
        if action_applied is None:
            action_applied = (
                status == "applied"
                and feature_id != "native_tool_loop_governor"
            )
        if action_applied:
            outcome_status = "applied"
        elif status == "applied":
            outcome_status = "no_action"
        else:
            outcome_status = status
        outcome: dict[str, Any] = {
            "action": feature_id,
            "status": outcome_status,
            "reason": _action_reason(item.get("reason")),
        }
        lifecycle = _action_reason(item.get("lifecycle"))
        if lifecycle:
            outcome["lifecycle"] = lifecycle
        counters = _numeric_metrics(item.get("metrics"))
        if counters:
            outcome["metrics"] = counters
        outcomes.append(outcome)
        metrics = item.get("metrics")
        metrics = metrics if isinstance(metrics, Mapping) else {}
        if metrics.get("reasoning_effort_adjusted") is True:
            effort_from = str(
                metrics.get("configured_reasoning_effort") or ""
            ).strip().casefold()[:32]
            effort_to = str(
                metrics.get("effective_reasoning_effort") or ""
            ).strip().casefold()[:32]
            if effort_from and effort_to:
                outcomes.append({
                    "action": (
                        "reasoning_effort_adjustment:"
                        f"{effort_from}_to_{effort_to}"
                    ),
                    "status": "applied",
                    "reason": "",
                })
    provider_loop = details.get("provider_event_loop")
    provider_loop = provider_loop if isinstance(provider_loop, Mapping) else {}
    runtime_outcomes = provider_loop.get("runtime_action_outcomes")
    runtime_outcomes = runtime_outcomes if isinstance(runtime_outcomes, list) else []
    for item in runtime_outcomes:
        if not isinstance(item, Mapping):
            continue
        action = str(item.get("action") or "").strip()[:160]
        if action:
            outcomes.append({
                "action": action,
                "status": _action_status(item.get("status")),
                "reason": _action_reason(item.get("reason")),
            })
    unique: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for item in outcomes:
        unique[(
            item["action"], item["status"], item["reason"],
            str(item.get("lifecycle") or ""),
        )] = item
    return list(unique.values())


MAX_OUTCOME_METRICS = 24


def _numeric_metrics(value: object) -> dict[str, int | float | bool]:
    """A report's counters and flags, bounded; text and nested values are dropped."""

    if not isinstance(value, Mapping):
        return {}
    metrics: dict[str, int | float | bool] = {}
    for key, item in value.items():
        name = str(key)
        if len(name) > 64 or _action_reason(name) != name:
            continue
        if isinstance(item, bool) or (
            isinstance(item, (int, float)) and math.isfinite(item)
        ):
            metrics[name] = item
            if len(metrics) >= MAX_OUTCOME_METRICS:
                break
    return metrics


def _action_status(value: object) -> str:
    status = str(value or "unknown").strip().casefold()
    return status if status in {
        "applied", "failed", "failed_open", "no_action", "skipped", "unknown"
    } else "unknown"


def _action_reason(value: object) -> str:
    """Keep stable diagnostic codes while excluding free-form text."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) > 128 or any(
        not character.isascii()
        or not (character.isalnum() or character in {"_", "-", ".", ":"})
        for character in raw
    ):
        return "redacted"
    return raw
