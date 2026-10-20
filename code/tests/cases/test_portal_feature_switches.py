"""Portal 管理的 Lineage 开关应修改真实组件暂停状态。"""

import pytest
from urllib.parse import urlsplit

from management_portal import portal_feature_switches as switches
from management_portal.http_admin import AdminHttp


def _site():
    return {"deployment": {
        "development_projection": {"lineage_id": "lineage_dev", "architecture_audit_effect_id": "effect_audit"},
        "pulse": {"lineage_id": "lineage_system", "event_source_ids": ["source_daily", "source_manual"]},
    }}


def test_architecture_switch_pauses_the_effect(monkeypatch):
    monkeypatch.setattr(switches, "read_site_config", _site)
    state = {"effect_audit": False}
    calls = []

    def views(lineage_id, actor, *, component_id):
        assert (lineage_id, actor) == ("lineage_dev", "admin")
        return [{"kind": "effect", "component_id": component_id, "paused": state[component_id]}]

    def set_paused(lineage_id, component_id, paused, actor):
        calls.append((lineage_id, component_id, paused, actor))
        state[component_id] = paused

    monkeypatch.setattr(switches.component_runtime, "views", views)
    monkeypatch.setattr(switches.component_runtime, "set_paused", set_paused)
    assert switches.set_enabled("architecture_audit", False, "admin")["enabled"] is False
    assert calls == [("lineage_dev", "effect_audit", True, "admin")]
    assert switches.set_enabled("architecture_audit", True, "admin")["enabled"] is True


def test_pulse_switch_restores_prior_state_on_partial_failure(monkeypatch):
    monkeypatch.setattr(switches, "read_site_config", _site)
    state = {"source_daily": False, "source_manual": False}
    monkeypatch.setattr(switches.component_runtime, "views", lambda _lineage, _actor, *, component_id:
                        [{"kind": "event_source", "component_id": component_id, "paused": state[component_id]}])
    state["source_daily"] = True
    assert switches.status("pulse", "admin") == {"enabled": True, "partial": True,
        "components": [{"id": "source_daily", "paused": True}, {"id": "source_manual", "paused": False}]}
    state["source_daily"] = False

    def set_paused(_lineage, component_id, paused, _actor):
        if component_id == "source_manual" and paused:
            raise RuntimeError("cannot pause manual source")
        state[component_id] = paused

    monkeypatch.setattr(switches.component_runtime, "set_paused", set_paused)
    with pytest.raises(RuntimeError, match="cannot pause"):
        switches.set_enabled("pulse", False, "admin")
    assert state == {"source_daily": False, "source_manual": False}


def test_invalid_switch_and_non_boolean_payload(monkeypatch):
    monkeypatch.setattr(switches, "read_site_config", _site)
    with pytest.raises(ValueError, match="未知"):
        switches.status("wall", "admin")
    with pytest.raises(ValueError, match="布尔"):
        switches.set_enabled("pulse", 0, "admin")


def test_admin_api_requires_admin_and_accepts_only_named_boolean_switch(monkeypatch):
    actor = ["viewer"]
    payload = [{"name": "pulse", "enabled": False}]
    changed = []
    monkeypatch.setattr(switches, "status", lambda name, user: {"enabled": True, "name": name, "actor": user})
    monkeypatch.setattr(switches, "set_enabled", lambda name, enabled, user:
                        changed.append((name, enabled, user)) or {"enabled": enabled})
    admin = AdminHttp(respond=lambda _request, code, value: (code, value),
                      session_actor=lambda _request: actor[0], access_payload=lambda _user: {},
                      read_json=lambda _request: payload[0], is_admin=lambda user: user == "admin")
    parsed = urlsplit("/api/admin/feature-switch?name=pulse")
    assert admin.feature_switch_get(None, parsed, None)[0] == 403
    assert admin.feature_switch_post(None, parsed, None)[0] == 403
    actor[0] = "admin"
    assert admin.feature_switch_get(None, parsed, None) == (200, {"enabled": True, "name": "pulse", "actor": "admin"})
    payload[0] = {"name": "pulse", "enabled": False, "extra": True}
    assert admin.feature_switch_post(None, parsed, None)[0] == 400
    payload[0] = {"name": "pulse", "enabled": False}
    assert admin.feature_switch_post(None, parsed, None) == (200, {"enabled": False})
    assert changed == [("pulse", False, "admin")]
