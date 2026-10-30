""":module: platform.core

Portal access and proxy settings use isolated configuration files.
"""
from concurrent.futures import ThreadPoolExecutor
import importlib
import socket
import threading
from types import SimpleNamespace

import pytest


@pytest.fixture
def network(tmp_path, monkeypatch):
    module = importlib.import_module("management_common.portal_network")
    for key in module.PROXY_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PORTAL_NETWORK_CONFIG", str(tmp_path / "network.env"))
    legacy = tmp_path / "legacy.env"
    legacy.write_text('HTTPS_PROXY="http://legacy.invalid:3128"\nNO_PROXY="example.invalid"\n')
    monkeypatch.setenv("PORTAL_PROVIDER_EGRESS_ENV_FILE", str(legacy))
    return module


def save(network, **changes):
    return network.update_settings({"version": network.read_settings()["version"], **changes})


def test_new_install_is_local_and_legacy_policy_is_preserved_until_explicit_save(network):
    path = network.config_path()
    state = network.read_settings()
    assert not path.exists()
    assert state["access"]["mode"] == "local"
    assert state["access"]["port"] == 18084
    assert state["access"]["urls"] == ["http://127.0.0.1:18084"]
    assert state["proxy"]["https"] == "http://legacy.invalid:3128"
    network.initialize_allowed_networks(["10.12.3.4/24", "2001:db8::/32"])
    original = path.read_bytes()
    assert path.stat().st_mode & 0o777 == 0o600
    assert b"PROXY" not in original
    assert network.read_settings()["access"]["mode"] == "remote"
    assert network.read_settings()["access"]["legacy_restriction"]
    assert network.allowed_networks() == ["10.12.3.0/24", "2001:db8::/32", "127.0.0.0/8", "::1/128"]
    network.initialize_allowed_networks(["0.0.0.0/0"])
    assert path.read_bytes() == original
    local = save(network, access={"mode": "local", "port": 18084})
    assert local["access"]["mode"] == "local"
    assert not local["access"]["legacy_restriction"]
    assert network.allowed_networks() == list(network.LOOPBACK_NETWORKS)
    assert b"PORTAL_ALLOWED_NETWORKS" not in path.read_bytes()
    remote = save(network, access={"mode": "remote", "port": 18084})
    assert remote["access"]["mode"] == "remote"
    assert set(network.allowed_networks()) == {"0.0.0.0/0", "::/0"}


@pytest.mark.parametrize("change", [
    {"access": {"mode": "other", "port": 18084}},
    {"access": {"mode": "remote", "port": 80}},
    {"access": {"mode": "remote", "port": 65536}},
    {"access": {"mode": "remote", "port": True}},
    {"access": {"mode": "remote", "port": "18084"}},
    {"access": {"mode": "remote"}},
    {"proxy": {"http": "socks5://localhost:1080", "https": "", "all": "", "no_proxy": ""}},
    {"proxy": {"http": "http://host:99999", "https": "", "all": "", "no_proxy": ""}},
    {"proxy": {"http": "http://host/path", "https": "", "all": "", "no_proxy": ""}},
    {"proxy": {"http": "http://host\nX=1", "https": "", "all": "", "no_proxy": ""}},
    {"proxy": {"https": "http://host"}}, {"unexpected": True},
    {"allowed_networks": ["0.0.0.0/0"]},
    {"access": {"mode": "local", "port": 18084}, "proxy": {}},
])
def test_invalid_changes_do_not_write(network, change):
    before = network.config_path().read_bytes() if network.config_path().exists() else b""
    with pytest.raises(ValueError):
        save(network, **change)
    after = network.config_path().read_bytes() if network.config_path().exists() else b""
    assert after == before


def test_port_conflict_does_not_commit(network):
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        port = occupied.getsockname()[1]
        with pytest.raises(ValueError, match="暂不可用"):
            save(network, access={"mode": "local", "port": port})
    assert not network.config_path().exists()


def test_proxy_roundtrip_group_isolation_and_conflict(network):
    first = network.read_settings()
    proxy = {"http": "http://user:p%24ss@proxy.invalid:8080", "https": "https://[::1]:8443",
             "all": "http://fallback.invalid", "no_proxy": "example.invalid,10.0.0.0/8"}
    updated = save(network, proxy=proxy)
    assert updated["proxy_source"] == "saved"
    assert updated["access"] == first["access"]
    assert updated["proxy"]["http"] == proxy["http"]
    assert {"localhost", "127.0.0.1", "::1"} <= set(updated["proxy"]["no_proxy"].split(","))
    assert updated["activation"] == {"access": "router_restart", "proxy": "new_processes"}
    with pytest.raises(network.VersionConflict):
        network.update_settings({"version": first["version"], "access": {"mode": "remote", "port": 18084}})
    remote = save(network, access={"mode": "remote", "port": 18084})
    assert remote["proxy"] == updated["proxy"]
    save(network, proxy={"http": "", "https": "", "all": "", "no_proxy": ""})
    env = network.saved_proxy_environment()
    assert env["HTTPS_PROXY"] == env["https_proxy"] == ""
    assert env["HTTP_PROXY"] == env["ALL_PROXY"] == ""


def test_same_version_concurrent_updates_have_one_winner(network):
    version = network.read_settings()["version"]
    barrier = threading.Barrier(2)
    def update(mode):
        barrier.wait()
        try:
            return network.update_settings({"version": version, "access": {"mode": mode, "port": 18084}})
        except network.VersionConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ["local", "remote"]))
    assert len([item for item in results if item is not None]) == 1


