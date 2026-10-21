"""
:module: workbench.notifications
:design: decision_fc26f883773d
"""
from datetime import date, datetime, timedelta, timezone
from unittest import mock

import pytest

from management_portal import agent_status_backend as status, usage_read_model
from management_portal.society_service import resident_usage_projection as projection
from management_portal.society_service import resource_client, resident_catalog
from tests.cases.test_resource_tombstone_query_consistency import query_owner  # noqa: F401


def test_canonical_resource_query_retains_resident_usage_attribution(query_owner, monkeypatch):
    """从规范资源查询得到的 Resident 与 Lineage 身份可用于用量归属，内部记录不保存权限字段

    :design-point:"""
    from tests.cases.canonical_domain_fixture import canonical_projection
    target = query_owner
    lineage = "lineage_090909090909"
    resident = "resident_090909090909"
    for rid, kwargs in [(lineage, {"title": "Usage team"}),
                        (resident, {"title": "Usage worker", "lineage_id": lineage})]:
        item = canonical_projection(rid, **kwargs)
        target.resource.sync_collaboration_resource(item["descriptor"])
    target.index.adapter_layer.refresh_now("collaboration", force=True)
    monkeypatch.setattr(resource_client, "get_resource_service", lambda: target.resource)
    roots = resource_client.visible_resident_usage_roots(actor="fixture-query-owner")
    assert roots[resident] == {"resident_id": resident, "title": "Usage worker",
                               "lineage_id": lineage, "lineage_title": "Usage team"}
    assert target.resource.get(resident, actor="fixture-query-owner")["capabilities"]["read"]
    assert "capabilities" not in target.descriptors.get(resident)
    assert "capabilities" not in target.store.internal_get(resident)
    now = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
    row = {"provider": "codex", "resident_id": resident,
           "bucket_hour": "2026-10-08T10:00:00Z", "usage": {"total_tokens": 123}}
    projected = projection._build_variant([row], roots, include_cached_input=True, now=now)
    assert projected["daily_by_resident"]["2026-10-08"][resident] == 123


def test_usage_move_uses_current_resident_lineage_instead_of_old_index_relation(query_owner, monkeypatch):
    """Usage 移动后按 Resident 当前归属同额转移历史用量

    资源索引仍保留旧关系时从规范Resident读取当前Lineage；20,892 token全部转移到目标团队且总量不变。

    :module: execution.service
    :design-point:
    """
    from tests.cases.canonical_domain_fixture import canonical_projection
    target = query_owner
    source, destination = "lineage_090909090901", "lineage_090909090902"
    resident = "resident_090909090903"
    for rid, kwargs in [(source, {"title": "Source"}), (destination, {"title": "Destination"}),
                        (resident, {"title": "Moved worker", "lineage_id": source})]:
        item = canonical_projection(rid, **kwargs)
        target.resource.sync_collaboration_resource(item["descriptor"])
    target.index.adapter_layer.refresh_now("collaboration", force=True)
    monkeypatch.setattr(resource_client, "get_resource_service", lambda: target.resource)
    before = resource_client.visible_resident_usage_roots(actor="fixture-query-owner")
    moved = canonical_projection(resident, title="Moved worker", lineage_id=destination,
                                 updated_at="2026-10-08T11:59:55Z")
    target.resource.sync_collaboration_resource(moved["descriptor"])
    target.index.adapter_layer.refresh_now("collaboration", force=True)
    after = resource_client.visible_resident_usage_roots(actor="fixture-query-owner")
    assert before[resident]["lineage_id"] == source
    assert after[resident]["lineage_id"] == destination
    assert after[resident]["lineage_title"] == "Destination"
    now = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
    rows = [{"provider": "codex", "resident_id": resident,
             "bucket_hour": "2026-10-08T10:00:00Z", "usage": {"total_tokens": 20892}}]
    for roots, expected in [(before, source), (after, destination)]:
        projected = projection._build_variant(rows, roots, include_cached_input=False, now=now)
        assert projected["resident_lineages"][resident] == expected
        assert projected["daily_by_resident"]["2026-10-08"][resident] == 20892
        with mock.patch.object(status, "_date_today", return_value=now.date()):
            payload = status._token_usage_payload(projected, include_cached_input=False)
        assert payload["windows"]["today"]["total_tokens"] == 20892
        assert [(item["lineage_id"], item["periods"]["today"]) for item in payload["lineages"]] == [(expected, 20892)]


def test_usage_identity_failure_is_reported_instead_of_becoming_other(monkeypatch):
    """读取 Resident 身份失败时用量汇总报告错误，不把用量归入其他

    :design-point:"""
    monkeypatch.setattr(projection.execution_client, "token_usage", lambda **kw: {"rows": []})
    def unavailable(**kwargs):
        raise RuntimeError("resource identities unavailable")
    monkeypatch.setattr(resource_client, "visible_resident_usage_roots", unavailable)
    result = projection.collect(actor="fixture-query-owner")
    assert result["status"] == "error"
    assert "resource identities unavailable" in result["error"]


def account_rows(values):
    return [{"account_id": "codex_" + key, "provider": "codex", "usage": {
        "status": (value.get("sources", {}).get("usage", {}).get("status") or "ok"), **value.get("usage", {})}}
        for key, value in values.items()]


