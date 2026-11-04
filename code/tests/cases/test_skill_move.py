"""Skill and registered Knowledge use one relocation journal and identity lock."""
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from unittest.mock import Mock

import pytest

from management_portal.resources import knowledge_move as move
from management_portal.resources.directory_registrations import register_owned
from management_portal.resources.registry_store import get_registry_store
from skill_board_lan import skill_backend as skills
from tests.cases.test_knowledge_move import relocation, invoke, ACTOR  # noqa: F401

SKILL = "skill_2a6c9d0f3b71"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install_skill(relocation, monkeypatch, *, package=False, nested=False, project_field=True):
    normal, _payload, *_ = relocation
    projects_root = normal.parents[3]
    base = projects_root / "AlignMe" / "knowledge_base" / "skill"
    root = base / ("writing" if package else "writing.md")
    primary = root / ("docs/guide.md" if nested else "SKILL.md") if package else root
    primary.parent.mkdir(parents=True)
    header = f"id: {SKILL}" if package else f"kb_id: {SKILL}\ntype: skill"
    if project_field:
        header += "\nproject: AlignMe"
    primary.write_bytes((f"---\n{header}\nname: writing\n---\n# Writing\n"
                         "See thinking_abcdef012345 and ./image.bin.\n").encode())
    if package:
        (root / "image.bin").write_bytes(b"\0\xffpayload")
        if nested:
            (root / "skill.yaml").write_text("primary_file: docs/guide.md\n")
    register_owned("knowledge", SKILL, [root])
    monkeypatch.setattr(skills, "PROJECTS_ROOT", projects_root)
    def record(*_args, **_kwargs):
        document = get_registry_store().get("knowledge", SKILL)
        if document is None:
            raise KeyError(SKILL)
        path, package_root = move._paths(document, SKILL)
        return {"resource_id": SKILL, "resource_type": "skill", "facets": ["skill"],
                "source": {"path": str(path)}, "attributes": {
                    "project_ids": ["Destination" if "Destination" in path.parts else "AlignMe"],
                    "content_root": str(package_root or path.parent), "single_file": package_root is None}}
    monkeypatch.setattr(skills, "_resolve_skill_record", record)
    return primary, root, {"items": [{"resource_id": SKILL, "expected_version": digest(primary)}]}


@pytest.mark.parametrize("package,nested,project_field", [(False, False, True), (True, False, False), (True, True, True)])
def test_skill_move_preserves_registered_package_and_digest(relocation, monkeypatch, package, nested, project_field):
    primary, root, payload = install_skill(relocation, monkeypatch, package=package,
                                            nested=nested, project_field=project_field)
    before = primary.read_bytes().split(b"---", 2)[2]
    neighbor = root.parent / "unrelated.md"
    neighbor.write_text("belongs to another resource")
    inode = (root / "image.bin").stat().st_ino if package else None
    callback = Mock()
    result = invoke(payload, callback)
    assert result["status"] == "completed", result.get("error")
    record = result["committed"][0]
    path = Path(record["path"])
    assert path.read_bytes().split(b"---", 2)[2] == before
    assert record["version"] == digest(path)
    assert record["single_file"] is not package
    assert record["project_ids"] == ["Destination"]
    assert not root.exists() and neighbor.read_text() == "belongs to another resource"
    if package:
        new_root = Path(record["content_root"])
        assert (new_root / "image.bin").stat().st_ino == inode
        assert (new_root / "image.bin").read_bytes() == b"\0\xffpayload"
        assert path.relative_to(new_root) == primary.relative_to(root)
    assert invoke(payload, callback) == result and callback.call_count == 1


@pytest.mark.parametrize("version", [1, {"number": 1}, "f" * 64])
def test_skill_requires_current_digest_before_any_write(relocation, monkeypatch, version):
    primary, root, payload = install_skill(relocation, monkeypatch)
    payload["items"][0]["expected_version"] = version
    before = primary.read_bytes()
    with pytest.raises((ValueError, move.ResourceVersionConflict)):
        invoke(payload)
    assert primary.read_bytes() == before
    assert not (root.parents[2].parent / "Destination" / "knowledge_base" / "skill").exists()


def test_skill_package_preparation_keeps_source_bytes_and_resumes(relocation, monkeypatch):
    primary, root, payload = install_skill(relocation, monkeypatch, package=True)
    before = primary.read_bytes()
    original_commit = move._commit
    observed = []
    def interrupted(journal, plan):
        observed.append(primary.read_bytes())
        raise OSError("simulated database unavailable")
    monkeypatch.setattr(move, "_commit", interrupted)
    failed = invoke(payload)
    assert failed["status"] == "partial" and failed["committed"] == []
    assert observed == [before] and primary.read_bytes() == before
    monkeypatch.setattr(move, "_commit", original_commit)
    result = invoke(payload)
    assert result["status"] == "completed", result
    assert not root.exists()


def test_skill_projection_failure_retries_without_rewriting_primary(relocation, monkeypatch):
    primary, root, payload = install_skill(relocation, monkeypatch, package=True)
    failed = invoke(payload, Mock(side_effect=RuntimeError("index unavailable")))
    assert failed["status"] == "partial" and len(failed["committed"]) == 1
    new_primary = Path(failed["committed"][0]["path"])
    inode, version = new_primary.stat().st_ino, digest(new_primary)
    assert root.exists()
    result = invoke(payload)
    assert result["status"] == "completed" and not root.exists()
    assert new_primary.stat().st_ino == inode and digest(new_primary) == version


