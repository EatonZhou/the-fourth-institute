"""Default account policy: quota, compatibility, pinning and safe recovery."""
from copy import deepcopy
import pytest

from management_portal.execution.account_selection import candidates
from management_portal.society_service import resident_default_backend as defaults
from management_portal.society_service.resident_account_policy import fallback_account, migration_target_options


def account(name, remaining=80, **extra):
    return dict(account_id=name, provider="codex", backend_route_id=name, kind="subscription_login",
                enabled=True, login_state="logged_in", max_concurrency=2, active_count=0,
                quota_reserve={"status": "available", "fresh": True, "remaining": remaining,
                               "used_up": remaining <= 0,
                               "windows": [{"remaining_percent": remaining}, {"remaining_percent": 100}]},
                selection_catalog={"fresh": True, "models": ["model"], "efforts_by_model": {"model": ["high"]}}, **extra)


def api_account(name="custom", *, provider="codex", api_id="custom-api"):
    row = account(name)
    row.update(provider=provider, kind="api_profile", billing_account_id=api_id,
               status="ok", login_state="not_applicable",
               balance={"status": "not_supported"},
               quota_reserve={"status": "available", "fresh": False, "remaining": None,
                              "used_up": False, "automatic_paused": False,
                              "close_threshold": {"metric": "balance", "value": 0, "supported": False}})
    row["selection_catalog"]["default_model"] = "model"
    return row


@pytest.mark.parametrize("provider", ["codex", "claude", "kimi", "dsh", "opencode"])
def test_configured_api_without_balance_is_usable_for_selection_and_recovery(provider):
    from management_portal.execution.account_selection import headroom
    from management_portal.society_service.resident_account_policy import account_available, sufficient_reserve
    row = api_account(provider=provider)
    before = deepcopy(row)
    assert candidates([row], row, model="model", effort="high") == [row]
    assert account_available(row)
    assert headroom(row) is None and not sufficient_reserve(row)
    assert candidates([row], row, reserve=5) == []
    assert row == before  # Unknown balance must stay unknown.


@pytest.mark.parametrize("remaining", [None, 0, -1, float("nan")])
def test_first_admission_uses_the_default_api_without_a_balance_endpoint(monkeypatch, remaining):
    from management_portal.execution import account_selection, account_registrations
    row = api_account()
    row["quota_reserve"].update(remaining=remaining, fresh=True)
    registration = {"account_id": row["account_id"], "route_id": row["backend_route_id"],
                    "provider": row["provider"], "kind": row["kind"], "enabled": True,
                    "config": {"api_id": row["billing_account_id"]}}
    monkeypatch.setattr(account_registrations, "registrations", lambda provider=None: [registration])
    monkeypatch.setattr(account_selection, "observations", lambda: [row])
    monkeypatch.setattr(defaults, "selection_priority", lambda record: [])
    monkeypatch.setattr(defaults, "role_configuration", lambda role: {
        "backend": "codex", "backend_route_id": row["backend_route_id"],
        "runtime_settings": {"backend_role": role, "account_selection": "auto",
                             "model": "model", "reasoning_effort": "high"}})
    record = {"backend": "codex", "backend_route_id": "", "execution_settings": {"backend_role": "default"}}
    result = defaults.configuration(record, {})
    assert result["backend_route_id"] == row["backend_route_id"]
    assert result["runtime_settings"]["account_selection"] == "auto"
    assert result["runtime_settings"]["model"] == "model"


@pytest.mark.parametrize("change", [
    {"enabled": False},
    {"quota_reserve": {"status": "reserved"}},
    {"quota_reserve": {"used_up": True}},
    {"quota_reserve": {"automatic_paused": True}},
    {"selection_catalog": {"fresh": True, "models": ["other"]}},
])
def test_unmeasured_api_still_respects_account_and_model_readiness(change):
    row = api_account()
    for key, value in change.items():
        if key == "quota_reserve":
            row[key].update(value)
        else:
            row[key] = value
    assert candidates([row], row, model="model", effort="high") == []


@pytest.mark.parametrize("balance", [
    {"status": "not_supported"},
    {"status": "unavailable"},
    {"status": "ok", "metric_kind": "availability", "is_available": True},
])
def test_optional_balance_reading_does_not_gate_the_configured_api(balance):
    row = api_account()
    row["balance"] = balance
    assert candidates([row], row, model="model") == [row]