def test_account_gap_counts_only_in_the_view_with_cache_for_every_period():
    """账号日用量与 ledger 的差额只计入含缓存口径，各时间范围的图表与总量一致

    :design-point:"""
    now = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
    resident_id = "resident_000000000001"
    rows = [{
        "provider": provider, "resident_id": resident_id if index % 2 else "others",
        "bucket_hour": f"{day}T08:00:00Z",
        "usage": {"total_tokens": 100, "cached_input_tokens": cached},
    } for index, (day, provider, cached) in enumerate([
        ("2026-01-05", "codex", 90),
        ("2026-09-01", "deepseek", 80),
        ("2026-09-21", "codex", 70),
        ("2026-09-23", "openrouter", 60),
        ("2026-09-24", "codex", 50),
        ("2026-09-24", "kimi", 40),
    ])]

    def query(*, start_hour, end_hour, provider, include_accounts=False):
        return {"rows": [row for row in rows if start_hour <= row["bucket_hour"] < end_hour]}

    account_usage = {
        "zero": {"usage": {"daily_buckets": [
            {"date": day, "tokens": tokens} for day, tokens in [
                ("2026-01-05", 150), ("2026-09-01", 30), ("2026-09-21", 100),
                ("2026-09-22", 40), ("2026-09-23", 900), ("2026-09-24", 900),
            ]
        ]}},
        "one": {"usage": {"daily_buckets": [{"date": "2026-09-21", "tokens": 180}]}},
    }
    supplements = {"2026-01-05": 50, "2026-09-01": 30, "2026-09-21": 180, "2026-09-22": 40}
    with (
        mock.patch.object(projection, "_local_now", return_value=now),
        mock.patch.object(projection, "_visible_roots", return_value={resident_id: {
            "resident_id": resident_id, "title": "Worker", "lineage_id": "team", "lineage_title": "Team",
        }}),
        mock.patch.object(projection.execution_client, "token_usage", side_effect=query),
        mock.patch.object(status, "_date_today", return_value=now.date()),
        mock.patch.object(status, "get_society_service") as service,
        mock.patch.object(status.ExecutionService, "account_usage", return_value={"accounts": account_rows(account_usage)}) as accounts,
    ):
        service.return_value.projection.collect_resident_usage.return_value = projection.collect(actor="viewer")
        result = status._build_token_usage_summary("viewer")
    accounts.assert_called_once_with()
    assert result["status"] == "ok"
    included = result["variants"]["including_cached_input"]
    excluded = result["variants"]["excluding_cached_input"]
    for period, start in usage_read_model.period_starts(now.date()).items():
        expected_rows = [row for row in rows if row["bucket_hour"][:10] >= start.isoformat()]
        expected_cache = sum(row["usage"]["cached_input_tokens"] for row in expected_rows)
        total = included["windows"][period]["total_tokens"]
        non_cache = excluded["windows"][period]["total_tokens"]
        expected_supplement = sum(tokens for day, tokens in supplements.items() if day >= start.isoformat())
        assert total == 100 * len(expected_rows) + expected_supplement
        # An account's own daily total includes cache and cannot be split, so
        # the view without cache leaves it out.
        assert total - non_cache == expected_cache + expected_supplement
        for variant, expected in ((included, total), (excluded, non_cache)):
            for grouping in ("chart_series", "lineage_chart_series"):
                points = variant[grouping][period]
                assert sum(point["tokens"] for point in points) == expected
                assert all(sum(segment["tokens"] for segment in point["segments"]) == point["tokens"] for point in points)
            window = variant["windows"][period]
            assert window["assigned_tokens"] + window["other_tokens"] == expected
        assert excluded["windows"][period]["other_tokens"] <= included["windows"][period]["other_tokens"]
    day = next(point for point in included["chart_series"]["month"] if point["date"] == "2026-09-22")
    assert day["tokens"] == 40
    assert sum(item["tokens"] for item in day["segments"]) == 40
    day = next(point for point in excluded["chart_series"]["month"] if point["date"] == "2026-09-22")
    assert day["tokens"] == 0
    assert excluded["account_totals_omitted"] is True
    assert "account_totals_omitted" not in included


