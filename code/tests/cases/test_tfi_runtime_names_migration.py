"""One-time migration tests use synthetic retired spellings and isolated data."""
from copy import deepcopy
from pathlib import Path
import json
import os

import pytest

from scripts import migrate_tfi_runtime_names as migration
from management_portal.service_invocation_contract import digest
from management_portal.society_service.seed_state_marker import read_marker

NAMES = {
    "environment_aliases": {"PORTAL_RESIDENT_ID": ["ARCHIVE_RESIDENT_ID"]},
    "plan_schemas": ["archive.service-invocation-plan.v1"],
    "seed_markers": ["archive-seed-state"],
    "state_file_names": {"archive-login.json": "test-actor-storage-state.json"},
}


def plan():
    batches = [{"batch_id": "batch-000001", "input_fingerprint": "0" * 64}]
    job = {"created_by": "a", "surface": "s", "search_id": "q", "run_generation": 1}
    return {"actor": "a", "surface": "s", "search_id": "q", "generation": 1,
            "batches_json": batches,
            "plan_fingerprint": digest({"schema": NAMES["plan_schemas"][0], **job, "batches": batches})}


def test_explicit_names_and_identity_conflicts_preserve_business_body():
    old = {"params": {"resident_environment": {"ARCHIVE_RESIDENT_ID": "r"}},
           "prompt": "ARCHIVE_RESIDENT_ID is historical text",
           "content_parts": [{"resident_environment": {"ARCHIVE_RESIDENT_ID": "body"}}],
           "payload": {"plan_fingerprint": "old"}}
    before = deepcopy(old)
    new = migration.transform(old, {"old": "new"}, environment_aliases=NAMES["environment_aliases"])
    assert old == before
    assert new["params"]["resident_environment"] == {"PORTAL_RESIDENT_ID": "r"}
    for key in ("prompt", "content_parts", "payload"):
        assert new[key] == old[key]
    assert migration.transform(old) == old
    with pytest.raises(ValueError, match="Conflicting"):
        migration.transform({"resident_environment": {"ARCHIVE_RESIDENT_ID": "a", "PORTAL_RESIDENT_ID": "b"}},
                            environment_aliases=NAMES["environment_aliases"])


def test_plan_migration_validates_digest_manifest_and_is_idempotent():
    old = plan()
    before = deepcopy(old)
    current = migration.migrated_plan(old, NAMES["plan_schemas"])
    assert current != old["plan_fingerprint"] and old == before
    assert migration.migrated_plan({**old, "plan_fingerprint": current}, NAMES["plan_schemas"]) == current
    with pytest.raises(ValueError, match="validation"):
        migration.migrated_plan(old)
    with pytest.raises(ValueError, match="validation"):
        migration.migrated_plan({**old, "plan_fingerprint": "0" * 64}, NAMES["plan_schemas"])
    with pytest.raises(ValueError, match="manifest"):
        migration.migrated_plan({**old, "batches_json": []}, NAMES["plan_schemas"])


@pytest.mark.parametrize("prefix", ["", "\ufeff\n  "])
def test_seed_migration_and_runtime_reader_agree(tmp_path, prefix):
    from management_portal.society_service.templates.explore import _dismissal
    old = prefix + '<!-- archive-seed-state {"status":"dismissed"} -->\r\n# body\r\n'
    new = migration.migrate_seed_text(old, NAMES["seed_markers"])
    assert new == old.replace("archive-seed-state", "tfi-hatg-seed-state", 1)
    assert read_marker(old) is None
    assert read_marker(new) == {"status": "dismissed"}
    assert migration.migrate_seed_text(new, NAMES["seed_markers"]) == new
    file = tmp_path / "seed.md"
    file.write_bytes(new.encode())
    assert _dismissal(file) == {"status": "dismissed"}


def test_invalid_old_marker_removes_only_comment_and_known_dismissal_is_repaired():
    body = "\n# body\n<!-- archive-seed-state invalid -->\n"
    assert migration.migrate_seed_text("<!-- archive-seed-state invalid -->" + body,
                                       NAMES["seed_markers"]) == body
    fixed = migration.migrate_seed_text(body, NAMES["seed_markers"], dismissed=True)
    assert fixed.endswith(body) and read_marker(fixed) == {"status": "dismissed"}
    assert migration.migrate_seed_text(fixed, NAMES["seed_markers"], dismissed=True) == fixed


