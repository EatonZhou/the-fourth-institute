"""Policy cells: tunables, champion and challenger, the daily evaluation.

:module: spec.design-governance
"""

from __future__ import annotations

import json
import sys
import time

import pytest

from management_portal.execution import harness_evolution_evaluation as evaluation
from management_portal.execution.harness_evolution import (
    CHALLENGER,
    CHAMPION,
    EXPLORING,
    FROZEN,
    PROMOTION_RECOMMENDED,
    STABLE,
    HarnessEvolutionStore,
    PolicyCell,
    PolicyCellCache,
    assign_arm,
)
from management_portal.execution.harness_evolution_evaluation import (
    EvolutionCriteria,
    TurnOutcome,
    candidate_steps,
    changed_tunables,
    compare_arms,
    evaluate_cells,
    exposed,
    exposure_of,
    propose_challengers,
    render_report,
)
from management_portal.execution.harness_policy_table import (
    TUNABLES,
    parse_override,
    policy_parameters,
    validate_overrides,
    with_overrides,
)


def _turn(
    arm: str,
    index: int,
    *,
    input_tokens: int = 100_000,
    status: str = "completed",
    failure_kind: str = "none",
    quality: str = "unknown",
    cell: str = "claude/repository_work",
    version: int = 1,
    origin: str = "user",
    exposed: bool = True,
) -> TurnOutcome:
    return TurnOutcome(
        execution_id=f"{arm}-{index}",
        provider=cell.split("/", 1)[0],
        cell=cell,
        arm=arm,
        version=version,
        origin=origin,
        agent_id="agent",
        status=status,
        failure_kind=failure_kind,
        input_tokens=input_tokens,
        uncached_input_tokens=input_tokens // 4,
        output_tokens=500,
        provider_latency_ms=20_000.0,
        quality=quality,
        recorded_at=time.time(),
        signals=(
            {
                "context_pressure_percent": 60.0,
                "previous_output_bytes": 200_000,
                "previous_tool_calls": 40,
                "configured_reasoning_effort": "high",
            }
            if exposed
            else {"context_pressure_percent": 5.0, "configured_reasoning_effort": "high"}
        ),
        plan=("early_context_compaction", "tool_output_offloading") if exposed else (),
    )


SAVES = {"available": True, "saving": 0.12, "low": 0.06, "high": 0.19, "sessions": 9, "days": 14.0, "compactions": {"champion": 20, "challenger": 26}}
COSTS = {"available": True, "saving": -0.10, "low": -0.16, "high": -0.04, "sessions": 9, "days": 14.0}


def _fake_replay(calls, *, provider, family, champion, challenger, **_kwargs):
    """A replay that saves 1% per 10k tokens of compaction limit taken off, and 1% per threshold point."""

    parameters = policy_parameters(provider)
    saving = 0.0
    for name, value in challenger.items():
        before = champion.get(name, getattr(parameters, name))
        if name == "compaction_max_context_tokens" and before:
            saving += (before - value) / 1_000_000
        elif name == "compaction_threshold_percent":
            saving += (before - value) / 100
        else:
            return {"available": False, "reason": "not_replayable_tunable"}
    return {"available": True, "saving": round(saving, 4), "low": round(saving - 0.02, 4), "high": round(saving + 0.02, 4), "sessions": 9, "days": 14.0}


def test_tunables_bound_every_override():
    """策略覆盖值受可调参数表约束，未知名称、越界值和错误类型被拒绝"""
    claude = policy_parameters("claude")
    assert TUNABLES["compaction_max_context_tokens"].neighbours(400_000) == [300_000, 500_000]
    # A Backend that has a behaviour off keeps it off.
    assert TUNABLES["compaction_max_context_tokens"].neighbours(0) == []
    assert TUNABLES["compaction_threshold_percent"].neighbours(85) == [80]
    assert "preprocess_large_input_images" not in TUNABLES
    assert with_overrides(claude, {"offload_trigger_bytes": 32 * 1024}).offload_trigger_bytes == 32 * 1024
    assert validate_overrides({"compaction_max_context_tokens": 0}) == {"compaction_max_context_tokens": 0}
    for overrides in (
        {"unknown": 1},
        {"compaction_threshold_percent": 95},
        {"high_tool_load_calls": 20.5},
        {"preprocess_large_input_images": 1},
        {"offload_trigger_bytes": True},
    ):
        with pytest.raises(ValueError):
            validate_overrides(overrides)
    with pytest.raises(ValueError):
        parse_override("preprocess_large_input_images=true")
    assert parse_override("protect_history_pressure_percent=46") == ("protect_history_pressure_percent", 46.0)
    for text in ("offload_trigger_bytes", "nothing=1", "compaction_threshold_percent=10"):
        with pytest.raises(ValueError):
            parse_override(text)


