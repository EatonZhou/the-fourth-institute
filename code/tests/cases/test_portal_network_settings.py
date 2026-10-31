""":module: platform.core

Portal access and proxy settings use isolated configuration files.
"""
import importlib
from pathlib import Path
import socket
import subprocess
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


def configure(network, **entries):
    """Write the network file as the installation's target configuration does."""
    network.config_path().write_text("".join(f'{key}="{value}"\n' for key, value in entries.items()))


def configure_proxy(network, *, http="", https="", all="", no_proxy=""):
    """A saved proxy: the effective variables, with each protocol falling back to ALL_PROXY, and the fields as set."""
    fields = {"HTTP_PROXY": http, "HTTPS_PROXY": https, "ALL_PROXY": all, "NO_PROXY": no_proxy}
    effective = {key: value or (all if key in {"HTTP_PROXY", "HTTPS_PROXY"} else "") for key, value in fields.items()}
    configure(network, **effective, **{key.lower(): value for key, value in effective.items()},
              **{"PORTAL_NETWORK_" + key: value for key, value in fields.items()})


def test_new_install_is_local_and_legacy_networks_are_kept_until_a_mode_is_set(network):
    path = network.config_path()
    state = network.read_settings()
    assert not path.exists()
    assert state["access"]["mode"] == "local"
    assert state["access"]["port"] == 18084
    assert state["access"]["urls"] == ["http://127.0.0.1:18084"]
    assert state["proxy"]["https"] == "http://legacy.invalid:3128"
    assert state["proxy_source"] == "environment"
    network.initialize_allowed_networks(["10.12.3.4/24", "2001:db8::/32"])
    original = path.read_bytes()
    assert path.stat().st_mode & 0o777 == 0o600
    assert b"PROXY" not in original
    assert network.read_settings()["access"]["mode"] == "remote"
    assert network.read_settings()["access"]["legacy_restriction"]
    assert network.allowed_networks() == ["10.12.3.0/24", "2001:db8::/32", "127.0.0.0/8", "::1/128"]
    network.initialize_allowed_networks(["0.0.0.0/0"])
    assert path.read_bytes() == original
    configure(network, PORTAL_ACCESS_MODE="local", PORTAL_PORT="18084")
    local = network.read_settings()
    assert local["access"]["mode"] == "local" and not local["access"]["legacy_restriction"]
    assert network.allowed_networks() == list(network.LOOPBACK_NETWORKS)
    configure(network, PORTAL_ACCESS_MODE="remote", PORTAL_PORT="18084")
    assert network.read_settings()["access"]["mode"] == "remote"
    assert set(network.allowed_networks()) == {"0.0.0.0/0", "::/0"}


@pytest.mark.parametrize("entries", [{"PORTAL_ACCESS_MODE": "other"}, {"PORTAL_PORT": "80"}, {"PORTAL_PORT": "65536"}])
def test_invalid_saved_access_is_an_error(network, entries):
    configure(network, **entries)
    with pytest.raises(ValueError):
        network.read_settings()


def test_saved_proxy_is_read_back_with_its_source(network):
    configure_proxy(network, http="http://user:p%24ss@proxy.invalid:8080", https="https://[::1]:8443",
                    all="http://fallback.invalid", no_proxy="example.invalid,10.0.0.0/8")
    settings = network.read_settings()
    assert settings["proxy_source"] == "saved"
    assert settings["proxy"] == {"http": "http://user:p%24ss@proxy.invalid:8080", "https": "https://[::1]:8443",
                                 "all": "http://fallback.invalid", "no_proxy": "example.invalid,10.0.0.0/8"}
    assert set(settings) == {"access", "proxy", "proxy_source"}
    configure_proxy(network)
    env = network.saved_proxy_environment()
    assert env["HTTPS_PROXY"] == env["https_proxy"] == ""
    assert env["HTTP_PROXY"] == env["ALL_PROXY"] == ""


