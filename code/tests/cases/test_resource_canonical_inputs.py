"""Resource protocol identifiers are canonical, independent of site aliases.

These cases use temporary files and in-memory domain inputs, never live services.
"""
from __future__ import annotations

from email.message import Message
import importlib
import json
from types import SimpleNamespace

import pytest
from starlette.datastructures import Headers


@pytest.fixture
def obsolete_settings(tmp_path, monkeypatch):
    site = tmp_path / "site.json"
    site.write_text(json.dumps({"deployment": {"compatibility": {
        "format_namespaces": ["archive."],
        "domain_values": {
            "runtime_identity_header_aliases": {
                "X-Portal-Execution-ID": ["X-Archive-Execution-ID"],
                "X-Portal-Lineage-ID": ["X-Archive-Lineage-ID"],
            },
            "directory_media_type": "application/vnd.archive.directory+json",
            "repo_ownership_rules": [{"remote_contains": "example.invalid/source/", "ownership": "archive"}],
            "paper_curated_source": "Archive curated venues",
            "paper_catalog_source_version": "Archive catalog",
        },
    }}}))
    monkeypatch.setenv("PORTAL_SITE_CONFIG", str(site))
    return site


@pytest.mark.parametrize("name", ["Execution", "Lineage"])
def test_header_alias_cannot_supply_missing_or_replace_empty_identity(obsolete_settings, name):
    from management_portal.runtime_identity_headers import runtime_identity_header
    canonical, alias = f"X-Portal-{name}-ID", f"X-Archive-{name}-ID"
    assert runtime_identity_header({alias: "untrusted"}, canonical) == ""
    assert runtime_identity_header({canonical: "", alias: "untrusted"}, canonical) == ""
    assert runtime_identity_header({canonical: " selected ", alias: "untrusted"}, canonical) == "selected"


@pytest.mark.parametrize("kind", ["http", "asgi"])
@pytest.mark.parametrize("second", ["different", ""])
def test_conflicting_canonical_headers_are_rejected(kind, second):
    from management_portal.runtime_identity_headers import runtime_identity_header
    if kind == "http":
        headers = Message()
        headers["X-Portal-Execution-ID"] = "selected"
        headers["x-portal-execution-id"] = second
    else:
        headers = Headers(raw=[(b"x-portal-execution-id", b"selected"),
                               (b"x-portal-execution-id", second.encode())])
    with pytest.raises(ValueError, match="Conflicting runtime identity headers"):
        runtime_identity_header(headers, "X-Portal-Execution-ID")


def test_identical_canonical_headers_keep_identity():
    from management_portal.runtime_identity_headers import runtime_identity_header
    headers = Message()
    headers["X-Portal-Lineage-ID"] = "lineage_123456789abc"
    headers["X-Portal-Lineage-ID"] = " lineage_123456789abc "
    assert runtime_identity_header(headers, "X-Portal-Lineage-ID") == "lineage_123456789abc"


@pytest.mark.parametrize("actor", [None, "", "resident_invalid", "service:resource-service"])
def test_resource_identity_never_falls_back_to_environment(actor, monkeypatch):
    from management_portal.resources.access import require_identity
    monkeypatch.setenv("PORTAL_RESIDENT_ID", "resident_123456789abc")
    with pytest.raises(PermissionError):
        require_identity(actor)


def test_resource_identity_keeps_the_authenticated_resident():
    from management_portal.resources.access import require_identity
    selected = "resident_123456789abc"
    records = {selected: {"resource_id": selected, "status": "active"}}
    store = SimpleNamespace(get=records.__getitem__)
    assert require_identity(selected, descriptors=store).id == selected
    with pytest.raises(PermissionError):
        require_identity("resident_abcdef123456", descriptors=store)


def test_folder_description_and_bytes_have_canonical_media_type(obsolete_settings, tmp_path):
    from management_portal.resources.providers import folder_files
    importlib.reload(folder_files)
    root = tmp_path / "empty-folder"
    root.mkdir()
    result = folder_files.describe({"address": "workspace_123456789abc/file",
        "root_id": "workspace_123456789abc", "exists": True}, root, (), hide=False)
    assert result["media_type"] == "application/vnd.tfi.directory+json"
    data, content_type, _ = folder_files.read_content(result, root)
    assert content_type == "application/vnd.tfi.directory+json"
    assert json.loads(data) == {"entries": [], "truncated": False, "hidden_count": 0}


def test_source_site_remote_mapping_cannot_assign_repo_ownership(obsolete_settings):
    from management_portal.resource_identity_backend import _ownership
    assert _ownership([{"identity_remote": "https://example.invalid/source/project.git"}]) == "external"
    assert _ownership([{"identity_remote": ""}]) == "unknown"


def test_paper_generated_metadata_is_canonical_and_authored_source_is_preserved(obsolete_settings, monkeypatch):
    from management_portal import paper_ccf
    entries = [{"dblp_conf_key": "fixture"}, {"dblp_conf_key": "authored", "source": "Editorial source"}]
    monkeypatch.setattr(paper_ccf.paper_state_store, "curated_venues", lambda: entries)
    venues = paper_ccf._load_curated_venues()
    assert venues[0]["source"] == "TFI Portal curated chip/circuit venue list"
    assert venues[1]["source"] == "Editorial source"
    assert entries == [{"dblp_conf_key": "fixture"}, {"dblp_conf_key": "authored", "source": "Editorial source"}]
    monkeypatch.setattr(paper_ccf, "_ccf_catalog", lambda **kwargs: venues)
    assert paper_ccf.ccf_venues()["summary"]["source_version"] == "CCF recommended list v7 / 2026 + TFI Portal curated chip/circuit venues"