def test_arm_assignment_is_fixed_per_session_and_follows_the_share():
    """同一 session 固定分到同一 arm，challenger 比例符合份额且只在探索中的 cell 运行"""
    cell = PolicyCell(
        "codex", "tool_heavy", version=3, status=EXPLORING,
        champion={}, challenger={"offload_trigger_bytes": 49152}, challenger_share=0.1,
    )
    arms = [
        assign_arm(cell, provider="codex", workload_family="tool_heavy", session_key=f"s{index}")
        for index in range(2000)
    ]
    share = sum(item.arm == CHALLENGER for item in arms) / len(arms)
    assert 0.07 <= share <= 0.13
    assert all(
        assign_arm(cell, provider="codex", workload_family="tool_heavy", session_key=f"s{index}").arm == arms[index].arm
        for index in range(100)
    )
    challenger = next(item for item in arms if item.arm == CHALLENGER)
    assert challenger.overrides == {"offload_trigger_bytes": 49152}
    assert challenger.signals()["policy_cell"] == "codex/tool_heavy"
    assert challenger.signals()["policy_version"] == 3
    # Only an exploring cell runs its challenger.
    for status in (STABLE, FROZEN):
        held = PolicyCell(
            "codex", "tool_heavy", version=3, status=status,
            champion={"high_tool_load_calls": 24}, challenger={"offload_trigger_bytes": 49152},
            challenger_share=1.0,
        )
        result = assign_arm(held, provider="codex", workload_family="tool_heavy", session_key="s1")
        assert (result.arm, result.overrides) == (CHAMPION, {"high_tool_load_calls": 24})
    empty = assign_arm(None, provider="codex", workload_family="short_text", session_key="s1")
    assert (empty.arm, empty.version, empty.overrides) == (CHAMPION, 0, {})


def test_cell_lifecycle_keeps_one_row_and_an_event_log(isolated_portal_case):
    """策略 cell 经过启动、推荐、晋升、冻结和回滚后仍只有一行，并留下完整事件日志"""
    with isolated_portal_case():
        store = HarnessEvolutionStore()
        assert store.cells() == []
        with pytest.raises(ValueError, match="share"):
            store.start_challenger("claude", "repository_work", {"compaction_max_context_tokens": 300_000}, actor="t", share=0.5)
        with pytest.raises(ValueError, match="equals the champion"):
            store.start_challenger("claude", "repository_work", {}, actor="t")
        with pytest.raises(ValueError, match="names who"):
            store.start_challenger("claude", "repository_work", {"compaction_max_context_tokens": 300_000}, actor=" ")

        started = store.start_challenger(
            "claude", "repository_work", {"compaction_max_context_tokens": 300_000}, actor="t",
        )
        assert (started.version, started.status, started.challenger_share) == (1, EXPLORING, 0.1)
        with pytest.raises(ValueError, match="already runs"):
            store.start_challenger("claude", "repository_work", {"compaction_max_context_tokens": 500_000}, actor="t")

        recommended = store.record_evaluation(
            "claude", "repository_work", {"decision": "promote"}, actor="t", recommend_promotion=True,
        )
        # The recommendation keeps the arms as they are until a person decides.
        assert (recommended.version, recommended.status) == (1, PROMOTION_RECOMMENDED)
        promoted = store.promote("claude", "repository_work", actor="case-a")
        assert promoted.champion == {"compaction_max_context_tokens": 300_000}
        assert (promoted.version, promoted.status, promoted.challenger) == (2, STABLE, None)

        store.start_challenger(
            "claude", "repository_work",
            {"compaction_max_context_tokens": 300_000, "compaction_threshold_percent": 65},
            actor="t",
        )
        frozen = store.freeze("claude", "repository_work", actor="case-a")
        assert (frozen.status, frozen.challenger, frozen.version) == (FROZEN, None, 4)
        with pytest.raises(ValueError, match="frozen"):
            store.start_challenger("claude", "repository_work", {}, actor="t")
        assert store.freeze("claude", "repository_work", actor="case-a", frozen=False).status == STABLE
        store.start_challenger("claude", "repository_work", {"compaction_max_context_tokens": 200_000}, actor="t")
        retired = store.retire("claude", "repository_work", "rolled_back", actor="t", evidence={"why": "failures"})
        assert retired.champion == {"compaction_max_context_tokens": 300_000}
        with pytest.raises(ValueError, match="not a way"):
            store.retire("claude", "repository_work", "dropped", actor="t")

        assert len(store.cells()) == 1
        events = store.events(provider="claude")
        assert [event["kind"] for event in reversed(events)] == [
            "challenger_started", "promotion_recommended", "promoted",
            "challenger_started", "frozen", "unfrozen", "challenger_started", "rolled_back",
        ]
        assert events[0]["challenger"] == {"compaction_max_context_tokens": 200_000}
        assert events[0]["evidence"] == {"why": "failures"}
        assert {event["actor"] for event in events} == {"t", "case-a"}


