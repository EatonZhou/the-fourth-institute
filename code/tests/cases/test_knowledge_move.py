"""Knowledge move commits and recovery share the production registry owner.

:module: resources.types
:design: decision_3ece26686464
"""
from pathlib import Path
from unittest.mock import Mock

import pytest

from knowledge_board_lan import knowledge_backend as kb
from knowledge_board_lan.knowledge_source import _yaml_front_matter
from management_portal.resources import knowledge_move as move
from management_portal.resources.directory_registrations import register_owned
from management_portal.resources.registry_store import get_registry_store
from tests.cases.test_knowledge_versioning import _markdown, KNOWLEDGE_ID


ACTOR = "resident_0123456789ab"
SECOND = "thinking_abcdef012345"


@pytest.fixture
def relocation(tmp_path, monkeypatch):
    projects = {key: {"id": key, "path": str(tmp_path / key)} for key in ("AlignMe", "Destination")}
    for p in projects.values():
        Path(p["path"]).mkdir()
    monkeypatch.setattr(kb, "_parse_projects_yaml", lambda: list(projects.values()))
    monkeypatch.setattr(kb, "_get_tag_config", lambda: (["thinking"], {"thinking": "thinking"}, {}, "test"))
    permissions = Mock(side_effect=lambda actor, instruction, parameter, ids: set(ids))
    monkeypatch.setattr(move.permission_system, "permitted_ids", permissions)
    sync = Mock()
    monkeypatch.setattr(move.permission_system, "sync_resource", sync)
    def create(rid=KNOWLEDGE_ID):
        source = tmp_path / "AlignMe" / "knowledge_base" / "thinking" / (rid + ".md")
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(_markdown().replace(KNOWLEDGE_ID, rid), encoding="utf-8")
        companion = source.parent / rid
        (companion / "history").mkdir(parents=True)
        (companion / "history" / "v1.md").write_text("historical version")
        (companion / "diagram.bin").write_bytes(b"\0\xffimage")
        register_owned("knowledge", rid, [source, companion])
        return source
    source = create()
    payload = {"items": [{"resource_id": KNOWLEDGE_ID, "expected_version": 3}]}
    return source, payload, create, permissions, sync


def invoke(payload, callback=None):
    return move.move_knowledge("Destination", payload, actor=ACTOR, project_batch=callback or Mock())


def current(rid=KNOWLEDGE_ID):
    document = get_registry_store().get("knowledge", rid)
    return move._paths(document, rid)[0]


def test_batch_moves_all_content_preserves_audit_and_retries_without_version_increment(relocation):
    source, payload, create, permissions, sync = relocation
    other = create(SECOND)
    payload["items"].append({"resource_id": SECOND, "expected_version": {"number": 3}})
    before = source.read_text()
    callback = Mock()
    result = invoke(payload, callback)
    assert result["status"] == "completed" and result["projected"] and not result["pending"]
    assert len(result["committed"]) == 2
    assert callback.call_count == 1 and len(callback.call_args.args[0]) == 2
    assert not source.exists() and not other.exists()
    after = current().read_text()
    old, new = _yaml_front_matter(before), _yaml_front_matter(after)
    assert new["project"] == "Destination" and new["version"]["number"] == 4
    assert old["version"]["created_by"] == new["version"]["created_by"]
    assert old["version"]["created_at"] == new["version"]["created_at"]
    assert {k: v for k, v in old.items() if k not in {"version", "project"}} == {
        k: v for k, v in new.items() if k not in {"version", "project"}}
    assert before.split("---", 2)[2] == after.split("---", 2)[2]
    assert (current().parent / KNOWLEDGE_ID / "history" / "v1.md").read_text() == "historical version"
    assert (current().parent / KNOWLEDGE_ID / "diagram.bin").read_bytes() == b"\0\xffimage"
    assert sync.call_count == 2 and sync.call_args.kwargs["connection"] is not None
    replay = invoke(payload, callback)
    assert replay == result and callback.call_count == 1 and sync.call_count == 2
    assert current().read_text() == after


