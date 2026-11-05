"""Quota-switch preference is Resident policy, not a Provider parameter.

:module: resident.session
:design: decision_ccbb1cac24aa, decision_d40f59f5508f
"""
from types import SimpleNamespace

import pytest

from management_portal.execution import resident_runtime_settings
from management_portal.society_service import (
    resident_catalog, resident_execution_domain, resident_runtime_domain,
    resident_restart_domain, resident_runtime_payload, resident_settings_contract,
)
from management_portal.society_service.errors import LineageConflictError
from tests.cases.resident_execution_test_support import resident_record  # noqa: F401


def test_quota_setting_roundtrip_cas_and_no_provider_io(resident_record, monkeypatch):
    from management_portal.execution.provider_catalog import AGENT_PROVIDERS
    def unexpected(*args, **kwargs):
        pytest.fail("Changing quota policy must not contact a Provider")
    adapter = type(AGENT_PROVIDERS["codex"].runtime_adapter())
    monkeypatch.setattr(adapter, "validate_updates", unexpected)
    rid = resident_record["resident_id"]
    read = lambda: resident_execution_domain.read_resident_runtime_settings(rid, "CASE-A")
    initial = read()
    assert "auto_switch_on_quota" not in initial["runtime_settings"]
    for enabled in (True, False):
        before = read()
        result = resident_execution_domain.change_resident_runtime_settings(rid, "CASE-A", {
            "expected_revision": before["settings_revision"],
            "runtime_settings": {"auto_switch_on_quota": enabled},
        })
        assert result["runtime_settings"]["auto_switch_on_quota"] is enabled
        assert result["quota_switch"]["auto_switch_on_quota"] is enabled
        assert result["next_turn_fields"] == []
        assert resident_catalog.get_catalog().state_store.read_resident_execution_settings(rid)["auto_switch_on_quota"] is enabled
        assert read()["settings_revision"] == result["settings_revision"]
    with pytest.raises(LineageConflictError):
        resident_execution_domain.change_resident_runtime_settings(rid, "CASE-A", {
            "expected_revision": initial["settings_revision"], "runtime_settings": {"auto_switch_on_quota": True},
        })


@pytest.mark.parametrize("value", [None, 0, 1, "false", "true", {}])
def test_quota_setting_rejects_non_boolean(value):
    for normalize in (resident_runtime_payload.runtime_settings_updates_from_payload,
                      resident_runtime_settings.normalize_settings,
                      resident_settings_contract.split_runtime_settings):
        with pytest.raises(ValueError, match="auto_switch_on_quota"):
            normalize({"auto_switch_on_quota": value})


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("provider", ["claude", "provider_api"])
def test_quota_setting_survives_cross_provider_target(enabled, provider, monkeypatch):
    # Exercise the actual target projection while isolating catalog and session I/O.
    monkeypatch.setattr(resident_runtime_domain, "read_resident_runtime_settings",
                        lambda _: {"auto_switch_on_quota": enabled, "model": "source"})
    # Use the common native adapter via the actual Provider definition.
    from management_portal.execution.provider_catalog import AGENT_PROVIDERS
    definition = AGENT_PROVIDERS[provider] if provider != "provider_api" else None
    if definition:
        monkeypatch.setattr(type(definition), "catalog", lambda *args: {"models": ["target"], "default_model": "target"})
    monkeypatch.setattr(resident_runtime_domain, "resident_runtime_settings_from_register_payload",
                        lambda record, payload, actor, now: resident_runtime_settings.normalize_settings(payload["runtime_settings"]))
    target = SimpleNamespace(definition=definition, provider=provider, route="test", source_provider="codex")
    result = resident_restart_domain._target_runtime_settings({}, {}, target, "CASE-A")
    assert result["auto_switch_on_quota"] is enabled


def test_quota_setting_merge_preserves_absence_and_false():
    assert "auto_switch_on_quota" not in resident_runtime_settings.merge_settings({}, {"model": "a"})
    merged = resident_runtime_settings.merge_settings({"auto_switch_on_quota": True}, {"auto_switch_on_quota": False})
    execution, harness = resident_settings_contract.split_runtime_settings(merged)
    assert resident_settings_contract.runtime_settings_from_parts(execution, harness)["auto_switch_on_quota"] is False