def test_cell_cache_keeps_its_copy_when_the_database_fails():
    """数据库读取失败时 PolicyCellCache 保留上次加载的 cell"""
    class Store:
        calls = 0

        def cells(self):
            Store.calls += 1
            if Store.calls > 1:
                raise OSError("database unavailable")
            return [PolicyCell("codex", "short_text", version=2, champion={"high_tool_load_calls": 24})]

    cache = PolicyCellCache(Store(), refresh_seconds=0.0)
    cache.refresh()
    assert cache.lookup("codex", "short_text").version == 2
    cache.refresh()
    assert cache.lookup("codex", "short_text").version == 2
    assert cache.lookup("codex", "tool_heavy") is None
    assert Store.calls >= 2


def test_comparison_rolls_back_rejects_promotes_or_waits():
    """arm 对比按失败率、评审质量、离线回放节省和运行天数给出回滚、拒绝、晋升或继续等待"""
    criteria = EvolutionCriteria(bootstrap_rounds=200)
    champion = [_turn(CHAMPION, index, quality="pass" if index < 40 else "unknown") for index in range(200)]

    failing = [
        _turn(CHALLENGER, index, status="failed", failure_kind="execution_failure")
        if index % 4 == 0 else _turn(CHALLENGER, index)
        for index in range(30)
    ]
    assert compare_arms(champion, failing, criteria=criteria, age_days=2, offline=SAVES)["decision"] == "rollback"

    # Failures a policy cannot cause are not held against it.
    limited = [
        _turn(CHALLENGER, index, status="failed", failure_kind="usage_limit")
        if index % 4 == 0 else _turn(CHALLENGER, index)
        for index in range(30)
    ]
    assert compare_arms(champion, limited, criteria=criteria, age_days=2, offline=SAVES)["decision"] == "continue"

    worse = [
        _turn(CHALLENGER, index, quality=("fail" if index < 12 else "pass") if index < 24 else "unknown")
        for index in range(40)
    ]
    rejected = compare_arms(champion, worse, criteria=criteria, age_days=2, offline=SAVES)
    assert (rejected["decision"], rejected["reasons"]) == ("rejected", ["reviewed_quality_fell"])

    # The replay says it saves, and the live arm is no worse within the margins.
    sound = [_turn(CHALLENGER, index, quality="pass" if index < 40 else "unknown") for index in range(60)]
    promoted = compare_arms(champion, sound, criteria=criteria, age_days=8, offline=SAVES)
    assert (promoted["decision"], promoted["reasons"]) == ("promote", ["saves_input_tokens"])
    assert promoted["failure_rate_rise_upper"] < 0.05
    assert promoted["pass_rate_fall_upper"] < 0.10

    # Fewer tokens for more waiting on compactions waits for a person.
    slower = compare_arms(champion, sound, criteria=criteria, age_days=8, offline=dict(SAVES, extra_compaction_seconds_per_day=600.0))
    assert (slower["decision"], slower["reasons"]) == ("trade_off", ["saves_input_tokens", "costs_compaction_time"])

    # Not before it has run a week, nor without enough labels or turns.
    early = compare_arms(champion, sound, criteria=criteria, age_days=3, offline=SAVES)
    assert (early["decision"], early["reasons"]) == ("continue", ["needs_days"])
    unlabelled = [_turn(CHALLENGER, index) for index in range(60)]
    waiting = compare_arms(champion, unlabelled, criteria=criteria, age_days=8, offline=SAVES)
    assert waiting["decision"] == "continue"
    assert {"needs_reviewed_turns", "quality_not_bounded"} <= set(waiting["reasons"])
    # Two failures in 40 turns are no proof of harm, nor of its absence.
    unsure = [
        _turn(CHALLENGER, index, status="failed", failure_kind="execution_failure")
        if index in {3, 17} else _turn(CHALLENGER, index, quality="pass" if index < 40 else "unknown")
        for index in range(42)
    ]
    unsure_result = compare_arms(champion, unsure, criteria=criteria, age_days=8, offline=SAVES)
    assert unsure_result["decision"] == "continue"
    assert "failures_not_bounded" in unsure_result["reasons"]

    # Without a replay a challenger is never recommended; it ends inconclusive.
    unknown = {"available": False, "reason": "not_replayable_tunable"}
    blind = compare_arms(champion, sound, criteria=criteria, age_days=8, offline=unknown)
    assert (blind["decision"], blind["reasons"][0]) == ("continue", "no_offline_estimate:not_replayable_tunable")
    assert compare_arms(champion, sound, criteria=criteria, age_days=22, offline=unknown)["decision"] == "inconclusive"
    # A step the replay finds dearer, or sure to save too little, is rejected at once.
    assert compare_arms(champion, sound, criteria=criteria, age_days=1, offline=COSTS)["reasons"] == ["costs_more"]
    small = dict(SAVES, saving=0.01, low=-0.002, high=0.019)
    assert compare_arms(champion, sound, criteria=criteria, age_days=1, offline=small)["reasons"] == ["saves_too_little"]
    # A wide interval that may still reach the margin keeps it running.
    wide = dict(SAVES, saving=0.03, low=-0.01, high=0.08)
    assert compare_arms(champion, sound, criteria=criteria, age_days=8, offline=wide)["reasons"] == ["no_offline_saving"]