def test_account_supplement_subtracts_private_and_unregistered_codex_usage_only():
    """账号补差只扣除同一 Provider 的 ledger 用量，包括不可见和未登记的 session

    :design-point:"""
    now = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
    rows = [{
        "bucket_hour": "2026-09-21T08:00:00Z", "provider": provider,
        "resident_id": resident_id, "usage": {"total_tokens": tokens, "cached_input_tokens": tokens // 2},
    } for provider, resident_id, tokens in [
        ("codex", "resident_000000000001", 100),
        ("codex", "resident_000000000002", 50),
        ("codex", "others", 25),
        ("kimi", "resident_000000000001", 500),
    ]]
    inclusive = projection._build_variant(rows, {}, include_cached_input=True, now=now)
    assert inclusive["daily_by_provider"] == {"2026-09-21": {"codex": 175, "kimi": 500}}
    assert inclusive["daily_by_resident"] == {}
    with mock.patch.object(status, "_date_today", return_value=now.date()):
        supplement, complete = status._account_daily_supplement(inclusive, account_rows({
            "zero": {"usage": {"daily_buckets": [{"date": "2026-09-21", "tokens": 100}]}},
            "one": {"usage": {"daily_buckets": [{"date": "2026-09-21", "tokens": 150}]}},
        }))
    assert complete
    assert supplement == {"2026-09-21": 75}


def test_account_supplement_never_caps_ledger_or_invents_recent_daily_usage():
    """账号补差不削减 ledger 用量，也不为近两天和未来日期补出用量

    :design-point:"""
    resident = {"daily_by_provider_utc": {"2026-09-21": {"codex": 100}}}
    with mock.patch.object(status, "_date_today", return_value=date(2026, 9, 24)):
        supplement, complete = status._account_daily_supplement(resident, account_rows({
            "zero": {"usage": {"daily_buckets": [
                {"date": "2026-09-21", "tokens": 50},
                {"date": "2026-09-22", "tokens": -10},
                {"date": "2026-09-23", "tokens": 999},
                {"date": "2026-09-24", "tokens": 999},
                {"date": "2026-09-25", "tokens": 999},
            ]}},
        }))
    assert complete
    assert supplement == {}


@pytest.mark.parametrize("missing", [
    {"status": "loading", "usage": {}},
    {"usage": {"daily_buckets": []}, "sources": {"usage": {"status": "stale"}}},
    {"usage": {"daily_buckets": [{"date": "invalid", "tokens": 500}]}},
])
def test_missing_account_usage_keeps_available_data_and_marks_only_inclusive_variant_partial(missing):
    """某个账号用量缺失或过期时保留已有数据，只把含缓存口径标为部分

    :design-point:"""
    resident = {
        "status": "ok", "daily_by_provider_utc": {},
        "daily_assigned": {"2026-09-21": 10},
        "excluding_cached_input": {"daily_assigned": {"2026-09-21": 2}},
    }
    with (
        mock.patch.object(status, "_date_today", return_value=date(2026, 9, 24)),
        mock.patch.object(status, "get_society_service") as service,
        mock.patch.object(status.ExecutionService, "account_usage", return_value={"accounts": account_rows({
            "zero": {"usage": {"daily_buckets": [{"date": "2026-09-21", "tokens": 50}]}},
            "one": missing,
        })}),
    ):
        service.return_value.projection.collect_resident_usage.return_value = resident
        result = status._build_token_usage_summary("viewer")
    included, excluded = result["variants"].values()
    assert included["status"] == "partial"
    assert excluded["status"] == "ok"
    assert included["windows"]["week"]["total_tokens"] == 60
    assert excluded["windows"]["week"]["total_tokens"] == 2


def test_account_reconciliation_requires_provider_breakdown():
    """缺少按 Provider 拆分的日用量时账号补差直接报错

    :design-point:"""
    with pytest.raises(ValueError, match="daily provider breakdown"):
        status._account_daily_supplement({"daily_tracked": {"2026-09-21": 10}}, {})


def test_missing_cache_variant_is_not_presented_as_non_cache_usage():
    """Resident 用量缺少不含缓存口径时报错，不以含缓存数据代替

    :design-point:"""
    with mock.patch.object(status, "get_society_service") as service:
        service.return_value.projection.collect_resident_usage.return_value = {"status": "ok"}
        with pytest.raises(ValueError, match="missing the non-cache variant"):
            status._build_token_usage_summary("viewer")


def test_usage_lineage_metadata_requires_read_access_to_both_resources():
    """Usage 分组只公开可读的 Resident 与 Lineage 身份

    验证成员可读但 Lineage 不可读时不返回所属 Lineage 的 ID 或名称，查询沿用当前 actor。

    :design-point:
    """
    def resource(resource_id, title, readable, lineage_id=""):
        return {
            "resource_id": resource_id,
            "title": title,
            "capabilities": {"read": readable},
            "attributes": {"relations": [{"kind": "lineage", "target_id": lineage_id}]},
        }

    records = {
        "lineage": [resource("lineage_visible", "Team", True), resource("lineage_secret", "Secret", False)],
        "resident": [
            resource("resident_visible", "Visible", True, "lineage_visible"),
            resource("resident_shared", "Shared", True, "lineage_secret"),
            resource("resident_secret", "Private", False, "lineage_visible"),
        ],
    }
    canonical = [{"resident_id": record["resource_id"],
                  "lineage_id": record["attributes"]["relations"][0]["target_id"]}
                 for record in records["resident"] if record["capabilities"]["read"]]
    catalog = mock.Mock()
    catalog.get_usage_memberships.return_value = canonical
    with mock.patch.object(resource_client, "_query_all", side_effect=lambda kind, **kwargs: records[kind]) as query, \
            mock.patch.object(resident_catalog, "get_catalog", return_value=catalog):
        roots = resource_client.visible_resident_usage_roots(actor="viewer")
    catalog.get_usage_memberships.assert_called_once()
    assert set(catalog.get_usage_memberships.call_args.args[0]) == {"resident_visible", "resident_shared"}
    assert roots["resident_visible"]["lineage_title"] == "Team"
    assert "lineage_id" not in roots["resident_shared"]
    assert "resident_secret" not in roots
    assert "Secret" not in str(roots)
    assert "lineage_secret" not in str(roots)
    assert query.call_args_list == [mock.call("lineage", actor="viewer"), mock.call("resident", actor="viewer")]


@pytest.mark.parametrize("include_cache", [False, True])
def test_lineage_groups_all_residents_before_ranking_and_preserves_totals(include_cache):
    """Usage 先合并 Lineage 全部成员再选择 Top 20

    验证包含缓存和排除缓存两种口径下，45 个成员先按 Lineage 合并；四个时间范围总量不变，Top 20 以外与匿名用量仍计入。

    :design-point:
    """
    now = datetime(2026, 9, 22, 12, tzinfo=timezone.utc)
    rows, visible = [], {}
    # None of the 25 small members makes the resident top 20, but their team is first.
    for index in range(45):
        rid = f"resident_{index:012x}"
        lineage = "lineage_team" if index < 25 else f"lineage_{index:012x}"
        tokens = 10 if index < 25 else 20
        visible[rid] = {"resident_id": rid, "title": f"Worker {index}", "lineage_id": lineage, "lineage_title": lineage}
        rows.append({"provider": "codex", "bucket_hour": now.isoformat(), "resident_id": rid, "usage": {"total_tokens": tokens, "cached_input_tokens": 2}})
    rows.extend([
        {"provider": "codex", "bucket_hour": now.isoformat(), "resident_id": "resident_ffffffffffff", "usage": {"total_tokens": 30}},
        {"provider": "codex", "bucket_hour": now.isoformat(), "resident_id": "others", "usage": {"total_tokens": 5}},
    ])
    with mock.patch.object(projection, "_parse_bucket", return_value=now), mock.patch.object(status, "_date_today", return_value=now.date()):
        projected = projection._build_variant(rows, visible, include_cached_input=include_cache, now=now)
        payload = status._token_usage_payload(projected, include_cached_input=include_cache)
    assert "resident_ffffffffffff" not in str(payload)
    team_tokens = 250 if include_cache else 200
    for period in ("today", "week", "month", "year"):
        resident_points = payload["chart_series"][period]
        lineage_points = payload["lineage_chart_series"][period]
        assert len(resident_points) == len(lineage_points)
        assert sum(point["tokens"] for point in lineage_points) == payload["windows"][period]["total_tokens"]
        for left, right in zip(resident_points, lineage_points):
            assert {key: value for key, value in left.items() if key != "segments"} == {key: value for key, value in right.items() if key != "segments"}
            assert sum(segment["tokens"] for segment in right["segments"]) == right["tokens"]
        segments = [segment for point in lineage_points for segment in point["segments"]]
        named_keys = {segment["key"] for segment in segments} - {"other_residents", "unregistered"}
        assert len(named_keys) == 20
        small_members = {f"resident_{index:012x}" for index in range(25)}
        assert not small_members.intersection(segment["key"] for point in resident_points for segment in point["segments"])
        assert sum(segment["tokens"] for segment in segments if segment["key"] == "lineage_team") == team_tokens
        assert sum(segment["tokens"] for segment in segments if segment["key"] == "unregistered") == 5
        assert sum(segment["tokens"] for segment in segments if segment["key"] == "other_residents") >= 30
    team = next(item for item in payload["lineages"] if item["lineage_id"] == "lineage_team")
    assert team["periods"] == dict.fromkeys(("today", "week", "month", "year"), team_tokens)


@pytest.mark.parametrize("include_cache", [False, True])
def test_lineage_privacy_preserves_totals_and_anonymous_categories(include_cache):
    """Usage 权限过滤保留匿名用量及总量

    贯通 ResourceService 权限映射、Resident 投影和图表响应；不可读、删除或无可读归属的正式成员进入 Other resident(s)，others 保留为 Unregistered session(s)，Details 不泄露隐藏身份。

    不可读成员、不可读归属和缺失归属保持匿名，但正式 Resident 总量不丢失。

    :design-point:
    """
    now = datetime(2026, 9, 22, 12, tzinfo=timezone.utc)
    visible_id, shared_id, orphan_id, private_id = [f"resident_{index:012x}" for index in range(1, 5)]
    team_id, secret_id = "lineage_000000000001", "lineage_000000000002"
    def record(resource_id, title, readable, lineage_id=""):
        return {"resource_id": resource_id, "title": title, "capabilities": {"read": readable},
                "attributes": {"relations": [{"kind": "lineage", "target_id": lineage_id}] if lineage_id else []}}
    records = {
        "lineage": [record(team_id, "Visible team", True), record(secret_id, "Secret team", False)],
        "resident": [record(visible_id, "Visible member", True, team_id),
                     record(shared_id, "Shared member", True, secret_id),
                     record(orphan_id, "No team member", True),
                     record(private_id, "Private member", False, team_id)],
    }
    rows = [{"provider": "codex", "bucket_hour": now.isoformat(), "resident_id": rid,
             "usage": {"total_tokens": tokens, "cached_input_tokens": 2}}
            for rid, tokens in [(visible_id, 40), (shared_id, 30), (orphan_id, 20), (private_id, 10), ("others", 5)]]
    canonical = [{"resident_id": record["resource_id"],
                  "lineage_id": next((r["target_id"] for r in record["attributes"]["relations"]), "")}
                 for record in records["resident"] if record["capabilities"]["read"]]
    catalog = mock.Mock()
    catalog.get_usage_memberships.return_value = canonical
    with mock.patch.object(resource_client, "_query_all", side_effect=lambda kind, **kwargs: records[kind]), \
            mock.patch.object(resident_catalog, "get_catalog", return_value=catalog):
        visible = resource_client.visible_resident_usage_roots(actor="viewer")
    with mock.patch.object(status, "_date_today", return_value=now.date()):
        projected = projection._build_variant(rows, visible, include_cached_input=include_cache, now=now)
        payload = status._token_usage_payload(projected, include_cached_input=include_cache)
    for secret in (private_id, "Private member", secret_id, "Secret team"):
        assert secret not in str(payload)
    delta = 0 if include_cache else 2
    for period in ("today", "week", "month", "year"):
        points = payload["lineage_chart_series"][period]
        segments = {segment["key"]: segment["tokens"] for point in points for segment in point["segments"]}
        assert segments == {team_id: 40 - delta, "other_residents": 60 - 3 * delta, "unregistered": 5 - delta}
        assert sum(point["tokens"] for point in points) == payload["windows"][period]["total_tokens"] == 105 - 5 * delta
        resident_keys = {segment["key"] for point in payload["chart_series"][period] for segment in point["segments"]}
        assert {visible_id, shared_id, orphan_id} <= resident_keys
    assert [item["lineage_id"] for item in payload["lineages"]] == [team_id]


@pytest.mark.parametrize("include_cache", [False, True])
def test_project_grouping_follows_each_readable_lineage_into_its_readable_project(include_cache):
    """Usage 按 Project 归属：经当前可读 Lineage 归入可读 Project

    贯通 ResourceService 权限映射、Resident 投影和图表响应；同一 Project 的多个 Lineage 合计，Lineage 或 Project 不可读、Lineage 无 Project 的成员进入 Other resident(s)，图例与排名用 Project 标题，总量不变且不泄露隐藏名称。

    Resident 用量经当前 Lineage 归入其 Project；不可读的 Lineage 或 Project 不具名，总量不丢失。

    :design-point:
    """
    now = datetime(2026, 9, 22, 12, tzinfo=timezone.utc)
    first, second, other, shared, plain = [f"resident_{index:012x}" for index in range(1, 6)]
    team_a, team_b, team_c, secret_team, loose = [f"lineage_{index:012x}" for index in range(1, 6)]
    def record(resource_id, title, readable, lineage_id="", project_id=""):
        return {"resource_id": resource_id, "title": title, "capabilities": {"read": readable},
                "attributes": {"relations": [{"kind": "lineage", "target_id": lineage_id}] if lineage_id else [],
                               "project_ids": [project_id] if project_id else []}}
    records = {
        "project": [record("AI员工", "AlignMe", True), record("Secret", "Secret project", False)],
        # Two Lineages of one Project add up; a Lineage of an unreadable Project
        # and a readable member of an unreadable Lineage stay anonymous.
        "lineage": [record(team_a, "Team A", True, project_id="AI员工"),
                    record(team_b, "Team B", True, project_id="AI员工"),
                    record(team_c, "Team C", True, project_id="Secret"),
                    record(secret_team, "Hidden team", False, project_id="AI员工"),
                    record(loose, "No project", True)],
        "resident": [record(first, "First", True, team_a), record(second, "Second", True, team_b),
                     record(other, "Other", True, team_c), record(shared, "Shared", True, secret_team),
                     record(plain, "Plain", True, loose)],
    }
    canonical = [{"resident_id": item["resource_id"], "lineage_id": item["attributes"]["relations"][0]["target_id"]}
                 for item in records["resident"]]
    catalog = mock.Mock()
    catalog.get_usage_memberships.return_value = canonical
    with mock.patch.object(resource_client, "_query_all", side_effect=lambda kind, **kwargs: records[kind]), \
            mock.patch.object(resident_catalog, "get_catalog", return_value=catalog):
        visible = resource_client.visible_resident_usage_roots(actor="viewer")
    assert {rid: visible[rid].get("project_title") for rid in visible} == {
        first: "AlignMe", second: "AlignMe", other: None, shared: None, plain: None}
    rows = [{"provider": "codex", "bucket_hour": now.isoformat(), "resident_id": rid,
             "usage": {"total_tokens": tokens, "cached_input_tokens": 2}}
            for rid, tokens in [(first, 40), (second, 30), (other, 20), (shared, 10), (plain, 8), ("others", 5)]]
    with mock.patch.object(status, "_date_today", return_value=now.date()):
        projected = projection._build_variant(rows, visible, include_cached_input=include_cache, now=now)
        payload = status._token_usage_payload(projected, include_cached_input=include_cache)
    for secret in ("Secret project", "Hidden team", secret_team):
        assert secret not in str(payload)
    delta = 0 if include_cache else 2
    for period in ("today", "week", "month", "year"):
        points = payload["project_chart_series"][period]
        segments = {segment["key"]: segment["tokens"] for point in points for segment in point["segments"]}
        assert segments == {"AI员工": 70 - 2 * delta, "other_residents": 38 - 3 * delta, "unregistered": 5 - delta}
        assert sum(point["tokens"] for point in points) == payload["windows"][period]["total_tokens"] == 113 - 6 * delta
        assert next(segment["label"] for point in points for segment in point["segments"]
                    if segment["key"] == "AI员工") == "AlignMe"
    assert payload["projects"] == [{"project_id": "AI员工", "title": "AlignMe",
                                    "periods": dict.fromkeys(("today", "week", "month", "year"), 70 - 2 * delta)}]
    assert [group["id"] for bucket in payload["bucket_groups"]["project"] for group in bucket["groups"]] == ["AI员工", ""]


def test_lineage_grouping_preserves_historical_resident_usage():
    """按 Lineage 分组时历史 Resident 用量合并到所属 Lineage 且总量不变

    :design-point:"""
    resident = {
        "resident_titles": {"r1": "One", "r2": "Two", "r3": "Three"},
        "resident_lineages": {"r1": "team", "r2": "team", "r3": "other"},
        "lineage_titles": {"team": "Team", "other": "Other team"},
        "daily_assigned": {"2026-09-01": 90},
        "daily_by_resident": {"2026-09-01": {"r1": 30, "r2": 30, "r3": 30}},
    }
    with mock.patch.object(status, "_date_today", return_value=date(2026, 9, 22)):
        individual = status._build_chart_series(resident)["month"][0]
        grouped = status._build_chart_series(resident, group_by="lineage")["month"][0]
    assert grouped["tokens"] == 90
    assert sum(segment["tokens"] for segment in grouped["segments"]) == 90
    assert grouped["segments"][0]["tokens"] == sum(segment["tokens"] for segment in individual["segments"] if segment["key"] in {"r1", "r2"})


@pytest.mark.parametrize("include_cache", [True, False])
def test_all_providers_share_attribution_without_codex_cap_or_double_count(include_cache):
    """所有 Provider 的用量按同一规则归属，不设 codex 上限也不重复计数

    :design-point:"""
    now = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)
    first, second, private = (f"resident_{index:012x}" for index in (1, 2, 3))
    team = "lineage_000000000001"
    visible = {
        resident_id: {"resident_id": resident_id, "title": title, "lineage_id": team, "lineage_title": "Team"}
        for resident_id, title in ((first, "First"), (second, "Second"))
    }
    inputs = [
        (20, "codex", first, 40, 10),
        (20, "deepseek", second, 70, 15),
        (21, "codex", first, 200, 80),
        (21, "deepseek", first, 80, 20),
        (21, "openrouter", second, 40, 10),
        (21, "kimi", private, 30, 5),
        (21, "openrouter", "others", 10, 2),
        (22, "codex", first, 20, 5),
        (22, "deepseek", second, 50, 10),
        (23, "codex", first, 30, 10),
        (23, "kimi", first, 60, 20),
        (23, "deepseek", second, 40, 10),
        (23, "openrouter", "others", 20, 5),
    ]
    rows = [{
        "bucket_hour": f"2026-09-{day:02d}T08:00:00Z", "provider": provider,
        "resident_id": resident_id,
        "usage": {"input_tokens": tokens - 5, "output_tokens": 5, "total_tokens": tokens, "cached_input_tokens": cached},
    } for day, provider, resident_id, tokens, cached in inputs]

    def token_usage(*, start_hour, end_hour, provider, include_accounts=False):
        assert provider == "*"
        return {"rows": [row for row in rows if start_hour <= row["bucket_hour"] < end_hour]}

    with (
        mock.patch.object(projection, "_local_now", return_value=now),
        mock.patch.object(projection, "_visible_roots", return_value=visible),
        mock.patch.object(projection.execution_client, "token_usage", side_effect=token_usage),
        mock.patch.object(status, "_date_today", return_value=now.date()),
    ):
        projected = projection.collect(actor="usage-viewer")
        assert projected["status"] == "ok"
        variant = projected if include_cache else projected["excluding_cached_input"]
        payload = status._token_usage_payload(variant, include_cached_input=include_cache)

    assert set(payload["providers"]) == {"codex", "kimi", "deepseek", "openrouter"}
    expected = {"today": 150, "week": 580, "month": 690, "year": 690} if include_cache else {
        "today": 105, "week": 403, "month": 488, "year": 488,
    }
    assert private not in str(payload)
    for period, total in expected.items():
        assert payload["windows"][period]["total_tokens"] == total
        for grouping in ("chart_series", "lineage_chart_series"):
            points = payload[grouping][period]
            assert sum(point["tokens"] for point in points) == total
            assert all(sum(segment["tokens"] for segment in point["segments"]) == point["tokens"] for point in points)
    rankings = {item["resident_id"]: item["periods"] for item in payload["residents"]}
    assert rankings[first]["week"] == (390 if include_cache else 255)
    assert rankings[second]["week"] == (130 if include_cache else 100)
    assert payload["lineages"][0]["periods"]["week"] == (520 if include_cache else 355)
    assert payload["source"] == "execution_ledger_and_account_daily"
    assert "account_tokens" not in payload["windows"]["month"]
    day = next(point for point in payload["chart_series"]["month"] if point["date"] == "2026-09-20")
    assert day["tokens"] == (110 if include_cache else 85)


