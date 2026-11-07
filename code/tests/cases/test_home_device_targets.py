from pathlib import Path
from types import SimpleNamespace
import importlib.util
import subprocess

import pytest

from management_portal import agent_status_backend as status


def test_configured_disk_targets_used_by_list_and_lookup(monkeypatch):
    target = {"resource_id": "remote_disk", "display_name": "Remote",
              "kind": "ssh", "ssh_host": "test-host", "ssh_config": "/test/ssh",
              "paths": ["/", "/home"]}
    configuration = {"deployment": {"disk_resources": [target]}}
    monkeypatch.setattr(status, "read_site_config", lambda: configuration)
    monkeypatch.setattr(status, "_collect_disk_status", lambda resource, **kwargs: {**resource, "status": "ok"})
    assert status.get_disk_resource_status()["resources"][0]["resource_id"] == "remote_disk"
    assert status.get_disk_resource_status(resource_id="remote_disk")["display_name"] == "Remote"
    status._disk_resources()[0]["paths"].append("/tmp")
    assert target["paths"] == ["/", "/home"]
    configuration["deployment"]["disk_resources"] = []
    assert status.get_disk_resource_status()["resources"] == []


@pytest.mark.parametrize("targets", [
    {}, [{"resource_id": "one", "kind": "local", "paths": ["relative"]}],
    [{"resource_id": "one", "kind": "ssh", "paths": ["/"], "ssh_host": "-oProxyCommand=bad", "ssh_config": "/ssh"}],
    [{"resource_id": "one", "kind": "local", "paths": ["/"]}] * 2,
])
def test_invalid_disk_targets_rejected(monkeypatch, targets):
    monkeypatch.setattr(status, "read_site_config", lambda: {"deployment": {"disk_resources": targets}})
    with pytest.raises(ValueError):
        status._disk_resources()


def test_remote_disk_missing_config_does_not_use_another_host_config(monkeypatch):
    monkeypatch.setattr(status.subprocess, "run", lambda *a, **k: pytest.fail("must not connect"))
    with pytest.raises(ValueError, match="SSH config not found"):
        status._run_disk_script({"kind": "ssh", "ssh_host": "test", "ssh_config": "/missing/ssh"}, "df", [])


def test_local_gpu_script_needs_no_ssh_config(monkeypatch, capsys):
    path = Path(__file__).resolve().parents[2] / "scripts/thor_gpu_status.py"
    spec = importlib.util.spec_from_file_location("gpu_status_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "parse_args", lambda: SimpleNamespace(
        local=True, ssh_config="/missing/ssh", host="unused", identity_file="", timeout=5, pretty=False))
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, '{"status":"ok","gpu_count":1}', '')
    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.main() == 0
    assert calls[0][0] == [module.sys.executable, "-"]
    assert calls[0][1]["input"] == module.REMOTE_SCRIPT
    assert '"gpu_count": 1' in capsys.readouterr().out