@pytest.mark.parametrize("catalog", [{}, {"fresh": False, "models": ["old-model"]}])
@pytest.mark.parametrize("login_state", ["unknown", "loading", None])
def test_startup_does_not_wait_for_optional_monitor_observations(catalog, login_state):
    row = account("one")
    row.update(selection_catalog=catalog, login_state=login_state, status="loading", quota_reserve={})
    assert candidates([row], row, model="new-model", effort="high") == [row]
    assert candidates([row], row, model="new-model", reserve=5) == []


@pytest.mark.parametrize("priority", [[], ["codex:subscription_login"]])
def test_first_admission_without_catalog_uses_provider_default(monkeypatch, priority):
    from management_portal.execution import account_selection, account_registrations
    row = account("one")
    row.update(selection_catalog={}, login_state="unknown", quota_reserve={})
    registration = {**row, "route_id": "one"}
    monkeypatch.setattr(account_selection, "observations", lambda: [row])
    monkeypatch.setattr(account_registrations, "registrations", lambda provider=None: [registration])
    monkeypatch.setattr(defaults, "selection_priority", lambda record: priority)
    record = {"backend": "codex", "backend_route_id": "one",
              "execution_settings": {"account_selection": "auto"}}
    chosen = defaults.configuration(record, {})
    assert chosen["backend_route_id"] == "one"
    assert chosen["runtime_settings"]["model"] == ""


@pytest.mark.parametrize("remaining", [None, 0, -1, float("nan")])
@pytest.mark.parametrize("fresh", [False, True])
def test_disabled_balance_protection_never_requires_positive_balance(remaining, fresh):
    from management_portal.execution.account_selection import priority_candidates
    row = api_account()
    row["quota_reserve"].update(remaining=remaining, fresh=fresh)
    assert candidates([row], row, model="model") == [row]
    assert priority_candidates([row], ["codex:api_profile:custom-api"]) == [row]
    assert candidates([row], row, reserve=5) == []


def test_balance_protection_still_obeys_scheduler_pause():
    from management_portal.execution.account_quota import quota_state
    row = api_account()
    row["quota_reserve"] = quota_state(
        {"remaining_balance": 0, "observed_at": 100, "complete": True}, now=100,
        threshold={"metric": "balance", "unit": "USD", "value": 1, "supported": True},
    )
    assert candidates([row], row) == []
    row["quota_reserve"] = quota_state(
        {"remaining_balance": 0, "observed_at": 100}, now=100,
        threshold={"metric": "balance", "unit": "USD", "value": 0, "supported": True},
    )
    assert candidates([row], row) == [row]


def test_unmeasured_api_preserves_billing_scope_and_requires_explicit_selection(monkeypatch):
    from management_portal.execution import account_selection, account_registrations
    from management_portal.execution.accounts import DefaultAccountUnavailable
    from management_portal.execution.account_selection import priority_candidates
    row = api_account()
    other_api = api_account("other", api_id="other-api")
    other_harness = api_account("other-harness", provider="kimi")
    missing_scope = api_account("missing", api_id="")
    assert candidates([other_api, other_harness, missing_scope], row) == []
    assert candidates([missing_scope], missing_scope) == []
    assert priority_candidates([row], ["codex:subscription_login"]) == []
    assert priority_candidates([row], ["codex:api_profile:custom-api"]) == [row]
    monkeypatch.setattr(account_registrations, "registrations", lambda provider: [
        {"account_id": row["account_id"], "route_id": row["backend_route_id"],
         "provider": "codex", "kind": "api_profile", "config": {"api_id": "custom-api"}}])
    for anchor in ("", "removed"):
        with pytest.raises(DefaultAccountUnavailable):
            account_selection.select("codex", anchor=anchor, accounts=[row])


def test_api_ranking_handles_unknown_balances_without_inventing_headroom():
    unknown, measured = api_account("unknown"), api_account("measured")
    measured["quota_reserve"].update(fresh=True, remaining=80)
    assert candidates([unknown, measured], unknown) == [measured, unknown]
    assert candidates([unknown, measured], unknown, current="unknown")[0] == unknown
    measured["active_count"] = 2
    assert candidates([measured, unknown], unknown)[0] == unknown