def test_historical_private_resident_share_stays_anonymous():
    """不可见 Resident 的历史用量计入其他 Resident，不暴露其 ID

    :design-point:"""
    public_id, private_id = "resident_000000000001", "resident_000000000002"
    rows = [
        {"provider": "codex", "resident_id": resident_id, "bucket_hour": "2026-09-20T08:00:00Z", "usage": {"total_tokens": 100}}
        for resident_id in (public_id, private_id)
    ]
    now = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)
    visible = {public_id: {"resident_id": public_id, "title": "Public"}}
    with mock.patch.object(status, "_date_today", return_value=now.date()):
        variant = projection._build_variant(rows, visible, include_cached_input=True, now=now)
        result = status._token_usage_payload(variant, include_cached_input=True)
    day = result["chart_series"]["month"][19]
    assert {segment["key"]: segment["tokens"] for segment in day["segments"]} == {
        public_id: 100, "other_residents": 100,
    }
    assert result["residents"][0]["periods"]["month"] == 100
    assert result["windows"]["month"]["assigned_tokens"] == 200
    assert private_id not in str(result)


@pytest.mark.parametrize("include_cache", [True, False])
def test_new_year_keeps_previous_year_week(include_cache):
    """跨年时本周用量仍包含上一年的日期，今天、本月和本年只计新年

    :design-point:"""
    now = datetime(2027, 1, 1, 12, tzinfo=timezone.utc)
    resident_id = "resident_000000000001"
    team_id = "lineage_000000000001"
    rows = [{
        "provider": "deepseek", "account_id": "deepseek_primary", "resident_id": resident_id, "bucket_hour": hour,
        "usage": {"total_tokens": 100, "cached_input_tokens": 20},
    } for hour in ("2026-12-03T08:00:00Z", "2026-12-29T08:00:00Z", "2027-01-01T08:00:00Z")]

    def query(*, start_hour, end_hour, provider, include_accounts=False):
        return {"rows": [row for row in rows if start_hour <= row["bucket_hour"] < end_hour]}

    with (
        mock.patch.object(projection, "_local_now", return_value=now),
        mock.patch.object(projection, "_visible_roots", return_value={resident_id: {
            "resident_id": resident_id, "title": "Public", "lineage_id": team_id, "lineage_title": "Team",
        }}),
        mock.patch.object(projection.execution_client, "token_usage", side_effect=query),
        mock.patch.object(status, "_date_today", return_value=now.date()),
    ):
        projected = projection.collect(actor="usage-viewer")
        variant = projected if include_cache else projected["excluding_cached_input"]
        result = status._token_usage_payload(variant, include_cached_input=include_cache)
    tokens = 100 if include_cache else 80
    assert result["windows"]["week"]["total_tokens"] == tokens * 2
    for period in ("today", "month", "year"):
        assert result["windows"][period]["total_tokens"] == tokens
    for ranking in ("residents", "lineages"):
        assert result[ranking][0]["periods"] == {"today": tokens, "week": tokens * 2, "month": tokens, "year": tokens}
    for key in ("chart_series", "lineage_chart_series"):
        for period, points in result[key].items():
            assert sum(point["tokens"] for point in points) == result["windows"][period]["total_tokens"]
    windows = projection._query_windows(now)
    assert datetime.fromisoformat(windows[0][0].replace("Z", "+00:00")).date() <= now.date() - timedelta(days=now.weekday())