def test_daily_evaluation_applies_decisions_and_waits_for_a_person_to_promote(isolated_portal_case, monkeypatch):
    """每日评估直接回滚失败的 challenger，晋升只记为推荐并等待人工确认"""
    with isolated_portal_case():
        store = HarnessEvolutionStore()
        store.start_challenger("claude", "repository_work", {"compaction_max_context_tokens": 300_000}, actor="t")
        store.start_challenger("codex", "tool_heavy", {"offload_trigger_bytes": 49152}, actor="t")
        turns = (
            [_turn(CHAMPION, index, quality="pass" if index < 40 else "unknown") for index in range(200)]
            + [_turn(CHALLENGER, index, input_tokens=80_000 + index, quality="pass" if index < 30 else "unknown") for index in range(50)]
            # Test traffic and other versions never count.
            + [_turn(CHALLENGER, 900 + index, input_tokens=500_000, origin="test") for index in range(50)]
            + [_turn(CHALLENGER, 950 + index, input_tokens=500_000, version=7) for index in range(50)]
            + [
                _turn(CHALLENGER, 2000 + index, cell="codex/tool_heavy", status="failed", failure_kind="timeout")
                for index in range(20)
            ]
            + [_turn(CHAMPION, 3000 + index, cell="codex/tool_heavy") for index in range(100)]
        )
        monkeypatch.setattr(evaluation, "load_outcomes", lambda **_kwargs: list(turns))
        monkeypatch.setattr(evaluation, "estimate_step", _fake_replay)
        week = time.time() + 8 * 86400

        dry = evaluate_cells(store, now=week, criteria=EvolutionCriteria(bootstrap_rounds=200), apply=False, calls=[])
        assert {item["cell"]: item["decision"] for item in dry} == {
            "claude/repository_work": "promote",
            "codex/tool_heavy": "rollback",
        }
        assert store.cell("codex", "tool_heavy").challenger is not None

        results = evaluate_cells(store, now=week, criteria=EvolutionCriteria(bootstrap_rounds=200), calls=[])
        applied = {item["cell"]: item["applied"] for item in results}
        assert applied == {
            "claude/repository_work": "promotion_recommended",
            "codex/tool_heavy": "rolled_back",
        }
        claude = store.cell("claude", "repository_work")
        assert claude.status == PROMOTION_RECOMMENDED
        assert claude.champion == {}
        assert claude.evaluation["decision"] == "promote"
        assert claude.evaluation["offline"]["saving"] == 0.1
        assert store.cell("codex", "tool_heavy").challenger is None

        report = render_report(
            cells=store.cells(), evaluations=results, proposals=[],
            traffic={("claude", "repository_work"): 30.0, ("kimi", "short_text"): 2.0},
            generated_at=time.time(),
        )
        assert "# Harness 演化日报" in report
        assert "claude/repository_work" in report
        assert "输入 token 节省 10.0%" in report
        assert "kimi/short_text" in report