@pytest.mark.parametrize("operation", ["edit", "delete"])
def test_waiting_skill_writer_resolves_location_under_identity_lock(relocation, monkeypatch, operation):
    primary, root, payload = install_skill(relocation, monkeypatch, package=True, nested=True)
    from management_portal.resources.workspace_store import get_workspace_store
    get_workspace_store()
    entered, attempted = threading.Event(), threading.Event()
    from management_portal.resources import knowledge_move_locks
    from contextlib import contextmanager
    original_lock = knowledge_move_locks.knowledge_identity_lock
    @contextmanager
    def observed_lock(rid):
        attempted.set()
        with original_lock(rid):
            yield
    monkeypatch.setattr(knowledge_move_locks, "knowledge_identity_lock", observed_lock)
    results = []
    def writer():
        assert entered.wait(5)
        results.append(skills.save_address_text(SKILL + "/file/docs/guide.md", "edited", actor=ACTOR)
                       if operation == "edit" else skills.delete_skill(SKILL, actor=ACTOR))
    def project(_records):
        entered.set()
        assert attempted.wait(5)
        assert not results
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(writer)
        result = invoke(payload, project)
        future.result(timeout=10)
    assert result["status"] == "completed", result
    assert results[0][0] == 200, results
    new_primary = Path(result["committed"][0]["path"])
    if operation == "edit":
        assert new_primary.read_text() == "edited"
        assert results[0][1]["project_ids"] == ["Destination"]
    else:
        from management_common.directory_cleanup_store import get_directory_cleanup_store
        get_directory_cleanup_store().run_pending()
        assert not new_primary.exists()
    assert not root.exists()


def test_registered_single_markdown_moves_without_synthetic_companion(relocation):
    primary, payload, *_ = relocation
    rid = payload["items"][0]["resource_id"]
    with get_registry_store().writer("knowledge") as writer:
        doc = writer.get("knowledge", rid)
        doc["directories"] = doc["directories"][:1]
        writer.put("knowledge", rid, doc)
    companion = primary.parent / rid
    result = invoke(payload)
    assert result["status"] == "completed", result
    assert companion.exists()
    new_primary = Path(result["committed"][0]["path"])
    assert not (new_primary.parent / rid).exists()
    assert len(get_registry_store().get("knowledge", rid)["directories"]) == 1


def test_directory_knowledge_moves_without_flattening_or_copying_payload(relocation):
    primary, payload, *_ = relocation
    rid = payload["items"][0]["resource_id"]
    root = primary.parent / "research-notes"
    root.mkdir()
    packaged = root / "README.md"
    primary.rename(packaged)
    attachment = root / "experiment.bin"
    attachment.write_bytes(b"experiment data")
    inode = attachment.stat().st_ino
    with get_registry_store().writer("knowledge") as writer:
        doc = writer.get("knowledge", rid)
        doc["directories"] = [{"path": str(root), "owner_id": rid}]
        writer.put("knowledge", rid, doc)
    result = invoke(payload)
    assert result["status"] == "completed", result.get("error")
    record = result["committed"][0]
    moved = Path(record["content_root"])
    assert moved.name == root.name and Path(record["path"]) == moved / "README.md"
    assert (moved / "experiment.bin").stat().st_ino == inode
    assert not root.exists()
    assert get_registry_store().get("knowledge", rid)["directories"][0]["path"] == str(moved)


def test_skill_scan_cannot_claim_prepared_target_or_retired_source(relocation, monkeypatch):
    primary, root, payload = install_skill(relocation, monkeypatch, package=True)
    real_commit = move._commit
    def check_prepared(journal, plan):
        target_root = Path(plan["result"]["content_root"])
        assert register_owned("knowledge", SKILL, [target_root], scanning=True) is None
        assert register_owned("knowledge", SKILL, [root], scanning=True)
        return real_commit(journal, plan)
    monkeypatch.setattr(move, "_commit", check_prepared)
    def check_committed(records):
        assert register_owned("knowledge", SKILL, [root], scanning=True) is None
        assert register_owned("knowledge", SKILL, [Path(records[0]["content_root"])], scanning=True)
    assert invoke(payload, check_committed)["status"] == "completed"


def test_registered_directory_knowledge_delete_cleans_files(relocation, monkeypatch):
    from knowledge_board_lan import knowledge_backend
    from management_common.directory_cleanup_store import get_directory_cleanup_store
    from management_portal.resources.workspace_store import get_workspace_store
    get_workspace_store()
    primary, payload, *_ = relocation
    rid = payload["items"][0]["resource_id"]
    monkeypatch.setattr(knowledge_backend, "resolve_item_record", lambda *a, **kw: {
        "kb_id": rid, "type": "thinking", "path": str(primary), "project_ids": ["AlignMe"]})
    result = knowledge_backend.delete_item(rid, actor=ACTOR)
    assert result["deleted"] and result["cleanup_ids"]
    get_directory_cleanup_store().run_pending()
    assert not primary.exists() and not (primary.parent / rid).exists()
