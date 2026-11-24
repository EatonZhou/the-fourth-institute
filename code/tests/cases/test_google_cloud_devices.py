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
