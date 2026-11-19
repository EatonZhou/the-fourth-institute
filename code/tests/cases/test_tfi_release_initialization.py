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


@pytest.mark.parametrize("rollback", [False, True])
def test_directory_initialization_from_raw_empty_database(monkeypatch, rollback):
    """The real installer has a raw connection, not a schema-creating pool."""
    import os
    import uuid
    import psycopg
    from management_common.portal_database import portal_database_url, _database_schema
    from scripts.tfi_release_initialization import initialize_directory_dependencies

    monkeypatch.setenv("PORTAL_DATABASE_NAMESPACE",
                       os.environ["PORTAL_DATABASE_NAMESPACE"] + "_fresh_" + uuid.uuid4().hex[:8])
    schema = _database_schema("directory_lifecycle")
    with psycopg.connect(portal_database_url(), autocommit=True) as connection:
        assert connection.execute("SELECT to_regnamespace(%s)", (schema,)).fetchone()[0] is None
        try:
            with connection.transaction():
                result = initialize_directory_dependencies(connection)
                assert result["entries"] == 0
                assert connection.execute(
                    f'SELECT ready FROM "{schema}".directory_dependency_state WHERE singleton'
                ).fetchone()[0] is True
                assert connection.execute("SELECT to_regclass(%s)",
                                          (f'"{schema}".cleanup',)).fetchone()[0]
                assert initialize_directory_dependencies(connection) == result
                if rollback:
                    raise RuntimeError("abort installer transaction")
        except RuntimeError as exc:
            assert rollback and str(exc) == "abort installer transaction"
        exists = connection.execute("SELECT to_regnamespace(%s)", (schema,)).fetchone()[0]
        assert bool(exists) is not rollback