@pytest.mark.parametrize("change", [
    {"enabled": False}, {"login_state": "login_expired"},
    {"quota_reserve": {"status": "reserved", "used_up": True}},
    {"selection_catalog": {"fresh": True, "models": ["other"]}},
    {"provider": "claude"}, {"kind": "api_profile", "billing_account_id": "paid"},
])
def test_automatic_excludes_unavailable_and_incompatible_accounts(change):
    source, bad, good = account("source", 0), account("bad", 100), account("good")
    bad.update(change)
    assert candidates([source, bad, good], source, model="model", effort="high") == [good]


def test_capacity_then_headroom_and_bound_session_stability():
    busy, free, fuller = account("busy", 100), account("free", 60), account("fuller", 80)
    busy["active_count"] = 2
    assert candidates([busy, free, fuller], busy)[0] == fuller
    assert candidates([busy, free, fuller], busy, current="free")[0] == free
    assert candidates([free, fuller], busy, effort="unsupported", model="model") == []


def test_recovery_uses_same_candidates_and_never_implicit_cross_backend():
    source, depleted, good = account("source", 0), account("depleted", 0), account("good")
    other = account("other"); other["provider"] = "claude"
    assert fallback_account([source, depleted, other], "codex", "source", []) == {}
    assert fallback_account([source, good, other], "codex", "source", [], model="model") == good
    assert migration_target_options(good, "codex", source)["context_strategy"] == "preserve"


