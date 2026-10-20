"""Offline estimate of what a compaction step saves, from the calls of daily use.

A compaction limit acts mechanically: it decides after which call a session's
context is summarised and how much context the calls after it carry.  Given
the context every model call of a session carried, replaying the session with
another limit tells how much context its calls would have carried.  The
champion and the challenger replay the *same* sessions, so the differences
between sessions -- which swamp any comparison of separate turns -- cancel out,
and a few days of calls decide what weeks of a live split could not.

What a replay cannot tell is whether an earlier summary loses something a
later turn needed.  That is what the live challenger is for: it runs on a
share of the sessions and its policy failures and reviewed quality are held
against the champion's (``harness_evolution_evaluation``).

The Execution Service keeps each call's usage only as long as it keeps the
execution (about a day), so ``collect_turn_calls`` copies the calls of every
observed turn into ``harness_turn_calls`` (kept 30 days); the
Harness evolution Lineage submits it to ExecutionService every hour.

The replay follows how each Provider compacts:

* Codex compacts at the limit the turn configures (a share of the model's
  effective window, bounded by ``compaction_max_context_tokens``); a turn that
  configures none uses Codex's own limit.
* Claude Code takes ``compaction_max_context_tokens`` only, and its session
  keeps the lowest limit it was ever given (``claude_code_daemon``); a session
  never given one compacts near its window.
* Other Providers are not replayed.

Each Provider compacts a little before its limit and leaves a summary of
roughly constant size; both are calibrated from the compactions in the data.
The point a compaction triggers at lies between the last two calls before it,
so their midpoint estimates it.

A compaction also makes the session wait (about two minutes for both Claude
Code and Codex): the gap between the calls around it, less a normal gap
between calls.  A lower limit saves tokens and costs waiting; the estimate
reports both, and the evaluation leaves that trade to a person.
A sharp drop of the context between two calls is a compaction when the
session recorded one in between, otherwise the context was reset (a new
Provider session) and the replay starts again from the real size.
"""

from __future__ import annotations
from management_common.site_compatibility import compatibility_value

import json
import random
import statistics
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from management_common.portal_database import _database_schema, portal_database
from management_common.database_time import instant, decode_times
from management_common.json_columns import json_value

from .harness_evolution import EVOLUTION_SCHEMA_NAME, HarnessEvolutionStore
from .harness_policy_table import (
    CompactionReplayModel,
    HarnessPolicyParameters,
    compaction_replay_model,
    policy_parameters,
    with_overrides,
)


OFFLINE_SCHEMA = "tfi.harness-offline-estimate.v1"
# The tunables whose effect on input tokens a replay can estimate.
OFFLINE_TUNABLES = frozenset({"compaction_threshold_percent", "compaction_max_context_tokens"})
GROW, COMPACTED, RESET = 0, 1, 2
# A drop to below 60% of a context of at least 50k tokens is a compaction or
# a reset; smaller changes are the history growing or being trimmed.
DROP_MINIMUM_TOKENS = 50_000
DROP_RATIO = 0.6
# How far a compaction segment may lie outside the calls around its drop.
COMPACTION_TIME_SLACK_SECONDS = 2.0
DEFAULT_TRIGGER_RATIO = 0.93
TEST_ACTORS = frozenset(compatibility_value("harness_test_actors", []))


@dataclass(frozen=True)
class TurnCalls:
    """The model calls of one turn and the compaction limit it configured."""

    execution_id: str
    agent_id: str
    provider: str
    cell: str
    arm: str
    version: int
    recorded_at: float
    # The early compaction the turn configured: ``limit`` (tokens), and what
    # it came from (``window``, ``threshold_percent``,
    # ``max_context_tokens``).  Empty when the turn configured none.
    compaction: dict[str, int] = field(default_factory=dict)
    # (time, input tokens, cached input tokens) of each model call, in order.
    calls: tuple[tuple[float, int, int], ...] = ()
    # Times of the compactions the turn recorded.
    compactions: tuple[float, ...] = ()

    @property
    def started_at(self) -> float:
        return self.calls[0][0] if self.calls else self.recorded_at