def test_router_applies_access_mode_and_invalid_policy_fails_closed(network):
    from management_portal.portal_deployment_router import PortalRouterServer
    network.initialize_allowed_networks(["10.0.0.0/8"])
    server = PortalRouterServer(("127.0.0.1", 0), None, {}, [], 1, network_settings_path=network.config_path())
    try:
        assert server.verify_request(None, ("10.1.2.3", 123))
        assert not server.verify_request(None, ("192.168.1.1", 123))
        configure(network, PORTAL_ACCESS_MODE="remote", PORTAL_PORT="18084")
        assert server.verify_request(None, ("192.168.1.1", 123))
        configure(network, PORTAL_ACCESS_MODE="local", PORTAL_PORT="18084")
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
    configure(network, PORTAL_ACCESS_MODE="local", PORTAL_PORT="19084")
    binds, port, networks = _listener_settings(old_unit)
    assert binds == ["127.0.0.1"] and port == 19084
    assert {str(item) for item in networks} == set(network.LOOPBACK_NETWORKS)
    configure(network, PORTAL_ACCESS_MODE="remote", PORTAL_PORT="19084")
    binds, port, networks = _listener_settings(old_unit)
    assert binds == ["0.0.0.0"] and port == 19084
    assert {str(item) for item in networks} == {"0.0.0.0/0", "::/0"}


def test_admin_http_shows_network_settings_to_administrators_only(network):
    from management_portal import http_admin
    actor = None
    admin = http_admin.AdminHttp(respond=lambda request, status, value: (status, value),
                                session_actor=lambda _: actor, access_payload=lambda _: {},
                                read_json=lambda _: {}, is_admin=lambda a: a == "admin")
    assert admin.network_get(None, None, "admin")[0] == 403
    actor = "ordinary"
    assert admin.network_get(None, None, "admin")[0] == 403
    actor = "admin"
    assert admin.network_get(None, None, None) == (200, network.read_settings())
    # The installation owns these settings: there is no route that changes them.
    assert not any(route.path == "/api/admin/network" and route.method != "GET" for route in admin.routes())


def test_changed_port_is_used_by_runtime_registry(network, monkeypatch):
    from management_common import site_registry
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
    configure(network, PORTAL_ACCESS_MODE="local", PORTAL_PORT=str(port))
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
    configure_proxy(network, all="http://new.invalid:8080")
    env = account_login.login_environment("codex", "test")
    assert env["ALL_PROXY"] == "http://new.invalid:8080"
    assert env["HTTPS_PROXY"] == env["HTTP_PROXY"] == "http://new.invalid:8080"
    assert network.read_settings()["proxy"]["https"] == "", "the ALL_PROXY fallback is not shown as the HTTPS field"
    assert "ANTHROPIC_API_KEY" not in env
    configure_proxy(network)
    assert account_login.login_environment("codex", "test")["HTTPS_PROXY"] == ""


def test_new_resident_process_uses_saved_proxy_without_changing_identity(network, monkeypatch):
    from management_portal.resident_shell_environment import resident_process_environment
    monkeypatch.setenv("HTTPS_PROXY", "http://old.invalid")
    identity = {"PORTAL_RESIDENT_ID": "resident_123456789abc", "PORTAL_LINEAGE_ID": "lineage_123456789abc"}
    configure_proxy(network, https="http://new.invalid")
    env, actual_identity = resident_process_environment({"resident_environment": identity})
    assert env["HTTPS_PROXY"] == env["https_proxy"] == "http://new.invalid"
    assert actual_identity == identity
    assert all(env[key] == value for key, value in identity.items())
    configure_proxy(network)
    assert resident_process_environment({})[0]["HTTPS_PROXY"] == ""


def test_management_page_shows_network_settings_read_only():
    """Portal Management 的网络设置只读展示

    jsdom 中挂载管理页：打开网络设置分栏才读取一次，显示访问范围、端口、访问地址与代理，空值显示“未设置”；
    没有表单、可编辑字段或保存按钮；非管理员不能打开；读取失败报告错误，重新读取后显示；读取中禁用重新读取。

    :module: platform.core
    """
    result = subprocess.run(
        ["node", "--experimental-vm-modules", "--test",
         str(Path(__file__).with_name("javascript") / "portal_management_network.cjs")],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
