"""The offline replay of compaction limits on recorded model calls.

:module: spec.design-governance
"""

from __future__ import annotations

import json
import time

from management_portal.execution import harness_evolution_offline as offline
from management_portal.execution.harness_evolution import HarnessEvolutionStore
from management_portal.execution.harness_evolution_offline import (
    COMPACTED,
    GROW,
    RESET,
    TurnCalls,
    calibrate,
    estimate_step,
    replay,
    sessions_of,
    turn_calls_from_rows,
)

CODEX_WINDOW = 258_400


def _session(
    agent: str,
    *,
    provider: str = "codex",
    cells: list[str],
    configured: list[int],
    calls_per_turn: int = 12,
    growth: int = 9_000,
    start: int = 60_000,
    summary: int = 80_000,
    limit_of_session=None,
    ratio: float = 0.95,
    clock: float = 1_000_000.0,
    wait: float = 120.0,
) -> list[TurnCalls]:
    """Turns of a session whose context grows until the Provider compacts it.

    ``configured`` is the limit each turn asks for (0: none);
    ``limit_of_session`` turns the requests into the limit in force.
    """

    turns = []
    context = start
    in_force = 0
    for index, (cell, requested) in enumerate(zip(cells, configured)):
        in_force = limit_of_session(in_force, requested) if limit_of_session else requested
        calls = []
        compactions = []
        for _ in range(calls_per_turn):
            clock += 10.0
            calls.append((clock, context, int(context * 0.95)))
            if in_force and context >= ratio * in_force:
                compactions.append(clock + 5.0)
                clock += wait
                context = summary
            else:
                context += growth
        settings = {}
        if requested:
            settings = {
                "limit": requested,
                "window": CODEX_WINDOW if provider == "codex" else 0,
                "threshold_percent": 70,
                "max_context_tokens": requested if provider == "claude" else 0,
            }
        turns.append(TurnCalls(
            execution_id=f"{agent}-{index}", agent_id=agent, provider=provider, cell=cell,
            arm="champion", version=1, recorded_at=clock, compaction=settings,
            calls=tuple(calls), compactions=tuple(compactions),
        ))
    return turns


def _sticky(in_force: int, requested: int) -> int:
    return min(in_force or requested, requested) if requested else in_force


def test_a_drop_is_a_compaction_when_the_session_recorded_one_and_a_reset_otherwise():
    """context 回落时有记录的压缩算作压缩，没有记录的算作重置"""
    turn = TurnCalls(
        execution_id="e1", agent_id="a", provider="codex", cell="codex/short_text", arm="champion",
        version=1, recorded_at=100.0,
        calls=((10.0, 170_000, 0), (20.0, 80_000, 0), (30.0, 90_000, 0), (40.0, 150_000, 0), (50.0, 20_000, 0)),
        compactions=(15.0,),
    )
    [session] = sessions_of([turn])
    assert [event for _tokens, event in session.events[0]] == [GROW, COMPACTED, GROW, GROW, RESET]
    assert session.summary_tokens == 80_000


