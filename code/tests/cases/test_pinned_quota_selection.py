"""Pinned subscription recovery preserves model and excludes unknown quota."""
from copy import deepcopy

from management_portal.execution.account_selection import same_model_candidates
from management_portal.society_service.resident_account_policy import fallback_account
from tests.cases.test_account_selection import account, api_account


def test_highest_limiting_window_and_stable_tie():
    source, busy, fuller = account("source", 0), account("busy", 90), account("fuller", 80)
    busy["quota_reserve"]["windows"][1]["remaining_percent"] = 10
    assert same_model_candidates([source, busy, fuller], source, model="model", effort="high")[0] == fuller
    tie = account("tie", 80)
    assert same_model_candidates([tie, fuller], source, model="model")[0] == fuller


def test_scope_and_evidence_are_required():
    source, good = account("source", 0), account("good", 70)
    invalid = []
    for field, value in [("enabled", False), ("login_state", "unknown"), ("provider", "claude")]:
        row = deepcopy(good)
        row[field] = value
        invalid.append(row)
    for field in ["quota_reserve", "selection_catalog"]:
        row = deepcopy(good)
        row[field]["fresh"] = False
        invalid.append(row)
    invalid.append(api_account())
    assert same_model_candidates(invalid, source, model="model", effort="high") == []
    assert fallback_account([source, good, *invalid], "codex", "source", [],
                            model="model", effort="high") == good
    assert fallback_account([source, good], "codex", "source", [], model="missing") == {}


def test_fixed_switch_does_not_use_site_priority(monkeypatch):
    from management_portal.society_service.family_quota import auto_migration_priority
    assert auto_migration_priority({"execution_settings": {
        "auto_switch_on_quota": True, "account_selection": "fixed", "quota_switch_strategy":"same_provider_model"}}) == []


def test_legacy_fixed_true_has_no_emergency_order(monkeypatch):
    from management_portal.society_service import family_quota, resident_default_backend
    monkeypatch.setattr(resident_default_backend,'selection_priority',lambda record:['legacy-site-order'])
    for strategy in ({},{'quota_switch_strategy':'legacy'}):
        record={'execution_settings':{'auto_switch_on_quota':True,'account_selection':'fixed',**strategy}}
        assert family_quota.auto_migration_priority(record)==[]


def test_retired_strategy_is_dropped_from_old_records():
    from management_portal.society_service.resident_settings_contract import runtime_settings_from_parts
    settings = runtime_settings_from_parts({"quota_switch_strategy": "legacy"}, {})
    assert "quota_switch_strategy" not in settings
