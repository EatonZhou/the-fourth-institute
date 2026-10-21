"""Exact namespace compatibility at the projection Python read boundaries.

:module: workbench.panels
:importance: P1
:design: decision_15aab18c0ab3, decision_7a5ae487e2e9

These tests call the real validators/readers without constructing a Store,
starting a service, saving a revision, or building an application.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
import sys

import pytest

from management_portal.resources import ResourceOperationError
from management_portal.workbench_projections import libraries, page
from management_portal.workbench_projections.store import ProjectionError, validate_manifest


NAMESPACES = ("tfi",)
ASSET = b"/* fixed library bytes */\n"


def _definition(namespace):
    return {
        "title": "Projection fixture", "html": "", "navigation": "menu",
        "bindings": {"main": {"lineage_id": "lineage_111111111111",
                                "residents": {"facade": "resident_111111111111"}}},
        "manifest": {
            "schema": f"{namespace}.projection.v2",
            "sidebar": [{"id": "main", "title": "Main", "source": "panels/main.html"}],
            "chat": {"binding": "main", "role": "facade"},
            "context": [{"channel": "focus", "publishers": ["main"], "subscribers": ["main"]}],
        },
        "panels": {"panels/main.html": "<p>Body stays unchanged.</p>"},
    }


def _library_root(tmp_path, monkeypatch, schema):
    root = tmp_path / "libraries"
    root.mkdir()
    path = "example/1.0.0/main.js"
    asset = root / path
    asset.parent.mkdir(parents=True)
    asset.write_bytes(ASSET)
    item = {"path": path, "sha256": hashlib.sha256(ASSET).hexdigest(),
            "size": len(ASSET), "content_type": "text/javascript"}
    manifest = {"schema": schema, "libraries": {"example": {
        "version": "1.0.0", "entry": path, "files": [item], "licenses": [{"path": path}],
    }}}
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(libraries, "_ROOT", root)
    return manifest_path, asset, manifest


@contextmanager
def _read_only():
    """Reject write/network/process audit events only during the actual calls."""
    active = True
    violations = []
    forbidden = {"os.remove", "os.rename", "os.mkdir", "os.rmdir", "os.link",
                 "os.symlink", "os.truncate", "os.chmod", "os.utime",
                 "socket.connect", "socket.bind", "subprocess.Popen"}

    def audit(event, args):
        if not active:
            return
        writing = event == "open" and (
            any(char in (args[1] or "") for char in "wax+")
            or bool(args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
        )
        if writing or event in forbidden:
            violations.append(event)
            raise AssertionError(f"read path attempted side effect: {event}")

    sys.addaudithook(audit)
    try:
        yield
    finally:
        active = False
    assert not violations


@pytest.mark.parametrize("namespace", NAMESPACES)
def test_tfi_projection_format_compatibility_page_accepts_exact_tag(namespace):
    """Read-time validation preserves the complete payload and schema spelling."""
    value = _definition(namespace)
    before = deepcopy(value)
    with _read_only():
        page.validate_page(value["manifest"], value["panels"], value["bindings"])
        result = validate_manifest(value)
    assert value == before == result
    assert result is not value
    assert result["manifest"] is not value["manifest"]


@pytest.mark.parametrize("namespace", NAMESPACES)
def test_tfi_projection_format_compatibility_libraries_accepts_exact_tag(tmp_path, monkeypatch, namespace):
    """Real manifest and asset reads neither rewrite nor normalize stored bytes."""
    manifest_path, asset, manifest = _library_root(
        tmp_path, monkeypatch, f"{namespace}.projection-libraries.v1")
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (manifest_path, asset)}
    with _read_only():
        assert libraries.registry() == manifest["libraries"]
        assert libraries.read_library(manifest["libraries"]["example"]["entry"]) == (ASSET, "text/javascript")
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before}


@pytest.mark.parametrize("tag", [
    "archive.projection.v2",
    "archive.projection.v1", "tfi.projection.v1", "archive.projection.v3", "tfi.projection.v3",
    "tfi.projection.v02", "tfi.projection.v2.extra", "tfi.projection-libraries.v2",
    "other.projection.v2", "TFI.projection.v2", " tfi.projection.v2", "tfi.projection.v2 ",
    None, 2, [], {"schema": "tfi.projection.v2"},
])
def test_tfi_projection_format_compatibility_page_rejects_other_tags(tag):
    value = _definition("tfi")
    value["manifest"]["schema"] = tag
    before = deepcopy(value)
    with _read_only(), pytest.raises(page.ContentTemplateError, match="清单版本"):
        page.validate_page(value["manifest"], value["panels"], value["bindings"])
    with _read_only(), pytest.raises(ProjectionError) as raised:
        validate_manifest(value)
    assert raised.value.code == "projection.manifest"
    assert value == before


@pytest.mark.parametrize("tag", [
    "archive.projection-libraries.v1",
    "archive.projection-libraries.v2", "tfi.projection-libraries.v2",
    "tfi.projection-libraries.v01", "tfi.projection-libraries.v1.extra",
    "tfi.projection.v1", "other.projection-libraries.v1", "TFI.projection-libraries.v1",
    " tfi.projection-libraries.v1", "tfi.projection-libraries.v1 ", None, 1, [], {},
])
def test_tfi_projection_format_compatibility_library_rejects_other_tags(tmp_path, monkeypatch, tag):
    manifest_path, asset, _ = _library_root(tmp_path, monkeypatch, tag)
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (manifest_path, asset)}
    with _read_only(), pytest.raises(ResourceOperationError) as raised:
        libraries.registry()
    assert raised.value.code == "projection.library_registry"
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before}


@pytest.mark.parametrize("namespace", NAMESPACES)
@pytest.mark.parametrize("case,code", [
    ("missing-facade", "projection.manifest"), ("wrong-alias", "projection.manifest"),
    ("invalid-lineage", "projection.binding"), ("invalid-resident", "projection.resident"),
    ("unknown-channel-member", "projection.manifest"), ("unknown-field", "projection.manifest"),
    ("missing-source", "projection.manifest"), ("forbidden-template", "projection.manifest"),
])
def test_tfi_projection_format_compatibility_page_keeps_binding_and_content_checks(namespace, case, code):
    value = _definition(namespace)
    if case == "missing-facade":
        value["bindings"]["main"]["residents"] = {}
    elif case == "wrong-alias":
        value["manifest"]["chat"]["binding"] = "unbound"
    elif case == "invalid-lineage":
        value["bindings"]["main"]["lineage_id"] = "lineage_wrong"
    elif case == "invalid-resident":
        value["bindings"]["main"]["residents"]["facade"] = "resident_wrong"
    elif case == "unknown-channel-member":
        value["manifest"]["context"][0]["subscribers"] = ["unknown"]
    elif case == "unknown-field":
        value["manifest"]["extra"] = True
    elif case == "missing-source":
        value["panels"] = {}
    else:
        value["panels"]["panels/main.html"] = "<nav>Untrusted shell</nav>"
    before = deepcopy(value)
    with _read_only(), pytest.raises(ProjectionError) as raised:
        validate_manifest(value)
    assert raised.value.code == code
    assert value == before


@pytest.mark.parametrize("namespace", NAMESPACES)
def test_tfi_projection_format_compatibility_package_entry_and_commit_checks(namespace):
    value = _definition(namespace)
    value["manifest"]["sidebar"][0]["source"] = "package:main"
    value["panels"] = {}
    value["application"] = {"repo_id": "repo_111111111111", "directory": "app",
                            "commit": "a" * 40, "entries": {"main": "main.html"}}
    with _read_only():
        assert validate_manifest(value) == value
    for application in ({**value["application"], "commit": "a" * 39},
                        {**value["application"], "entries": {"other": "main.html"}},
                        {**value["application"], "artifact_hash": "b" * 64}):
        with pytest.raises(ProjectionError) as raised:
            validate_manifest({**value, "application": application})
        assert raised.value.code == "projection.manifest"


@pytest.mark.parametrize("namespace", NAMESPACES)
@pytest.mark.parametrize("case,code", [
    ("hash", "integrity"), ("size", "integrity"), ("symlink", "integrity"),
    ("unregistered", "not_found"), ("traversal", "path"), ("entry", "registry"),
])
def test_tfi_projection_format_compatibility_library_keeps_integrity_checks(tmp_path, monkeypatch, namespace, case, code):
    manifest_path, asset, manifest = _library_root(
        tmp_path, monkeypatch, f"{namespace}.projection-libraries.v1")
    path = manifest["libraries"]["example"]["entry"]
    if case == "hash":
        asset.write_bytes(b"X" * len(ASSET))
    elif case == "size":
        manifest["libraries"]["example"]["files"][0]["size"] += 1
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif case == "symlink":
        target = tmp_path / "outside.js"
        target.write_bytes(ASSET)
        asset.unlink()
        asset.symlink_to(target)
    elif case == "unregistered":
        path = "unknown.js"
    elif case == "traversal":
        path = "../outside.js"
    else:
        manifest["libraries"]["example"]["entry"] = "missing.js"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with _read_only(), pytest.raises(ResourceOperationError) as raised:
        libraries.read_library(path)
    assert raised.value.code == "projection.library_" + code


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
