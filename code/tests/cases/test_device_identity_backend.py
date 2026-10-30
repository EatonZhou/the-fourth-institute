from __future__ import annotations

from pathlib import Path
import json
import subprocess
from unittest import mock

import pytest
import yaml

from management_portal import (
    agent_status_backend,
    device_identity_backend,
    resource_index_backend,
)
from management_portal import resource_index_registries
from management_portal.resources import ResourceService
from management_portal.resources.collaboration_registry import CollaborationDescriptorStore
from management_portal.resources.providers import device
from management_portal.resources.registration import ResourceRegistrationStore
from tests.cases.registry_support import put_registry_document, registry_document


SERVER_ID = "device_0123456789ab"
GPU_ID = "device_abcdef012345"
GPU_UUID = "GPU-01234567-89ab-cdef-0123-456789abcdef"


def test_deployment_ssh_paths_are_shared_by_parent_access_and_gpu_probe(tmp_path, monkeypatch):
    site_path = tmp_path / "site.json"
    monkeypatch.setenv("PORTAL_SITE_CONFIG", str(site_path))
    server = {
        "device_id": SERVER_ID,
        "access_methods": {"ssh": {
            "host": "gpu-test", "config_path": "/retired/ssh-config",
            "identity_file": "/retired/key",
        }},
    }
    child = {"device_id": GPU_ID, "parent_device_id": SERVER_ID}
    monkeypatch.setattr(device_identity_backend, "load_device_records", lambda: [server, child])
    monkeypatch.setattr(device_identity_backend, "has_device_ssh_password", lambda *a, **kw: False)
    monkeypatch.setattr(agent_status_backend, "_summarize_gpu_status", lambda payload, *a, **kw: payload)
    site_path.write_text(json.dumps({"deployment": {"device_ssh_paths": {
        SERVER_ID: {"config_path": "/current/ssh-config", "identity_file": "/current/key"},
    }}}))
    inherited = device_identity_backend.effective_ssh_access(GPU_ID)
    assert inherited["config_path"] == "/current/ssh-config"
    assert inherited["identity_file"] == "/current/key"
    assert inherited["via_resource_id"] == SERVER_ID
    with mock.patch.object(agent_status_backend.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, '{"status":"ok"}')) as run:
        assert agent_status_backend._read_gpu_status_direct(server)["status"] == "ok"
    command = run.call_args.args[0]
    assert command[command.index("--ssh-config") + 1] == inherited["config_path"]
    assert command[command.index("--identity-file") + 1] == inherited["identity_file"]
    assert command[command.index("--host") + 1] == "gpu-test"
    # A fresh read picks up edits, and omitted local paths do not revive old values.
    site_path.write_text(json.dumps({"deployment": {"device_ssh_paths": {
        SERVER_ID: {"config_path": "/changed/ssh-config"},
    }}}))
    changed = device_identity_backend.effective_ssh_access(GPU_ID)
    assert changed["config_path"] == "/changed/ssh-config"
    assert changed["identity_file"] == ""
    assert server["access_methods"]["ssh"]["config_path"] == "/retired/ssh-config"


@pytest.mark.parametrize("value", [None, [], {"config_path": "relative/path"}, {"config_path": 7}, {"password": "unsupported"}])
def test_deployment_ssh_paths_reject_invalid_config(tmp_path, monkeypatch, value):
    site_path = tmp_path / "site.json"
    monkeypatch.setenv("PORTAL_SITE_CONFIG", str(site_path))
    site_path.write_text(json.dumps({"deployment": {"device_ssh_paths": {SERVER_ID: value}}}))
    with pytest.raises(ValueError, match="deployment.device_ssh_paths"):
        device_identity_backend.ssh_access_for_record({"device_id": SERVER_ID})


def _write_device(record: dict) -> None:
    put_registry_document(device_identity_backend.DEVICE_COLLECTION, record["device_id"], record)