def test_proposals_start_the_step_the_replay_finds_saving_most(isolated_portal_case, monkeypatch):
    """提议为 cell 启动离线回放节省最多的一步调整，已退役的 challenger 不再重试"""
    monkeypatch.setattr(evaluation, "estimate_step", _fake_replay)
    with isolated_portal_case():
        store = HarnessEvolutionStore()
        traffic = {
            ("claude", "repository_work"): 40.0,
            ("codex", "tool_heavy"): 30.0,
            ("codex", "short_text"): 25.0,
            ("kimi", "short_text"): 3.0,
        }
        store.freeze("codex", "tool_heavy", actor="case-a")
        proposals = propose_challengers(store, traffic=traffic, calls=[])
        assert [item["cell"] for item in proposals] == ["claude/repository_work", "codex/short_text"]
        # Claude: 400k -> 300k saves 10%, more than a threshold point step.
        assert store.cell("claude", "repository_work").challenger == {"compaction_max_context_tokens": 300_000}
        assert proposals[0]["offline"]["saving"] == 0.1
        # Codex has no bound to lower (0 is off); its threshold step saves 5%.
        assert proposals[1]["challenger"] == {"compaction_threshold_percent": 65}

        # A retired challenger is not tried again, and the cell rests first.
        store.retire("claude", "repository_work", "rejected", actor="t")
        assert propose_challengers(store, traffic={("claude", "repository_work"): 40.0}, calls=[]) == []
        later = time.time() + 3 * 86400
        again = propose_challengers(store, traffic={("claude", "repository_work"): 40.0}, now=later, calls=[])
        assert again[0]["challenger"] == {"compaction_threshold_percent": 65}

    # Nothing the replay finds saving enough: no challenger.
    monkeypatch.setattr(evaluation, "estimate_step", lambda *_args, **_kwargs: dict(SAVES, saving=0.03, low=0.01))
    with isolated_portal_case():
        assert propose_challengers(HarnessEvolutionStore(), traffic={("claude", "repository_work"): 40.0}, calls=[]) == []


def test_proposals_leave_out_cells_whose_challenger_could_not_be_decided(isolated_portal_case, monkeypatch):
    """受影响 turn 太少、期限内无法得出结论的 cell 不生成 challenger 提议"""
    # 30 exposed challenger turns at a 10% share within 21 days: a cell of 60
    # turns a day needs its change to act on about a quarter of them, and a
    # cell of 10 turns a day can never get there.
    monkeypatch.setattr(evaluation, "estimate_step", _fake_replay)
    with isolated_portal_case():
        store = HarnessEvolutionStore()
        cells = {
            ("claude", "repository_work"): (60.0, 2),
            ("codex", "repository_work"): (60.0, 5),
            ("dsh", "repository_work"): (10.0, 1),
        }
        traffic = {key: daily for key, (daily, _every) in cells.items()}
        turns = {
            key: [_turn(CHAMPION, index, cell="/".join(key), exposed=index % every == 0) for index in range(40)]
            for key, (_daily, every) in cells.items()
        }
        proposals = propose_challengers(store, traffic=traffic, turns=turns, apply=False, calls=[])
        assert [item["cell"] for item in proposals] == ["claude/repository_work"]
        assert proposals[0]["exposure"] == 0.5


def test_candidate_steps_change_one_tunable_of_the_champion():
    """候选步骤只调整 champion 的一个参数，跳过已试过的值且不开启已关闭的行为"""
    offload = {"offload_trigger_bytes"}
    assert [step[3] for step in candidate_steps("codex", {}, tried=[], names=offload)] == [
        {"offload_trigger_bytes": 49152}, {"offload_trigger_bytes": 81920},
    ]
    assert [step[3] for step in candidate_steps(
        "codex", {}, tried=[{"offload_trigger_bytes": 49152}], names=offload,
    )] == [{"offload_trigger_bytes": 81920}]
    # Back to the Backend's value drops the override.
    assert {} in [step[3] for step in candidate_steps(
        "codex", {"offload_trigger_bytes": 81920}, tried=[], names=offload,
    )]
    # Codex's compaction bound is off: it is never switched on by a step.
    assert [step[0] for step in candidate_steps(
        "codex", {}, tried=[], names={"compaction_max_context_tokens", "compaction_threshold_percent"},
    )] == ["compaction_threshold_percent", "compaction_threshold_percent"]