def test_names_file_rejects_ambiguous_and_canonical_sources(tmp_path):
    path = tmp_path / "names.json"
    path.write_text(json.dumps(NAMES))
    assert migration.load_names(path) == NAMES
    for aliases in [
        {"PORTAL_RESIDENT_ID": ["PORTAL_LINEAGE_ID"]},
        {"PORTAL_RESIDENT_ID": ["ARCHIVE_ID"], "PORTAL_LINEAGE_ID": ["ARCHIVE_ID"]},
    ]:
        path.write_text(json.dumps({**NAMES, "environment_aliases": aliases}))
        with pytest.raises(ValueError):
            migration.load_names(path)


def test_native_shell_path_ignores_retired_fallback_and_allows_explicit_deployment(tmp_path, monkeypatch):
    from management_portal import resident_shell_environment as shell
    monkeypatch.delenv("PORTAL_DATABASE_URL", raising=False)
    monkeypatch.delenv("PORTAL_POSTGRES_ENV_FILE", raising=False)
    monkeypatch.setattr(shell.Path, "home", lambda: tmp_path)
    archive = tmp_path / "archive.env"
    archive.write_text('PORTAL_DATABASE_URL="postgresql://fixture"\n')
    settings = {"deployment": {"compatibility": {"domain_values": {
        "native_shell_database_env_file": str(archive)}}}}
    monkeypatch.setattr(shell, "read_site_config", lambda: settings)
    with pytest.raises(RuntimeError, match="unavailable"):
        shell.portal_database_url()
    settings["deployment"]["native_shell"] = {"database_env_file": str(archive)}
    assert shell.portal_database_url() == "postgresql://fixture"
    del settings["deployment"]["native_shell"]
    canonical = tmp_path / ".config/tfi/portal-postgres.env"
    canonical.parent.mkdir(parents=True)
    canonical.write_text('PORTAL_DATABASE_URL="postgresql://canonical"\n')
    assert shell.portal_database_url() == "postgresql://canonical"


def test_state_dry_run_backup_collision_and_repeat(tmp_path, monkeypatch):
    monkeypatch.setattr(migration, "state_process_references", lambda *roots: {"references": [], "unverified": []})
    old, new = tmp_path / "old", tmp_path / "new"
    for root in (old, new):
        (root / "dev").mkdir(parents=True)
        (root / "dev/runs.lock").touch()
    inode = (new / "dev/runs.lock").stat().st_ino
    original = json.dumps({"artifact": str(old / "dev/artifact"),
                           "storage_state": str(old / "archive-login.json"),
                           "output": str(old / "dev/artifact")}).encode()
    (old / "dev/runs.json").write_bytes(original)
    (old / "dev/artifact").write_bytes(b"evidence")
    (old / "archive-login.json").write_text("{}")
    (old / "link").symlink_to(old / "dev/artifact")
    result = migration.migrate_state_directory(old, new, names=NAMES)
    assert result["status"] == "would_migrate" and old.exists()
    with pytest.raises(ValueError, match="backup"):
        migration.migrate_state_directory(old, new, names=NAMES, apply=True)
    result = migration.migrate_state_directory(old, new, names=NAMES, apply=True, backup_dir=tmp_path / "backups")
    assert result["moved"] and result["metadata"] == 1 and result["symlinks"] == 1
    assert (Path(result["backup"]) / "source/dev/runs.json").read_bytes() == original
    assert (new / "dev/runs.lock").stat().st_ino == inode
    assert not old.exists() and (new / "test-actor-storage-state.json").exists()
    assert (new / "link").resolve() == new / "dev/artifact"
    assert json.loads((new / "dev/runs.json").read_text())["artifact"] == str(new / "dev/artifact")
    assert json.loads((new / "dev/runs.json").read_text())["storage_state"] == str(new / "test-actor-storage-state.json")
    assert json.loads((new / "dev/runs.json").read_text())["output"] == str(old / "dev/artifact")
    assert migration.migrate_state_directory(old, new, names=NAMES)["status"] == "absent"


