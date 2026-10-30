"""Daily evaluation of the Harness policy cells on the turns of daily use.

Every turn of a cell leaves an observation (``harness_strategy_observations``)
with its arm, its cost and how it ended; quality labels come from the review
of sampled turns.  Once a day this module

* judges each challenger and rolls it back, rejects it, recommends (or makes)
  its promotion, or lets it run on;
* starts a challenger in the busiest cells that run none, one tunable one step
  away from the champion, where a replay says the step saves;
* picks the turns of the previous day that the reviewer reads and labels.

What a step costs or saves is not read from the live split: the input tokens
of single turns differ so much from session to session that a 10% share would
need months to show a few percent.  It comes from replaying the recorded
calls of the same sessions under both parameter sets
(``harness_evolution_offline``), which only the compaction tunables allow.
The live challenger answers the other question -- does the step hurt -- and is
held to non-inferiority: its policy failures and reviewed quality may not be
worse than the champion's by more than a margin, with 90% confidence, before
it can be promoted, and a significant loss rolls it back or rejects it.

Most tunables act on few turns (a pressure threshold only matters once a
session gets near it), so failures are compared on the turns *exposed* to the
change -- those whose signals at the start of the turn let the champion's and
the challenger's values act differently -- and on all turns.

Configured test-account traffic never counts.  Failures a policy cannot cause
(cancellations, usage limits, credentials, rate limits, lost workers) are not
held against an arm.
"""

from __future__ import annotations
from management_common.site_compatibility import compatibility_value

import json
import logging
import math
import random
import re
import statistics
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from management_common.json_columns import json_value
from management_common.portal_database import _database_schema, portal_database
from management_common.database_time import instant, decode_times

from .harness_effectiveness import MANUAL_PLAN_VARIANT, HarnessEffectivenessStore
from .harness_evolution_offline import OFFLINE_TUNABLES, TurnCalls, estimate_step, load_turn_calls
from .harness_evolution import (
    CHALLENGER,
    CHAMPION,
    DEFAULT_CHALLENGER_SHARE,
    EXPLORING,
    FROZEN,
    HarnessEvolutionStore,
    PolicyCell,
)
from .harness_policy_table import TUNABLES, policy_parameters

LOGGER = logging.getLogger(__name__)

EVALUATION_SCHEMA = "tfi.harness-evolution-evaluation.v1"
REVIEW_SAMPLE_SCHEMA = "tfi.harness-review-sample.v1"
EVOLUTION_ACTOR = "harness-evolution"
REVIEW_QUALITY_SOURCE = "resident_review"
# Failures that come from the turn's work and so may come from its policy.
POLICY_FAILURE_KINDS = frozenset({
    "execution_failure", "timeout", "provider_bad_request", "context_overflow",
})
FINISHED_EXECUTION_STATUSES = frozenset({"completed", "failed", "cancelled"})
TEST_ACTORS = frozenset(compatibility_value("harness_test_actors", []))
HIGH_EFFORTS = frozenset({"high", "xhigh", "max", "ultra"})
_USER_MESSAGE_RE = re.compile(r"<user-message>\s*(.*?)\s*</user-message>", re.DOTALL)

@dataclass(frozen=True)
class EvolutionCriteria:
    # Exposed turns a challenger needs before its failures can clear it.
    minimum_challenger_turns: int = 30
    # Reviewed turns each arm needs before its quality can clear it.
    minimum_labels_per_arm: int = 20
    # Days a challenger runs before it can be recommended.
    minimum_days: float = 7.0
    # Turns a challenger needs before its failures can roll it back.
    guardrail_minimum_turns: int = 15
    # The rise of the policy failure rate and the fall of the reviewed pass
    # rate a promotion accepts: the upper end of their one-sided 90% interval
    # must stay below these.  A rise (fall) beyond them that is also
    # significant rolls the challenger back (rejects it).
    failure_margin: float = 0.05
    quality_margin: float = 0.10
    # Input-token saving the replay must show, with its 90% interval above zero.
    minimum_saving: float = 0.05
    maximum_challenger_days: float = 21.0
    # Average turns per day (last 7 days, without tests) from which a cell
    # gets a challenger; below it a challenger would never be decided.
    eligible_daily_turns: float = 10.0
    proposal_cooldown_days: float = 2.0
    retry_after_days: float = 30.0
    max_new_challengers: int = 2
    bootstrap_rounds: int = 1000
    # Share of a cell's turns a new challenger's change must act on.
    minimum_exposure: float = 0.10
    traffic_days: float = 7.0
    # Days of recorded calls the replay reads, and the sessions it needs.
    offline_days: float = 14.0
    minimum_sessions: int = 5
    # Promotions wait for a person unless this is on.
    auto_promote: bool = False


@dataclass
class TurnOutcome:
    execution_id: str
    provider: str
    cell: str
    arm: str
    version: int
    origin: str
    agent_id: str
    status: str
    failure_kind: str
    input_tokens: int
    uncached_input_tokens: int
    output_tokens: int
    provider_latency_ms: float
    quality: str
    recorded_at: float
    load_family: str = ""
    overrides: dict[str, Any] = field(default_factory=dict)
    signals: dict[str, Any] = field(default_factory=dict)
    plan: tuple[str, ...] = ()

    @property
    def policy_failure(self) -> bool:
        return self.status != "completed" and self.failure_kind in POLICY_FAILURE_KINDS

    @property
    def counted(self) -> bool:
        """Whether the turn says anything about its policy."""

        return self.status == "completed" or self.failure_kind in POLICY_FAILURE_KINDS