def test_review_sample_puts_challenger_cells_first(monkeypatch):
    """评审抽样优先取运行 challenger 的 cell，跳过非用户、已评审和失败的 turn"""
    turns = (
        [_turn(CHAMPION, index, cell="codex/short_text") for index in range(10)]
        + [_turn(CHAMPION, 100 + index) for index in range(10)]
        + [_turn(CHALLENGER, 200 + index) for index in range(10)]
        + [_turn(CHALLENGER, 300 + index, origin="resident") for index in range(10)]
        + [_turn(CHAMPION, 400 + index, quality="pass") for index in range(10)]
        + [_turn(CHALLENGER, 410 + index, status="failed", failure_kind="timeout") for index in range(10)]
    )
    monkeypatch.setattr(evaluation, "load_outcomes", lambda **_kwargs: list(turns))
    monkeypatch.setattr(
        evaluation, "_review_packet",
        lambda turn: {"execution_id": turn.execution_id, "cell": turn.cell, "arm": turn.arm},
    )
    samples = evaluation.sample_for_review(since=0, per_arm=3, limit=8, seed="x", cells=[])
    assert [(item["cell"], item["arm"]) for item in samples] == (
        [("claude/repository_work", CHALLENGER), ("claude/repository_work", CHAMPION)] * 3
        + [("codex/short_text", CHAMPION)] * 2
    )
    assert all(item["execution_id"].split("-")[1] not in {str(n) for n in range(300, 420)} for item in samples)

    # In a challenger's cell the turns its change acts on are read first.
    mixed = (
        [_turn(CHALLENGER, 500 + index, exposed=index < 2) for index in range(10)]
        + [_turn(CHAMPION, 600 + index, exposed=index < 2) for index in range(10)]
    )
    monkeypatch.setattr(evaluation, "load_outcomes", lambda **_kwargs: list(mixed))
    running = PolicyCell(
        "claude", "repository_work", version=1, status=EXPLORING,
        challenger={"compaction_max_context_tokens": 300_000}, challenger_share=0.1,
    )
    focused = evaluation.sample_for_review(since=0, per_arm=2, limit=4, seed="x", cells=[running])
    assert sorted(item["execution_id"] for item in focused) == [
        "challenger-500", "challenger-501", "champion-600", "champion-601",
    ]

    # Without challengers the busiest cells come first, one turn each in rotation.
    busy = (
        [_turn(CHAMPION, index, cell="codex/short_text") for index in range(30)]
        + [_turn(CHAMPION, 100 + index, cell="codex/repository_work") for index in range(20)]
        + [_turn(CHAMPION, 200 + index, cell="claude/single_media") for index in range(2)]
    )
    monkeypatch.setattr(evaluation, "load_outcomes", lambda **_kwargs: list(busy))
    spread = evaluation.sample_for_review(since=0, per_arm=4, limit=5, seed="x", cells=[])
    assert [item["cell"] for item in spread] == [
        "codex/short_text", "codex/repository_work", "claude/single_media",
        "codex/short_text", "codex/repository_work",
    ]


