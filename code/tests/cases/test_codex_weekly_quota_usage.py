from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import codex_weekly_quota_usage as weekly_usage


def _token_count_line(
    timestamp: str,
    *,
    used_percent: float,
    reset_at: int,
) -> str:
    return json.dumps({
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "last_token_usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 50,
                    "output_tokens": 10,
                    "reasoning_output_tokens": 2,
                    "total_tokens": 110,
                }
            },
            "rate_limits": {
                "limit_id": "codex",
                "primary": {
                    "used_percent": used_percent,
                    "window_minutes": 10080,
                    "resets_at": reset_at,
                },
                "plan_type": "pro",
            },
        },
    })


def _daily_row(
    start_day: int,
    *,
    cost_usd: float,
    usage_percent: float,
    lag_minutes: float = 10,
    reset_count: int = 0,
) -> dict:
    next_day = start_day + 1
    return {
        "day_start_at": f"2026-07-{start_day:02d}T21:00:00Z",
        "day_end_at": f"2026-07-{next_day:02d}T21:00:00Z",
        "estimated_api_cost_usd": {"total_cost_usd": cost_usd},
        "usage_change_used_percent_24h": usage_percent,
        "usage_reset_count_24h": reset_count,
        "usage_alignment": {"lag_minutes": lag_minutes},
        "next_usage_alignment": {"lag_minutes": lag_minutes},
    }


