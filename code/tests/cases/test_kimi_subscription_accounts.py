"""Native Kimi subscription isolation, public device login and model selection.

:module: execution.providers
:importance: P1
"""
from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace

import pytest

from management_portal import kimi_account_runtime as runtime, kimi_provider_catalog as catalog
from management_portal.execution import account_registrations, kimi_account_login as login


def test_subscription_registration_and_config_preserve_managed_credentials(tmp_path, monkeypatch):
    row = {"account_id": "kimi_sub", "provider": "kimi", "kind": "subscription_login", "config": {}}
    account_registrations._validate(row)
    config = tmp_path / "config.toml"
    content = b'[providers."managed:kimi-code"]\ntype="kimi"\n'
    config.write_bytes(content)
    monkeypatch.setattr(runtime.kimi_portal_tools, "install", lambda home: None)
    monkeypatch.setattr(runtime.kimi_provider_catalog, "profile", lambda _: pytest.fail("Subscription used API credentials"))
    runtime._write_config(row, tmp_path)
    assert config.read_bytes() == content


def test_subscription_catalog_only_exposes_authenticated_managed_models(monkeypatch):
    from management_portal.execution.account_adapters import KimiAccounts
    row = {"account_id": "kimi_sub", "provider": "kimi", "kind": "subscription_login", "route_id": "subscription",
           "max_concurrency": 2, "config": {}}
    auth = {"managed_provider": {"status": "authenticated"}}
    config = {"default_model": "subscription-model", "models": {
        "subscription-model": {"provider": "managed:kimi-code", "max_context_size": 200000},
        "paid-api": {"provider": "custom"}}}
    client = SimpleNamespace(_request=lambda method, path: auth if path == "auth" else config)
    monkeypatch.setattr(runtime, "client_for_route", lambda route: client)
    assert catalog.catalog(row)["models"] == ["subscription-model"]
    accounts = KimiAccounts.__new__(KimiAccounts)
    accounts.entries = [row]
    assert accounts.accounts()[0].billing_account_id == "kimi_sub"
    auth["managed_provider"]["status"] = "expired"
    with pytest.raises(ValueError, match="订阅登录"):
        catalog.catalog(row)


class Store:
    def __init__(self):
        self.session = None
        self.state = "logged_out"
    def get(self, account): return self.session
    def begin(self, account, **kwargs):
        self.session = {"account_id": account, "state": "pending"}
        return self.session
    def transition(self, session, state, **kwargs):
        self.session = {**session, "state": state, **kwargs}
    def observe(self, account, state): self.state = state
    def login_state(self, account): return self.state


def test_device_flow_reuses_pending_code_and_observes_auth_cancel_expiry(monkeypatch):
    store = Store()
    flow = {"status": "pending", "user_code": "AAAA-BBBB", "verification_uri": "https://www.kimi.com/code/authorize_device",
            "expires_at": (login.now() + timedelta(minutes=20)).isoformat()}
    auth = {"managed_provider": {"status": "unauthenticated"}}
    calls = []
    def request(method, path, *args):
        calls.append((method, path))
        if path == "auth": return auth
        if method == "DELETE": flow["status"] = "cancelled"
        return flow
    monkeypatch.setattr(login, "_lock", lambda _: nullcontext())
    monkeypatch.setattr(login, "login_store", lambda: store)
    monkeypatch.setattr(login, "client_for_route", lambda _: SimpleNamespace(_request=request))
    row = {"account_id": "kimi_sub", "route_id": "sub"}
    opened = login.login(row, "open")
    assert opened["session"]["state"] == "awaiting_user"
    assert ("POST", "oauth/login") not in calls
    assert opened["session"]["requires_code_input"] is False
    flow["status"] = "authenticated"
    auth["managed_provider"]["status"] = "authenticated"
    assert login.login(row, "get")["session"]["state"] == "succeeded"
    assert store.state == "logged_in"
    flow["status"] = "pending"
    login.login(row, "open")
    assert login.login(row, "cancel")["session"]["state"] == "failed"
    assert ("DELETE", "oauth/login") in calls
    auth["managed_provider"]["status"] = "expired"
    assert login.login(row, "get")["login_state"] == "login_expired"
    flow.update(status="pending", verification_uri="https://untrusted.example/device")
    with pytest.raises(ValueError, match="authorization URL"):
        login.login(row, "open")


def test_subscription_hook_patch_never_writes_redacted_credentials(monkeypatch, tmp_path):
    calls = []
    def request(method, path, payload=None):
        calls.append((method, path, payload))
        return {"providers": {"managed:kimi-code": {"has_api_key": True}}, "hooks": []}
    monkeypatch.setattr(runtime.kimi_portal_tools, "service_ready", lambda home: False)
    monkeypatch.setattr(runtime.kimi_portal_tools, "install", lambda home: None)
    client = SimpleNamespace(home=tmp_path, _request=request)
    assert runtime._subscription_tools({"kind": "subscription_login"}, client) is client
    assert set(calls[-1][2]) == {"hooks"}