def test_cli_starts_a_challenger_and_writes_the_daily_report(isolated_portal_case, monkeypatch, capsys, tmp_path):
    """harness_evolution 命令行启动 challenger、生成日报，并拒绝带备注的 --actor"""
    from scripts import harness_evolution as cli
    from management_portal.execution import harness_evolution_runner as runner

    with isolated_portal_case():
        monkeypatch.setattr(sys, "argv", [
            "harness_evolution.py", "--actor", "case-a", "start", "claude", "repository_work",
            "--set", "compaction_max_context_tokens=300000", "--share", "0.05",
        ])
        assert cli.main() == 0
        started = json.loads(capsys.readouterr().out)
        assert started["challenger"] == {"compaction_max_context_tokens": 300000}
        assert started["challenger_share"] == 0.05

        monkeypatch.setattr(evaluation, "load_outcomes", lambda **_kwargs: [])
        monkeypatch.setattr(runner, "collect_turn_calls", lambda **_kwargs: {"collected": 3, "removed": 0})
        monkeypatch.setattr(runner, "cell_turns", lambda **_kwargs: {
            ("claude", "repository_work"): [_turn(CHAMPION, index) for index in range(84)],
        })
        report = tmp_path / "state" / "latest-report.md"
        status = tmp_path / "state" / "status.json"
        monkeypatch.setattr(sys, "argv", [
            "harness_evolution.py", "daily", "--report", str(report), "--status", str(status),
        ])
        assert cli.main() == 0
        capsys.readouterr()
        text = report.read_text(encoding="utf-8")
        assert "claude/repository_work" in text
        assert "本次收集了 3 个 turn 的调用记录" in text
        written = json.loads(status.read_text(encoding="utf-8"))
        assert written["evaluations"][0]["decision"] == "continue"
        assert written["collected"] == {"collected": 3, "removed": 0}
        assert written["cells"][0]["challenger"] == {"compaction_max_context_tokens": 300000}

        labels = []
        monkeypatch.setattr(cli, "record_review", lambda execution_id, **kwargs: labels.append((execution_id, kwargs)))
        monkeypatch.setattr(sys, "argv", [
            "harness_evolution.py", "label", "exec-1", "--result", "fail", "--reason", "answered the wrong file",
        ])
        assert cli.main() == 0
        assert labels == [("exec-1", {"result": "fail", "reason": "answered the wrong file", "score": None})]
        capsys.readouterr()

        monkeypatch.setattr(sys, "argv", ["harness_evolution.py", "--actor", "case-a", "freeze", "claude", "repository_work"])
        assert cli.main() == 0
        assert json.loads(capsys.readouterr().out)["status"] == FROZEN

        # Event actors are identities; a note in --actor is refused before anything changes.
        monkeypatch.setattr(sys, "argv", [
            "harness_evolution.py", "--actor", "resident_57f585eb8ae9 (CASE-A 授权实验)", "unfreeze", "claude", "repository_work",
        ])
        with pytest.raises(SystemExit, match="--reason"):
            cli.main()
        assert HarnessEvolutionStore().cell("claude", "repository_work").status == FROZEN


def test_arms_are_compared_on_the_turns_the_change_acts_on():
    """arm 对比只统计调整实际作用到的 turn，任何 turn 的失败仍触发回滚"""
    assert changed_tunables("claude", {}, {"compaction_max_context_tokens": 300_000}) == {
        "compaction_max_context_tokens": (400_000, 300_000),
    }
    # Naming the Backend's own value is no change.
    assert changed_tunables("claude", {}, {"compaction_max_context_tokens": 400_000}) == {}
    hot = _turn(CHAMPION, 1)
    cold = _turn(CHAMPION, 2, exposed=False)
    for name, values in (
        ("compaction_max_context_tokens", (400_000, 300_000)),
        ("offload_trigger_bytes", (65_536, 49_152)),
        ("protect_history_pressure_percent", (58.0, 64.0)),
        ("reasoning_downgrade_pressure_percent", (64.0, 58.0)),
        ("high_tool_load_calls", (40, 48)),
    ):
        assert exposed(hot, name, values) is True, name
        assert exposed(cold, name, values) is False, name
    # A per-turn threshold acts only between its two values: above both,
    # either arm treats the turn the same.
    for name, values in (
        ("protect_history_pressure_percent", (52.0, 58.0)),
        ("reasoning_downgrade_pressure_percent", (52.0, 46.0)),
        ("high_tool_load_calls", (24, 32)),
    ):
        assert exposed(hot, name, values) is False, name
    low_effort = _turn(CHAMPION, 3)
    low_effort.signals["configured_reasoning_effort"] = "medium"
    assert exposed(low_effort, "reasoning_downgrade_pressure_percent", (64.0, 58.0)) is False

    criteria = EvolutionCriteria(bootstrap_rounds=200)
    exposure = exposure_of({"compaction_max_context_tokens": (400_000, 300_000)})
    champion = (
        [_turn(CHAMPION, index, quality="pass" if index < 40 else "unknown") for index in range(100)]
        + [_turn(CHAMPION, 100 + index, input_tokens=5_000, exposed=False) for index in range(400)]
    )
    challenger = (
        [_turn(CHALLENGER, index, quality="pass" if index < 25 else "unknown") for index in range(45)]
        + [_turn(CHALLENGER, 100 + index, input_tokens=9_000, exposed=False) for index in range(150)]
    )
    focused = compare_arms(champion, challenger, criteria=criteria, age_days=8, exposure=exposure, offline=SAVES)
    assert focused["decision"] == "promote"
    assert (focused["champion"]["turns"], focused["champion"]["all_turns"]) == (100, 500)
    assert (focused["challenger"]["turns"], focused["challenger"]["all_turns"]) == (45, 195)
    # Turns the change does not act on do not count towards its evidence.
    sparse = challenger[:20] + challenger[45:]
    assert "needs_turns" in compare_arms(champion, sparse, criteria=criteria, age_days=8, exposure=exposure, offline=SAVES)["reasons"]

    # Failures anywhere still roll the challenger back.
    failing = challenger + [
        _turn(CHALLENGER, 500 + index, status="failed", failure_kind="execution_failure", exposed=False)
        for index in range(40)
    ]
    assert compare_arms(champion, failing, criteria=criteria, age_days=5, exposure=exposure, offline=SAVES)["decision"] == "rollback"


