"""A controlled test session must satisfy its configured account category."""
import json
from types import SimpleNamespace

import pytest

from management_portal.test_framework import compatibility, dev_target
from management_portal.test_framework.tools import provision_portal_test_profile as profile


@pytest.fixture
def identity_config(monkeypatch):
    config = {"actor": "tau-member", "actor_category": "member"}
    monkeypatch.setattr(compatibility, "read_site_config", lambda: {"deployment": {"test_framework": config}})
    return config


@pytest.mark.parametrize("change", [
    {"category": "portal_admin"}, {"category": "intern"},
    {"configured": False}, {"status": "disabled"},
])
def test_provision_rejects_wrong_account_before_issuing(tmp_path, monkeypatch, identity_config, change):
    user = {"configured": True, "status": "active", "category": "member", **change}
    calls = []
    monkeypatch.setattr(profile, "resolve_unique_dev", lambda _: SimpleNamespace(deployment_id="dev"))
    monkeypatch.setattr(profile, "get_portal_access_store", lambda: SimpleNamespace(user=lambda _: user))
    monkeypatch.setattr(profile, "_issue_session_token", lambda actor: calls.append(actor) or "private")
    output = tmp_path / "profile.json"
    output.write_text("existing profile")
    with pytest.raises(PermissionError):
        profile.provision(output)
    assert calls == []
    assert output.read_text() == "existing profile"


def test_provision_uses_current_config_and_keeps_cookie_private(tmp_path, monkeypatch, identity_config):
    calls, audits = [], []
    user = {"configured": True, "status": "active", "category": "member"}
    monkeypatch.setattr(profile, "resolve_unique_dev", lambda _: SimpleNamespace(deployment_id="dev", origin="http://127.0.0.1:28399"))
    monkeypatch.setattr(profile, "get_portal_access_store", lambda: SimpleNamespace(user=lambda _: user, audit=lambda *a, **kw: audits.append(kw)))
    monkeypatch.setattr(profile, "_issue_session_token", lambda actor: calls.append(actor) or "private-cookie")
    monkeypatch.setattr(profile, "_verify_session_token", lambda _: identity_config["actor"])
    output = tmp_path / "profile.json"
    for actor in ("tau-member", "tau-member-next"):
        identity_config["actor"] = actor
        result = profile.provision(output)
        assert result["account"] == actor
        assert result["user_category"] == "member"
        assert "private-cookie" not in json.dumps([result, audits])
    assert calls == ["tau-member", "tau-member-next"]
    assert output.stat().st_mode & 0o777 == 0o600
    assert json.loads(output.read_text())["cookies"][0]["value"] == "private-cookie"


@pytest.fixture
def target(tmp_path, monkeypatch, identity_config):
    target_file = tmp_path / "target.json"
    target_file.write_text(json.dumps({"schema": "tfi.portal-test-target.v1", "deployment": {
        "id": "dev", "kind": "dev", "label": "test", "origin": "http://127.0.0.1:28399", "build_id": "candidate"}}))
    monkeypatch.setenv("PORTAL_TEST_TARGET_FILE", str(target_file))
    storage = tmp_path / "profile.json"
    storage.write_text(json.dumps({"cookies": [{"name": "session", "value": "private", "domain": "127.0.0.1"}]}))
    me = {"username": "tau-member", "access": {"user_category": "member", "status": "active"},
          "portal_deployment": "dev", "build_id": "candidate"}
    replies = {"/api/health": {"deployment": "dev", "build_id": "candidate"},
               "/development?view=test": {},
               "/development/native-views?view=test": {"html": '<section data-test-workbench-version="component-v2"></section>'},
               "/api/me": me}
    def get(url, _headers):
        return json.dumps(replies['/' + url.split('/', 3)[-1]]).encode()
    return dev_target.DevTargetVerifier(http_get=get), storage, me


@pytest.mark.parametrize("category", ["portal_admin", "intern", None])
def test_preflight_rechecks_category_on_each_use(target, category):
    verifier, storage, me = target
    assert verifier.verify(storage).actor == "tau-member"
    me["access"]["user_category"] = category
    with pytest.raises(dev_target.DevTargetError, match="类别"):
        verifier.verify(storage)


def test_preflight_records_category_and_rejects_disabled(target):
    verifier, storage, me = target
    assert verifier.verify(storage).result_details()["user_category"] == "member"
    me["access"]["status"] = "disabled"
    with pytest.raises(dev_target.DevTargetError, match="启用"):
        verifier.verify(storage)


def test_preflight_does_not_share_observation_across_identity_requirements(target, identity_config):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    verifier, storage, _me = target
    entered, release = threading.Event(), threading.Event()
    original = verifier.http_get
    first_thread = []
    def get(url, headers):
        if not first_thread:
            first_thread.append(threading.get_ident())
            entered.set()
            assert release.wait(5)
        return original(url, headers)
    verifier.http_get = get
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(verifier.verify, storage)
        assert entered.wait(5)
        identity_config["actor_category"] = "portal_admin"
        second = pool.submit(verifier.verify, storage)
        try:
            with pytest.raises(dev_target.DevTargetError, match="类别"):
                second.result(timeout=3)
        finally:
            release.set()
        assert first.result(timeout=3).user_category == "member"


def test_unspecified_category_keeps_existing_account_contract(target, identity_config):
    verifier, storage, me = target
    identity_config.pop("actor_category")
    me["access"]["user_category"] = "portal_admin"
    assert verifier.verify(storage).user_category == "portal_admin"


@pytest.mark.parametrize("category", ["", "admin", [], False])
def test_invalid_category_configuration_fails_closed(identity_config, category):
    identity_config["actor_category"] = category
    with pytest.raises(ValueError):
        compatibility.test_actor()
