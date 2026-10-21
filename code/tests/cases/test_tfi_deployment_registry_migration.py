"""The explicit migration preserves deployment values and refuses stale input."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from management_portal.portal_deployment_registry import load_portal_deployments
from scripts import migrate_tfi_runtime_names as migration


def registry(tmp_path, schema="archive.portal-deployments.v1"):
    path = tmp_path / "deployments.json"
    payload = {"schema": schema, "instances": [
        {"id": "dev", "label": "Dev", "kind": "dev", "order": 1,
         "origin": "http://127.0.0.1:8820", "build_id": "preserved"}],
        "private_extension": {"credential_ref": "unchanged"}}
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def run(path, tmp_path, **kw):
    options = {"source_schema": "archive.portal-deployments.v1",
               "expected_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
               "backup_dir": tmp_path / "backup"}
    return migration.migrate_deployment_registry(path, **(options | kw))


def test_registry_dryrun_then_apply_preserves_values_and_strict_reader(tmp_path):
    path = registry(tmp_path)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="schema"):
        load_portal_deployments(path)
    assert run(path, tmp_path)["status"] == "would_migrate"
    assert path.read_bytes() == before and not (tmp_path / "backup").exists()
    result = run(path, tmp_path, apply=True)
    expected = before.replace(b'archive.portal-deployments.v1', b'tfi.portal-deployments.v1')
    assert path.read_bytes() == expected
    assert load_portal_deployments(path)[0].build_id == "preserved"
    assert result["status"] == "migrated"
    assert next((tmp_path / "backup").rglob("*.before")).read_bytes() == before
    assert run(path, tmp_path, apply=True)["status"] == "canonical"


@pytest.mark.parametrize("change", ["stale", "version", "route", "symlink"])
def test_registry_migration_rejects_unverified_input_without_writes(tmp_path, change):
    path = registry(tmp_path)
    options = {}
    if change == "stale":
        options["expected_sha256"] = "0" * 64
    elif change == "version":
        path = registry(tmp_path, "archive.portal-deployments.v2")
        options["source_schema"] = "archive.portal-deployments.v2"
    elif change == "route":
        path.write_text(path.read_text().replace("127.0.0.1", "192.0.2.1"))
    else:
        link = tmp_path / "link.json"
        link.symlink_to(path)
        path = link
    before = path.read_bytes()
    with pytest.raises(ValueError):
        run(path, tmp_path, apply=True, **options)
    assert path.read_bytes() == before and not (tmp_path / "backup").exists()


def test_registry_cli_needs_no_database_and_keeps_default_read_only(tmp_path):
    path = registry(tmp_path)
    before = path.read_bytes()
    names = tmp_path / "private-plan.json"
    names.write_text(json.dumps({"deployment_registry": {
        "source_schema": "archive.portal-deployments.v1",
        "sha256": hashlib.sha256(before).hexdigest()}}))
    command = [sys.executable, str(Path(migration.__file__)), "--names-file", str(names),
               "--deployment-registry", str(path)]
    env = dict(os.environ)
    env.pop("PORTAL_DATABASE_URL", None)
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "would_migrate"
    assert path.read_bytes() == before