def test_only_finished_turns_with_a_policy_cause_count_as_policy_failures():
    """只有已结束且可归因于策略的失败 turn 计为策略失败，取消和重启后完成的不计入"""
    def row(execution_id, *, operation="turn", status="completed", failure_kind="none", execution_status="completed"):
        return {
            "execution_id": execution_id,
            "provider": "claude",
            "load_family": "repository_work",
            "status": status,
            "failure_kind": failure_kind,
            "input_tokens": 0 if operation != "turn" else 120_000,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "provider_latency_ms": 5.0,
            "provider_latency_observed": 1,
            "quality_result": None,
            "signals_json": json.dumps({
                "operation": operation,
                "policy_cell": "claude/repository_work",
                "policy_arm": "challenger",
                "policy_version": 2,
            }),
            "production_plan_json": "[]",
            "recorded_at": time.time(),
            "actor_ref": "sample_admin",
            "agent_id": "resident_1",
            "execution_status": execution_status,
        }

    # Session operations are observed too, but are not turns.
    for operation in ("create", "fork", "release", "compact"):
        assert evaluation.outcome_from_row(row(operation, operation=operation)) is None
    # An execution still running has only an interrupted attempt on record.
    assert evaluation.outcome_from_row(row("running", status="failed", failure_kind="execution_failure", execution_status="running")) is None

    failed = evaluation.outcome_from_row(row("failed", status="failed", failure_kind="execution_failure", execution_status="failed"))
    assert failed.policy_failure and failed.counted
    overflow = evaluation.outcome_from_row(row("overflow", status="failed", failure_kind="context_overflow", execution_status="failed"))
    assert overflow.policy_failure

    cancelled = evaluation.outcome_from_row(row("cancelled", status="failed", failure_kind="execution_failure", execution_status="cancelled"))
    assert cancelled.failure_kind == "cancelled" and not cancelled.counted
    # A restart failed one attempt and a later attempt finished the turn.
    resumed = evaluation.outcome_from_row(row("resumed", status="failed", failure_kind="execution_failure", execution_status="completed"))
    assert resumed.failure_kind == "interrupted" and not resumed.counted

    # Rows without an agent execution keep what the observation says.
    legacy = evaluation.outcome_from_row({**row("legacy", status="failed", failure_kind="execution_failure"), "execution_status": None})
    assert legacy.policy_failure
    turn = evaluation.outcome_from_row(row("turn"))
    assert (turn.cell, turn.arm, turn.version, turn.origin) == ("claude/repository_work", "challenger", 2, "user")


def test_cell_cache_refresh_does_not_block_the_turn_thread():
    """PolicyCellCache 在后台线程刷新，查询它的 turn 线程不等待数据库

    :module: execution.harness"""
    import threading
    entered, release = threading.Event(), threading.Event()
    threads = []
    class Store:
        def cells(self):
            threads.append(threading.get_ident())
            entered.set()
            assert release.wait(5)
            return [PolicyCell("codex", "short_text", version=3)]
    cache = PolicyCellCache(Store())
    try:
        assert cache.lookup("codex", "short_text") is None
        assert entered.wait(2)
        assert threads[0] != threading.get_ident()
        assert cache.lookup("codex", "short_text") is None
    finally:
        release.set()
    cache.refresh()
    assert cache.lookup("codex", "short_text").version == 3