def test_fixed_and_bound_sessions_do_not_reselect(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("A pinned session must not query another account")
    monkeypatch.setattr(defaults, "role_configuration", unexpected)
    record = {"backend": "codex", "backend_route_id": "one", "execution_settings": {"account_selection": "fixed"}}
    assert defaults.configuration(record, {}) is None
    record["execution_settings"]["account_selection"] = "auto"
    assert defaults.configuration(record, {"backend_route_id": "one", "backend_session_id": "thread"}) is None


def test_first_admission_selects_same_model_from_role_pool(monkeypatch):
    from management_portal.execution import account_selection, account_registrations
    rows = [{"account_id": n, "provider": "codex", "kind": "subscription_login", "route_id": n} for n in ("one", "two")]
    monkeypatch.setattr(account_registrations, "registrations", lambda provider=None: rows)
    monkeypatch.setattr(account_selection, "observations", lambda: [account("one", 0), account("two")])
    record = {"backend": "codex", "backend_route_id": "one", "execution_settings": {"model": "model", "account_selection": "auto"}}
    before = deepcopy(record)
    result = defaults.configuration(record, {})
    assert result["backend_route_id"] == "two"
    assert result["runtime_settings"]["model"] == "model" and record == before


def test_preparation_uses_saved_binding_before_account_selection():
    from types import SimpleNamespace
    from management_portal.execution.service import ExecutionPreparation
    from management_portal.execution.contracts import ExecutionRequest, SessionOptions
    from unittest.mock import Mock
    accounts, manager = Mock(), Mock()
    manager.prepare.return_value = SimpleNamespace(backend_route_id="saved", backend_session_id="native")
    accounts.resolve.return_value = SimpleNamespace(route_id="saved")
    request = ExecutionRequest.new(actor="test", prompt="hello", provider="codex", service_type="agent_session",
        operation="turn", session=SessionOptions(mode="persistent", binding_id="resident:test"))
    result = ExecutionPreparation(accounts=accounts, session_manager=manager).prepare(request)
    accounts.resolve.assert_called_once_with("codex", "saved", request.runtime_options, bound=True)
    assert result.session.backend_route_id == "saved"


def test_automatic_respects_close_threshold_and_api_billing_boundary():
    source, other = account("source"), account("other")
    other["quota_reserve"]["status"] = "reserved"
    assert candidates([other], source) == []
    for row, billing in [(source, "first"), (other, "second")]:
        row.update(kind="api_profile", billing_account_id=billing)
        row["quota_reserve"]["status"] = "available"
    assert candidates([other], source) == []


def test_selection_setting_round_trips_and_validates():
    from management_portal.society_service.resident_runtime_payload import runtime_settings_updates_from_payload
    from management_portal.execution.resident_runtime_settings import normalize_settings
    for value in ["auto", "fixed"]:
        assert normalize_settings(runtime_settings_updates_from_payload({"account_selection": value}))["account_selection"] == value
    with pytest.raises(ValueError):
        runtime_settings_updates_from_payload({"account_selection": "random"})


def test_no_candidate_or_missing_scope_never_uses_unrelated_api(monkeypatch):
    from management_portal.execution import account_selection, account_registrations
    from management_portal.execution.accounts import DefaultAccountUnavailable
    monkeypatch.setattr(account_registrations, "registrations", lambda provider: [
        {"account_id": "known", "route_id": "known", "provider": "codex", "kind": "subscription_login"}])
    for anchor in ("known", "deleted"):
        with pytest.raises(DefaultAccountUnavailable):
            account_selection.select("codex", anchor=anchor, accounts=[account("known", 0)])


def test_backend_priority_drives_first_choice_and_recovery(monkeypatch):
    from management_portal.execution import account_selection, account_registrations
    from management_portal.society_service import family_quota
    source, fuller, claude = account("one", 10), account("two", 80), account("claude", 50)
    claude.update(provider="claude", selection_catalog={"fresh": True, "default_model": "sonnet", "models": ["sonnet"]})
    for a in [source, fuller]:
        a["selection_catalog"]["default_model"] = "model"
    samples = [source, fuller, claude]
    rows = [{**a, "route_id": a["backend_route_id"]} for a in samples]
    order = ["claude:subscription_login", "codex:subscription_login"]
    monkeypatch.setattr(account_registrations, "resolve_role", lambda name: ({"backend_priority": order}, None))
    monkeypatch.setattr(account_registrations, "registrations", lambda provider: [a for a in rows if a["provider"] == provider])
    monkeypatch.setattr(account_selection, "observations", lambda: samples)
    record = {"backend": "codex", "backend_route_id": "one", "execution_settings": {"model": "model", "reasoning_effort": "high", "account_selection": "auto"}}
    selected = defaults.configuration(record, {})
    assert selected["backend"] == "claude" and selected["runtime_settings"]["model"] == "sonnet"
    assert "reasoning_effort" not in selected["runtime_settings"]
    assert family_quota.auto_migration_priority(record) == order
    # Existing sessions keep same-kind preference before crossing to the sorted fallback.
    assert fallback_account(samples, "codex", "one", [], priority=order, model="model") == fuller
    fuller["quota_reserve"]["remaining"] = 0
    fuller["quota_reserve"]["used_up"] = True
    assert fallback_account(samples, "codex", "one", [], priority=order, model="model") == claude
    order.reverse()
    assert defaults.configuration(record, {})["backend_route_id"] == "one"
    claude["selection_catalog"]["fresh"] = False
    assert account_selection.priority_candidates(samples, ["claude:subscription_login"]) == [claude]


def test_backend_priority_never_includes_unlisted_paid_kind():
    from management_portal.execution.account_selection import priority_candidates
    paid = account("paid"); paid.update(kind="api_profile", billing_account_id="api")
    paid["selection_catalog"]["default_model"] = "model"
    assert priority_candidates([paid], ["codex:subscription_login"]) == []
    assert priority_candidates([paid], ["codex:api_profile:api"]) == [paid]


def test_explicit_priority_applies_even_when_role_reference_is_direct_api(monkeypatch):
    from management_portal.execution import account_selection, account_registrations
    target = account("target")
    target["selection_catalog"]["default_model"] = "model"
    order = ["codex:subscription_login"]
    monkeypatch.setattr(account_registrations, "resolve_role", lambda name: ({"backend_priority": order}, None))
    monkeypatch.setattr(account_registrations, "registrations", lambda provider: [])
    monkeypatch.setattr(account_selection, "observations", lambda: [target])
    record = {"backend": "provider_api", "backend_route_id": "api", "execution_settings": {"account_selection": "auto"}}
    assert defaults.configuration(record, {})["backend"] == "codex"
    order.clear()
    assert defaults.configuration(record, {})["backend"] == "provider_api"
