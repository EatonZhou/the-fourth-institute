from pathlib import Path

from management_portal import google_cloud_devices


def test_colab_terminal_connects_to_named_existing_runtime(monkeypatch):
    monkeypatch.setattr(google_cloud_devices, "settings", lambda: {
        "compute_device_id": "device_000000000001",
        "colab_session": "portal-colab",
        "colab_cli": "/opt/colab",
    })
    monkeypatch.setattr(google_cloud_devices, "portal_path", lambda _: Path("/instance/secrets"))
    monkeypatch.setattr(Path, "is_file", lambda _: True)
    command = google_cloud_devices.colab_ssh_command("device_000000000001", "bash", "-il")
    assert command[-8:] == [
        "ssh", "-s", "portal-colab", "-i", "/instance/secrets/colab-cli/ssh-ed25519", "--", "bash", "-il",
    ]
    assert not google_cloud_devices.colab_session("device_000000000002")


def test_cloud_metrics_only_expose_public_numeric_fields(monkeypatch):
    monkeypatch.setattr(google_cloud_devices, "portal_path", lambda _: Path("/instance/secrets"))
    monkeypatch.setattr(Path, "is_file", lambda _: True)
    monkeypatch.setattr("management_common.transfer_network.resolve_route", lambda: "inherited")
    monkeypatch.setattr(google_cloud_devices, "_read", lambda command, **_: (
        "Current balance: 1072.88 compute units\nUsage rate: 0.08/hr\nActive assignments: 1\n"
        if command[-1] == "usage"
        else '{"total": 1000, "used": 4, "free": 994, "trashed": 2, "account": "private"}'
    ))
    config = {"colab_cli": "/opt/colab", "rclone": "/opt/rclone"}
    assert google_cloud_devices.read_status(config, "google_colab") == {
        "compute_units": 1072.88, "compute_units_per_hour": 0.08, "active_assignments": 1,
    }
    assert google_cloud_devices.read_status(config, "google_drive") == {
        "storage_total_bytes": 1000, "storage_used_bytes": 4,
        "storage_free_bytes": 994, "storage_trashed_bytes": 2,
    }


def test_colab_actions_share_session_and_respect_device_permission(monkeypatch, tmp_path):
    import pytest
    from management_portal import permission_system, device_identity_backend

    device = "device_ca437b806200"
    (tmp_path / "colab-cli").mkdir()
    monkeypatch.setattr(google_cloud_devices, "portal_path", lambda _: tmp_path)
    monkeypatch.setattr(google_cloud_devices, "settings", lambda: {"compute_device_id": device})
    monkeypatch.setattr(google_cloud_devices, "_colab_command", lambda _: ["colab"])
    monkeypatch.setattr(permission_system, "allows", lambda actor, _, args:
                        actor in ("alice", "bob") and args["resource_id"] == device)
    monkeypatch.setattr(permission_system, "allows_roles", lambda actor, roles:
                        actor in ("alice", "bob") and roles == [("@contributor", device)])
    active = {}
    def read(argv, **kwargs):
        name = argv[3]
        if argv[1] == "status":
            if name in active:
                return f"[{name}] endpoint | Hardware: {active[name]} | Status: IDLE"
            return f"[colab] Session '{name}' not found."
        if argv[1] == "new":
            assert name not in active and argv[4:] == ["--gpu", "A100"]
            active[name] = "A100"
            return "[colab] Session READY."
        assert argv[1] == "stop"
        del active[name]
        return "[colab] Session terminated."
    monkeypatch.setattr(google_cloud_devices, "_read", read)
    def call(actor, action="colab.start", hardware="A100"):
        return google_cloud_devices.change_colab_session(device, action, {"hardware": hardware}, actor=actor)
    with pytest.raises(PermissionError):
        call("viewer")
    assert call("alice")["changed"]
    assert not call("bob")["changed"]
    with pytest.raises(ValueError, match="already uses A100"):
        call("bob", hardware="T4")

    def run(argv, **kwargs):
        from types import SimpleNamespace
        assert argv[-2:] == ["--", "nvidia-smi -L"] and "ssh" in argv
        assert kwargs["timeout"] == 120
        kwargs["stdout"].write(b"GPU 0")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(device_identity_backend.subprocess, "run", run)
    assert device_identity_backend.run_device_ssh(device, "nvidia-smi -L", actor="bob")["output"] == "GPU 0"
    assert call("bob", "colab.stop")["changed"]
    assert not call("alice", "colab.stop")["changed"]