def _registry() -> list[dict]:
    _write_device(
        {
            "schema": device_identity_backend.REGISTRY_SCHEMA,
            "device_id": SERVER_ID,
            "name": "Test GPU Server",
            "device_kind": "gpu_server",
            "lifecycle": "active",
            "hostname": "gpu-test",
            "network": {
                "primary_ip": "192.0.2.10",
                "mac_addresses": ["00:11:22:33:44:55"],
            },
            "access_methods": {
                "ssh": {
                    "host": "gpu-test",
                    "config_path": "/tmp/ssh-config",
                }
            },
            "monitor": {"kind": "nvidia_smi", "expected_gpu_count": 1},
        },
    )
    _write_device(
        {
            "schema": device_identity_backend.REGISTRY_SCHEMA,
            "device_id": GPU_ID,
            "name": "Test GPU 0",
            "device_kind": "gpu",
            "lifecycle": "active",
            "parent_device_id": SERVER_ID,
            "gpu_index": 0,
            "gpu_uuid": GPU_UUID,
            "model": "Test GPU",
        },
    )
    return device_identity_backend.load_device_records()


def _observation() -> dict:
    return {
        "status": "ok",
        "gpu_count": 1,
        "gpus": [
            {
                "index": 0,
                "uuid": GPU_UUID,
                "name": "Test GPU",
                "gpu_util_percent": 75,
                "memory_total_mb": 100,
                "memory_used_mb": 80,
            }
        ],
        "updated_at": "2026-07-23T03:00:00+08:00",
    }


def test_device_registry_validates_topology_and_resolves_gpu_location_contracts(tmp_path, subtests, isolated_portal_case):
    """设备登记校验拓扑且 GPU 地址继承父设备连接信息"""
    with subtests.test(contract='test_device_registry_validates_topology_and_resolves_gpu_location'), isolated_portal_case() as case_path:
        tmp_path = case_path
        records = _registry()

        assert len(records) == 2
        assert device_identity_backend.gpu_children(SERVER_ID, records=records)[0]["device_id"] == GPU_ID
        resolved = device_identity_backend.resolve_device_address(
            f"{SERVER_ID}/gpu/0",
            status_path=tmp_path / "missing-status.json",
        )
        assert resolved["root_id"] == SERVER_ID
        assert resolved["device_id"] == GPU_ID
        assert resolved["record"]["parent_device_id"] == SERVER_ID
        assert "access_methods" not in resolved["record"]
        assert resolved["access_methods"]["ssh"]["host"] == "gpu-test"
        assert resolved["access_methods"]["ssh"]["config_path"] == "/tmp/ssh-config"
        assert resolved["access_methods"]["ssh"]["via_resource_id"] == SERVER_ID
        assert resolved["access_methods"]["ssh"]["credential_saved"] is False

        with pytest.raises(ValueError, match="unsupported nested device address"):
            device_identity_backend.resolve_device_address(
                f"{SERVER_ID}/gpu/00",
            )
    with subtests.test(contract='test_device_scan_isolates_one_invalid_registry_file'), isolated_portal_case() as case_path:
        tmp_path = case_path
        _registry()
        broken_id = "device_deadbeef1234"
        put_registry_document(device_identity_backend.DEVICE_COLLECTION, broken_id, {"schema": ["invalid"]})

        records, errors = device_identity_backend.scan_device_records()

        assert {item["device_id"] for item in records} == {SERVER_ID, GPU_ID}
        assert errors[0]["resource_id"] == broken_id
        with pytest.raises(ValueError, match=broken_id):
            device_identity_backend.load_device_records()
    with subtests.test(contract='test_device_registry_rejects_unknown_identity_fields'), isolated_portal_case() as case_path:
        tmp_path = case_path
        _write_device(
            {
                "schema": device_identity_backend.REGISTRY_SCHEMA,
                "device_id": SERVER_ID,
                "resource_id": "gpu_server_test",
                "name": "Test",
                "device_kind": "gpu_server",
            },
        )

        with pytest.raises(ValueError, match="unsupported fields"):
            device_identity_backend.load_device_records()
    with subtests.test(contract='test_device_registration_rejects_unknown_fields'), isolated_portal_case() as case_path:
        tmp_path = case_path
        with pytest.raises(ValueError, match="current contract"):
            device_identity_backend.register_device(
                {
                    "name": "Lab Workstation",
                    "device_kind": "workstation",
                    "unexpected": "value",
                },
            )
        assert device_identity_backend.scan_device_records() == ([], [])
    with subtests.test(contract='test_device_ssh_password_is_kept_out_of_registry_and_is_owner_only'), isolated_portal_case() as case_path:
        tmp_path = case_path
        secret_root = tmp_path / "secrets"
        password = "correct horse battery staple"
        result = device_identity_backend.save_device_ssh_password(
            SERVER_ID,
            password,
            secret_root=secret_root,
        )

        secret_path = secret_root / f"{SERVER_ID}.ssh-password"
        assert result["credential_saved"] is True
        assert secret_path.stat().st_mode & 0o777 == 0o600
        assert device_identity_backend.read_device_ssh_password(
            SERVER_ID,
            secret_root=secret_root,
        ) == password
        assert password not in yaml.safe_dump(_registry())

        deleted = device_identity_backend.delete_device_ssh_password(
            SERVER_ID,
            secret_root=secret_root,
        )
        assert deleted["credential_saved"] is False
        assert not secret_path.exists()
    with subtests.test(contract='test_gpu_status_is_keyed_by_device_ids_and_exposed_as_observations'), isolated_portal_case() as case_path:
        tmp_path = case_path
        records = _registry()
        server = next(item for item in records if item["device_id"] == SERVER_ID)
        normalized = agent_status_backend._normalize_gpu_observation(
            _observation(),
            server,
            records=records,
        )

        assert normalized["device_id"] == SERVER_ID
        assert normalized["gpus"][0]["device_id"] == GPU_ID
        assert normalized["gpus"][0]["parent_device_id"] == SERVER_ID
        status_path = tmp_path / "status.json"
        device_identity_backend.store_gpu_server_status(
            SERVER_ID,
            normalized,
            status_path=status_path,
        )
        observations = device_identity_backend.load_device_observations(status_path)
        assert set(observations) == {SERVER_ID, GPU_ID}
        assert observations[GPU_ID]["state"] == "busy"

        mismatched = _observation()
        mismatched["gpus"][0]["uuid"] = "GPU-wrong"
        with pytest.raises(ValueError, match="GPU identity mismatch"):
            agent_status_backend._normalize_gpu_observation(
                mismatched,
                server,
                records=records,
            )


