from concurrent.futures import ThreadPoolExecutor

import pytest

from management_portal.execution.account_usage_store import AccountUsageStore


def sample(*values):
    return {"status": "ok", "daily_buckets": [
        {"date": day, "tokens": tokens} for day, tokens in values
    ]}


def test_daily_history_survives_new_store_and_shorter_upstream_window():
    """按日用量历史在重建存储后仍在，上游窗口变短时合并保留旧日期"""
    store = AccountUsageStore()
    store.save("codex", "one", sample(("2026-09-01", 100), ("2026-09-02", 50)), observed_at=1000)
    restarted = AccountUsageStore()
    saved = restarted.read("codex", "one")
    assert saved["status"] == "stale"
    assert saved["observed_at"] == 1000
    assert "checked_at" not in saved
    fresh = restarted.save("codex", "one", sample(("2026-09-02", 70), ("2026-09-03", 25)), observed_at=2000)
    assert fresh["daily_buckets"] == sample(("2026-09-01", 100), ("2026-09-02", 70), ("2026-09-03", 25))["daily_buckets"]


def test_explicit_corrections_including_zero_are_not_forced_to_historical_maximum():
    """较新的用量更正（包括改为 0）直接生效，较早的观测不能覆盖"""
    store = AccountUsageStore()
    store.save("codex", "one", sample(("2026-09-01", 100)), observed_at=1000)
    corrected = store.save("codex", "one", sample(("2026-09-01", 0)), observed_at=2000)
    assert corrected["daily_buckets"] == sample(("2026-09-01", 0))["daily_buckets"]
    late = AccountUsageStore().save("codex", "one", sample(("2026-09-01", 200)), observed_at=1500)
    assert late == corrected


def test_accounts_and_providers_do_not_share_daily_rows():
    """不同账号和不同 Provider 的按日用量各自存储，互不共享"""
    store = AccountUsageStore()
    for index, identity in enumerate((("codex", "one"), ("codex", "two"), ("other", "one"))):
        store.save(*identity, sample(("2026-09-01", index + 1)), observed_at=1000)
    assert store.read("codex", "one")["daily_buckets"][0]["tokens"] == 1
    assert store.read("codex", "two")["daily_buckets"][0]["tokens"] == 2
    assert store.read("other", "one")["daily_buckets"][0]["tokens"] == 3
    assert store.read("codex", "unknown") == {}


@pytest.mark.parametrize("invalid", [
    {}, {"status": "partial", "daily_buckets": []}, sample(),
    sample(("2026-09-01", -1)), sample(("2026-09-01", None)),
    sample(("2026-09-01", True)), sample(("2026-09-01", 1.5)),
    sample(("invalid-date", 1)), sample(("2026-09-01", 1), ("2026-09-01", 2)),
    {"status": "ok", "daily_buckets": [{"date": "2026-09-01"}]},
])
def test_invalid_or_missing_data_cannot_replace_saved_usage(invalid):
    """缺失、负数、非整数、日期非法或重复日期的用量报告被拒绝，已保存用量不变"""
    store = AccountUsageStore()
    store.save("codex", "one", sample(("2026-09-01", 100)), observed_at=1000)
    before = store.read("codex", "one")
    with pytest.raises(ValueError):
        store.save("codex", "one", invalid, observed_at=2000)
    assert store.read("codex", "one") == before


def test_empty_report_for_account_with_no_usage_is_valid():
    """没有用量的账号可以保存空的按日用量报告"""
    store = AccountUsageStore()
    assert store.save("codex", "new", sample(), observed_at=1000)["daily_buckets"] == []
    assert store.read("codex", "new")["observed_at"] == 1000


def test_concurrent_service_writers_preserve_all_days_and_newest_observation():
    """多个服务并发写入用量时保留全部日期，并以最新观测为准"""
    def save(index):
        return AccountUsageStore().save("codex", "one", sample(
            ("2026-09-01", index), (f"2026-09-{index + 2:02d}", index),
        ), observed_at=1000 + index)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(save, range(4)))
    persisted = AccountUsageStore().read("codex", "one")
    assert persisted["observed_at"] == 1003
    assert persisted["daily_buckets"][0]["tokens"] == 3
    assert len(persisted["daily_buckets"]) == 5
