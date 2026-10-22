"""Windows Resident paths keep WorkspaceStore's portable spelling end to end."""
from contextlib import nullcontext
from pathlib import PureWindowsPath
from types import SimpleNamespace

import pytest

from management_portal.resources import workspace_store
from management_portal.society_service import resident_catalog, resident_record_domain
from management_portal.society_service import resident_workspace_domain, resource_client
from management_portal.society_service import resident_workspace_storage as storage

RID = "resident_111111111111"
LID = "lineage_111111111111"
OWN = "workspace_111111111111"
EXTRA = "workspace_222222222222"


class WindowsPath(PureWindowsPath):
    def expanduser(self):
        return self

    def resolve(self, strict=False):
        return self


@pytest.fixture(params=["C:/Users/测试 user/Portal", "//server/share/Portal"])
def windows_record(request, monkeypatch):
    # Exercise the actual canonicalizer with Windows path semantics on every CI
    # host. Native filesystem/ACL behavior belongs to the native platform suite.
    for module in (workspace_store, resident_catalog, resident_record_domain,
                   resident_workspace_domain, resource_client):
        monkeypatch.setattr(module, "Path", WindowsPath)
    folder = WindowsPath(request.param) / LID
    return dict(resident_id=RID, lineage_id=LID, workspace_path=str(folder),
                owned_workspace_id=OWN, owned_workspace={}, workspace_bindings=[])


def test_new_resident_workspace_commits_and_reloads(windows_record, monkeypatch):
    record = windows_record
    resident_record_domain._set_resident_owned_workspace_binding(record)
    expected = (WindowsPath(record["workspace_path"]) / "residents" / RID / "workspace").as_posix()
    calls = []
    monkeypatch.setattr(workspace_store, "get_workspace_store", lambda: SimpleNamespace(
        ensure=lambda *args, **kwargs: calls.append((args, kwargs))))
    connection = object()
    resource_client.ensure_workspaces(record, connection=connection)
    assert calls == [((OWN, expected), dict(connection=connection, move=True))]
    assert record["owned_workspace"]["path"] == record["default_cwd"] == expected
    loaded = storage.project_records([record], {RID: []}, {OWN: expected},
                                     {LID: record["workspace_path"]})[0]
    assert resident_catalog.default_cwd_path(loaded) == expected
    resident_record_domain._require_resident_owned_workspace_binding(loaded)


def test_imported_workspace_path_is_a_valid_execution_directory(windows_record):
    record = windows_record
    path = WindowsPath(record["workspace_path"]) / "residents" / RID / "workspace"
    record["owned_workspace"] = dict(workspace_id=OWN, path=path.as_posix())
    assert resident_catalog.default_cwd_path(record) == path.as_posix()


@pytest.mark.parametrize("wrong", ["identity", "location"])
def test_other_workspace_is_still_rejected(windows_record, monkeypatch, wrong):
    record = windows_record
    resident_record_domain._set_resident_owned_workspace_binding(record)
    if wrong == "identity":
        record["owned_workspace"]["workspace_id"] = EXTRA
    else:
        record["owned_workspace"]["path"] += "-other"
    monkeypatch.setattr(workspace_store, "get_workspace_store", lambda: SimpleNamespace(
        ensure=lambda *args, **kwargs: pytest.fail("invalid workspace was stored")))
    assert resident_catalog.default_cwd_path(record) == ""
    with pytest.raises(ValueError, match="identity or location mismatch"):
        resource_client.ensure_workspaces(record, connection=object())


def test_reference_binding_roundtrip_keeps_identity_and_purpose(windows_record, monkeypatch):
    from management_portal.society_service import domain_ports
    record = windows_record
    resident_record_domain._set_resident_owned_workspace_binding(record)
    path = WindowsPath(record["workspace_path"]) / "extra"
    record["workspace_bindings"] = [dict(workspace_id=EXTRA, path=path.as_posix(), purpose="keep")]
    monkeypatch.setattr(resident_workspace_domain, "_validated_reference_workspace_path", WindowsPath)
    monkeypatch.setattr(domain_ports, "resource_port", SimpleNamespace(
        workspace_id_for_path=lambda *args, **kwargs: pytest.fail("identity lost")))
    resident_workspace_domain._set_resident_reference_bindings(record, [
        str(path), path.as_posix(), record["owned_workspace"]["path"]])
    assert record["workspace_bindings"] == [dict(workspace_id=EXTRA, path=path.as_posix(), purpose="keep")]


@pytest.mark.parametrize("invalid_owned_path", [False, True])
def test_workspace_roots_keep_valid_owned_identity_primary(windows_record, monkeypatch, invalid_owned_path):
    record = windows_record
    resident_record_domain._set_resident_owned_workspace_binding(record)
    expected = record["owned_workspace"]["path"]
    extra_path = (WindowsPath(record["workspace_path"]) / "extra").as_posix()
    record["workspace_bindings"] = [dict(workspace_id=EXTRA, path=extra_path, purpose="reference")]
    if invalid_owned_path:
        record["owned_workspace"]["path"] += "-other"
    monkeypatch.setattr(workspace_store, "get_workspace_store", lambda: SimpleNamespace(
        rows=lambda ids: {OWN: {"title": "Owned"}, EXTRA: {"title": "Reference"}}))

    roots, cwd = resident_workspace_domain._resident_workspace_roots_with_usage(record)

    assert cwd == ("" if invalid_owned_path else expected)
    assert [root["workspace_id"] for root in roots if root["is_primary"]] == (
        [] if invalid_owned_path else [OWN])
    assert roots[1]["path"] == extra_path
    assert roots[1]["purpose"] == "reference"


def test_workspace_api_keeps_canonical_path_equal_to_default_cwd(windows_record, monkeypatch):
    record = windows_record
    resident_record_domain._set_resident_owned_workspace_binding(record)
    monkeypatch.setattr(workspace_store, "get_workspace_store", lambda: SimpleNamespace(rows=lambda ids: {}))
    monkeypatch.setattr(WindowsPath, "exists", lambda self: True, raising=False)
    monkeypatch.setattr(WindowsPath, "is_dir", lambda self: True, raising=False)
    monkeypatch.setattr(resident_workspace_domain, "_find_resident", lambda rid: record)
    monkeypatch.setattr(resident_workspace_domain.resident_lineage, "access_snapshot_scope", nullcontext)
    monkeypatch.setattr(resident_workspace_domain.business_fact_views, "canonical_views", lambda records, **kwargs: records)
    monkeypatch.setattr(resident_workspace_domain, "ensure_resident_visible_to_actor", lambda *args: None)

    result = resident_workspace_domain._get_resident_workspace_roots(RID, "sample_admin")

    root, = result["roots"]
    assert root["path"] == result["default_cwd"] == record["owned_workspace"]["path"]
    assert root["workspace_id"] == result["workspace_id"] == OWN
    assert root["exists"] and root["is_dir"]