def _origin(actor_ref: object) -> str:
    actor = str(actor_ref or "")
    if not actor:
        return "unlinked"
    if actor in TEST_ACTORS:
        return "test"
    if actor.startswith("resident_"):
        return "resident"
    return "user"


def load_outcomes(*, since: float, until: float | None = None, provider: str = "") -> list[TurnOutcome]:
    """The observed turns since ``since``, with their arm and origin."""

    agent = _database_schema("agent")
    clauses = ["o.recorded_at >= %s"]
    values: list[object] = [instant(since)]
    if until is not None:
        clauses.append("o.recorded_at < %s")
        values.append(instant(until))
    if provider:
        clauses.append("o.provider = %s")
        values.append(provider)
    database = portal_database(schema="harness_effectiveness")
    with database.read() as connection:
        rows = connection.execute(
            "SELECT o.execution_id, o.provider, o.load_family, o.status, o.failure_kind,"
            " o.input_tokens, o.cached_input_tokens, o.output_tokens,"
            " o.provider_latency_ms, o.provider_latency_observed, o.quality_result,"
            " o.signals_json, o.production_plan_json, o.recorded_at, a.actor_ref, a.agent_id,"
            " a.status AS execution_status"
            " FROM harness_observations o"
            f" LEFT JOIN {agent}.executions a ON a.execution_id = o.execution_id"
            " WHERE " + " AND ".join(clauses),
            values,
        ).fetchall()
    return [
        outcome for outcome in (outcome_from_row(decode_times(row, ('recorded_at',), format='seconds')) for row in rows)
        if outcome is not None
    ]


def outcome_from_row(row: Mapping[str, Any]) -> TurnOutcome | None:
    """The outcome of one observed turn, or None when it is not evidence yet.

    The Harness also observes session operations (create, fork, release,
    compact); they cost nothing and are not turns.  An execution that is
    still running has only an interrupted attempt on record.  A failed
    attempt whose execution was cancelled, or was finished by a later
    attempt after a restart, says nothing about the policy either.
    """

    try:
        signals = json_value(row["signals_json"] or "{}")
    except (TypeError, ValueError):
        signals = {}
    signals = signals if isinstance(signals, dict) else {}
    if str(signals.get("operation") or "turn") != "turn":
        return None
    status = str(row["status"] or "")
    failure_kind = str(row["failure_kind"] or "")
    execution_status = str(row["execution_status"] or "")
    if execution_status and execution_status not in FINISHED_EXECUTION_STATUSES:
        return None
    if status != "completed" and execution_status == "cancelled":
        failure_kind = "cancelled"
    elif status != "completed" and execution_status == "completed":
        failure_kind = "interrupted"
    try:
        plan = json_value(row["production_plan_json"] or "[]")
    except (TypeError, ValueError):
        plan = []
    plan = tuple(str(item) for item in plan) if isinstance(plan, list) else ()
    input_tokens = int(row["input_tokens"] or 0)
    cached = int(row["cached_input_tokens"] or 0)
    family = str(row["load_family"] or "")
    return TurnOutcome(
        execution_id=str(row["execution_id"]),
        provider=str(row["provider"]),
        # Turns from before the cells name theirs by Provider and family;
        # they have no arm, so they are never evidence about one.
        cell=str(signals.get("policy_cell") or f"{row['provider']}/{family}"),
        arm=str(signals.get("policy_arm") or ""),
        version=int(signals.get("policy_version") or 0),
        origin=_origin(row["actor_ref"]),
        agent_id=str(row["agent_id"] or ""),
        status=status,
        failure_kind=failure_kind,
        input_tokens=input_tokens,
        uncached_input_tokens=max(0, input_tokens - cached),
        output_tokens=int(row["output_tokens"] or 0),
        provider_latency_ms=(
            float(row["provider_latency_ms"] or 0.0)
            if int(row["provider_latency_observed"] or 0) else 0.0
        ),
        quality=str(row["quality_result"] or "unknown"),
        recorded_at=float(row["recorded_at"] or 0.0),
        load_family=family,
        overrides=dict(signals.get("policy_overrides") or {}) if isinstance(signals.get("policy_overrides"), dict) else {},
        signals=signals,
        plan=plan,
    )


# Exposure ------------------------------------------------------------------


def exposed(turn: TurnOutcome, name: str, values: tuple[Any, Any]) -> bool:
    """Whether ``name`` set to either of ``values`` can act differently on the turn.

    Read from the turn's signals at its start and the features its plan
    selected; neither depends on the value of ``name``.
    """

    signals = turn.signals
    low, high = min(values), max(values)
    if name in {"compaction_threshold_percent", "compaction_max_context_tokens"}:
        # A compaction changes the history every later turn carries, so the
        # turns after the one that crossed a threshold are exposed as well.
        return "early_context_compaction" in turn.plan
    if name == "offload_trigger_bytes":
        # Output sizes of this turn are not known at its start; a session
        # whose previous turn produced that much output is the proxy.
        return (
            "tool_output_offloading" in turn.plan
            and _number(signals.get("previous_output_bytes")) >= low
        )
    # The thresholds below decide one turn at a time: a turn at or above
    # both values, or below both, is treated the same by either arm.
    if name == "protect_history_pressure_percent":
        return low <= _number(signals.get("context_pressure_percent")) < high
    if name == "reasoning_downgrade_pressure_percent":
        return (
            str(signals.get("configured_reasoning_effort") or "").casefold() in HIGH_EFFORTS
            and low <= _number(signals.get("context_pressure_percent")) < high
        )
    if name == "high_tool_load_calls":
        return low <= _number(signals.get("previous_tool_calls")) < high
    return True