@dataclass(frozen=True)
class Calibration:
    """How a Provider's compactions behave in the data."""

    # A compaction happens once the context reaches this share of the limit.
    trigger_ratio: float = DEFAULT_TRIGGER_RATIO
    # Context left after a compaction, for sessions that recorded none.
    summary_tokens: int = 0
    # The limit of a turn that configures none (0: none known).
    default_limit: int = 0
    compactions: int = 0
    # How long a compaction holds the session up.
    compaction_seconds: float = 0.0


# Collection -------------------------------------------------------------------


def parse_time(value: object) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def compaction_settings(action_outcomes: object) -> dict[str, int]:
    """The early compaction limit a turn configured, from its action outcomes."""

    try:
        actions = json_value(action_outcomes or "[]")
    except (TypeError, ValueError):
        return {}
    for action in actions if isinstance(actions, list) else []:
        if not isinstance(action, dict) or action.get("action") != "early_context_compaction":
            continue
        if action.get("status") != "applied":
            return {}
        metrics = action.get("metrics") if isinstance(action.get("metrics"), dict) else {}
        settings = {
            "limit": _int(metrics.get("agent_loop_auto_compact_token_limit")),
            "window": _int(metrics.get("agent_loop_effective_context_window")),
            "threshold_percent": _int(metrics.get("threshold_percent")),
            "max_context_tokens": _int(metrics.get("max_context_tokens")),
        }
        return settings if settings["limit"] or settings["threshold_percent"] else {}
    return {}


def saved_compaction_settings(checkpoint_json: object) -> dict[str, int]:
    try:
        checkpoint = json_value(checkpoint_json or "{}")
    except (TypeError, ValueError):
        return {}
    plan = checkpoint.get("harness_plan", {}) if isinstance(checkpoint, dict) else {}
    features = plan.get("features", {}) if isinstance(plan, dict) else {}
    settings = features.get("early_context_compaction", {}) if isinstance(features, dict) else {}
    if not isinstance(settings, dict) or settings.get("enabled") is not True:
        return {}
    return {
        "limit": 0, "window": 0,
        "threshold_percent": _int(settings.get("policy_threshold_percent")) or 70,
        "max_context_tokens": _int(settings.get("max_context_tokens")),
    }


