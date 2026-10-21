"""GetUsage reports totals, a time series, a ranking, freshness and source state (decision_4527448ad07f).

:module: isa.instructions
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from management_portal.execution import isa as execution_isa
from management_portal.hatg.contracts import InstructionError


def _usage(arguments: dict) -> dict:
    invocation = SimpleNamespace(caller="alice", argument=lambda name, default=None: arguments.get(name, default))
    return execution_isa.get_usage(invocation)["usage"]


HKT = timezone(timedelta(hours=8))


class _Scheduler:
    def __init__(self, rows, *, fail=False):
        self.rows, self.fail, self.calls = rows, fail, []

    def token_usage(self, **window):
        self.calls.append(window)
        if self.fail:
            raise ConnectionError("execution service offline")
        return {"rows": self.rows, "updated_at": "2026-09-29T03:10:00Z"}


@pytest.fixture
def scheduler(monkeypatch):
    rows = [
        {"bucket_hour": "2026-09-29T01:00:00Z", "resident_id": "resident_aaaaaaaaaaaa",
         "usage": {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 30, "reasoning_output_tokens": 10}},
        {"bucket_hour": "2026-09-29T02:00:00Z", "resident_id": "resident_bbbbbbbbbbbb",
         "usage": {"input_tokens": 500, "output_tokens": 50}},
        {"bucket_hour": "2026-09-29T02:00:00Z", "resident_id": "resident_aaaaaaaaaaaa",
         "usage": {"input_tokens": 10, "output_tokens": 5}},
    ]
    fake = _Scheduler(rows)
    monkeypatch.setattr(execution_isa, "_SERVER", SimpleNamespace(scheduler=fake))
    monkeypatch.setattr(execution_isa, "_local_zone", lambda: HKT)
    monkeypatch.setattr(execution_isa, "_usage_roots",
                        lambda actor: {rid: {"resident_id": rid} for rid in
                                       ("resident_aaaaaaaaaaaa", "resident_bbbbbbbbbbbb")})
    return fake


def test_usage_has_totals_series_ranking_and_source(scheduler):
    """GetUsage 返回总量、按小时序列、Resident 排名、更新时间和数据源状态"""
    usage = _usage({"period": "today", "group_by": "resident", "include_cached": True})

    assert usage["totals"] == {"input_tokens": 610, "cached_input_tokens": 40, "output_tokens": 85,
                               "reasoning_output_tokens": 10, "total_tokens": 695}
    assert [item["bucket"] for item in usage["series"]] == ["2026-09-29T09:00:00+08:00", "2026-09-29T10:00:00+08:00"]
    assert usage["series"][1]["tokens"]["total_tokens"] == 565
    assert [item["id"] for item in usage["ranking"]] == ["resident_bbbbbbbbbbbb", "resident_aaaaaaaaaaaa"]
    assert usage["updated_at"] == "2026-09-29T03:10:00Z"
    assert usage["source"] == {"status": "ok", "provider": "*", "account_id": "", "missing_windows": []}


def test_ranking_merges_unreadable_residents_into_one_anonymous_entry(scheduler, monkeypatch):
    """GetUsage 排名中调用者不可读的 Resident 合并为一条匿名项，总量不变"""
    seen = []

    def readable(actor):
        seen.append(actor)
        return {"resident_aaaaaaaaaaaa": {"resident_id": "resident_aaaaaaaaaaaa"}}

    monkeypatch.setattr(execution_isa, "_usage_roots", readable)
    usage = _usage({"period": "today", "group_by": "resident", "include_cached": True})

    assert seen == ["alice"]
    assert usage["totals"]["total_tokens"] == 695
    assert usage["ranking"] == [
        {"id": "resident_aaaaaaaaaaaa", "tokens": {"input_tokens": 110, "cached_input_tokens": 40, "output_tokens": 35,
                                                   "reasoning_output_tokens": 10, "total_tokens": 145}},
        {"id": "", "anonymous": True, "tokens": {"input_tokens": 500, "cached_input_tokens": 0, "output_tokens": 50,
                                                 "reasoning_output_tokens": 0, "total_tokens": 550}},
    ]


def test_project_grouping_ranks_each_readable_project_and_keeps_the_rest_anonymous(scheduler, monkeypatch):
    """GetUsage 按 Project 排名，无可读 Project 的用量匿名合并

    验证 GetUsage group_by=project 按 Resident 当前 Lineage 所属的可读 Project 排名，其余用量合并为匿名项，总量与 bucket_groups 保持完整。
    """
    # resident_a's Lineage is in a readable Project; resident_b's is not, so
    # its usage stays in the total but is not named.
    monkeypatch.setattr(execution_isa, "_usage_roots", lambda actor: {
        "resident_aaaaaaaaaaaa": {"resident_id": "resident_aaaaaaaaaaaa", "lineage_id": "lineage_aaaaaaaaaaaa",
                                  "project_id": "AlignMe"},
        "resident_bbbbbbbbbbbb": {"resident_id": "resident_bbbbbbbbbbbb", "lineage_id": "lineage_bbbbbbbbbbbb"},
    })
    usage = _usage({"period": "today", "group_by": "project", "include_cached": True})

    assert usage["group_by"] == "project"
    assert usage["totals"]["total_tokens"] == 695
    assert [(item["id"], item["tokens"]["total_tokens"]) for item in usage["ranking"]] == [("AlignMe", 145), ("", 550)]
    assert {group["id"] for bucket in usage["bucket_groups"] for group in bucket["groups"]} == {"AlignMe", ""}


def test_excluding_cache_removes_cached_input_from_input(scheduler):
    """GetUsage 不计缓存时从输入 token 和总量中扣除命中缓存的部分"""
    usage = _usage({"period": "today", "group_by": "resident", "include_cached": False})

    assert usage["totals"]["input_tokens"] == 570
    assert usage["totals"]["cached_input_tokens"] == 0
    assert usage["totals"]["total_tokens"] == 655


def test_cache_is_excluded_unless_asked_for_like_home_usage(scheduler):
    """GetUsage 缺省不计缓存，与 Home 用量口径一致

    不传 include_cached 时，GetUsage 的总量不含命中缓存的输入（Codex 每日补充同样不计）；显式 include_cached=true 时才计入，与 Home 用量的“计入缓存”勾选一致。

    Home Usage leaves "Include cache" unticked by default; a GetUsage call
    without include_cached read 20 times the page's total for the same day.

    :design: decision_fc26f883773d
    :design-point:
    """
    usage = _usage({"period": "today", "group_by": "resident"})

    assert usage["include_cached"] is False
    assert usage["totals"] == _usage({"period": "today", "group_by": "resident", "include_cached": False})["totals"]


def test_longer_periods_bucket_the_series_by_day(scheduler):
    """GetUsage 查询月份等较长周期时时间序列按天分桶"""
    usage = _usage({"period": "month", "group_by": "resident"})

    assert [item["bucket"] for item in usage["series"]] == ["2026-09-29"]


def test_an_unreadable_source_is_owner_unavailable(monkeypatch):
    """执行服务无法读取时 GetUsage 返回 owner_unavailable"""
    monkeypatch.setattr(execution_isa, "_SERVER", SimpleNamespace(scheduler=_Scheduler([], fail=True)))

    with pytest.raises(InstructionError) as error:
        _usage({"period": "today", "group_by": "resident"})
    assert error.value.code == "instruction.owner_unavailable"


def test_periods_and_buckets_follow_the_server_local_day(scheduler, monkeypatch):
    """GetUsage 的周期起点和分桶按服务器本地时区的日期计算"""
    # 05:30 in Hong Kong is 21:30 UTC of the previous day.
    now = datetime(2026, 10, 4, 21, 30, tzinfo=timezone.utc)
    assert execution_isa._period_start("today", now) == datetime(2026, 10, 4, 16, tzinfo=timezone.utc)
    assert execution_isa._period_start("week", now) == datetime(2026, 10, 4, 16, tzinfo=timezone.utc)
    assert execution_isa._period_start("month", now) == datetime(2026, 9, 30, 16, tzinfo=timezone.utc)
    assert execution_isa._period_start("year", now) == datetime(2025, 12, 31, 16, tzinfo=timezone.utc)
    assert execution_isa._series_bucket("week", "2026-10-04T21:00:00Z") == "2026-10-05"
    assert execution_isa._series_bucket("year", "2026-09-30T16:00:00Z") == "2026-10"
