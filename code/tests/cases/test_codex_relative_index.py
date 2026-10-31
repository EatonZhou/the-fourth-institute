"""Codex rollout indexes survive account-root relocation."""
import json
from pathlib import Path
import shutil
import sqlite3
import threading
import time

import pytest

from management_portal.execution.codex_rollout_index import prepare_account_index

THREAD = "01a040e2-0340-7dc3-a74b-233978f3d546"


def index_fixture(home, stored=None, metadata_id=THREAD):
    relative = f"sessions/2026/08/27/rollout-{THREAD}.jsonl"
    rollout = home / relative
    rollout.parent.mkdir(parents=True)
    rollout.write_text(json.dumps({"type": "session_meta", "payload": {"id": metadata_id}}) + "\n")
    database = home / "state_5.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL)")
        connection.execute("INSERT INTO threads VALUES (?, ?)", (THREAD, stored or str(rollout)))
    return database, relative


def test_migration_backs_up_and_preserves_missing_or_mismatched_sources(tmp_path):
    home = tmp_path / "configured-account"
    database, relative = index_fixture(home, f"/previous/deployment/{'sessions/2026/08/27/rollout-' + THREAD + '.jsonl'}")
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO threads VALUES ('missing', '/previous/sessions/missing.jsonl')")
        connection.execute("INSERT INTO threads VALUES ('other', ?)", (str(home / relative),))
    result = prepare_account_index(home)["databases"][0]
    assert result["updated"] == 1
    assert set(result["unresolved"]) == {"missing", "other"}
    with sqlite3.connect(result["backup"]) as connection:
        assert connection.execute("SELECT rollout_path FROM threads WHERE id=?", (THREAD,)).fetchone()[0].startswith("/previous/")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT rollout_path FROM threads WHERE id=?", (THREAD,)).fetchone()[0] == relative
    assert prepare_account_index(home)["databases"][0]["updated"] == 0


def test_insert_resume_and_archive_writers_keep_relative_paths_after_move(tmp_path):
    home = tmp_path / "old-account"
    database, relative = index_fixture(home)
    prepare_account_index(home)
    relocated = tmp_path / "different-root" / "account"
    relocated.parent.mkdir()
    shutil.move(home, relocated)
    assert prepare_account_index(relocated)["databases"][0]["updated"] == 0
    with sqlite3.connect(relocated / database.name) as connection:
        connection.execute("PRAGMA recursive_triggers=ON")
        connection.execute("UPDATE threads SET rollout_path=? WHERE id=?", (str(relocated / relative), THREAD))
        assert connection.execute("SELECT rollout_path FROM threads WHERE id=?", (THREAD,)).fetchone()[0] == relative
        archived = f"archived_sessions/rollout-{THREAD}.jsonl"
        connection.execute("UPDATE threads SET rollout_path=? WHERE id=?", (str(relocated / archived), THREAD))
        connection.execute("INSERT INTO threads VALUES ('new-thread', ?)", (str(relocated / "sessions/new.jsonl"),))
        assert dict(connection.execute("SELECT id,rollout_path FROM threads")) == {THREAD: archived, "new-thread": "sessions/new.jsonl"}


def test_missing_database_is_not_bootstrapped_with_a_partial_vendor_schema(tmp_path):
    home = tmp_path / "new-account"
    home.mkdir()
    assert prepare_account_index(home)["databases"] == []
    assert not list(home.glob("*.sqlite"))


def test_source_identity_mismatch_and_path_traversal_are_not_rebased(tmp_path):
    home = tmp_path / "account"
    database, relative = index_fixture(home, metadata_id="different-thread")
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO threads VALUES ('escape', '/old/sessions/../outside.jsonl')")
    result = prepare_account_index(home)["databases"][0]
    assert result["updated"] == 0
    assert set(result["unresolved"]) == {THREAD, "escape"}


@pytest.mark.parametrize("metadata", [[], {"type": "session_meta", "payload": None}])
def test_malformed_source_metadata_is_reported_without_mutation(tmp_path, metadata):
    home = tmp_path / "account"
    database, relative = index_fixture(home)
    (home / relative).write_text(json.dumps(metadata) + "\n")
    result = prepare_account_index(home)["databases"][0]
    assert result["updated"] == 0 and result["unresolved"] == [THREAD]