@pytest.mark.parametrize("problem", ["stale", "denied", "occupied", "symlink", "duplicate", "skill"])
def test_full_batch_preflight_leaves_first_resource_untouched(relocation, monkeypatch, problem):
    source, payload, create, permissions, sync = relocation
    other = create(SECOND)
    payload["items"].append({"resource_id": SECOND, "expected_version": 3})
    before = source.read_bytes()
    if problem == "stale":
        payload["items"][1]["expected_version"] = 2
    elif problem == "denied":
        permissions.side_effect = lambda actor, instruction, parameter, ids: set(ids) - {SECOND}
    elif problem in {"occupied", "symlink"}:
        target = source.parents[3] / "Destination" / "knowledge_base" / "thinking" / other.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if problem == "occupied":
            target.write_text("Someone else's data")
        else:
            target.symlink_to(other)
    elif problem == "duplicate":
        payload["items"][1]["resource_id"] = KNOWLEDGE_ID
    else:
        payload["items"][1]["resource_id"] = "skill_abcdef012345"
    with pytest.raises((ValueError, RuntimeError, FileExistsError, PermissionError)):
        invoke(payload)
    assert source.read_bytes() == before and current() == source
    sync.assert_not_called()


class Crash(BaseException):
    pass


@pytest.mark.parametrize("point", ["after_publish", "before_commit", "after_commit"])
def test_process_interruption_resumes_durable_receipt(relocation, monkeypatch, point):
    source, payload, _create, _permissions, sync = relocation
    real_commit = move._commit
    real_rename = move.rename_directory_no_replace
    def rename(*args):
        real_rename(*args)
        raise Crash()
    def commit(*args):
        if point == "after_commit":
            real_commit(*args)
        raise Crash()
    with monkeypatch.context() as patch:
        if point == "after_publish":
            patch.setattr(move, "rename_directory_no_replace", rename)
        else:
            patch.setattr(move, "_commit", commit)
        with pytest.raises(Crash):
            invoke(payload)
    assert source.exists()  # readers of the previous live projection still work
    assert current().exists()
    result = invoke(payload)
    assert result["status"] == "completed" and not source.exists()
    assert _yaml_front_matter(current().read_text())["version"]["number"] == 4
    assert sync.call_count == 1


def test_registry_and_permission_failure_roll_back_together(relocation, monkeypatch):
    source, payload, _create, _permissions, sync = relocation
    sync.side_effect = RuntimeError("permission facts unavailable")
    result = invoke(payload)
    assert result["status"] == "partial" and not result["committed"]
    assert result["pending"] == [KNOWLEDGE_ID] and current() == source
    assert source.exists()
    sync.side_effect = None
    assert invoke(payload)["status"] == "completed"
    assert _yaml_front_matter(current().read_text())["version"]["number"] == 4


def test_partial_batch_and_projection_failure_retry_only_uncommitted_versions(relocation, monkeypatch):
    source, payload, create, _permissions, sync = relocation
    other = create(SECOND)
    payload["items"].append({"resource_id": SECOND, "expected_version": 3})
    real = move._commit
    def fail_second(journal, plan):
        if plan["resource_id"] == SECOND:
            raise RuntimeError("second temporarily unavailable")
        real(journal, plan)
    callback = Mock()
    with monkeypatch.context() as patch:
        patch.setattr(move, "_commit", fail_second)
        result = invoke(payload, callback)
    assert result["status"] == "partial" and result["pending"] == [SECOND]
    assert [r["resource_id"] for r in result["committed"]] == [KNOWLEDGE_ID]
    assert other.exists() and not source.exists()
    callback.side_effect = RuntimeError("live index unavailable")
    result = invoke(payload, callback)
    assert result["status"] == "partial" and not result["pending"] and not result["projected"]
    callback.side_effect = None
    assert invoke(payload, callback)["status"] == "completed"
    assert sync.call_count == 2
    assert all(_yaml_front_matter(current(rid).read_text())["version"]["number"] == 4
               for rid in (KNOWLEDGE_ID, SECOND))


def test_operation_id_cannot_change_request(relocation):
    source, payload, _create, _permissions, _sync = relocation
    payload["operation_id"] = "stable-operation"
    assert invoke(payload)["status"] == "completed"
    payload["items"][0]["expected_version"] = 4
    with pytest.raises(ValueError, match="different arguments"):
        invoke(payload)