def changed_tunables(
    provider: str,
    champion: Mapping[str, Any],
    challenger: Mapping[str, Any],
) -> dict[str, tuple[Any, Any]]:
    """The tunables whose value differs between the arms: (champion, challenger)."""

    parameters = policy_parameters(provider)
    changes: dict[str, tuple[Any, Any]] = {}
    for name in sorted(set(champion) | set(challenger)):
        if name not in TUNABLES:
            continue
        before = champion.get(name, getattr(parameters, name))
        after = challenger.get(name, getattr(parameters, name))
        if before != after:
            changes[name] = (before, after)
    return changes


def exposure_of(changes: Mapping[str, tuple[Any, Any]]) -> Callable[[TurnOutcome], bool] | None:
    if not changes:
        return None
    return lambda turn: any(exposed(turn, name, values) for name, values in changes.items())


def _number(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


# Statistics ----------------------------------------------------------------


def arm_metrics(turns: list[TurnOutcome]) -> dict[str, Any]:
    counted = [turn for turn in turns if turn.counted]
    failures = sum(1 for turn in counted if turn.policy_failure)
    labelled = [turn for turn in counted if turn.quality in {"pass", "fail"}]
    passes = sum(1 for turn in labelled if turn.quality == "pass")
    completed = [turn for turn in counted if turn.status == "completed"]
    latency = [turn.provider_latency_ms for turn in completed if turn.provider_latency_ms > 0]
    return {
        "turns": len(counted),
        "user_turns": sum(1 for turn in counted if turn.origin == "user"),
        "policy_failures": failures,
        "failure_rate": _rate(failures, len(counted)),
        "cancelled": sum(1 for turn in turns if turn.failure_kind == "cancelled"),
        "labels": len(labelled),
        "pass_rate": _rate(passes, len(labelled)) if labelled else None,
        "mean_input_tokens": _mean(turn.input_tokens for turn in completed),
        "median_input_tokens": _median(turn.input_tokens for turn in completed),
        "mean_uncached_input_tokens": _mean(turn.uncached_input_tokens for turn in completed),
        "mean_output_tokens": _mean(turn.output_tokens for turn in completed),
        "median_provider_seconds": round(_median(latency) / 1000.0, 2) if latency else None,
    }


def compare_arms(
    champion: list[TurnOutcome],
    challenger: list[TurnOutcome],
    *,
    criteria: EvolutionCriteria,
    age_days: float,
    exposure: Callable[[TurnOutcome], bool] | None = None,
    offline: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The evidence about one challenger and what it calls for.

    ``offline`` is the replay's estimate of the step's saving
    (``harness_evolution_offline.estimate_step``); without one a challenger
    can be stopped but never recommended.  Failures are compared on the
    turns ``exposure`` accepts and on all turns; the reviewed quality on all
    labelled turns, since labels are few.
    """

    base_all = [turn for turn in champion if turn.counted]
    trial_all = [turn for turn in challenger if turn.counted]
    if exposure is not None:
        champion = [turn for turn in champion if exposure(turn)]
        challenger = [turn for turn in challenger if exposure(turn)]
    champion_metrics = arm_metrics(champion)
    challenger_metrics = arm_metrics(challenger)
    champion_metrics["all_turns"] = len(base_all)
    challenger_metrics["all_turns"] = len(trial_all)
    champion_metrics["all_labels"] = sum(turn.quality in {"pass", "fail"} for turn in base_all)
    challenger_metrics["all_labels"] = sum(turn.quality in {"pass", "fail"} for turn in trial_all)
    base = [turn for turn in champion if turn.counted]
    trial = [turn for turn in challenger if turn.counted]
    base_failures = sum(turn.policy_failure for turn in base)
    trial_failures = sum(turn.policy_failure for turn in trial)
    failure = _proportion_difference(trial_failures, len(trial), base_failures, len(base))
    failure_all = _proportion_difference(
        sum(turn.policy_failure for turn in trial_all), len(trial_all),
        sum(turn.policy_failure for turn in base_all), len(base_all),
    )
    base_labels = [turn.quality == "pass" for turn in base_all if turn.quality in {"pass", "fail"}]
    trial_labels = [turn.quality == "pass" for turn in trial_all if turn.quality in {"pass", "fail"}]
    quality = _proportion_difference(
        sum(trial_labels), len(trial_labels), sum(base_labels), len(base_labels),
    )
    # How much worse the challenger may be, at most, with 90% confidence.
    failure_upper = _upper_difference(trial_failures, len(trial), base_failures, len(base))
    quality_upper = _upper_difference(
        sum(base_labels), len(base_labels), sum(trial_labels), len(trial_labels),
    )
    offline = dict(offline or {})
    estimated = bool(offline.get("available"))
    reasons: list[str] = []
    decision = "continue"

    def failures_rose(item: Mapping[str, Any], turns: int) -> bool:
        return (
            turns >= criteria.guardrail_minimum_turns
            and item["difference"] is not None
            and item["difference"] > criteria.failure_margin
            and item["z"] >= 1.645
        )

    if failures_rose(failure, len(trial)) or failures_rose(failure_all, len(trial_all)):
        decision = "rollback"
        reasons.append("policy_failure_rate_rose")
    elif (
        len(trial_labels) >= criteria.minimum_labels_per_arm
        and len(base_labels) >= criteria.minimum_labels_per_arm
        and quality["difference"] is not None
        and quality["difference"] < -criteria.quality_margin
        and quality["z"] <= -1.282
    ):
        decision = "rejected"
        reasons.append("reviewed_quality_fell")
    elif (
        estimated
        and offline["saving"] <= -criteria.minimum_saving
        and offline["high"] < 0
    ):
        decision = "rejected"
        reasons.append("costs_more")
    elif estimated and offline["high"] < criteria.minimum_saving:
        # The replay is sure the step cannot save enough to be recommended.
        decision = "rejected"
        reasons.append("saves_too_little")
    else:
        saves = (
            estimated
            and offline["saving"] >= criteria.minimum_saving
            and offline["low"] > 0
        )
        waiting = {
            "needs_days": age_days < criteria.minimum_days,
            "needs_turns": len(trial) < criteria.minimum_challenger_turns,
            "failures_not_bounded": failure_upper is None or failure_upper >= criteria.failure_margin,
            "needs_reviewed_turns": (
                len(trial_labels) < criteria.minimum_labels_per_arm
                or len(base_labels) < criteria.minimum_labels_per_arm
            ),
            "quality_not_bounded": quality_upper is None or quality_upper >= criteria.quality_margin,
        }
        if saves and not any(waiting.values()):
            if float(offline.get("extra_compaction_seconds_per_day") or 0.0) > 0:
                # Fewer tokens for more waiting on compactions is a person's choice.
                decision = "trade_off"
                reasons.extend(["saves_input_tokens", "costs_compaction_time"])
            else:
                decision = "promote"
                reasons.append("saves_input_tokens")
        elif age_days >= criteria.maximum_challenger_days:
            decision = "inconclusive"
            reasons.append("no_decision_within_maximum_days")
        else:
            if not estimated:
                reasons.append(f"no_offline_estimate:{offline.get('reason') or 'unknown'}")
            elif not saves:
                reasons.append("no_offline_saving")
            reasons.extend(name for name, pending in waiting.items() if pending)
    return {
        "decision": decision,
        "reasons": reasons,
        "age_days": round(age_days, 2),
        "champion": champion_metrics,
        "challenger": challenger_metrics,
        "failure_rate_difference": failure,
        "failure_rate_difference_all_turns": failure_all,
        "failure_rate_rise_upper": failure_upper,
        "pass_rate_difference": quality,
        "pass_rate_fall_upper": quality_upper,
        "offline": offline,
    }


def _upper_difference(success_a: int, total_a: int, success_b: int, total_b: int) -> float | None:
    """Upper end of the one-sided 90% interval of rate ``a`` minus rate ``b``.

    Agresti-Caffo: one success and one failure added to each arm keep the
    interval honest when an arm has none of either.
    """

    if not total_a or not total_b:
        return None
    rate_a = (success_a + 1) / (total_a + 2)
    rate_b = (success_b + 1) / (total_b + 2)
    spread = math.sqrt(rate_a * (1 - rate_a) / (total_a + 2) + rate_b * (1 - rate_b) / (total_b + 2))
    return round(rate_a - rate_b + 1.2816 * spread, 4)


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _mean(values: Iterable[float]) -> float | None:
    items = list(values)
    return round(sum(items) / len(items), 1) if items else None


def _median(values: Iterable[float]) -> float:
    items = list(values)
    return float(statistics.median(items)) if items else 0.0


def _proportion_difference(success_a: int, total_a: int, success_b: int, total_b: int) -> dict[str, Any]:
    """Rate of ``a`` minus rate of ``b``, with the two-proportion z score."""

    if not total_a or not total_b:
        return {"difference": None, "z": 0.0}
    rate_a = success_a / total_a
    rate_b = success_b / total_b
    pooled = (success_a + success_b) / (total_a + total_b)
    spread = math.sqrt(pooled * (1 - pooled) * (1 / total_a + 1 / total_b))
    z = (rate_a - rate_b) / spread if spread > 0 else 0.0
    return {"difference": round(rate_a - rate_b, 4), "z": round(z, 3)}


# Daily run ------------------------------------------------------------------


def evaluate_cells(
    store: HarnessEvolutionStore,
    *,
    now: float | None = None,
    criteria: EvolutionCriteria | None = None,
    apply: bool = True,
    actor: str = EVOLUTION_ACTOR,
    calls: list[TurnCalls] | None = None,
) -> list[dict[str, Any]]:
    """Decide every running challenger; with ``apply`` the decisions take effect.

    ``calls`` are the recorded calls the replay reads (default: the last
    ``criteria.offline_days``).
    """

    now = time.time() if now is None else float(now)
    criteria = criteria or EvolutionCriteria()
    results = []
    running = [cell for cell in store.cells() if cell.challenger is not None and cell.status != FROZEN]
    if not running:
        return results
    if calls is None:
        calls = load_turn_calls(since=now - criteria.offline_days * 86400.0, until=now)
    since = min(cell.challenger_since for cell in running)
    by_cell: dict[tuple[str, int], list[TurnOutcome]] = defaultdict(list)
    for turn in load_outcomes(since=since, until=now):
        if turn.origin in {"user", "resident"} and turn.cell:
            by_cell[(turn.cell, turn.version)].append(turn)
    for cell in running:
        turns = by_cell.get((cell.key, cell.version), [])
        changes = changed_tunables(cell.provider, cell.champion, cell.challenger or {})
        offline = estimate_step(
            calls,
            provider=cell.provider,
            family=cell.workload_family,
            champion=cell.champion,
            challenger=cell.challenger or {},
            rounds=criteria.bootstrap_rounds,
            seed=f"{cell.key}|{cell.version}",
            minimum_sessions=criteria.minimum_sessions,
        )
        comparison = compare_arms(
            [turn for turn in turns if turn.arm == CHAMPION],
            [turn for turn in turns if turn.arm == CHALLENGER],
            criteria=criteria,
            age_days=(now - cell.challenger_since) / 86400.0,
            exposure=exposure_of(changes),
            offline=_estimate_summary(offline),
        )
        evaluation = {
            "schema": EVALUATION_SCHEMA,
            "evaluated_at": now,
            "version": cell.version,
            "champion_parameters": dict(cell.champion),
            "challenger_parameters": dict(cell.challenger or {}),
            "changed": {name: list(values) for name, values in changes.items()},
            **comparison,
        }
        decision = comparison["decision"]
        applied = ""
        if apply:
            if decision == "rollback":
                store.retire(cell.provider, cell.workload_family, "rolled_back", actor=actor, evidence=evaluation)
                applied = "rolled_back"
            elif decision in {"rejected", "inconclusive"}:
                store.retire(cell.provider, cell.workload_family, decision, actor=actor, evidence=evaluation)
                applied = decision
            elif decision == "promote" and criteria.auto_promote:
                store.promote(cell.provider, cell.workload_family, actor=actor, evidence=evaluation)
                applied = "promoted"
            else:
                store.record_evaluation(
                    cell.provider, cell.workload_family, evaluation, actor=actor,
                    recommend_promotion=decision == "promote",
                )
                applied = "promotion_recommended" if decision == "promote" and cell.status == EXPLORING else "evaluated"
        results.append({"cell": cell.key, "applied": applied, **evaluation})
    return results


def cell_turns(*, now: float | None = None, days: float = 7.0) -> dict[tuple[str, str], list[TurnOutcome]]:
    """The counted turns of each Provider and load family in the last ``days``, without tests."""

    now = time.time() if now is None else float(now)
    turns: dict[tuple[str, str], list[TurnOutcome]] = defaultdict(list)
    for turn in load_outcomes(since=now - days * 86400.0, until=now):
        if turn.origin not in {"user", "resident"} or not turn.counted:
            continue
        family = turn.cell.split("/", 1)[1] if "/" in turn.cell else turn.load_family
        # A plan a person set is not the automatic policy's.
        if not family or family == MANUAL_PLAN_VARIANT:
            continue
        turns[(turn.provider, family)].append(turn)
    return dict(turns)


def traffic_of(turns: Mapping[tuple[str, str], list[TurnOutcome]], days: float = 7.0) -> dict[tuple[str, str], float]:
    return {key: round(len(items) / days, 2) for key, items in turns.items()}


def cell_traffic(*, now: float | None = None, days: float = 7.0) -> dict[tuple[str, str], float]:
    """Average counted turns per day of each Provider and load family, without tests."""

    return traffic_of(cell_turns(now=now, days=days), days)


def propose_challengers(
    store: HarnessEvolutionStore,
    *,
    now: float | None = None,
    criteria: EvolutionCriteria | None = None,
    apply: bool = True,
    actor: str = EVOLUTION_ACTOR,
    traffic: Mapping[tuple[str, str], float] | None = None,
    turns: Mapping[tuple[str, str], list[TurnOutcome]] | None = None,
    calls: list[TurnCalls] | None = None,
) -> list[dict[str, Any]]:
    """Start a challenger in the busiest cells that run none, where a replay says it saves.

    The candidates are the steps of the tunables a replay can estimate
    (``OFFLINE_TUNABLES``).  A step must act on enough of the cell's recent
    ``turns`` that its share of the traffic reaches
    ``criteria.minimum_challenger_turns`` exposed turns before
    ``criteria.maximum_challenger_days`` end, and the replay must show a
    saving of ``criteria.minimum_saving`` with its 90% interval above zero;
    the largest saving goes live.
    """

    now = time.time() if now is None else float(now)
    criteria = criteria or EvolutionCriteria()
    if traffic is None:
        if turns is None:
            turns = cell_turns(now=now, days=criteria.traffic_days)
        traffic = traffic_of(turns, criteria.traffic_days)
    traffic = dict(traffic)
    turns = dict(turns or {})
    cells = {(cell.provider, cell.workload_family): cell for cell in store.cells()}
    recent = store.events(since=now - criteria.retry_after_days * 86400.0, limit=5000)
    proposals: list[dict[str, Any]] = []
    for (provider, family), daily in sorted(traffic.items(), key=lambda item: -item[1]):
        if len(proposals) >= criteria.max_new_challengers:
            break
        if daily < criteria.eligible_daily_turns:
            continue
        cell = cells.get((provider, family)) or PolicyCell(provider, family)
        if cell.status == FROZEN or cell.challenger is not None:
            continue
        cell_events = [event for event in recent if event["provider"] == provider and event["workload_family"] == family]
        ended = [
            event["recorded_at"] for event in cell_events
            if event["kind"] in {"promoted", "rolled_back", "rejected", "inconclusive", "withdrawn"}
        ]
        if ended and now - max(ended) < criteria.proposal_cooldown_days * 86400.0:
            continue
        tried = [
            event["challenger"] for event in cell_events
            if event["kind"] == "challenger_started" and isinstance(event["challenger"], dict)
        ]
        recent_turns = turns.get((provider, family))
        reachable = criteria.minimum_challenger_turns / (
            daily * DEFAULT_CHALLENGER_SHARE * criteria.maximum_challenger_days
        )
        if recent_turns and reachable > 1.0:
            continue
        best: tuple[float, dict[str, Any], dict[str, Any], float | None] | None = None
        for name, current, value, candidate in candidate_steps(
            provider, cell.champion, tried=tried, names=OFFLINE_TUNABLES,
        ):
            rate = None
            if recent_turns:
                rate = sum(exposed(turn, name, (current, value)) for turn in recent_turns) / len(recent_turns)
                if rate < max(criteria.minimum_exposure, reachable):
                    continue
            if calls is None:
                calls = load_turn_calls(since=now - criteria.offline_days * 86400.0, until=now)
            estimate = estimate_step(
                calls,
                provider=provider,
                family=family,
                champion=cell.champion,
                challenger=candidate,
                rounds=criteria.bootstrap_rounds,
                minimum_sessions=criteria.minimum_sessions,
            )
            if not (
                estimate.get("available")
                and estimate["saving"] >= criteria.minimum_saving
                and estimate["low"] > 0
            ):
                continue
            if best is None or estimate["saving"] > best[0]:
                best = (estimate["saving"], candidate, estimate, rate)
        if best is None:
            continue
        _saving, challenger, estimate, rate = best
        proposal = {
            "cell": f"{provider}/{family}",
            "daily_turns": daily,
            "champion": dict(cell.champion),
            "challenger": challenger,
            "exposure": round(rate, 3) if rate is not None else None,
            "offline": _estimate_summary(estimate),
        }
        if apply:
            store.start_challenger(
                provider, family, challenger, actor=actor,
                evidence={
                    "reason": "daily_proposal",
                    "daily_turns": daily,
                    "exposure": proposal["exposure"],
                    "offline": proposal["offline"],
                },
            )
        proposals.append(proposal)
    return proposals


def candidate_steps(
    provider: str,
    champion: Mapping[str, Any],
    *,
    tried: list[Mapping[str, Any]],
    names: Iterable[str] | None = None,
) -> list[tuple[str, Any, Any, dict[str, Any]]]:
    """The champion with one tunable one step away, not tried recently.

    Each item is (tunable, champion's value, step's value, overrides).
    """

    parameters = policy_parameters(provider)
    wanted = set(TUNABLES) if names is None else set(names)
    tried_sets = [dict(item) for item in tried]
    steps = []
    for name, tunable in TUNABLES.items():
        if name not in wanted:
            continue
        current = champion.get(name, getattr(parameters, name))
        for value in tunable.neighbours(current):
            candidate = dict(champion)
            if value == getattr(parameters, name):
                candidate.pop(name, None)
            else:
                candidate[name] = value
            if candidate == dict(champion) or candidate in tried_sets:
                continue
            steps.append((name, current, value, candidate))
    return steps


def _estimate_summary(estimate: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "available", "reason", "saving", "low", "high", "uncached_saving",
        "sessions", "turns", "days", "compactions", "tokens_saved_per_day",
        "extra_compaction_seconds_per_day",
    )
    return {key: estimate[key] for key in keys if key in estimate}


# Review ---------------------------------------------------------------------


def sample_for_review(
    *,
    since: float,
    until: float | None = None,
    per_arm: int = 4,
    limit: int = 24,
    seed: str = "",
    cells: list[PolicyCell] | None = None,
) -> list[dict[str, Any]]:
    """Unreviewed turns a person typed, spread over cells and arms, with what the reviewer reads.

    Cells that run a challenger come first, both arms taking turns, and the
    turns exposed to the challenger's change before the others; the other
    cells follow busiest first, one turn from each in rotation, so a small
    ``limit`` still covers several cells.
    """

    until = time.time() if until is None else float(until)
    if cells is None:
        try:
            cells = HarnessEvolutionStore().cells()
        except Exception:
            LOGGER.warning("Harness policy cells unreadable; the review sample ignores challengers", exc_info=True)
            cells = []
    exposure_by_cell = {
        cell.key: exposure_of(changed_tunables(cell.provider, cell.champion, cell.challenger))
        for cell in cells
        if cell.challenger is not None
    }
    # Failed turns already count as policy failures; the reviewer reads answers.
    turns = [
        turn for turn in load_outcomes(since=since, until=until)
        if turn.origin == "user" and turn.quality == "unknown"
        and turn.status == "completed" and turn.cell
    ]
    groups: dict[tuple[str, str], list[TurnOutcome]] = defaultdict(list)
    for turn in turns:
        groups[(turn.cell, turn.arm)].append(turn)
    rng = random.Random(seed or f"{since}:{until}")
    for key in sorted(groups):
        rng.shuffle(groups[key])
        exposure = exposure_by_cell.get(key[0])
        if exposure is not None:
            # Taken from the end: exposed turns first.
            groups[key].sort(key=lambda turn: bool(exposure(turn)))
    challenger_cells = {cell for cell, arm in groups if arm == CHALLENGER}
    first = sorted(key for key in groups if key[0] in challenger_cells)
    rest = sorted(
        (key for key in groups if key[0] not in challenger_cells),
        key=lambda key: (-len(groups[key]), key),
    )
    samples: list[dict[str, Any]] = []
    taken: dict[tuple[str, str], int] = defaultdict(int)

    def take(key: tuple[str, str]) -> bool:
        candidates = groups[key]
        while candidates and taken[key] < per_arm:
            packet = _review_packet(candidates.pop())
            if packet is not None:
                samples.append(packet)
                taken[key] += 1
                return True
        return False

    for keys in (first, rest):
        progress = True
        while progress and len(samples) < limit:
            progress = False
            for key in keys:
                if len(samples) >= limit:
                    break
                progress = take(key) or progress
    return samples


def _review_packet(turn: TurnOutcome) -> dict[str, Any] | None:
    agent = _database_schema("agent")
    database = portal_database(schema="agent")
    with database.read() as connection:
        segments = connection.execute(
            f"SELECT s.segment_type, s.content_type, p.body AS content, s.metadata_json, COALESCE(s.finished_at,s.started_at) AS created_at FROM {agent}.segments s JOIN {agent}.payloads p ON p.payload_ref=s.content_ref"
            " WHERE s.execution_id=%s AND s.segment_type IN ('input', 'model') ORDER BY s.finished_at NULLS LAST",
            (turn.execution_id,),
        ).fetchall()
        execution = connection.execute(
            f"SELECT agent_id, started_at, finished_at FROM {agent}.executions WHERE execution_id=%s",
            (turn.execution_id,),
        ).fetchone()
        following = None
        if execution is not None and str(execution["finished_at"] or ""):
            following = connection.execute(
                f"SELECT e.execution_id, e.started_at, p.body AS content FROM {agent}.executions e"
                f" JOIN {agent}.segments s ON s.execution_id = e.execution_id AND s.segment_type = 'input'"
                f" JOIN {agent}.payloads p ON p.payload_ref=s.content_ref"
                " WHERE e.agent_id=%s AND e.started_at > %s ORDER BY e.started_at LIMIT 1",
                (str(execution["agent_id"]), str(execution["started_at"])),
            ).fetchone()
    request = ""
    answers: list[str] = []
    for segment in segments:
        text = _text(segment["content"])
        if segment["segment_type"] == "input" and not request:
            match = _USER_MESSAGE_RE.search(text)
            request = match.group(1) if match else ""
        elif segment["segment_type"] == "model":
            try:
                metadata = json_value(segment["metadata_json"] or "{}")
            except (TypeError, ValueError):
                metadata = {}
            if isinstance(metadata, dict) and metadata.get("kind") == "final_answer" and text:
                answers.append(text)
    if not request:
        return None
    next_message = ""
    next_gap_seconds = None
    if following is not None:
        match = _USER_MESSAGE_RE.search(_text(following["content"]))
        next_message = match.group(1) if match else ""
        next_gap_seconds = _seconds_between(str(execution["finished_at"]), str(following["started_at"]))
    answer = "\n\n".join(answers)
    return {
        "schema": REVIEW_SAMPLE_SCHEMA,
        "execution_id": turn.execution_id,
        "cell": turn.cell,
        "arm": turn.arm,
        "status": turn.status,
        "failure_kind": turn.failure_kind,
        "input_tokens": turn.input_tokens,
        "request": _clip(request, 2000),
        "answer": _clip(answer, 3000),
        "next_message": _clip(next_message, 1000),
        "next_message_after_seconds": next_gap_seconds,
    }


def record_review(
    execution_id: str,
    *,
    result: str,
    reason: str,
    score: float | None = None,
    store: HarnessEffectivenessStore | None = None,
) -> None:
    """Keep the reviewer's judgement of one turn as its quality label."""

    effectiveness = store or HarnessEffectivenessStore.shared()
    effectiveness.record_quality(
        execution_id, result=result, source=REVIEW_QUALITY_SOURCE, score=score, reason=reason,
    )
    effectiveness.flush()


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8", errors="replace")
    return str(value)


def _clip(text: str, limit: int) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + "\n…\n" + text[-half:]


def _seconds_between(start: str, end: str) -> float | None:
    from datetime import datetime

    try:
        first = datetime.fromisoformat(start.replace("Z", "+00:00"))
        second = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    return round((second - first).total_seconds(), 1)


# Report ---------------------------------------------------------------------


def render_report(
    *,
    cells: list[PolicyCell],
    evaluations: list[dict[str, Any]],
    proposals: list[dict[str, Any]],
    traffic: Mapping[tuple[str, str], float],
    generated_at: float,
    collected: Mapping[str, int] | None = None,
) -> str:
    lines = [
        "# Harness 演化日报",
        "",
        f"生成时间：{time.strftime('%Y-%m-%d %H:%M', time.localtime(generated_at))}",
        "",
    ]
    if collected is not None:
        lines += [f"本次收集了 {collected.get('collected', 0)} 个 turn 的调用记录，删除了 {collected.get('removed', 0)} 个过期记录。", ""]
    lines += ["## 评估", ""]
    if not evaluations:
        lines += ["没有正在运行的挑战者。", ""]
    for item in evaluations:
        champion = item["champion"]
        challenger = item["challenger"]
        lines += [
            f"### {item['cell']}（v{item['version']}，运行 {item['age_days']} 天）",
            "",
            f"- 结论：**{item['decision']}**（{', '.join(item['reasons']) or '—'}）；执行：{item.get('applied') or '未执行'}",
            f"- 参数：冠军 `{json.dumps(item['champion_parameters'], ensure_ascii=False)}` → 挑战者 `{json.dumps(item['challenger_parameters'], ensure_ascii=False)}`",
            f"- 离线重放：{_offline_line(item.get('offline') or {})}",
            f"- 受影响的 turn：冠军 {champion['turns']} / 挑战者 {challenger['turns']}（全部 {champion.get('all_turns', champion['turns'])} / {challenger.get('all_turns', challenger['turns'])}）",
            f"- 策略失败率：{_pct(champion['failure_rate'])} / {_pct(challenger['failure_rate'])}；挑战者高出的上界 {_pct(item.get('failure_rate_rise_upper'))}（要求低于 5%）",
            f"- 评审标签：{champion.get('all_labels', champion['labels'])} / {challenger.get('all_labels', challenger['labels'])}；通过率 {_pct(champion['pass_rate'])} / {_pct(challenger['pass_rate'])}；挑战者低出的上界 {_pct(item.get('pass_rate_fall_upper'))}（要求低于 10%）",
            "",
        ]
    lines += ["## 新挑战者", ""]
    if not proposals:
        lines.append("今天没有新挑战者。")
    for item in proposals:
        exposure = item.get("exposure")
        lines.append(
            f"- {item['cell']}（日均 {item['daily_turns']} turn"
            + (f"，改动作用于 {exposure:.0%} 的 turn" if exposure is not None else "")
            + f"）：`{json.dumps(item['champion'], ensure_ascii=False)}` → `{json.dumps(item['challenger'], ensure_ascii=False)}`；"
            + f"离线重放 {_offline_line(item.get('offline') or {})}"
        )
    lines += ["", "## 单元格", "", "| 单元格 | 状态 | 版本 | 冠军 | 挑战者 | 份额 | 日均 turn |", "| --- | --- | --- | --- | --- | --- | --- |"]
    known = {(cell.provider, cell.workload_family) for cell in cells}
    for cell in cells:
        lines.append(
            f"| {cell.key} | {cell.status} | {cell.version} | `{json.dumps(cell.champion, ensure_ascii=False)}` | "
            f"`{json.dumps(cell.challenger, ensure_ascii=False) if cell.challenger is not None else '—'}` | "
            f"{cell.challenger_share:.0%} | {traffic.get((cell.provider, cell.workload_family), 0)} |"
        )
    for key, daily in sorted(traffic.items(), key=lambda item: -item[1]):
        if key not in known and daily >= 1:
            lines.append(f"| {key[0]}/{key[1]} | （默认） | 0 | `{{}}` | — | — | {daily} |")
    return "\n".join(lines) + "\n"


OFFLINE_REASONS = {
    "not_replayable_tunable": "这个参数不能离线估算",
    "not_replayable_provider": "这个 Provider 不能离线重放",
    "needs_sessions": "数据不足",
    "no_change": "没有改动",
    "no_tokens": "没有调用记录",
}


def _offline_line(offline: Mapping[str, Any]) -> str:
    if not offline.get("available"):
        reason = str(offline.get("reason") or "")
        text = OFFLINE_REASONS.get(reason, reason or "没有估算")
        if reason == "needs_sessions":
            text += f"（{offline.get('sessions', 0)} 个会话）"
        return text
    compactions = offline.get("compactions") or {}
    uncached = offline.get("uncached_saving")
    extra = float(offline.get("extra_compaction_seconds_per_day") or 0.0)
    return (
        f"输入 token 节省 {offline['saving']:.1%}（90% 区间 {offline['low']:.1%}～{offline['high']:.1%}）"
        + (f"，未缓存 token 节省 {uncached:.1%}" if uncached is not None else "")
        + f"，压缩 {compactions.get('champion', 0)}→{compactions.get('challenger', 0)} 次"
        + (f"，每天多等压缩约 {extra / 60:.0f} 分钟" if extra > 0 else "")
        + (f"，每天少等压缩约 {-extra / 60:.0f} 分钟" if extra < 0 else "")
        + f"；{offline.get('sessions', 0)} 个会话、近 {offline.get('days', 0)} 天"
    )


def _pct(value: object) -> str:
    return "—" if value is None else f"{float(value):.1%}"