def test_rolling_estimate_becomes_high_confidence_after_relaxed_threshold_contracts(subtests, isolated_portal_case):
    """周额度滚动估算按累计用量与重置调整置信度"""
    with subtests.test(contract='test_rolling_estimate_becomes_high_confidence_after_relaxed_threshold'), isolated_portal_case() as case_path:
        rows = [
            _daily_row(20, cost_usd=100, usage_percent=8),
            _daily_row(21, cost_usd=140, usage_percent=8),
            _daily_row(22, cost_usd=60, usage_percent=4),
        ]

        weekly_usage.annotate_daily_weekly_equivalent_estimates(rows)

        assert rows[0]["estimated_100_percent_weekly_cost_usd"] == 1250
        assert rows[0]["weekly_equivalent_estimate"]["confidence"] == "low"
        assert rows[1]["estimated_100_percent_weekly_cost_usd"] == 1500
        assert rows[1]["weekly_equivalent_estimate"]["confidence"] == "high"
        assert rows[1]["weekly_equivalent_estimate"]["window_days"] == 2
        assert rows[2]["estimated_100_percent_weekly_cost_usd"] == 1500
        assert rows[2]["weekly_equivalent_estimate"]["confidence"] == "high"
        assert rows[2]["weekly_equivalent_estimate"]["window_days"] == 3
    with subtests.test(contract='test_low_confidence_estimate_is_retained_with_reasons'), isolated_portal_case() as case_path:
        rows = [
            _daily_row(20, cost_usd=100, usage_percent=6),
            _daily_row(21, cost_usd=100, usage_percent=6, lag_minutes=45),
        ]

        weekly_usage.annotate_daily_weekly_equivalent_estimates(rows)

        details = rows[1]["weekly_equivalent_estimate"]
        assert rows[1]["estimated_100_percent_weekly_cost_usd"] == pytest.approx(
            1666.666667
        )
        assert details["confidence"] == "low"
        assert details["low_confidence_reasons"] == [
            "cumulative_usage_change_below_minimum",
            "boundary_observation_lag_above_maximum",
        ]
    with subtests.test(contract='test_reset_breaks_the_rolling_window_but_keeps_numeric_estimate'), isolated_portal_case() as case_path:
        rows = [
            _daily_row(20, cost_usd=100, usage_percent=20),
            _daily_row(
                21,
                cost_usd=200,
                usage_percent=20,
                reset_count=1,
            ),
            _daily_row(22, cost_usd=100, usage_percent=10),
        ]

        weekly_usage.annotate_daily_weekly_equivalent_estimates(rows)

        reset_details = rows[1]["weekly_equivalent_estimate"]
        assert rows[1]["estimated_100_percent_weekly_cost_usd"] == 1000
        assert reset_details["confidence"] == "low"
        assert "reset_inside_window" in reset_details["low_confidence_reasons"]
        assert rows[2]["estimated_100_percent_weekly_cost_usd"] == 1000
        assert rows[2]["weekly_equivalent_estimate"]["window_days"] == 1
    with subtests.test(contract='test_daily_summary_prefers_05am_snapshots_for_limit_change'), isolated_portal_case() as case_path:
        start = datetime(2026, 8, 3, 21, tzinfo=timezone.utc)
        following = datetime(2026, 8, 4, 21, tzinfo=timezone.utc)
        snapshots = {
            start: weekly_usage.DailyLimitSnapshot(
                boundary=start,
                observed_at=start.replace(second=50),
                account_id="zero",
                status="ok",
                stale=False,
                used_percent=66,
                reset_at=1786161158,
                source_path="2026-08-04.json",
            ),
            following: weekly_usage.DailyLimitSnapshot(
                boundary=following,
                observed_at=following.replace(second=2),
                account_id="zero",
                status="ok",
                stale=False,
                used_percent=89,
                reset_at=1786161158,
                source_path="2026-08-05.json",
            ),
        }
        observation = weekly_usage.WeeklyEvent(
            timestamp=start.replace(second=10),
            session_id="session",
            model="gpt-5.6-sol",
            model_provider="openai",
            plan_type="pro",
            limit_source="primary",
            reset_at=1786161158,
            used_percent=3,
            usage={"input_tokens": 100, "output_tokens": 10},
        )

        rows = weekly_usage.summarize_daily_intervals(
            [observation],
            [observation],
            [],
            generated_at=following + weekly_usage.timedelta(hours=1),
            range_until_utc=following + weekly_usage.timedelta(days=1),
            timezone_name="Asia/Hong_Kong",
            day_start_hour=5,
            daily_snapshots=snapshots,
        )

        row = next(item for item in rows if item["day_start_at"] == "2026-08-03T21:00:00Z")
        assert row["usage_alignment"]["used_percent"] == 66
        assert row["next_usage_alignment"]["used_percent"] == 89
        assert row["usage_change_used_percent_24h"] == 23
        assert row["usage_change_source"] == "daily_limit_snapshots"
        assert row["usage_change_status"] == "ok"
    with subtests.test(contract='test_snapshot_reset_change_counts_only_new_bucket_usage'), isolated_portal_case() as case_path:
        start = datetime(2026, 8, 1, 21, tzinfo=timezone.utc)
        first = weekly_usage.DailyLimitSnapshot(
            boundary=start,
            observed_at=start,
            account_id="zero",
            status="ok",
            stale=False,
            used_percent=73,
            reset_at=1785903031,
            source_path="before.json",
        )
        second = weekly_usage.DailyLimitSnapshot(
            boundary=start + weekly_usage.timedelta(days=1),
            observed_at=start + weekly_usage.timedelta(days=1),
            account_id="zero",
            status="ok",
            stale=False,
            used_percent=3,
            reset_at=1786161158,
            source_path="after.json",
        )

        assert weekly_usage._daily_snapshot_used_percent_change(first, second) == (
            3.0,
            1,
            "reset_at_changed",
        )
    with subtests.test(contract='test_snapshot_reset_at_one_second_jitter_is_same_bucket'), isolated_portal_case() as case_path:
        start = datetime(2026, 8, 2, 21, tzinfo=timezone.utc)
        first = weekly_usage.DailyLimitSnapshot(
            boundary=start,
            observed_at=start,
            account_id="one",
            status="ok",
            stale=False,
            used_percent=51,
            reset_at=1786160295,
            source_path="before.json",
        )
        second = weekly_usage.DailyLimitSnapshot(
            boundary=start + weekly_usage.timedelta(days=1),
            observed_at=start + weekly_usage.timedelta(days=1),
            account_id="one",
            status="ok",
            stale=False,
            used_percent=57,
            reset_at=1786160294,
            source_path="after.json",
        )

        assert weekly_usage._daily_snapshot_used_percent_change(first, second) == (
            6.0,
            0,
            "ok",
        )