def turn_calls_from_rows(
    observation: Mapping[str, Any],
    usage: Iterable[Mapping[str, Any]],
    compactions: Iterable[object],
) -> TurnCalls | None:
    """One turn's calls from its observation, usage events and compaction segments."""

    try:
        signals = json_value(observation["signals_json"] or "{}")
    except (TypeError, ValueError):
        signals = {}
    signals = signals if isinstance(signals, dict) else {}
    if str(signals.get("operation") or "turn") != "turn":
        return None
    calls = []
    for event in usage:
        try:
            values = json_value(event["usage_json"] or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(values, dict):
            continue
        tokens = _int(values.get("input_tokens"))
        if tokens > 0:
            calls.append((
                round(parse_time(event["occurred_at"]), 3),
                tokens,
                min(tokens, _int(values.get("cached_input_tokens"))),
            ))
    if not calls:
        return None
    calls.sort()
    provider = str(observation["provider"] or "")
    family = str(observation["load_family"] or "")
    return TurnCalls(
        execution_id=str(observation["execution_id"]),
        agent_id=str(observation["agent_id"] or ""),
        provider=provider,
        cell=str(signals.get("policy_cell") or f"{provider}/{family}"),
        arm=str(signals.get("policy_arm") or ""),
        version=_int(signals.get("policy_version")),
        recorded_at=float(observation["recorded_at"] or 0.0),
        compaction=compaction_settings(observation["action_outcomes_json"]) or saved_compaction_settings(observation["checkpoint_json"]),
        calls=tuple(calls),
        compactions=tuple(sorted(round(parse_time(item), 3) for item in compactions if parse_time(item))),
    )


def collect_turn_calls(
    *,
    now: float | None = None,
    lookback_days: float = 3.0,
    retention_days: float | None = None,
    store: HarnessEvolutionStore | None = None,
    limit: int = 5000,
) -> dict[str, int | bool]:
    """Copy the calls of observed, finished turns not kept yet; drop old ones.

    ``retention_days`` defaults to the administrator's Harness retention period.
    """

    from .service_settings import ExecutionSettings
    settings = ExecutionSettings()
    collected_at = time.time()
    skipped = {"collected": 0, "removed": 0, "skipped_recording_disabled": True}
    policy = settings.get()
    if not policy["harness_recording_enabled"]:
        return skipped
    if retention_days is None:
        retention_days = policy["harness_retention_days"]
    now = time.time() if now is None else float(now)
    store = store or HarnessEvolutionStore()
    store.ensure_schema()
    effectiveness = _database_schema("harness_effectiveness")
    agent = _database_schema("agent")
    execution = _database_schema("execution_runtime")
    evolution = _database_schema(EVOLUTION_SCHEMA_NAME)
    database = portal_database(schema=EVOLUTION_SCHEMA_NAME)
    with database.read() as connection:
        observations = connection.execute(
            "SELECT o.execution_id, o.provider, o.load_family, o.signals_json,"
            " o.action_outcomes_json, o.recorded_at, a.agent_id, a.actor_ref, e.checkpoint_json"
            f" FROM {effectiveness}.harness_observations o"
            f" JOIN {agent}.executions a ON a.execution_id = o.execution_id"
            f" LEFT JOIN {execution}.executions e ON e.execution_id = o.execution_id"
            " WHERE o.recorded_at >= %s AND a.status IN ('completed', 'failed', 'cancelled')"
            f" AND EXISTS (SELECT 1 FROM {execution}.execution_token_usage_events u"
            "  WHERE u.execution_id = o.execution_id)"
            f" AND NOT EXISTS (SELECT 1 FROM {evolution}.harness_turn_calls t"
            "  WHERE t.execution_id = o.execution_id)"
            " ORDER BY o.recorded_at LIMIT %s",
            (instant(now - lookback_days * 86400.0), int(limit)),
        ).fetchall()
        observations = [
            decode_times(row, ('recorded_at',), format='seconds') for row in observations
            if str(row["actor_ref"] or "") not in TEST_ACTORS
        ]
        ids = [str(row["execution_id"]) for row in observations]
        usage: dict[str, list[Any]] = defaultdict(list)
        segments: dict[str, list[object]] = defaultdict(list)
        if ids:
            for row in connection.execute(
                "SELECT execution_id, occurred_at, usage_json"
                f" FROM {execution}.execution_token_usage_events"
                " WHERE execution_id = ANY(%s) ORDER BY occurred_at",
                (ids,),
            ).fetchall():
                usage[str(row["execution_id"])].append(row)
            for row in connection.execute(
                f"SELECT execution_id, COALESCE(finished_at,started_at) AS created_at FROM {agent}.segments"
                " WHERE execution_id = ANY(%s) AND segment_type = 'compaction'",
                (ids,),
            ).fetchall():
                segments[str(row["execution_id"])].append(row["created_at"])
    turns = [
        turn for turn in (
            turn_calls_from_rows(row, usage[str(row["execution_id"])], segments[str(row["execution_id"])])
            for row in observations
        )
        if turn is not None
    ]
    # Reads above may take time. Re-check at the short persistence boundary,
    # so a close during collection cannot write evidence after it returns.
    with settings.admit_harness_recording(collected_at) as enabled:
        if not enabled:
            return skipped
        removed = store.write_turn_calls(turns, keep_since=now - retention_days * 86400.0)
    return {"collected": len(turns), "removed": removed}


def load_turn_calls(
    *,
    since: float,
    until: float | None = None,
    store: HarnessEvolutionStore | None = None,
) -> list[TurnCalls]:
    store = store or HarnessEvolutionStore()
    return [
        TurnCalls(
            execution_id=row["execution_id"],
            agent_id=row["agent_id"],
            provider=row["provider"],
            cell=row["cell"],
            arm=row["arm"],
            version=row["version"],
            recorded_at=row["recorded_at"],
            compaction={key: _int(value) for key, value in row["compaction"].items()},
            calls=tuple(
                (float(call[0]), int(call[1]), int(call[2]) if len(call) > 2 else 0)
                for call in row["calls"]
            ),
            compactions=tuple(float(at) for at in row["compactions"]),
        )
        for row in store.turn_calls(since=since, until=until)
    ]


# Replay -----------------------------------------------------------------------


@dataclass
class Session:
    """The turns of one agent on one Provider, with each call's event."""

    agent_id: str
    provider: str
    turns: list[TurnCalls]
    # Per turn: (input tokens, event) of each call.
    events: list[list[tuple[int, int]]] = field(default_factory=list)

    @property
    def summary_tokens(self) -> int:
        sizes = [
            tokens for calls in self.events for tokens, event in calls if event == COMPACTED
        ]
        return int(statistics.median(sizes)) if sizes else 0


def sessions_of(turns: Iterable[TurnCalls]) -> list[Session]:
    grouped: dict[tuple[str, str], list[TurnCalls]] = defaultdict(list)
    for turn in turns:
        if turn.calls:
            grouped[(turn.agent_id, turn.provider)].append(turn)
    sessions = []
    for (agent_id, provider), items in sorted(grouped.items()):
        items.sort(key=lambda turn: turn.started_at)
        session = Session(agent_id, provider, items)
        previous: tuple[float, int] | None = None
        previous_turn: TurnCalls | None = None
        for turn in items:
            nearby = turn.compactions + (previous_turn.compactions if previous_turn else ())
            labelled = []
            for at, tokens, _cached in turn.calls:
                event = GROW
                if (
                    previous is not None
                    and previous[1] >= DROP_MINIMUM_TOKENS
                    and tokens < DROP_RATIO * previous[1]
                ):
                    compacted = any(
                        previous[0] - COMPACTION_TIME_SLACK_SECONDS <= moment <= at + COMPACTION_TIME_SLACK_SECONDS
                        for moment in nearby
                    )
                    event = COMPACTED if compacted else RESET
                labelled.append((tokens, event))
                previous = (at, tokens)
            session.events.append(labelled)
            previous_turn = turn
        sessions.append(session)
    return sessions


def requested_limit(provider: str, parameters: HarnessPolicyParameters, settings: Mapping[str, int]) -> int:
    """The limit a turn that configured ``settings`` asks for under ``parameters``."""

    if not settings:
        return 0
    model = compaction_replay_model(provider)
    if model is None:
        return int(settings.get("limit") or 0)
    if model.sticky_absolute_limit:
        return int(parameters.compaction_max_context_tokens or 0)
    window = int(settings.get("window") or 0)
    if not window:
        return int(settings.get("limit") or 0)
    limit = window * int(parameters.compaction_threshold_percent) // 100
    bound = int(parameters.compaction_max_context_tokens or 0)
    return min(limit, bound) if bound else limit


def calibrate(sessions: Iterable[Session], provider: str) -> Calibration:
    """Trigger share, summary size and default limit of ``provider`` from its compactions."""

    model = compaction_replay_model(provider) or CompactionReplayModel()
    ratios: list[float] = []
    summaries: list[int] = []
    unconfigured: list[int] = []
    compaction_gaps: list[float] = []
    call_gaps: list[float] = []
    count = 0
    for session in sessions:
        if session.provider != provider:
            continue
        sticky = 0
        previous = 0
        before = 0
        previous_at = 0.0
        for turn, calls in zip(session.turns, session.events):
            limit = int(turn.compaction.get("limit") or 0)
            if model.sticky_absolute_limit and limit:
                sticky = min(sticky or limit, limit)
            effective = sticky if model.sticky_absolute_limit else limit
            for index, ((at, _tokens, _cached), (tokens, event)) in enumerate(zip(turn.calls, calls)):
                if event == COMPACTED and previous:
                    count += 1
                    summaries.append(tokens)
                    point = (before + previous) / 2 if 0 < before < previous else previous
                    if effective:
                        ratios.append(point / effective)
                    else:
                        unconfigured.append(point)
                    if previous_at:
                        compaction_gaps.append(at - previous_at)
                elif event == GROW and index and previous_at:
                    # Calls within one turn; between turns the user is thinking.
                    call_gaps.append(at - previous_at)
                before, previous = previous, tokens
                previous_at = at
    ratio = statistics.median(ratios) if ratios else DEFAULT_TRIGGER_RATIO
    ratio = min(1.0, max(0.5, ratio))
    default = 0
    if model.native_compact_tokens:
        default = model.native_compact_tokens
    elif unconfigured:
        default = int(statistics.median(unconfigured) / ratio)
    waiting = 0.0
    if compaction_gaps:
        normal = statistics.median(call_gaps) if call_gaps else 0.0
        waiting = max(0.0, statistics.median(compaction_gaps) - normal)
    return Calibration(
        trigger_ratio=round(ratio, 4),
        summary_tokens=int(statistics.median(summaries)) if summaries else 0,
        default_limit=default,
        compactions=count,
        compaction_seconds=round(waiting, 1),
    )


@dataclass(frozen=True)
class Replay:
    input_tokens: int
    uncached_input_tokens: int
    compactions: int


def replay(
    session: Session,
    calibration: Calibration,
    limit_of: Callable[[TurnCalls], int],
) -> Replay:
    """Input tokens of the session's calls, and its compactions, under ``limit_of``.

    ``limit_of`` gives the limit each turn configures (0: none).  A call's
    uncached input is what it adds to the context, except that the first
    call after a compaction or reset reads its whole context uncached.
    """

    model = compaction_replay_model(session.provider) or CompactionReplayModel()
    sticky_model = model.sticky_absolute_limit
    sticky = _initial_sticky_limit(session, calibration, model) if sticky_model else 0
    summary = session.summary_tokens or calibration.summary_tokens
    context: int | None = None
    previous = 0
    total = 0
    uncached = 0
    cold = True
    compactions = 0
    for turn, calls in zip(session.turns, session.events):
        requested = int(limit_of(turn) or 0)
        if sticky_model:
            if requested:
                sticky = min(sticky or requested, requested)
            limit = sticky or calibration.default_limit
        else:
            limit = requested or calibration.default_limit
        trigger = calibration.trigger_ratio * limit if limit else 0.0
        for tokens, event in calls:
            added = 0
            if context is None or event == RESET:
                context = tokens
                cold = True
            elif event == GROW:
                added = max(0, tokens - previous)
                context = max(1, context + tokens - previous)
            # A real compaction is replaced by the replay's own.
            total += context
            uncached += context if cold else added
            cold = False
            previous = tokens
            if trigger and context >= trigger:
                compactions += 1
                context = max(1, min(context, summary or int(0.3 * context)))
                cold = True
    return Replay(total, uncached, compactions)


def _initial_sticky_limit(session: Session, calibration: Calibration, model: CompactionReplayModel) -> int:
    """The limit a session with a kept limit had from before the data begins (0: none).

    A session that compacts before any turn configured a limit was given one
    earlier, at about the context it compacted at.
    """

    previous = 0
    for turn, calls in zip(session.turns, session.events):
        if turn.compaction.get("limit"):
            return 0
        for tokens, event in calls:
            if event == COMPACTED and previous:
                limit = int(previous / calibration.trigger_ratio)
                return limit if limit < model.native_compact_tokens * 0.9 else 0
            previous = tokens
    return 0


# Estimate ---------------------------------------------------------------------


def estimate_step(
    turns: Iterable[TurnCalls],
    *,
    provider: str,
    family: str,
    champion: Mapping[str, Any],
    challenger: Mapping[str, Any],
    rounds: int = 1000,
    seed: str = "",
    minimum_sessions: int = 5,
) -> dict[str, Any]:
    """Input-token saving of ``challenger`` over ``champion`` in one cell, by replay.

    Every session with a turn of the cell that configured a compaction is
    replayed under both parameter sets; the turns of other cells keep their
    own limits.  The saving is ``1 - challenger / champion`` over these
    sessions, with a 90% interval from resampling sessions.
    """

    base_parameters = with_overrides(policy_parameters(provider), champion)
    trial_parameters = with_overrides(policy_parameters(provider), challenger)
    changed = sorted(
        name for name in set(champion) | set(challenger)
        if getattr(base_parameters, name, None) != getattr(trial_parameters, name, None)
    )
    result: dict[str, Any] = {
        "schema": OFFLINE_SCHEMA,
        "available": False,
        "changed": changed,
    }
    if not changed:
        result["reason"] = "no_change"
        return result
    if any(name not in OFFLINE_TUNABLES for name in changed):
        result["reason"] = "not_replayable_tunable"
        return result
    if compaction_replay_model(provider) is None:
        result["reason"] = "not_replayable_provider"
        return result
    cell = f"{provider}/{family}"
    items = [turn for turn in turns if turn.provider == provider]
    sessions = sessions_of(items)
    calibration = calibrate(sessions, provider)

    def limits(parameters: HarnessPolicyParameters) -> Callable[[TurnCalls], int]:
        def limit_of(turn: TurnCalls) -> int:
            if turn.cell == cell and turn.compaction:
                return requested_limit(provider, parameters, turn.compaction)
            return int(turn.compaction.get("limit") or 0)
        return limit_of

    pairs = []
    exposed_turns = 0
    for session in sessions:
        cell_turns = sum(1 for turn in session.turns if turn.cell == cell and turn.compaction)
        if not cell_turns:
            continue
        exposed_turns += cell_turns
        base = replay(session, calibration, limits(base_parameters))
        trial = replay(session, calibration, limits(trial_parameters))
        pairs.append((
            base.input_tokens, trial.input_tokens, base.compactions, trial.compactions,
            base.uncached_input_tokens, trial.uncached_input_tokens,
        ))
    times = [turn.recorded_at for turn in items]
    days = max(1.0, (max(times) - min(times)) / 86400.0) if times else 1.0
    result.update({
        "sessions": len(pairs),
        "turns": exposed_turns,
        "days": round(days, 1),
        "calibration": calibration.__dict__,
    })
    if len(pairs) < minimum_sessions:
        result["reason"] = "needs_sessions"
        return result
    base_total = sum(item[0] for item in pairs)
    trial_total = sum(item[1] for item in pairs)
    if base_total <= 0:
        result["reason"] = "no_tokens"
        return result
    rng = random.Random(seed or f"{cell}|{json.dumps(dict(challenger), sort_keys=True)}")
    samples = []
    for _ in range(max(200, rounds)):
        drawn = [pairs[rng.randrange(len(pairs))] for _ in pairs]
        drawn_base = sum(item[0] for item in drawn)
        if drawn_base > 0:
            samples.append(1 - sum(item[1] for item in drawn) / drawn_base)
    samples.sort()
    base_uncached = sum(item[4] for item in pairs)
    trial_uncached = sum(item[5] for item in pairs)
    result.update({
        "available": True,
        "saving": round(1 - trial_total / base_total, 4),
        "uncached_saving": round(1 - trial_uncached / base_uncached, 4) if base_uncached else None,
        "low": round(samples[int(0.05 * (len(samples) - 1))], 4),
        "high": round(samples[int(0.95 * (len(samples) - 1))], 4),
        "input_tokens": {"champion": base_total, "challenger": trial_total},
        "uncached_input_tokens": {"champion": base_uncached, "challenger": trial_uncached},
        "tokens_saved_per_day": int((base_total - trial_total) / days),
        "compactions": {
            "champion": sum(item[2] for item in pairs),
            "challenger": sum(item[3] for item in pairs),
        },
        # Waiting for compactions the step adds per day (negative: saves).
        "extra_compaction_seconds_per_day": round(
            (sum(item[3] for item in pairs) - sum(item[2] for item in pairs))
            * calibration.compaction_seconds / days,
            1,
        ),
    })
    return result


def _int(value: object) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0
