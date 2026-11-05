"""Read-only account attribution must not require an execution default.

:design: decision_4dea0db1f45a
"""
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal.execution.accounts import DefaultAccountUnavailable, default_execution_accounts
from management_portal.execution.account_registrations import registrations
from management_portal.execution import account_selection
from management_portal.execution.service import ExecutionService, ExecutionServiceServer
from management_portal.society_service import resident_intake_domain as intake
from tests.cases.account_registration_test_support import register_codex
from tests.cases.resident_execution_test_support import resident_record  # noqa: F401


class RpcExecutionService(ExecutionService):
    # The Resident fixture stubs these on the base class. Exercise the actual
    # facade -> RPC -> account owner contract in these regression cases.
    resolve_account = ExecutionService.resolve_account
    accounts = ExecutionService.accounts


@pytest.fixture
def unassigned_site(resident_record, tmp_path, monkeypatch):
    registry = register_codex(monkeypatch, tmp_path, default="")
    # Automatic selection no longer needs a configured default. Registered
    # accounts with no usable login represent a genuinely unassigned site;
    # existing session bindings still belong to their original account.
    observed = account_selection.observations
    monkeypatch.setattr(account_selection, "observations", lambda: [
        {**row, "login_state": "logged_out"} for row in observed()
    ])
    accounts = default_execution_accounts(account_registry=registry)
    catalog = [{**asdict(a), "backend_route_id": a.route_id} for a in accounts.accounts()]
    server = object.__new__(ExecutionServiceServer)
    server.accounts = accounts
    server.quota_monitor = SimpleNamespace(snapshot=lambda: {"accounts": catalog})
    service = RpcExecutionService(client=SimpleNamespace(request=server.dispatch))
    monkeypatch.setattr(intake.execution_port, "service", lambda: service)
    return accounts, service, catalog


def test_account_details_exclude_unassigned_and_hidden_residents(unassigned_site, resident_record, monkeypatch):
    """Bound ownership survives unrelated Residents with no available account."""
    accounts, service, _ = unassigned_site
    before = registrations("codex")
    unassigned = {**resident_record, "resident_id": "resident_bbbbbbbbbbbb"}
    hidden = {**resident_record, "resident_id": "resident_cccccccccccc"}
    monkeypatch.setattr(intake.resident_lineage, "all", lambda: [resident_record, unassigned, hidden])
    monkeypatch.setattr(intake, "_resident_visible_to_actor", lambda record, actor:
                        actor == "viewer" and record["resident_id"] != hidden["resident_id"])
    summary = intake.get_account_resident_summary("codex_zero", "viewer")
    assert summary["total"] == 1
    assert summary["open_resident_ids"] == [resident_record["resident_id"]]
    assert summary["without_session"] == 0
    assert intake.get_account_resident_summary("codex_one", "viewer")["total"] == 0
    # Invisible Residents are never resolved, even when routing itself is broken.
    monkeypatch.setattr(service, "resolve_account", Mock(side_effect=AssertionError("hidden route inspected")))
    assert intake.get_account_resident_summary("codex_zero", "visitor")["total"] == 0
    with pytest.raises(PermissionError):
        intake.get_account_resident_summary("codex_zero", "")
    assert registrations("codex") == before
    with pytest.raises(DefaultAccountUnavailable):
        accounts.resolve("codex")


def test_read_only_resolution_caches_unassigned_but_execution_stays_strict(unassigned_site, resident_record, monkeypatch):
    accounts, service, catalog = unassigned_site
    resolve = Mock(wraps=service.resolve_account)
    monkeypatch.setattr(service, "resolve_account", resolve)
    account_for = intake._resident_quota_account_resolver(catalog)
    assert account_for(resident_record, {}) == ""
    assert account_for(dict(resident_record), {}) == ""
    resolve.assert_called_once()
    assert resolve.call_args.kwargs["allow_unassigned"] is True
    assert service.resolve_account("codex", "zero", allow_unassigned=True)["account_id"] == "codex_zero"
    with pytest.raises(ValueError, match="No compatible account"):
        service.resolve_account("codex")
    with pytest.raises(ValueError, match="unknown Codex account route"):
        service.resolve_account("codex", "missing", allow_unassigned=True)
    assert accounts.resident_selection("codex")["default_account_id"] == ""


def test_account_details_include_visible_automatic_selection(unassigned_site, resident_record, monkeypatch):
    """An available automatic route counts only visible, unbound Residents."""
    _, _, catalog = unassigned_site
    monkeypatch.setattr(account_selection, "observations", lambda: [
        {**row, "kind": "subscription_login", "login_state": "logged_in"}
        for row in catalog if row["account_id"] == "codex_one"
    ])
    automatic = {**resident_record, "resident_id": "resident_bbbbbbbbbbbb"}
    hidden = {**resident_record, "resident_id": "resident_cccccccccccc"}
    monkeypatch.setattr(intake.resident_lineage, "all", lambda: [resident_record, automatic, hidden])
    monkeypatch.setattr(intake, "_resident_visible_to_actor", lambda record, actor:
                        actor == "viewer" and record["resident_id"] != hidden["resident_id"])
    summary = intake.get_account_resident_summary("codex_one", "viewer")
    assert summary["total"] == 1
    # No intake snapshot exists for the synthetic unbound Resident. It is
    # counted for ownership, but must not become a bulk OpenIntake target.
    assert summary["open_resident_ids"] == []
    assert summary["intake"] == {"open": 0, "closed": 0, "unknown": 1}
    assert summary["without_session"] == 1
    assert intake.get_account_resident_summary("codex_zero", "viewer")["open_resident_ids"] == [resident_record["resident_id"]]


@pytest.mark.parametrize("error", [ValueError("Multiple default Codex accounts"), RuntimeError("Execution unavailable")])
def test_read_only_resolution_preserves_real_errors(unassigned_site, monkeypatch, error):
    accounts, service, _ = unassigned_site
    monkeypatch.setattr(accounts, "resolve", Mock(side_effect=error))
    with pytest.raises(type(error), match=str(error)):
        service.resolve_account("codex", allow_unassigned=True)


def test_read_only_resolution_preserves_catalog_consistency_check(unassigned_site, resident_record, monkeypatch):
    _, service, catalog = unassigned_site
    monkeypatch.setattr(service, "resolve_account", Mock(return_value={
        "account_id": "codex_missing", "backend_route_id": "missing",
    }))
    with pytest.raises(ValueError, match="absent from the account catalog"):
        intake._resident_quota_account_resolver(catalog)(resident_record, {})