def test_markdown_explains_and_marks_low_confidence_estimate_contracts(tmp_path, subtests, isolated_portal_case):
    """周额度报告标记低置信度估算并拒绝偏离边界的快照"""
    with subtests.test(contract='test_markdown_explains_and_marks_low_confidence_estimate'), isolated_portal_case() as case_path:
        tmp_path = case_path
        rows = [_daily_row(20, cost_usd=100, usage_percent=8)]
        weekly_usage.annotate_daily_weekly_equivalent_estimates(rows)
        payload = {
            "account": "zero",
            "generated_at": "2026-07-28T00:00:00Z",
            "sessions_root": "/tmp/sessions",
            "intervals": [],
            "daily_intervals": rows,
            "daily_session_stats": [],
            "inferred_resets": [],
        }

        weekly_usage.write_outputs(
            payload,
            tmp_path,
            "Asia/Hong_Kong",
        )
        markdown = (tmp_path / "latest-zero.md").read_text(encoding="utf-8")

        assert "`*` 表示低置信度滚动估算" in markdown
        assert "$1,250.00*" in markdown
        assert (tmp_path / "latest-zero.json").is_file()
    with subtests.test(contract='test_snapshot_loader_rejects_observation_far_from_05am'), isolated_portal_case() as case_path:
        tmp_path = case_path
        (tmp_path / "2026-08-01.json").write_text(
            json.dumps({
                "schema": "tfi.codex-daily-limit-snapshot.v2",
                "snapshot_date": "2026-08-01",
                "observed_at": "2026-08-01T03:23:00Z",
                "accounts": [{
                    "account_id": "zero",
                    "status": "ok",
                    "stale": False,
                    "updated_at": "2026-08-01T03:23:00Z",
                    "rate_limits": [{
                            "remaining_percent": 27,
                            "window_duration_mins": 10080,
                            "resets_at": 1785903031,
                            "observed_at": "2026-08-01T03:23:00Z",
                            "stale": False,
                    }],
                }],
            }),
            encoding="utf-8",
        )

        snapshots, audit = weekly_usage.load_daily_limit_snapshots(
            tmp_path,
            account_id="zero",
            since_utc=datetime(2026, 7, 31, 16, tzinfo=timezone.utc),
            until_utc=datetime(2026, 8, 2, 16, tzinfo=timezone.utc),
            timezone_name="Asia/Hong_Kong",
            day_start_hour=5,
        )

        assert snapshots == {}
        assert audit["snapshots_rejected"] == 1
        assert audit["warnings"] == [
            "snapshot_outside_05am_window:2026-08-01.json:383.0m"
        ]


def test_collect_events_excludes_fork_import_burst_and_keeps_physical_session(
    tmp_path: Path,
) -> None:
    """codex 用量采集排除分支导入记录并保留物理会话身份"""
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    rollout = sessions / "rollout.jsonl"
    rollout.write_text(
        "\n".join([
            json.dumps({
                "timestamp": "2026-08-04T21:24:44.000Z",
                "type": "session_meta",
                "payload": {
                    "id": "current-session",
                    "forked_from_id": "parent-session",
                    "model_provider": "openai",
                },
            }),
            json.dumps({
                "timestamp": "2026-08-04T21:24:44.001Z",
                "type": "session_meta",
                "payload": {"id": "parent-session"},
            }),
            _token_count_line(
                "2026-08-04T21:24:44.002Z",
                used_percent=3,
                reset_at=1786161158,
            ),
            json.dumps({
                "timestamp": "2026-08-04T21:24:46.000Z",
                "type": "turn_context",
                "payload": {"model": "gpt-5.6-sol"},
            }),
            _token_count_line(
                "2026-08-04T21:24:46.100Z",
                used_percent=89,
                reset_at=1786161158,
            ),
        ]) + "\n",
        encoding="utf-8",
    )

    events, observations, activities, stats, audits = weekly_usage.collect_events(
        sessions,
        since_utc=datetime(2026, 8, 4, 21, tzinfo=timezone.utc),
        until_utc=datetime(2026, 8, 5, 21, tzinfo=timezone.utc),
        plan_type_filter="pro",
        max_reset_lag_seconds=(10080 * 60) + 3600,
    )

    assert len(events) == len(observations) == 1
    assert activities[0].session_id == "current-session"
    assert observations[0].used_percent == 89
    assert observations[0].session_id == "current-session"
    assert observations[0].model == "gpt-5.6-sol"
    assert stats["fork_replay_token_counts_excluded"] == 1
    assert audits[0]["replay_token_counts_excluded"] == 1
