"""Real local Codex protocol startup, with no credentials or model execution.

:module: execution.providers
:profiles: local.fixture
:risk: no-external-side-effects
"""
import os
from pathlib import Path
import threading
import time

import pytest

from management_portal import codex_app_server_host as host


def test_macos_endpoint_byte_limit_and_isolation(tmp_path, monkeypatch):
    short = Path("/tmp/short.sock")
    assert host.unix_socket_path(short, platform="darwin") == short
    # The limit applies to encoded bytes, not characters.
    long = Path("/tmp") / ("中" * 35) / "codex.sock"
    mapped = host.unix_socket_path(long, platform="darwin")
    assert len(os.fsencode(mapped)) < 104
    assert mapped != host.unix_socket_path(long.with_name("other.sock"), platform="darwin")
    monkeypatch.setenv("TMPDIR", str(tmp_path / "another-temp-root"))
    assert mapped == host.unix_socket_path(long, platform="darwin")
    assert host.unix_socket_path(long, platform="linux") == long


def test_long_endpoint_rejects_symlinked_private_directory(tmp_path):
    from management_common.file_permissions import PrivateFileError
    logical = tmp_path / ("long" * 40) / "codex.sock"
    physical = host.unix_socket_path(logical, platform="darwin")
    physical.parent.symlink_to(tmp_path, target_is_directory=True)
    try:
        with pytest.raises(PrivateFileError):
            host.connect_transport(logical, platform="darwin")
    finally:
        physical.parent.unlink()


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable and Unix socket")
@pytest.mark.parametrize("long_path", [False, True])
def test_real_codex_start_initialize_catalog_and_cleanup(tmp_path, monkeypatch, installed_provider_paths, long_path):
    from management_common import portal_network
    from management_portal import codex_app_server_client as client_module
    binary = installed_provider_paths["codex_binary"]
    if not binary or not Path(binary).is_file():
        pytest.skip("Codex CLI is not installed")
    logical = tmp_path / ("实例" * 40 if long_path else "short") / "codex.sock"
    physical = host.unix_socket_path(logical, platform="darwin")
    home = tmp_path / "empty-account"
    home.mkdir()
    # Never pass the invoking Resident's auth, proxy or provider environment.
    clean = {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(tmp_path / "home"),
             "LANG": "C.UTF-8", "CODEX_HOME": str(home)}
    actual_spawn = host.spawn_process
    def spawn(command, **kwargs):
        return actual_spawn(command, **{**kwargs, "env": clean})
    monkeypatch.setattr(host, "spawn_process", spawn)
    monkeypatch.setattr(portal_network, "saved_proxy_environment", lambda: {})
    monkeypatch.setattr("management_portal.codex_app_server_rpc.connect_transport", lambda path: host.connect_transport(path, platform="darwin"))
    stop, errors = threading.Event(), []
    def run():
        try:
            host.run_host(logical, binary, home, stop, platform="darwin", startup_timeout=10)
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=run)
    client = client_module.CodexAppServerClient(logical, request_timeout_seconds=5)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not physical.exists() and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.02)
        assert physical.exists(), errors
        assert not errors
        models = client.request("model/list", {})
        assert models["data"]
        account = client.request("account/read", {})
        assert account.get("account") is None
    finally:
        client.close()
        stop.set()
        thread.join(15)
    assert not thread.is_alive()
    assert not errors
    assert not physical.exists()
    if physical != logical:
        physical.parent.rmdir()