def test_account_supplement_reconciles_each_opaque_provider_independently():
    """账号补差对每个未知 Provider 分别对账

    :design-point:"""
    resident = {"daily_by_provider_utc": {"2026-09-21": {"future": 100, "another": 50}}}
    accounts = [{"account_id": name, "provider": provider, "usage": {"status": "ok",
        "daily_buckets": [{"date": "2026-09-21", "tokens": tokens}]}}
        for name, provider, tokens in [("first", "future", 80), ("second", "future", 70), ("third", "another", 90)]]
    with mock.patch.object(status, "_date_today", return_value=date(2026, 9, 24)):
        supplement, complete = status._account_daily_supplement(resident, accounts)
    assert complete
    assert supplement == {"2026-09-21": 90}


def test_backend_account_scopes_reconcile_totals_without_disclosing_private_residents():
    """按 Provider 与账号筛选的用量范围合计等于总量，且不暴露不可见 Resident

    :design-point:"""
    now = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
    visible = "resident_000000000001"
    private = "resident_000000000002"
    rows = [{"bucket_hour": "2026-09-21T08:00:00Z", "provider": provider, "account_id": account,
             "resident_id": resident, "usage": {"total_tokens": tokens, "cached_input_tokens": tokens // 2}}
            for provider, account, resident, tokens in [
                ("codex", "codex_one", visible, 100), ("codex", "codex_two", private, 60),
                ("codex", "", "others", 40), ("future", "future_primary", visible, 50)]]
    accounts = [{"provider": "codex", "account_id": "codex_one", "usage": {"status": "ok", "daily_buckets": [{"date": "2026-09-21", "tokens": 250}]}},
                {"provider": "future", "account_id": "future_primary", "usage": {"status": "ok", "daily_buckets": []}},
                {"provider": "future", "account_id": "future_empty", "usage": {"status": "ok", "daily_buckets": []}}]
    with (mock.patch.object(projection, "_local_now", return_value=now),
          mock.patch.object(status, "_date_today", return_value=now.date()),
          mock.patch.object(projection, "_visible_roots", return_value={visible: {"resident_id": visible, "title": "Visible", "lineage_id": "team", "lineage_title": "Team"}}),
          mock.patch.object(projection.execution_client, "token_usage", side_effect=lambda **kw: {"rows": [r for r in rows if kw["start_hour"] <= r["bucket_hour"] < kw["end_hour"]]}),
          mock.patch.object(status, "get_society_service") as service,
          mock.patch.object(status.ExecutionService, "account_usage", return_value={"accounts": accounts, "catalog": [*accounts, {"provider": "unused_backend", "account_id": "unused_primary"}]})):
        service.return_value.projection.collect_resident_usage.return_value = projection.collect(actor="viewer")
        result = status._build_token_usage_summary("viewer")
    scopes = {(s["provider"], s["account_id"]): s for s in result["scopes"]}
    assert result["filters"]["backends"] == ["codex", "future", "unused_backend"]
    assert scopes["unused_backend", "unused_primary"]["windows"]["year"]["total_tokens"] == 0
    assert scopes["codex", "codex_one"]["account_history_incomplete"]
    for key in ("including_cached_input", "excluding_cached_input"):
        total = lambda scope: scope["variants"][key]["windows"]["week"]["total_tokens"]
        assert total(result) == total(scopes["codex", None]) + total(scopes["future", None])
        assert total(scopes["codex", None]) == (250 if key == "including_cached_input" else 100)
        assert total(scopes["future", "future_empty"]) == 0
        assert scopes["codex", "codex_two"]["variants"][key]["residents"] == []
    assert scopes["codex", ""]["windows"]["week"]["total_tokens"] == 40
    assert scopes["codex", "codex_one"]["windows"]["week"]["total_tokens"] == 250
    assert scopes["codex", "codex_one"]["variants"]["excluding_cached_input"]["account_totals_omitted"]
    assert not scopes["codex", ""]["variants"]["excluding_cached_input"]["account_totals_omitted"]
    assert private not in str(result)


@pytest.mark.parametrize("anonymous_tokens", [0, 70])
@pytest.mark.parametrize("native_status", ["ok", "stale"])
def test_native_daily_totals_keep_account_identity_and_global_deduplication(anonymous_tokens, native_status):
    """账号原生日用量按账号分别显示，全局合计时不与 ledger 重复计算

    :design-point:"""
    now = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
    rows = [{"bucket_hour": "2026-09-21T08:00:00Z", "provider": "codex", "account_id": account,
             "resident_id": "others", "usage": {"total_tokens": tokens, "cached_input_tokens": tokens // 2}}
            for account, tokens in [("codex_one", 10), ("codex_two", 20), ("", anonymous_tokens)] if tokens]
    accounts = [{"provider": "codex", "account_id": account, "usage": {"status": native_status,
                 "daily_buckets": [{"date": "2026-09-21", "tokens": tokens},
                                   {"date": "2026-09-23", "tokens": 999},
                                   {"date": "2026-09-24", "tokens": 999}]}}
                for account, tokens in [("codex_one", 80), ("codex_two", 120), ("codex_three", 40)]]
    with (mock.patch.object(projection, "_local_now", return_value=now),
          mock.patch.object(status, "_date_today", return_value=now.date()),
          mock.patch.object(projection, "_visible_roots", return_value={}),
          mock.patch.object(projection.execution_client, "token_usage", side_effect=lambda **kw: {"rows": [row for row in rows if kw["start_hour"] <= row["bucket_hour"] < kw["end_hour"]]}),
          mock.patch.object(status, "get_society_service") as service,
          mock.patch.object(status.ExecutionService, "account_usage", return_value={"accounts": accounts})):
        service.return_value.projection.collect_resident_usage.return_value = projection.collect(actor="viewer")
        result = status._build_token_usage_summary("viewer")
    scopes = {(scope["provider"], scope["account_id"]): scope for scope in result["scopes"]}
    expected_status = "ok" if native_status == "ok" else "partial"
    for scope in (result, scopes["codex", None]):
        assert scope["windows"]["week"]["total_tokens"] == 240
        assert scope["status"] == expected_status
        assert scope["variants"]["excluding_cached_input"]["windows"]["week"]["total_tokens"] == (30 + anonymous_tokens) // 2
    for account, tokens, ledger in [("codex_one", 80, 10), ("codex_two", 120, 20), ("codex_three", 40, 0)]:
        scope = scopes["codex", account]
        assert scope["windows"]["week"]["total_tokens"] == tokens
        assert scope["windows"]["today"]["total_tokens"] == 0
        assert scope["status"] == expected_status
        assert scope["residents"] == []
        assert scope["variants"]["excluding_cached_input"]["windows"]["week"]["total_tokens"] == ledger // 2
        assert scope["account_history_incomplete"] == bool(anonymous_tokens)
    if anonymous_tokens:
        unknown = scopes["codex", ""]
        assert unknown["windows"]["week"]["total_tokens"] == anonymous_tokens
        assert unknown["status"] == "ok"
    else:
        assert ("codex", "") not in scopes


def test_account_supplement_uses_utc_day_across_local_midnight():
    """账号补差按 UTC 日期对账，跨越本地午夜的用量不错位

    :design-point:"""
    local = timezone(timedelta(hours=8))
    instant = datetime(2026, 9, 22, 6, tzinfo=local)
    row = {"bucket_hour": "2026-09-21T22:00:00Z", "provider": "codex", "usage": {"total_tokens": 100}}
    with mock.patch.object(projection, "_parse_bucket", return_value=instant):
        ledger = projection._build_variant([row], {}, include_cached_input=True, now=datetime(2026, 9, 24, 12, tzinfo=local))
    assert ledger["daily_by_provider"] == {"2026-09-22": {"codex": 100}}
    assert ledger["daily_by_provider_utc"] == {"2026-09-21": {"codex": 100}}
    with mock.patch.object(status, "_date_today", return_value=date(2026, 9, 24)):
        supplement, complete = status._account_daily_supplement(ledger, account_rows({
            "zero": {"usage": {"daily_buckets": [{"date": "2026-09-21", "tokens": 180}]}},
        }))
    assert complete
    assert supplement == {"2026-09-21": 80}


@pytest.mark.parametrize("usage, included, excluded", [
    ({"input_tokens": 10, "output_tokens": 5, "cached_input_tokens": 3}, 15, 12),
    ({"total_tokens": 20, "input_tokens": 10, "output_tokens": 5, "cached_input_tokens": 3}, 20, 17),
    ({"total_tokens": 0, "input_tokens": 10, "output_tokens": 5}, 0, 0),
])
def test_usage_total_uses_reported_value_and_same_cache_exclusion(usage, included, excluded):
    """usage_total 优先采用上报的 total_tokens，两种口径按同一方式扣除缓存

    :design-point:"""
    from management_portal.execution.contracts import usage_total
    assert usage_total(usage, include_cached_input=True) == included
    assert usage_total(usage, include_cached_input=False) == excluded
