"""Resource document fields, rather than producer labels, select readable data."""
import copy
import json

import pytest

from management_portal import device_identity_backend as devices
from management_portal.resources.collaboration_registry import CollaborationDescriptorStore
from management_portal.resources.registry_store import get_registry_store


RESIDENT = "resident_0123456789ab"
DEVICE = "device_0123456789ab"


def descriptor():
    return {"resource_id": RESIDENT, "title": "Kept", "status": "active",
            "project_ids": ["Example"], "relations": [], "attributes": {},
            "created_at": "2026-11-01T00:00:00Z", "updated_at": "2026-11-02T00:00:00Z"}


def device():
    return {"device_id": DEVICE, "name": "Kept", "device_kind": "workstation"}


@pytest.mark.parametrize("version", [None, 1, 999])
def test_collaboration_descriptor_reads_and_noop_writes_preserve_persisted_label(version):
    raw = descriptor()
    if version is not None:
        raw["schema"] = f"tfi.collaboration-descriptor.v{version}"
    registry = get_registry_store()
    with registry.writer("resident") as writer:
        writer.put("resident", RESIDENT, raw)
    revision = registry.revision("resident")
    store = CollaborationDescriptorStore()
    read = store.get(RESIDENT)
    assert read["resource_id"] == RESIDENT and read["title"] == "Kept"
    assert store.attributes_for_records([{"resource_id": RESIDENT, "resource_type": "resident"}])[RESIDENT]["project_ids"] == ["Example"]
    _, changed = store.put_with_change(raw)
    assert not changed
    assert registry.get("resident", RESIDENT) == raw
    assert registry.revision("resident") == revision


@pytest.mark.parametrize("change,error", [
    ({"resource_id": "resident_invalid"}, None),
    ({"resource_id": "resident_abcdef012345"}, "does not match"),
    ({"status": "unsupported"}, "status"),
    ({"attributes": {"visibility": "public"}}, "Derived fields"),
    ({"relations": [{"kind": "parent", "target_id": "bad"}]}, None),
    ({"owner_service": "untrusted"}, "unsupported.*fields"),
])
def test_descriptor_field_and_identity_checks_remain(change, error):
    with pytest.raises(ValueError, match=error):
        CollaborationDescriptorStore().normalize(
            {**descriptor(), "schema": "tfi.collaboration-descriptor.v999", **change},
            expected_id=RESIDENT,
        )


@pytest.mark.parametrize("version", [None, 0, 999])
def test_device_registry_reads_fields_without_rewriting_producer_label(version):
    raw = device()
    if version is not None:
        raw["schema"] = f"tfi.device.v{version}"
    registry = get_registry_store()
    with registry.writer("device") as writer:
        writer.put("device", DEVICE, raw)
    revision = registry.revision("device")
    assert devices.load_device_records()[0]["device_id"] == DEVICE
    assert registry.get("device", DEVICE) == raw
    assert registry.revision("device") == revision


@pytest.mark.parametrize("change,error", [
    ({"device_id": "device_abcdef012345"}, "does not match"),
    ({"device_id": "bad"}, None),
    ({"name": ""}, "no name"),
    ({"device_kind": "gpu"}, "parent_device_id"),
    ({"network": []}, "object"),
    ({"resource_id": "another"}, "unsupported fields"),
])
def test_device_fields_remain_checked(change, error):
    with pytest.raises(ValueError, match=error):
        devices._normalize_record({**device(), "schema": "tfi.device.v999", **change}, DEVICE)


@pytest.mark.parametrize("version", [None, 0, 999])
def test_status_cache_preserves_readable_observations_and_source_bytes(tmp_path, version):
    payload = {"device_id": DEVICE, "reachable": True, "gpus": []}
    raw = {"servers": {DEVICE: {"payload": payload}}, "updated_at": "2026-11-02T00:00:00Z"}
    if version is not None:
        raw["schema"] = f"tfi.device_status.v{version}"
    path = tmp_path / "status.json"
    original = json.dumps(raw).encode()
    path.write_bytes(original)
    assert devices.cached_gpu_server_status(DEVICE, status_path=path) == payload
    observations = devices.load_device_observations(path)
    assert observations == {DEVICE: payload}
    observations[DEVICE]["reachable"] = False
    assert devices.load_device_observations(path)[DEVICE]["reachable"] is True
    assert path.read_bytes() == original


@pytest.mark.parametrize("raw", [[], {"servers": []}, {"servers": {DEVICE: {"payload": []}}}])
def test_malformed_status_cache_still_yields_no_observation(tmp_path, raw):
    path = tmp_path / "status.json"
    path.write_text(json.dumps(copy.deepcopy(raw)))
    assert devices.cached_gpu_server_status(DEVICE, status_path=path) is None
    assert devices.load_device_observations(path) == {}