def test_state_conflict_and_held_lock_retain_both_trees(tmp_path, monkeypatch):
    monkeypatch.setattr(migration, "state_process_references", lambda *roots: {"references": [], "unverified": []})
    from management_common.file_lock import try_lock_descriptor
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    (old / "record").write_text("old")
    (new / "record").write_text("different")
    with pytest.raises(ValueError, match="conflicts"):
        migration.migrate_state_directory(old, new, backup_dir=tmp_path / "backups", apply=True)
    (new / "record").unlink()
    with (old / "work.lock").open("w+b") as stream:
        assert try_lock_descriptor(stream.fileno())
        result = migration.migrate_state_directory(old, new, backup_dir=tmp_path / "backups", apply=True)
        assert result["status"] == "retained_in_use"
    assert (old / "record").read_text() == "old"
    assert not (tmp_path / "backups").exists()


def test_state_rejects_symlink_destination_and_overlapping_roots(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    (old / "nested").mkdir(parents=True)
    (old / "nested/record").write_text("source")
    new.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (new / "nested").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        migration.migrate_state_directory(old, new)
    with pytest.raises(ValueError, match="disjoint"):
        migration.migrate_state_directory(old, old / "nested")
    with pytest.raises(ValueError, match="outside"):
        migration.migrate_state_directory(old, new, backup_dir=old / "backup", apply=True)


def test_open_state_file_and_unknown_platform_prevent_moves(tmp_path, monkeypatch):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    with (old / "active.json").open("w+b"):
        if migration.sys.platform.startswith("linux"):
            result = migration.migrate_state_directory(old, new, apply=True, backup_dir=tmp_path / "backup")
            assert result["status"] == "retained_in_use"
            assert any(ref["pid"] == os.getpid() for ref in result["references"])
    monkeypatch.setattr(migration.sys, "platform", "unknown")
    assert migration.migrate_state_directory(old, new)["status"] == "retained_unverified"
    assert old.exists() and not new.exists()


@pytest.fixture
def database():
    # conftest always starts/selects a separate test PostgreSQL, never the Portal DB.
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(os.environ["PORTAL_POSTGRES_TEST_DSN"], row_factory=dict_row, autocommit=True) as connection:
        for schema in ("service_invocations", "execution_runtime", "lineage"):
            connection.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        connection.execute("CREATE TABLE service_invocations.service_invocations(actor text,surface text,search_id text,generation int,plan_fingerprint text,batches_json jsonb)")
        connection.execute("CREATE TABLE execution_runtime.executions(execution_id text,status text,request_json text,checkpoint_json text)")
        connection.execute("CREATE TABLE lineage.lineage_nodes(node_id text,lineage_id text,node_type text,deleted_at text)")
        connection.execute("CREATE TABLE lineage.lineage_components(node_id text,config_json jsonb)")
        try:
            yield connection
        finally:
            connection.execute("DROP TABLE service_invocations.service_invocations,execution_runtime.executions,lineage.lineage_nodes,lineage.lineage_components")


def test_database_apply_seed_write_and_resume_preserve_frozen_inputs(database, tmp_path):
    from psycopg.types.json import Jsonb
    row = plan()
    database.execute("INSERT INTO service_invocations.service_invocations VALUES(%s,%s,%s,%s,%s,%s)",
                     (*[row[k] for k in ("actor", "surface", "search_id", "generation", "plan_fingerprint")], Jsonb(row["batches_json"])))
    old = {"params": {"resident_environment": {"ARCHIVE_RESIDENT_ID": "r"}},
           "plan_fingerprint": row["plan_fingerprint"], "prompt": "historical ARCHIVE_RESIDENT_ID"}
    for key, status in [("done", "completed"), ("live", "running")]:
        database.execute("INSERT INTO execution_runtime.executions VALUES(%s,%s,%s,NULL)", (key, status, json.dumps(old)))
    root = tmp_path / "seeds"
    (root / "lineage_example").mkdir(parents=True)
    file = root / "lineage_example/idea.md"
    body = b"# preserved business body\r\n"
    file.write_bytes(body)
    database.execute("INSERT INTO lineage.lineage_nodes VALUES('seed_example','lineage_example','seed',NULL)")
    database.execute("INSERT INTO lineage.lineage_components VALUES('seed_example',%s)",
                     (Jsonb({"artifact_path": "idea.md", "status": "dismissed"}),))
    preview = migration.migrate(database, names=NAMES, seed_root=root)
    assert preview["plans"][0]["status"] == "would_migrate" and file.read_bytes() == body
    result = migration.migrate(database, names=NAMES, seed_root=root, apply=True, backup_dir=tmp_path / "backup")
    assert result["plans"][0]["status"] == "migrated"
    assert {r["status"] for r in result["execution_rows"]} == {"migrated", "retained_in_use"}
    assert result["seed_files"][0]["status"] == "migrated"
    assert file.read_bytes().endswith(body)
    assert read_marker(file.read_text()) == {"status": "dismissed"}
    current = migration.migrated_plan(row, NAMES["plan_schemas"])
    record = database.execute("SELECT * FROM execution_runtime.executions WHERE execution_id='done'").fetchone()
    assert json.loads(record["request_json"])["plan_fingerprint"] == current
    assert json.loads(record["request_json"])["prompt"] == old["prompt"]
    database.execute("UPDATE execution_runtime.executions SET status='completed' WHERE execution_id='live'")
    resumed = migration.migrate(database, names=NAMES, apply=True, backup_dir=tmp_path / "backup")
    assert resumed["plans"][0]["status"] == "canonical"
    assert {r["execution_id"]: r["status"] for r in resumed["execution_rows"]} == {
        "done": "historical_text_only", "live": "migrated"}
    assert all(r["status"] == "historical_text_only" for r in migration.migrate(database, names=NAMES)["execution_rows"])


def test_seed_preflight_rejects_escape_before_any_file_write(database, tmp_path):
    from psycopg.types.json import Jsonb
    root = tmp_path / "seeds"
    folder = root / "lineage_example"
    folder.mkdir(parents=True)
    file = folder / "idea.md"
    file.write_bytes(b"# preserved\n")
    for key, artifact in [("safe", "idea.md"), ("unsafe", "../../outside.md")]:
        database.execute("INSERT INTO lineage.lineage_nodes VALUES(%s,'lineage_example','seed',NULL)", (key,))
        database.execute("INSERT INTO lineage.lineage_components VALUES(%s,%s)",
                         (key, Jsonb({"artifact_path": artifact, "status": "dismissed"})))
    with pytest.raises(ValueError, match="escapes"):
        migration.migrate(database, names=NAMES, seed_root=root, apply=True, backup_dir=tmp_path / "backup")
    assert file.read_bytes() == b"# preserved\n"


def test_changed_seed_file_is_reported_without_claiming_success(database, tmp_path, monkeypatch):
    from psycopg.types.json import Jsonb
    root = tmp_path / "seeds"
    (root / "lineage_example").mkdir(parents=True)
    file = root / "lineage_example/idea.md"
    file.write_bytes(b"# preserved\n")
    database.execute("INSERT INTO lineage.lineage_nodes VALUES('seed','lineage_example','seed',NULL)")
    database.execute("INSERT INTO lineage.lineage_components VALUES('seed',%s)",
                     (Jsonb({"artifact_path": "idea.md", "status": "dismissed"}),))
    def changed(path, before, after):
        raise ValueError("File changed since inspection")
    monkeypatch.setattr(migration, "replace_file", changed)
    report = migration.migrate(database, names=NAMES, seed_root=root, apply=True, backup_dir=tmp_path / "backup")
    assert report["seed_files"][0]["status"] == "retained_changed"
    assert file.read_bytes() == b"# preserved\n"
    receipt = json.loads((Path(report["backup"]) / "result.json").read_text())
    assert receipt["seed_files"] == report["seed_files"]
