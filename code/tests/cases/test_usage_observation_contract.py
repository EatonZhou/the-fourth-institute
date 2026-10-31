"""Usage acceptance evidence comes from the actual authorized collection.

:module: workbench.notifications
:importance: P1
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal import agent_status_backend as home, usage_read_model
from management_portal.execution import isa
from management_portal.society_service import resident_usage_projection as projection, resource_client, resident_catalog


NOW = datetime(2026, 10, 8, 13, 30, tzinfo=timezone.utc)
R1, R2, R3, PRIVATE = [f"resident_{i:012d}" for i in range(1, 5)]
L1, L2, HIDDEN = [f"lineage_{i:012d}" for i in range(1, 4)]


class Clock:
    def __init__(self):
        self.now = NOW

    def read(self):
        self.now += timedelta(milliseconds=1)
        return self.now.isoformat(timespec="microseconds").replace("+00:00", "Z")


@pytest.fixture
def observed(monkeypatch, tmp_path):
    clock = Clock()
    monkeypatch.setattr(usage_read_model, "collection_time", clock.read)
    class DateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)
    monkeypatch.setattr(isa, "datetime", DateTime)
    monkeypatch.setattr(isa, "_local_zone", lambda: timezone.utc)
    monkeypatch.setattr(projection, "_local_now", lambda: NOW)
    monkeypatch.setattr(projection, "_parse_bucket", lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")))
    monkeypatch.setattr(home, "_date_today", lambda: NOW.date())
    roots = {R1: {"resident_id": R1, "title": "Same title", "lineage_id": L1, "lineage_title": "Team"},
             R2: {"resident_id": R2, "title": "Same title", "lineage_id": L2, "lineage_title": "Team"},
             R3: {"resident_id": R3, "title": "Readable Resident"}}
    authority = Mock(return_value=roots)
    monkeypatch.setattr(resource_client, "visible_resident_usage_roots", authority)
    rows = []
    def add(hour, resident, tokens, provider="codex", account="codex_one"):
        rows.append({"bucket_hour": NOW.replace(hour=hour, minute=0).isoformat(),
                     "provider": provider, "account_id": account, "resident_id": resident,
                     "usage": {"input_tokens": tokens, "cached_input_tokens": 1, "output_tokens": 2,
                               "total_tokens": tokens + 2}})
    for resident, tokens in [(R1, 10), (R2, 20), (R3, 30), (PRIVATE, 40), ("", 50)]:
        add(12, resident, tokens)
        add(13, resident, tokens + 1)
    calls = []
    def ledger(**query):
        calls.append({"query": query, "actual_start": clock.now})
        clock.now += timedelta(milliseconds=7)
        begin, end = [datetime.fromisoformat(query[k].replace("Z", "+00:00")) for k in ("start_hour", "end_hour")]
        result = [r for r in rows if begin <= datetime.fromisoformat(r["bucket_hour"]) < end
                  and query["provider"] in ("*", r["provider"])]
        calls[-1]["actual_end"] = clock.now
        return {"rows": deepcopy(result), "updated_at": "1999-01-01T00:00:00Z"}
    monkeypatch.setattr(projection.execution_client, "token_usage", ledger)
    monkeypatch.setattr(isa, "_SERVER", SimpleNamespace(scheduler=SimpleNamespace(token_usage=ledger)))
    monkeypatch.setattr(home, "get_society_service", lambda: SimpleNamespace(
        projection=SimpleNamespace(collect_resident_usage=projection.collect)))
    account_read = Mock(return_value={"accounts": [{"provider": "codex", "account_id": "codex_one",
                                                    "usage": {"status": "ok", "daily_buckets": []}}]})
    monkeypatch.setattr(home.ExecutionService, "account_usage", account_read)
    monkeypatch.setattr(home, "STATUS_CACHE_PATH", tmp_path / "status.json")
    return SimpleNamespace(clock=clock, roots=roots, authority=authority, rows=rows, add=add,
                           calls=calls, account_read=account_read)


def instruction(kind="lineage", include_cached=False, **filters):
    args = {"period": "today", "group_by": kind, "include_cached": include_cached, **filters}
    return isa.get_usage(SimpleNamespace(caller="viewer", argument=lambda key, default=None: args.get(key, default)))["usage"]


def matrix(buckets):
    return {b["bucket"]: {g["id"]: g["total_tokens"] for g in b["groups"]} for b in buckets}


@pytest.mark.parametrize("kind", ["resident", "lineage"])
@pytest.mark.parametrize("include_cached", [False, True])
def test_complete_closed_hour_groups_and_current_growth_share_authorized_source(observed, kind, include_cached):
    """Usage封闭小时完整分组一致，当前小时增量由同源账本算平

    :aspects: functional, behavior
    :design: decision_fc26f883773d, decision_4527448ad07f
    :design-point:
    """
    page = home._build_token_usage_summary("viewer")
    expected_reads = len(projection._query_windows(NOW))
    assert len(observed.calls) == expected_reads
    assert observed.authority.call_count == observed.account_read.call_count == 1
    variant = page["variants"]["including_cached_input" if include_cached else "excluding_cached_input"]
    first = instruction(kind, include_cached)
    assert len(observed.calls) == expected_reads + 1  # matrices cause no extra reads
    assert observed.authority.call_count == 2
    before = matrix(variant["bucket_groups"][kind])
    assert before == matrix(first["bucket_groups"])
    assert len(before) == 24
    assert sum(sum(g.values()) for g in before.values()) == first["totals"]["total_tokens"] == variant["windows"]["today"]["total_tokens"]
    text = json.dumps(variant["bucket_groups"])
    assert PRIVATE not in text and HIDDEN not in text
    if kind == "lineage":
        assert R3 not in json.dumps(first["bucket_groups"])
        assert before[NOW.replace(hour=12, minute=0).isoformat(timespec="seconds")][""] > 0
    for rid, tokens in [(R1, 233), (R2, 17), (PRIVATE, 200), ("", 69)]:
        observed.add(13, rid, tokens)
    second = instruction(kind, include_cached)
    after = matrix(second["bucket_groups"])
    for bucket in before:
        if datetime.fromisoformat(bucket).hour != 13:
            assert after[bucket] == before[bucket]  # every closed group, not just its sum
    growth = sum(sum(after[b].values()) - sum(before[b].values()) for b in before)
    assert growth == second["totals"]["total_tokens"] - first["totals"]["total_tokens"]
    current = NOW.replace(minute=0).isoformat(timespec="seconds")
    assert growth == sum(after[current].values()) - sum(before[current].values()) > 0


def test_real_collection_bounds_cover_reads_and_survive_cache_reads_and_failed_refresh(observed, monkeypatch):
    """Usage缓存及失败刷新保留真实后台采集时段

    :aspects: functional, behavior
    :design: decision_fc26f883773d, decision_4527448ad07f
    :design-point:
    """
    published = home._refresh_token_usage_summary("viewer", force_refresh=False)
    parse = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))
    begin, end = parse(published["collection_started_at"]), parse(published["collection_finished_at"])
    assert len(published["collection_windows"]) == len(observed.calls)
    for w, actual in zip(published["collection_windows"], observed.calls):
        assert begin <= parse(w["read_started_at"]) <= actual["actual_start"]
        assert actual["actual_end"] <= parse(w["read_finished_at"]) <= end
        assert w["start"] == actual["query"]["start_hour"] and w["end"] == actual["query"]["end_hour"]
    assert published["updated_at"] == "1999-01-01T00:00:00Z"
    observed.clock.now += timedelta(hours=3)
    monkeypatch.setattr(home, "_schedule_background_refresh", lambda *args: pytest.fail("fresh cached read recollected"))
    monkeypatch.setattr(usage_read_model, "collection_time", lambda: pytest.fail("cached read stamped a new collection"))
    cached = home.get_token_usage_summary("viewer")
    for key in ("collection_started_at", "collection_finished_at", "collection_windows", "bucket_groups", "snapshot_version"):
        assert cached[key] == published[key]
    monkeypatch.setattr(home, "_build_token_usage_summary", Mock(side_effect=RuntimeError("ledger down")))
    failed = home._refresh_token_usage_summary("viewer", force_refresh=False)
    assert "ledger down" in failed["refresh_error"]
    assert failed["collection_started_at"] == published["collection_started_at"]
    assert failed["collection_finished_at"] == published["collection_finished_at"]
    assert failed["bucket_groups"] == published["bucket_groups"]


def test_get_usage_has_real_read_bounds_and_preserves_filter_query_budget(observed):
    """GetUsage记录真实读取时段并保留过滤口径和查询预算

    :aspects: functional, architecture-conformance
    :design: decision_fc26f883773d, decision_4527448ad07f
    :design-point:
    """
    result = instruction("resident", provider="codex", account_id="codex_one")
    parse = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))
    w, call = result["collection_windows"][0], observed.calls[0]
    assert parse(result["collection_started_at"]) <= parse(w["read_started_at"]) <= call["actual_start"]
    assert call["actual_end"] <= parse(w["read_finished_at"]) <= parse(result["collection_finished_at"])
    assert len(observed.calls) == observed.authority.call_count == 1
    assert call["query"]["provider"] == "codex" and call["query"]["include_accounts"]
    assert result["source"]["account_id"] == "codex_one"
    scope = home._build_token_usage_summary("viewer")
    account = next(s for s in scope["scopes"] if s["provider"] == "codex" and s["account_id"] == "codex_one")
    assert result["bucket_groups"] == account["variants"]["excluding_cached_input"]["bucket_groups"]["resident"]


def test_complete_buckets_keep_all_authorized_groups_beyond_chart_limit(observed):
    """Usage小时取证包含全部授权归属，不受图表排名截断

    :design: decision_fc26f883773d, decision_4527448ad07f
    :design-point:
    """
    for i in range(100, 140):
        rid, lid = f"resident_{i:012d}", f"lineage_{i:012d}"
        observed.roots[rid] = {"resident_id": rid, "title": "Same name", "lineage_id": lid, "lineage_title": "Same name"}
        observed.add(12, rid, i)
    projected = home._build_token_usage_summary("viewer")["variants"]["excluding_cached_input"]
    result = instruction("lineage")
    expected = matrix(result["bucket_groups"])
    assert matrix(projected["bucket_groups"]["lineage"]) == expected
    hour = NOW.replace(hour=12, minute=0).isoformat(timespec="seconds")
    assert len(expected[hour]) == 43  # 42 named Lineages and one anonymous entry
    assert len(projected["lineage_chart_series"]["today"][12]["segments"]) < len(expected[hour])
    assert sum(expected[hour].values()) == projected["lineage_chart_series"]["today"][12]["tokens"]


def test_bucket_identity_preserves_local_timezone_for_same_closed_hour(observed, monkeypatch):
    """Usage完整小时桶保留本地时区与封闭小时身份

    :design: decision_fc26f883773d, decision_4527448ad07f
    :design-point:
    """
    local = timezone(timedelta(hours=8))
    monkeypatch.setattr(isa, "_local_zone", lambda: local)
    monkeypatch.setattr(projection, "_local_now", lambda: NOW.astimezone(local))
    monkeypatch.setattr(projection, "_parse_bucket", lambda value: datetime.fromisoformat(value).astimezone(local))
    page = home._build_token_usage_summary("viewer")["variants"]["excluding_cached_input"]
    result = instruction("lineage")
    assert page["bucket_groups"]["lineage"] == result["bucket_groups"]
    closed = NOW.replace(hour=12, minute=0).astimezone(local).isoformat(timespec="seconds")
    assert matrix(result["bucket_groups"])[closed]
    assert closed.endswith("20:00:00+08:00")


def test_observed_empty_account_scope_has_complete_zero_buckets_not_a_missing_projection(observed):
    """已观察空账号范围返回完整零桶，区分数据缺失

    :design: decision_fc26f883773d, decision_4527448ad07f
    :design-point:
    """
    observed.account_read.return_value["accounts"].append(
        {"provider": "codex", "account_id": "codex_empty", "usage": {"status": "ok", "daily_buckets": []}})
    page = home._build_token_usage_summary("viewer")
    scope = next(s for s in page["scopes"] if s["account_id"] == "codex_empty")
    buckets = scope["variants"]["excluding_cached_input"]["bucket_groups"]["resident"]
    assert len(buckets) == 24 and all(b["groups"] == [] for b in buckets)
    assert scope["status"] == "ok" and page["collection_windows"]


def test_shared_authorization_uses_one_membership_batch_without_per_resident_reads(monkeypatch):
    """Usage共享授权范围以单批归属读取避免逐Resident查询

    :aspects: functional, architecture-conformance
    :design: decision_fc26f883773d, decision_4527448ad07f
    :design-point:
    """
    residents = [{"resource_id": f"resident_{i:012d}", "capabilities": {"read": True}} for i in range(106)]
    lineage = {"resource_id": L1, "capabilities": {"read": True}, "title": "Team"}
    discover = Mock(side_effect=lambda kind, **kw: [lineage] if kind == "lineage" else residents)
    monkeypatch.setattr(resource_client, "_query_all", discover)
    batch = Mock(return_value=[{"resident_id": r["resource_id"], "lineage_id": L1} for r in residents])
    catalog = object.__new__(resident_catalog.ResidentCatalog)
    catalog.state_store = SimpleNamespace(
        resident_usage_memberships=batch,
        residents_by_id=lambda *_: pytest.fail("Usage assembled complete Resident objects"),
    )
    monkeypatch.setattr(resident_catalog, "get_catalog", lambda: catalog)
    roots = isa._usage_roots("viewer")
    assert len(roots) == 106
    assert discover.call_count == 2  # the existing Resident + Lineage read-access scopes
    batch.assert_called_once()
    assert list(batch.call_args.args[0]) == [record["resource_id"] for record in residents]

    # A deletion after authorized discovery must not revive that identity or
    # expose its former Lineage; survivors still use one live-membership batch.
    discover.reset_mock()
    batch.reset_mock()
    batch.return_value = batch.return_value[1:]
    roots = isa._usage_roots("viewer")
    assert set(roots) == {record["resource_id"] for record in residents[1:]}
    assert discover.call_count == 2
    batch.assert_called_once()
    assert list(batch.call_args.args[0]) == [record["resource_id"] for record in residents]


def test_quota_evidence_reads_reuse_sample_and_never_request_external_refresh(monkeypatch):
    """额度取证复用同观察样本与绝对重置时间，不请求外部刷新

    :aspects: functional, behavior
    :design: decision_4dea0db1f45a
    :design-point:
    """
    sample = {"account_id": "codex_one", "quota": {"observed_at": 1000},
              "rate_limits": [{"id": "primary", "observed_at": 1000, "resets_at": 2000, "remaining_percent": 89}]}
    monitor = SimpleNamespace(snapshot=Mock(side_effect=lambda: {"accounts": [deepcopy(sample)]}), request_refresh=Mock())
    monkeypatch.setattr(isa, "_SERVER", SimpleNamespace(quota_monitor=monitor))
    inv = SimpleNamespace(caller="usage-observer", argument=lambda k, default=None: {"account_id": "codex_one", "refresh": False}.get(k, default))
    a, b = isa.list_provider_accounts(inv), isa.list_provider_accounts(inv)
    assert a["accounts"] == b["accounts"]
    # The ISA adds registration/login fields; the observed quota stays unchanged.
    assert [{key: account[key] for key in sample} for account in a["accounts"]] == [sample]
    assert a["accounts"][0]["rate_limits"][0]["resets_at"] == 2000
    assert monitor.snapshot.call_count == 2
    monitor.request_refresh.assert_not_called()