def test_companion_symlink_rejected_before_copy(relocation, tmp_path):
    source, payload, _create, _permissions, sync = relocation
    secret = tmp_path / "unrelated.txt"
    secret.write_text("unrelated")
    (source.parent / KNOWLEDGE_ID / "link").symlink_to(secret)
    with pytest.raises(ValueError, match="regular files"):
        invoke(payload)
    sync.assert_not_called()
    assert current() == source


def test_source_update_during_interruption_releases_prepared_targets_for_new_cas(relocation, monkeypatch):
    source, payload, _create, _permissions, sync = relocation
    with monkeypatch.context() as patch:
        patch.setattr(move, "_commit", lambda *args: (_ for _ in ()).throw(Crash()))
        with pytest.raises(Crash):
            invoke(payload)
    source.write_text(source.read_text().replace("number: 3", "number: 4"))
    result = invoke(payload)
    assert result["status"] == "partial" and not result["committed"]
    assert "changed" in result["error"] and current() == source
    payload["items"][0]["expected_version"] = 4
    assert invoke(payload)["status"] == "completed"
    assert _yaml_front_matter(current().read_text())["version"]["number"] == 5
    assert sync.call_count == 1


def test_current_version_is_projected_after_interrupted_move_and_later_edit(relocation):
    source, payload, _create, _permissions, _sync = relocation
    callback = Mock(side_effect=RuntimeError("projection offline"))
    result = invoke(payload, callback)
    assert result["status"] == "partial" and not result["projected"] and source.exists()
    target = current()
    target.write_text(target.read_text().replace("number: 4", "number: 5").replace(
        "# Versioned Knowledge", "# A subsequent edit"))
    captured = []
    def read_current(records):
        captured.extend(Path(record["path"]).read_text() for record in records)
    assert invoke(payload, read_current)["status"] == "completed"
    assert "number: 5" in captured[0] and "# A subsequent edit" in captured[0]


def test_projection_callback_keeps_all_resource_mutation_scopes_locked(relocation):
    import threading
    source, payload, create, _permissions, _sync = relocation
    create(SECOND)
    payload["items"].append({"resource_id": SECOND, "expected_version": 3})
    acquired = threading.Event()
    attempted = threading.Event()
    threads = []
    def callback(records):
        def concurrent_writer():
            attempted.set()
            with move.mutation_projection_scope(SECOND):
                acquired.set()
        thread = threading.Thread(target=concurrent_writer)
        threads.append(thread)
        thread.start()
        assert attempted.wait(1)
        assert not acquired.wait(0.03)
    assert invoke(payload, callback)["status"] == "completed"
    threads[0].join(1)
    assert acquired.is_set()


def test_crlf_and_quoted_project_preserve_body_bytes(relocation):
    source, payload, _create, _permissions, _sync = relocation
    before = source.read_bytes().replace(b"project: AlignMe", b'"project": "AlignMe" # keep comment')
    before = b"\xef\xbb\xbf" + before.replace(b"\n", b"\r\n")
    source.write_bytes(before)
    assert invoke(payload)["status"] == "completed"
    after = current().read_bytes()
    assert before.split(b"---", 2)[2] == after.split(b"---", 2)[2]
    assert b'"project": "Destination" # keep comment' in after


def test_waiting_editor_resolves_new_location_under_stable_identity_lock(relocation, monkeypatch):
    import threading
    source, payload, _create, _permissions, _sync = relocation
    resolved_old = threading.Event()
    finished = threading.Event()
    failures = []
    first = True
    def resolve(*args, **kwargs):
        nonlocal first
        path = current()
        if first:
            first = False
            resolved_old.set()
        return {"kb_id": KNOWLEDGE_ID, "path": str(path), "selected_path": str(path),
                "selected_file_id": "primary", "type": "thinking", "project": "Destination"}
    monkeypatch.setattr(kb, "resolve_item_address", resolve)
    def edit():
        try:
            kb.update_item_text(KNOWLEDGE_ID, body="newer edit", actor=ACTOR, expected_version=4)
        except BaseException as error:
            failures.append(error)
        finally:
            finished.set()
    threads = []
    def callback(records):
        thread = threading.Thread(target=edit)
        threads.append(thread)
        thread.start()
        assert resolved_old.wait(1)
        assert not finished.wait(0.03)
    assert invoke(payload, callback)["status"] == "completed"
    threads[0].join(2)
    assert finished.is_set() and not failures
    assert current().read_text().endswith("newer edit")
    assert _yaml_front_matter(current().read_text())["version"]["number"] == 5
    assert not source.exists()


