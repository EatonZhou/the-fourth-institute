"""Creation proves the imported history is frozen before applying feature defaults."""
from pathlib import Path
import json

import pytest

from management_common.instance_manifest import load
from scripts import tfi_release_install as installer
from tests.cases.instance_test_support import write_instance


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    instance = load(write_instance(tmp_path / "instance"))
    manifest = {"run_id": "fixture", "supervisor_resident_id": "supervisor", "feature_resolution": {"valid": True}}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    children = []

    def child(selected, script, *arguments, timeout=1800):
        children.append(arguments)
        if arguments[0] == "verify-frozen":
            return json.dumps({"passed": True, "violations": {"old_work": 0}})
        return json.dumps({"status": "initialized", "work_dispatched": False})
    monkeypatch.setattr(installer, "code_child", child)
    evidence = tmp_path / "verified.json"
    evidence.write_text(json.dumps({"run_id": "fixture", "supervisor_resident_id": "supervisor",
                                    "manifest_sha256": installer.digest(path), "passed": True,
                                    "violations": {"old_work": 0}}))
    return instance, manifest, path, evidence, children


def test_foreign_or_failed_evidence_never_dispatches_the_helper(boundary):
    instance, manifest, path, evidence, children = boundary
    original = json.loads(evidence.read_text())
    for changes in ({"manifest_sha256": "b" * 64}, {"run_id": "foreign"}, {"passed": False},
                    {"violations": {"old_work": 1}}):
        evidence.write_text(json.dumps({**original, **changes}))
        with pytest.raises(ValueError, match="Freeze evidence"):
            installer.initialize_features(instance, path, manifest, evidence)
    assert children == []
    evidence.write_text(json.dumps(original))
    assert installer.initialize_features(instance, path, manifest, evidence)["status"] == "initialized"
    assert children == [("initialize", str(path))]


def test_supervised_bundle_requires_resident_evidence(boundary):
    instance, manifest, path, evidence, children = boundary
    with pytest.raises(ValueError, match="--freeze-evidence"):
        installer.initialize_features(instance, path, manifest)
    assert children == []


def test_unsupervised_bundle_verifies_frozen_history_first(boundary, monkeypatch):
    instance, manifest, path, evidence, children = boundary
    manifest = {**manifest, "supervisor_resident_id": ""}
    installer.initialize_features(instance, path, manifest)
    assert [arguments[0] for arguments in children] == ["verify-frozen", "initialize"]


def test_initialization_that_dispatches_work_is_rejected(boundary, monkeypatch):
    instance, manifest, path, evidence, children = boundary
    monkeypatch.setattr(installer, "code_child", lambda *a, **k: json.dumps({"work_dispatched": True}))
    with pytest.raises(ValueError, match="dispatch"):
        installer.initialize_features(instance, path, manifest, evidence)


def test_helper_is_selected_by_instance_root_only():
    source = (Path(installer.__file__).with_name("tfi_release_initialization.py")).read_text()
    assert "PORTAL_DATABASE_URL" in source and "instance_manifest import load" in source
    assert "installation.json" not in source and '["target"]' not in source
