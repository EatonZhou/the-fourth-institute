"""Batch reads of many Residents build the account adapters once.

:aspects: performance
"""
from management_portal.execution import accounts
from management_portal.society_service import resident_backend, resident_directory_domain


class _Accounts:
    def configuration(self, provider, route_id="", options=None):
        return {"account_id": f"{provider}:{route_id or 'default'}"}


def _counted_builds(monkeypatch):
    builds = []

    def build(**_kwargs):
        builds.append(1)
        return _Accounts()

    monkeypatch.setattr(accounts, "default_execution_accounts", build)
    return builds


def test_one_build_serves_a_batch_and_none_outlives_it(monkeypatch):
    """批量读取 Resident 时账号配置只构建一次

    在批量作用域内多次解析账号配置（含嵌套作用域）只构建一次账号适配器；作用域外每次解析都重新构建，离开作用域后不保留旧配置。
    """
    builds = _counted_builds(monkeypatch)
    # Outside a batch every read sees the configuration as it is now.
    accounts.resolve_account_configuration("codex", "zero")
    accounts.resolve_account_configuration("codex", "zero")
    assert len(builds) == 2

    builds.clear()
    with accounts.shared_account_configuration():
        with accounts.shared_account_configuration():
            accounts.resolve_account_configuration("codex", "zero")
        assert accounts.resolve_account_configuration("claude", "one") == {"account_id": "claude:one"}
    assert len(builds) == 1
    accounts.resolve_account_configuration("codex", "zero")
    assert len(builds) == 2


def test_conversation_activity_resolves_accounts_once_for_all_residents(monkeypatch):
    """会话活动列表为全部 Resident 只构建一次账号配置

    会话活动列表读取多个 Resident 的账号时共用一次账号适配器构建，并为每个 Resident 返回其会话路由对应的账号。
    """
    builds = _counted_builds(monkeypatch)
    records = [{"resident_id": f"resident_{index:012x}", "title": f"Worker {index}", "backend": "codex",
                "backend_route_id": "zero" if index % 2 else "one"} for index in range(6)]
    monkeypatch.setattr(resident_directory_domain.resident_access, "visible_resident_records",
                        lambda _ids, _actor, _entry: records)
    monkeypatch.setattr(resident_backend, "session_routes_for_bindings", lambda _ids: {})

    summaries = resident_directory_domain._engaged_conversation_records(
        [record["resident_id"] for record in records], "alice")

    assert [item["account_id"] for item in summaries] == [
        f"codex:{record['backend_route_id']}" for record in records]
    assert len(builds) == 1