def test_register_device_persists_ssh_connection_and_tests_it(tmp_path: Path) -> None:
    """设备登记持久保存 SSH 连接配置并支持连通检查"""
    registered = device_identity_backend.register_device(
        {
            "name": "Lab Workstation",
            "device_kind": "workstation",
            "hostname": "lab-ws-01",
            "primary_ip": "192.0.2.20",
            "access_methods": {
                "ssh": {
                    "host": "lab-ws",
                    "user": "operator",
                    "port": 2222,
                    "config_path": "/tmp/ssh-config",
                    "identity_file": "/tmp/id_ed25519",
                }
            },
        },
    )

    device_id = registered["id"]
    stored = next(record for record in device_identity_backend.load_device_records() if record["device_id"] == device_id)
    assert stored["device_kind"] == "workstation"
    assert stored["access_methods"]["ssh"] == {
        "host": "lab-ws",
        "config_path": "/tmp/ssh-config",
        "user": "operator",
        "port": 2222,
        "identity_file": "/tmp/id_ed25519",
    }

    completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
    with mock.patch.object(
        device_identity_backend.subprocess,
        "run",
        return_value=completed,
    ) as run:
        result = device_identity_backend.test_ssh_connection(
            device_id,
        )

    assert result["reachable"] is True
    assert result["via_resource_id"] == device_id
    command = run.call_args.args[0]
    assert command[-3:] == ["--", "operator@lab-ws", "true"]
    assert ["-p", "2222"] == command[command.index("-p"):command.index("-p") + 2]

    removed = device_identity_backend.unregister_device(
        device_id,
    )
    assert removed["unregistered"] is True
    assert registry_document(device_identity_backend.DEVICE_COLLECTION, device_id) is None


