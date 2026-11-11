from unittest.mock import Mock

import psutil
import pytest

from management_portal import kimi_account_runtime as runtime


def test_zombie_is_retired_before_reading_environment(monkeypatch, tmp_path):
    process = Mock()
    process.is_running.return_value = True
    process.status.return_value = psutil.STATUS_ZOMBIE
    process.environ.side_effect = psutil.AccessDenied(123)
    monkeypatch.setattr(psutil, "Process", lambda pid: process)
    assert runtime._owned_process(tmp_path, {"pid": 123}) is None
    process.environ.assert_not_called()


@pytest.mark.parametrize("status", [psutil.STATUS_ZOMBIE, psutil.STATUS_SLEEPING])
def test_stop_distinguishes_zombie_from_live_process(monkeypatch, tmp_path, status):
    process = Mock()
    process.children.return_value = []
    process.status.return_value = status
    monkeypatch.setattr(runtime, "_owned_process", lambda *args: process)
    monkeypatch.setattr(runtime, "_server_busy", lambda client: False)
    monkeypatch.setattr(runtime, "_client", lambda *args: None)
    monkeypatch.setattr(runtime, "control", lambda account: tmp_path)
    monkeypatch.setattr(runtime, "process_tree_id", lambda account: "test")
    monkeypatch.setattr(runtime, "terminate_process_tree", lambda *args, **kwargs: None)
    monkeypatch.setattr(psutil, "wait_procs", lambda *args, **kwargs: ([], [process]))
    marker = tmp_path / "server.json"
    marker.write_text("{}")
    if status == psutil.STATUS_ZOMBIE:
        runtime._stop_server({}, tmp_path, {"pid": 123, "port": 1234})
        assert not marker.exists()
    else:
        with pytest.raises(TimeoutError):
            runtime._stop_server({}, tmp_path, {"pid": 123, "port": 1234})
        assert marker.exists()