def test_router_applies_access_mode_and_invalid_policy_fails_closed(network):
    from management_portal.portal_deployment_router import PortalRouterServer
    network.initialize_allowed_networks(["10.0.0.0/8"])
    server = PortalRouterServer(("127.0.0.1", 0), None, {}, [], 1, network_settings_path=network.config_path())
    try:
        assert server.verify_request(None, ("10.1.2.3", 123))
        assert not server.verify_request(None, ("192.168.1.1", 123))
        save(network, access={"mode": "remote", "port": 18084})
        assert server.verify_request(None, ("192.168.1.1", 123))
        save(network, access={"mode": "local", "port": 18084})
        assert not server.verify_request(None, ("192.168.1.1", 123))
        assert server.verify_request(None, ("127.0.0.1", 123))
        network.config_path().write_text('PORTAL_ACCESS_MODE="bad"\n')
        assert not server.verify_request(None, ("192.168.1.1", 123))
        assert server.verify_request(None, ("127.0.0.1", 123))
    finally:
        server.server_close()


def test_router_listener_uses_saved_mode_and_port_over_old_unit_arguments(network):
    from management_portal.portal_deployment_router import _listener_settings
    old_unit = SimpleNamespace(bind=["0.0.0.0"], port=18084, allow_network=["10.0.0.0/8"])
    binds, port, networks = _listener_settings(old_unit)
    assert binds == ["0.0.0.0"] and port == 18084
    assert any(str(item) == "10.0.0.0/8" for item in networks)
    save(network, access={"mode": "local", "port": 19084})
    binds, port, networks = _listener_settings(old_unit)
    assert binds == ["127.0.0.1"] and port == 19084
    assert {str(item) for item in networks} == set(network.LOOPBACK_NETWORKS)
    save(network, access={"mode": "remote", "port": 19084})
    binds, port, networks = _listener_settings(old_unit)
    assert binds == ["0.0.0.0"] and port == 19084
    assert {str(item) for item in networks} == {"0.0.0.0/0", "::/0"}


def test_admin_http_requires_admin_and_refuses_network_changes(network, monkeypatch):
    from management_portal import http_admin
    actor = None
    body = {}
    admin = http_admin.AdminHttp(respond=lambda request, status, value: (status, value),
                                session_actor=lambda _: actor, access_payload=lambda _: {},
                                read_json=lambda _: body, is_admin=lambda a: a == "admin")
    assert admin.network_get(None, None, "admin")[0] == 403
    actor = "ordinary"
    assert admin.network_post(None, None, "admin")[0] == 403
    actor = "admin"
    status, initial = admin.network_get(None, None, None)
    assert status == 200
    body = {"version": initial["version"], "access": {"mode": "remote", "port": 18084}}
    status, response = admin.network_post(None, None, None)
    assert status == 409 and response["supported"] is False
    assert network.read_settings() == initial


def test_changed_port_is_used_by_runtime_registry(network, monkeypatch):
    from management_common import site_registry
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
    save(network, access={"mode": "local", "port": port})
    assert site_registry.get_runtime_config()["portal"]["port"] == port
    assert all(item["entry_port"] == port for item in site_registry.get_module_registry())


def test_unit_environment_file_is_last_and_optional(network, tmp_path, monkeypatch):
    from scripts import portal_instance_storage as storage
    monkeypatch.setattr(storage, "site_environment", lambda deployment=None: {"PORTAL_NETWORK_CONFIG": str(network.config_path())})
    source = tmp_path / "source.service"
    source.write_text('[Service]\nEnvironmentFile=/legacy.env\nExecStart=/bin/true\n[Install]\nWantedBy=default.target\n')
    rendered = storage.render_unit(source)
    directive = "EnvironmentFile=-" + str(network.config_path())
    assert rendered.index("EnvironmentFile=/legacy.env") < rendered.index(directive) < rendered.index("[Install]")
    assert not network.config_path().exists()


def test_native_and_login_use_explicit_proxy_or_direct_without_leaking_credentials(network, monkeypatch, tmp_path):
    from management_portal.execution import account_login
    monkeypatch.setattr(account_login, "account_home", lambda *a: tmp_path / "account")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "do-not-inherit")
    assert account_login.login_environment("codex", "test")["HTTPS_PROXY"] == "http://legacy.invalid:3128"
    proxy = {"http": "", "https": "", "all": "http://new.invalid:8080", "no_proxy": ""}
    save(network, proxy=proxy)
    env = account_login.login_environment("codex", "test")
    assert env["ALL_PROXY"] == proxy["all"]
    assert env["HTTPS_PROXY"] == env["HTTP_PROXY"] == proxy["all"]
    assert network.read_settings()["proxy"]["https"] == "", "fallback must not overwrite the editable value"
    assert "ANTHROPIC_API_KEY" not in env
    save(network, proxy={"http": "", "https": "", "all": "", "no_proxy": ""})
    assert account_login.login_environment("codex", "test")["HTTPS_PROXY"] == ""


def test_new_resident_process_uses_saved_proxy_without_changing_identity(network, monkeypatch):
    from management_portal.resident_shell_environment import resident_process_environment
    monkeypatch.setenv("HTTPS_PROXY", "http://old.invalid")
    identity = {"PORTAL_RESIDENT_ID": "resident_123456789abc", "PORTAL_LINEAGE_ID": "lineage_123456789abc"}
    save(network, proxy={"http": "", "https": "http://new.invalid", "all": "", "no_proxy": ""})
    env, actual_identity = resident_process_environment({"resident_environment": identity})
    assert env["HTTPS_PROXY"] == env["https_proxy"] == "http://new.invalid"
    assert actual_identity == identity
    assert all(env[key] == value for key, value in identity.items())
    save(network, proxy={"http": "", "https": "", "all": "", "no_proxy": ""})
    assert resident_process_environment({})[0]["HTTPS_PROXY"] == ""



from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