def test_device_index_merges_live_gpu_attributes_contracts(tmp_path, subtests, isolated_portal_case):
    """设备索引合并实时 GPU 属性并限制接口使用规范标识"""
    with subtests.test(contract='test_device_index_merges_live_gpu_attributes'), isolated_portal_case():
        records = _registry()
        server = next(item for item in records if item["device_id"] == SERVER_ID)
        normalized = agent_status_backend._normalize_gpu_observation(
            _observation(),
            server,
            records=records,
        )
        observations = {
            SERVER_ID: normalized,
            GPU_ID: normalized["gpus"][0],
        }
        with (
            mock.patch.object(
                resource_index_registries.device_identity_backend,
                "scan_device_records",
                return_value=(records, []),
            ),
            mock.patch.object(
                resource_index_registries.device_identity_backend,
                "load_device_observations",
                return_value=observations,
            ),
        ):
            indexed, state = resource_index_registries.DeviceRegistryAdapter().scan()

        by_id = {item["resource_id"]: item for item in indexed}
        assert state["counts"] == {"gpu_server": 1, "gpu": 1}
        assert by_id[SERVER_ID]["attributes"]["available_gpu_count"] == 0
        assert by_id[GPU_ID]["attributes"]["status"] == "busy"
        assert by_id[GPU_ID]["attributes"]["gpu_util_percent"] == 75
        assert by_id[GPU_ID]["attributes"]["parent_device_id"] == SERVER_ID
        assert by_id[SERVER_ID]["attributes"]["access_methods"] == {
            "ssh": {
                "host": "gpu-test",
                "config_path": "/tmp/ssh-config",
                "user": "",
                "port": None,
                "identity_file": "",
            }
        }
        for forbidden in (
            "connection",
            "ssh_host",
            "ssh_config",
            "ssh_user",
            "ssh_port",
            "identity_file",
            "credential_saved",
        ):
            assert forbidden not in by_id[SERVER_ID]["attributes"]
    with subtests.test(contract='test_gpu_status_api_accepts_only_canonical_device_ids'), isolated_portal_case():
        records = _registry()
        server = next(item for item in records if item["device_id"] == SERVER_ID)
        normalized = agent_status_backend._normalize_gpu_observation(
            _observation(),
            server,
            records=records,
        )
        with (
            mock.patch.object(agent_status_backend, "_gpu_devices", return_value=records),
            mock.patch.object(
                agent_status_backend,
                "_collect_gpu_status",
                return_value=normalized,
            ),
        ):
            gpu = agent_status_backend.get_gpu_device_status(device_id=GPU_ID)
            invalid = agent_status_backend.get_gpu_device_status(device_id="gpu_server_test")

        assert gpu["device_id"] == GPU_ID
        assert gpu["parent_device_id"] == SERVER_ID
        assert gpu["status"] == "busy"
        assert invalid["status"] == "error"
        assert "known_device_ids" in invalid
    with subtests.test(contract='test_resource_service_lists_and_resolves_devices'), isolated_portal_case():
        records = _registry()
        store = resource_index_backend.IndexStore()
        index = resource_index_backend.ResourceIndexService(store=store, adapters=[], start_worker=False)
        service = ResourceService(
            index_provider=index,

            collaboration_store=CollaborationDescriptorStore(),
            registration_store=ResourceRegistrationStore(),
            resolver_loader=device.install_resolver,
        )
        with (
            mock.patch.object(device_identity_backend, "scan_device_records", return_value=(records, [])),
            mock.patch.object(device_identity_backend, "load_device_observations", return_value={}),
        ):
            indexed, state = resource_index_registries.DeviceRegistryAdapter().scan()
            assert not state["resource_errors"]
            store.publish_snapshot("device", indexed, state, persist=False)
            listed = service.query(resource_types=["device"], actor="device-reader")
            resolved = service.resolve(f"{SERVER_ID}/gpu/0", actor="device-reader")
            # In open mode every valid User reads devices.
            assert service.query(resource_types=["device"], actor="unauthorized")["total"] == 2

        assert listed["total"] == 2
        assert {item["resource_id"] for item in listed["resources"]} == {
            SERVER_ID,
            GPU_ID,
        }
        assert resolved["root_id"] == SERVER_ID
        assert resolved["device_id"] == GPU_ID
        assert resolved["exists"] is True
