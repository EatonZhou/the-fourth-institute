""":module: platform.core

Portal access and proxy settings use isolated configuration files.
"""
import importlib
import json
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
    from tests.cases.instance_test_support import write_instance
    root = write_instance(tmp_path / 'instance')
    monkeypatch.setenv('PORTAL_INSTANCE_ROOT', str(root))
    private = root / 'secrets/host'
    private.mkdir(parents=True)
    (private / 'provider-egress.env').write_text('HTTPS_PROXY="http://host-egress.invalid:3128"\nNO_PROXY="example.invalid"\n')
    document = json.loads((root / 'instance.json').read_text())
    document['network']['entry_port'] = 18084
    document['deployments']['release']['web_port'] = 18084
    (root / 'instance.json').write_text(json.dumps(document))
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


def configure_listener(network, *, bind='127.0.0.1', port=18084):
    path = network.load().root / 'instance.json'
    document = json.loads(path.read_text())
    document['network'].update(bind=bind, entry_port=port)
    document['deployments']['release']['web_port'] = port
    path.write_text(json.dumps(document))


def test_selected_instance_is_local_and_host_egress_is_used(network):
    assert not network.config_path().exists()
    state = network.read_settings()
    assert state['access'] == {'mode': 'local', 'port': 18084, 'urls': ['http://127.0.0.1:18084']}
    assert network.inherited_proxy_environment()['HTTPS_PROXY'] == 'http://host-egress.invalid:3128'
    assert set(network.allowed_networks()) == set(network.LOOPBACK_NETWORKS)


@pytest.mark.parametrize('entries', [{'bind': 'invalid'}, {'port': 1}, {'port': 70000}])
def test_invalid_manifest_network_is_an_error(network, entries):
    configure_listener(network, **entries)
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


def test_router_applies_manifest_policy_and_invalid_policy_fails_closed(network):
    from management_portal.portal_deployment_router import PortalRouterServer
    configure_listener(network, bind='0.0.0.0')
    network.initialize_allowed_networks(['10.0.0.0/8'])
    server = PortalRouterServer(('127.0.0.1', 0), None, {}, [], 1, instance_root=network.load().root)
    try:
        assert server.verify_request(None, ('10.1.2.3', 123))
        assert not server.verify_request(None, ('192.168.1.1', 123))
        network.initialize_allowed_networks(['0.0.0.0/0'])
        assert server.verify_request(None, ('192.168.1.1', 123))
        configure_listener(network, bind='127.0.0.1')
        assert not server.verify_request(None, ('192.168.1.1', 123))
        assert server.verify_request(None, ('127.0.0.1', 123))
        (network.load().root / 'instance.json').write_text('{')
        assert not server.verify_request(None, ('192.168.1.1', 123))
        assert server.verify_request(None, ('127.0.0.1', 123))
    finally:
        server.server_close()


def test_router_listener_uses_manifest_port_and_bind(network):
    from management_portal.portal_deployment_router import _listener_settings
    binds, port, networks = _listener_settings()
    assert binds == ['127.0.0.1'] and port == 18084
    configure_listener(network, bind='0.0.0.0', port=19084)
    binds, port, networks = _listener_settings()
    assert binds == ['0.0.0.0'] and port == 19084
    assert {'0.0.0.0/0', '::/0'} <= {str(item) for item in networks}


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
    configure_listener(network, port=port)
    assert site_registry.get_runtime_config()["portal"]["port"] == port
    assert all(item["entry_port"] == port for item in site_registry.get_module_registry())



def test_native_and_login_use_explicit_proxy_or_direct_without_leaking_credentials(network, monkeypatch, tmp_path):
    from management_portal.execution import account_login
    monkeypatch.setattr(account_login, "account_home", lambda *a: tmp_path / "account")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "do-not-inherit")
    assert account_login.login_environment("codex", "test")["HTTPS_PROXY"] == "http://host-egress.invalid:3128"
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