def test_noop_same_project_preserves_version(relocation):
    source, payload, _create, _permissions, sync = relocation
    before = source.read_bytes()
    result = move.move_knowledge("AlignMe", payload, actor=ACTOR, project_batch=Mock())
    assert result["status"] == "completed" and result["committed"][0]["version"]["number"] == 3
    assert source.read_bytes() == before
    sync.assert_not_called()


def test_seventeen_item_batch_uses_one_projection(relocation):
    import time
    source, payload, create, _permissions, _sync = relocation
    source.write_text(source.read_text() + "Large body\n" * 66000)
    for n in range(1, 17):
        rid = f"thinking_{n:012x}"
        create(rid)
        payload["items"].append({"resource_id": rid, "expected_version": 3})
    callback = Mock()
    started = time.monotonic()
    result = invoke(payload, callback)
    elapsed = time.monotonic() - started
    assert result["status"] == "completed" and len(result["committed"]) == 17
    callback.assert_called_once()
    print(f"Knowledge move: 17 items incl 726KB body, one projection: {elapsed:.3f}s")


def test_prepared_target_is_not_discovered_by_old_scan(relocation, monkeypatch):
    source, payload, _create, _permissions, _sync = relocation
    real = move._commit
    observations = []
    def before_commit(journal, plan):
        target = move.cleanup_path(plan["target"])
        companion = move.cleanup_path(plan["target_companion"])
        assert target.is_file()
        observations.append(register_owned("knowledge", KNOWLEDGE_ID, [target, companion], scanning=True))
        assert register_owned("knowledge", KNOWLEDGE_ID,
                              [source, source.parent / KNOWLEDGE_ID], scanning=True)
        assert current() == source
        real(journal, plan)
    monkeypatch.setattr(move, "_commit", before_commit)
    assert invoke(payload)["status"] == "completed"
    assert observations == [None]


@pytest.mark.parametrize("child", ["repo", "workspace", "retention", "adopted"])
def test_nested_owners_and_adopted_directories_require_own_migration(relocation, child):
    source, payload, _create, _permissions, sync = relocation
    nested = source.parent / KNOWLEDGE_ID / "history"
    if child == "repo":
        with get_registry_store().writer("repo") as writer:
            writer.put("repo", "repo_0123456789ab", {"checkouts": [{"path": str(nested), "origin": "created"}]})
    elif child == "workspace":
        from management_portal.resources.workspace_store import get_workspace_store
        get_workspace_store().ensure("workspace_0123456789ab", str(nested), origin="created", owner_id="fixture")
    elif child == "retention":
        from management_common.directory_retention import RetentionStore
        RetentionStore().declare(nested, category="object_data", owner_id="fixture")
    else:
        with get_registry_store().writer("knowledge") as writer:
            document = writer.get("knowledge", KNOWLEDGE_ID)
            document["directories"][0]["origin"] = "adopted"
            writer.put("knowledge", KNOWLEDGE_ID, document)
    with pytest.raises(ValueError, match="independent|adopted"):
        invoke(payload)
    assert current() == source
    sync.assert_not_called()


def test_source_permission_precedes_any_metadata_version_read(relocation, monkeypatch):
    source, payload, _create, permissions, _sync = relocation
    permissions.side_effect = lambda *args: set()
    spy = Mock(side_effect=AssertionError("must not read unauthorized metadata"))
    monkeypatch.setattr(move, "_read_text", spy)
    with pytest.raises(PermissionError):
        invoke(payload)
    spy.assert_not_called()


def test_full_text_update_cannot_leave_half_migrated_project(relocation, monkeypatch):
    source, _payload, _create, _permissions, _sync = relocation
    monkeypatch.setattr(kb, "resolve_item_address", lambda *a, **k: {
        "kb_id": KNOWLEDGE_ID, "path": str(source), "selected_path": str(source),
        "selected_file_id": "primary", "type": "thinking"})
    before = source.read_text()
    with pytest.raises(ValueError, match="knowledge.move"):
        kb.update_item_text(KNOWLEDGE_ID, before.replace("project: AlignMe", "project: Destination"),
                            actor=ACTOR, expected_version=3)
    assert source.read_text() == before