def test_replay_follows_the_recorded_sessions_and_prices_another_threshold():
    """离线回放贴合记录的 session 用量，并估算另一个压缩阈值的 token 节省"""
    limit = CODEX_WINDOW * 70 // 100
    turns = []
    for number in range(6):
        turns += _session(
            f"agent-{number}", cells=["codex/repository_work"] * 8, configured=[limit] * 8,
            growth=7_000 + 500 * number, clock=1_000_000.0 + number * 10_000,
        )
    sessions = sessions_of(turns)
    calibration = calibrate(sessions, "codex")
    assert 0.9 <= calibration.trigger_ratio <= 1.0
    assert calibration.summary_tokens == 80_000
    assert calibration.compaction_seconds == 120.0
    recorded = sum(tokens for turn in turns for _at, tokens, _cached in turn.calls)
    replayed = sum(
        replay(session, calibration, lambda turn: int(turn.compaction.get("limit") or 0)).input_tokens
        for session in sessions
    )
    assert abs(replayed / recorded - 1) < 0.03

    lower = estimate_step(
        turns, provider="codex", family="repository_work",
        champion={}, challenger={"compaction_threshold_percent": 60}, rounds=300,
    )
    assert lower["available"] and lower["sessions"] == 6
    assert lower["saving"] > 0.03 and lower["low"] > 0
    assert lower["compactions"]["challenger"] > lower["compactions"]["champion"]
    # Each extra compaction holds the session up.
    assert lower["extra_compaction_seconds_per_day"] > 0
    # More compactions read more context uncached.
    assert lower["uncached_saving"] < 0
    higher = estimate_step(
        turns, provider="codex", family="repository_work",
        champion={}, challenger={"compaction_threshold_percent": 80}, rounds=300,
    )
    assert higher["saving"] < 0 and higher["high"] < 0
    # Another cell's turns keep their own limit.
    elsewhere = estimate_step(
        turns, provider="codex", family="short_text",
        champion={}, challenger={"compaction_threshold_percent": 60}, rounds=300,
    )
    assert elsewhere == {**elsewhere, "available": False, "reason": "needs_sessions", "sessions": 0}


def test_a_claude_session_keeps_the_lowest_limit_it_was_given():
    """claude session 沿用收到过的最低压缩上限，codex 按每个 turn 单独取上限"""
    # One turn of repository_work asks for 300k; the session then grows in
    # short_text turns that ask for none.
    cells = ["claude/repository_work"] + ["claude/short_text"] * 9
    calibration = offline.Calibration(trigger_ratio=0.92, summary_tokens=31_000, default_limit=800_000)
    claude = _session("c", provider="claude", cells=cells, configured=[300_000] + [0] * 9, growth=5_000)
    [session] = sessions_of(claude)
    asked = replay(session, calibration, lambda turn: int(turn.compaction.get("limit") or 0))
    # Claude Code keeps compacting at 300k in the later turns.
    assert asked.compactions >= 1
    assert replay(session, calibration, lambda turn: 0).compactions == 0
    # Codex takes the limit turn by turn: the later turns fall back to its own.
    codex = [TurnCalls(**{**turn.__dict__, "provider": "codex", "agent_id": "x"}) for turn in claude]
    [codex_session] = sessions_of(codex)
    assert replay(
        codex_session, offline.Calibration(trigger_ratio=0.92, summary_tokens=31_000, default_limit=0),
        lambda turn: int(turn.compaction.get("limit") or 0),
    ).compactions == 0

    turns = []
    for number in range(5):
        turns += _session(
            f"claude-{number}", provider="claude", cells=cells, configured=[400_000] + [0] * 9,
            growth=4_000, start=40_000, summary=31_000, ratio=0.92, limit_of_session=_sticky,
            clock=2_000_000.0 + number * 10_000,
        )
    lower = estimate_step(
        turns, provider="claude", family="repository_work",
        champion={}, challenger={"compaction_max_context_tokens": 300_000}, rounds=300,
    )
    assert lower["available"] and lower["saving"] > 0.05
    # Claude Code ignores the threshold.
    threshold = estimate_step(
        turns, provider="claude", family="repository_work",
        champion={}, challenger={"compaction_threshold_percent": 60}, rounds=300,
    )
    assert threshold["saving"] == 0


def test_only_compaction_steps_of_replayed_providers_with_enough_sessions_are_estimated():
    """只有可回放 Provider 的压缩参数调整且 session 足够时才给出离线估算"""
    turns = _session("a", cells=["codex/short_text"] * 3, configured=[180_880] * 3)
    assert estimate_step(
        turns, provider="codex", family="short_text", champion={}, challenger={"offload_trigger_bytes": 49152},
    )["reason"] == "not_replayable_tunable"
    assert estimate_step(
        turns, provider="dsh", family="short_text", champion={}, challenger={"compaction_threshold_percent": 65},
    )["reason"] == "not_replayable_provider"
    few = estimate_step(
        turns, provider="codex", family="short_text", champion={}, challenger={"compaction_threshold_percent": 65},
    )
    assert (few["available"], few["reason"], few["sessions"]) == (False, "needs_sessions", 1)