def test_service_template_uses_configured_home_and_explicit_owned_refresh(tmp_path, monkeypatch):
    from scripts import portal_instance_storage as storage, portal_service_units as units
    instance = tmp_path / "instance"
    directory = tmp_path / "units"
    deployment = {"source_root": str(tmp_path / "source"), "providers": {"codex_binary": "/usr/bin/true"}}
    monkeypatch.setattr(storage, "read_site_config", lambda: {"deployment": deployment})
    monkeypatch.setattr(units, "_isolated_codex_context", lambda: {"root": instance, "directory": directory, "prefix": "tfi-example-codex-app-server@"})
    target = units.install_codex_unit_template(reload=False)
    original = target.read_text()
    assert "-m management_portal.codex_app_server_host --socket " in original
    assert "Environment=PYTHONPATH=" + deployment["source_root"] in original
    assert "--account-config " in original and "/provider-accounts/codex/%i/launch.json" in original
    deployment["source_root"] = str(tmp_path / "moved-source")
    with pytest.raises(ValueError, match="different Provider"):
        units.install_codex_unit_template(reload=False)
    old_locator = next(line for line in target.read_text().splitlines() if line.startswith("Environment=PORTAL_PATHS_CONFIG="))
    target.write_text(target.read_text().replace(old_locator, f"Environment=PORTAL_PATHS_CONFIG={instance / 'config/target.json'}"))
    units.install_codex_unit_template(reload=False, refresh=True)
    assert "Environment=PYTHONPATH=" + deployment["source_root"] in target.read_text()
    target.write_text(target.read_text().replace("Environment=HOME=", "Environment=FOREIGN_HOME="))
    deployment["source_root"] = str(tmp_path / "another-source")
    with pytest.raises(ValueError, match="another instance"):
        units.install_codex_unit_template(reload=False, refresh=True)


def test_native_history_resume_and_fork_survive_configured_account_move(tmp_path, installed_provider_paths):
    from management_portal.codex_app_server_client import CodexAppServerClient
    from management_portal.codex_app_server_host import run_host
    binary = installed_provider_paths["codex_binary"]
    if not binary or not binary.is_file():
        pytest.skip("Installed Codex runtime unavailable")
    endpoint = tmp_path / "codex.sock"
    original = tmp_path / "account"
    original.mkdir()
    failures = []

    def start(home):
        stop = threading.Event()
        def run():
            try:
                run_host(endpoint, binary, home, stop, startup_timeout=10)
            except BaseException as error:
                failures.append(error)
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        deadline = time.monotonic() + 12
        while not endpoint.exists() and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(.02)
        assert endpoint.exists(), failures
        return stop, worker, CodexAppServerClient(endpoint)

    stop, worker, client = start(original)
    try:
        thread = client.request("thread/start", {"cwd": str(tmp_path)})["thread"]["id"]
        client.request("thread/inject_items", {"threadId": thread, "items": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "migration fixture"}]}]})
        with sqlite3.connect(next(original.glob("state_*.sqlite"))) as connection:
            stored = connection.execute("SELECT rollout_path FROM threads WHERE id=?", (thread,)).fetchone()[0]
            assert not Path(stored).is_absolute()
    finally:
        client.close(); stop.set(); worker.join(12)
    assert not failures and not worker.is_alive()
    moved = tmp_path / "moved-account"
    shutil.move(original, moved)
    stop, worker, client = start(moved)
    try:
        client.request("thread/turns/list", {"threadId": thread, "limit": 1, "itemsView": "summary"})
        client.request("thread/resume", {"threadId": thread, "excludeTurns": True})
        client.request("thread/fork", {"threadId": thread, "excludeTurns": True})
        with sqlite3.connect(next(moved.glob("state_*.sqlite"))) as connection:
            rows = connection.execute("SELECT rollout_path FROM threads").fetchall()
            assert len(rows) >= 2
            assert all(not Path(row[0]).is_absolute() and (moved / row[0]).is_file() for row in rows)
    finally:
        client.close(); stop.set(); worker.join(12)
    assert not failures and not worker.is_alive()
