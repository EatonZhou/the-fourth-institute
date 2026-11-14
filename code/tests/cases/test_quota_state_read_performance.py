"""Quota projections bound reads to this call without retaining stale policy.

:design: decision_d40f59f5508f, decision_8ac0b5a6c0e5
"""
from contextlib import contextmanager
from dataclasses import replace
from unittest.mock import Mock

import pytest

from management_portal.execution import account_registrations
from tests.cases.test_codex_quota_reserve import scheduler, sample


@pytest.mark.parametrize("count", [1, 500])
def test_projection_reads_one_catalog_and_only_selected_disable_flags(scheduler, monkeypatch, count):
    scheduler.update_account_quota("one", sample(100))
    scheduler.update_account_quota("two", sample(10))
    account_registrations.update_registration("codex_two", {"enabled": False}, 1, "alice")
    accounts = Mock(wraps=scheduler.accounts.accounts)
    monkeypatch.setattr(scheduler.accounts, "accounts", accounts)
    database = account_registrations.database()
    reads = []

    class RecordingConnection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, params):
            rows = self.connection.execute(sql, params).fetchall()
            reads.append((sql, params, rows))
            return rows

    @contextmanager
    def read():
        with database.read() as connection:
            yield RecordingConnection(connection)

    monkeypatch.setattr(account_registrations, "database", lambda: type("DB", (), {"read": staticmethod(read)})())
    bindings = {f"resident:resident_{n:012x}": "codex_one" for n in range(count)}
    result = scheduler.binding_quota_states(bindings)
    assert len(result) == count
    assert {row["reason"] for row in result.values()} == {"quota_used_up"}
    assert accounts.call_count == 1
    [(sql, params, rows)] = reads
    assert "SELECT account_id" in sql and "ANY(%s)" in sql and "NOT enabled" in sql
    assert params == (["codex_one"],)
    assert rows == []  # Disabled unselected account and its config never cross the query.


def test_projection_reloads_quota_override_disable_and_catalog_between_calls(scheduler, monkeypatch):
    binding = "resident:resident_000000000001"
    bindings = {binding: "codex_one"}
    scheduler.update_account_quota("one", sample(100))
    assert scheduler.binding_quota_states(bindings)[binding]["reason"] == "quota_used_up"
    scheduler.configure_binding_quota_overrides([{"binding_id": binding, "revision": 1, "enabled": True}])
    assert scheduler.binding_quota_states(bindings)[binding]["override_enabled"] is True
    account_registrations.update_registration("codex_one", {"enabled": False}, 1, "alice")
    assert scheduler.binding_quota_states(bindings)[binding]["reason"] == "account_disabled"
    account_registrations.update_registration("codex_one", {"enabled": True}, 2, "alice")
    scheduler.configure_binding_quota_overrides([{"binding_id": binding, "revision": 2, "enabled": False}])
    scheduler.update_account_quota("one", sample(10))
    assert scheduler.binding_quota_states(bindings)[binding]["state"] == "open"
    accounts = scheduler.accounts.accounts()
    monkeypatch.setattr(scheduler.accounts, "accounts", lambda: tuple(a for a in accounts if a.account_id != "codex_one"))
    assert scheduler.binding_quota_states(bindings)[binding]["reason"] == "account_unavailable"


def test_capacity_key_sharing_preserves_quota_policy(scheduler, monkeypatch):
    scheduler.update_account_quota("one", sample(100))
    accounts = scheduler.accounts.accounts()
    account = next(a for a in accounts if a.account_id == "codex_one")
    alias = replace(account, account_id="pool_alias", quota_policy="none", billing_account_id="")
    monkeypatch.setattr(scheduler.accounts, "accounts", lambda: (alias, *accounts))
    result = scheduler.binding_quota_states({"resident:alias": "pool_alias"})
    assert result["resident:alias"]["reason"] == "quota_used_up"