def test_recorded_calls_are_kept_and_read_back(isolated_portal_case):
    """turn 的模型调用记录按保留期写入并原样读回，session 操作不算 turn"""
    observation = {
        "execution_id": "execution-1",
        "provider": "claude",
        "load_family": "repository_work",
        "signals_json": json.dumps({"operation": "turn", "policy_cell": "claude/repository_work", "policy_arm": "challenger", "policy_version": 3}),
        "action_outcomes_json": json.dumps([{
            "action": "early_context_compaction", "status": "applied",
            "metrics": {"agent_loop_auto_compact_token_limit": 300_000, "max_context_tokens": 300_000, "threshold_percent": 70},
        }]),
        "recorded_at": round(time.time(), 6),
        "agent_id": "resident_1",
    }
    usage = [
        {"occurred_at": "2026-10-06T10:00:02.000Z", "usage_json": json.dumps({"input_tokens": 250_000, "cached_input_tokens": 240_000})},
        {"occurred_at": "2026-10-06T10:00:01.000Z", "usage_json": json.dumps({"input_tokens": 240_000, "cached_input_tokens": 230_000})},
        {"occurred_at": "2026-10-06T10:00:03.000Z", "usage_json": json.dumps({"input_tokens": 0})},
    ]
    turn = turn_calls_from_rows(observation, usage, ["2026-10-06T10:00:02.500000Z"])
    assert turn.cell == "claude/repository_work" and (turn.arm, turn.version) == ("challenger", 3)
    assert turn.compaction == {"limit": 300_000, "window": 0, "threshold_percent": 70, "max_context_tokens": 300_000}
    assert [tokens for _at, tokens, _cached in turn.calls] == [240_000, 250_000]
    assert turn.compactions == (offline.parse_time("2026-10-06T10:00:02.500Z"),)
    # Session operations are no turns.
    release = dict(observation, signals_json=json.dumps({"operation": "release"}))
    assert turn_calls_from_rows(release, usage, []) is None

    with isolated_portal_case():
        store = HarnessEvolutionStore()
        old = TurnCalls(**{**turn.__dict__, "execution_id": "execution-0", "recorded_at": time.time() - 40 * 86400})
        assert store.write_turn_calls([old, turn], keep_since=time.time() - 30 * 86400) == 1
        store.write_turn_calls([turn], keep_since=0)
        [kept] = offline.load_turn_calls(since=0, store=store)
        assert kept == turn


def test_recovery_compaction_settings_come_from_the_saved_initial_plan():
    """恢复的 turn 没有压缩记录时，从保存的初始计划 checkpoint 取压缩设置

    :module: execution.harness"""
    from management_common.postgres_database import PostgresRow

    checkpoint = {"harness_plan": {"features": {"early_context_compaction": {
        "enabled": True, "policy_threshold_percent": 65, "max_context_tokens": 200000,
    }}}}
    assert offline.saved_compaction_settings(json.dumps(checkpoint)) == {
        "limit": 0, "window": 0, "threshold_percent": 65, "max_context_tokens": 200000,
    }
    # The collection query returns PostgresRow, not dict. When no applied
    # compaction is recorded, use its nullable checkpoint column directly.
    observation = {
        "execution_id": "execution-recovered", "agent_id": "resident_1",
        "provider": "claude", "load_family": "repository_work",
        "signals_json": "{}", "action_outcomes_json": "[]", "recorded_at": time.time(),
        "checkpoint_json": json.dumps(checkpoint),
    }
    usage = [{"occurred_at": "2026-10-11T13:09:11Z", "usage_json": '{"input_tokens":100}'}]
    row = PostgresRow(tuple(observation), tuple(observation.values()))
    turn = turn_calls_from_rows(row, usage, [])
    assert turn.compaction == offline.saved_compaction_settings(json.dumps(checkpoint))
    observation["checkpoint_json"] = None
    row = PostgresRow(tuple(observation), tuple(observation.values()))
    assert turn_calls_from_rows(row, usage, []).compaction == {}
