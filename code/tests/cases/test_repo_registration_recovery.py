from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from management_portal import permission_system, resource_identity_backend as repos
from management_portal.resources import ResourceService
from management_portal.resources.providers import install_mutation_providers
from management_portal.resources.providers.common import canonical_record
from management_portal.resources.registry_store import get_registry_store
from tests.cases.registry_support import remove_registry_document


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def setup_recovery(tmp_path, monkeypatch, *, duplicate=True):
    original = tmp_path / "before" / "code"
    original.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(original)], check=True)
    (original / "data.txt").write_text("retained source\n")
    old = repos.register_repo_checkout(original, project_ids=["@public"])
    evidence = json.dumps({
        "schema": "tfi.hatg-instruction-result.v2",
        "instruction": "ResolveResource", "instruction_id": "inst_res_fixture",
        "status": "committed", "result": {"resource": canonical_record(old["id"], old)},
    }).encode()
    current = tmp_path / "after"
    original.rename(current)
    remove_registry_document("repo", old["id"])
    replacement = repos.register_repo_checkout(current) if duplicate else None
    index = mock.Mock()
    index.change_targets.return_value = ["resource_registry"]
    index.queue_change.return_value = ["resource_registry"]
    service = ResourceService(index_provider=index, mutation_provider_loader=install_mutation_providers)
    service.read_content = mock.Mock(return_value=(evidence, "application/json", {}))
    monkeypatch.setattr(permission_system, "allows", lambda *a, **kw: True)
    changes = {
        "operation": "restore_registration",
        "evidence_address": "workspace_123456789abc/file/resolve.json",
        "evidence_sha256": hashlib.sha256(evidence).hexdigest(),
        "checkout_moves": [{"from": str(original), "to": str(current)}],
        "replacements": ([{"resource_id": replacement["id"],
                           "expected_sha256": digest(canonical_record(replacement["id"], replacement))}]
                         if replacement else []),
        "reason": "Verified deployment move; restore original resource identity",
    }
    return service, old, replacement, current, changes


@pytest.mark.parametrize("duplicate", [False, True])
def test_restore_repo_identity_preserves_metadata_and_content(tmp_path, monkeypatch, duplicate):
    """迁移恢复沿原ID读取源码，原子替换同工程重复登记，保留历史时间。"""
    service, old, replacement, current, changes = setup_recovery(tmp_path, monkeypatch, duplicate=duplicate)
    restored = service.update(old["id"], changes, actor="tester")
    assert restored["resource_id"] == old["id"]
    assert restored["created_at"] == old["created_at"]
    assert restored["canonical_checkout"] == str(current)
    assert [c["path"] for c in restored["checkouts"]] == [str(current)]
    assert repos.resolve_repo_address(old["id"] + "/file/data.txt")["exists"]
    assert (current / "data.txt").read_text() == "retained source\n"
    if replacement:
        assert get_registry_store().get("repo", replacement["id"]) is None
    service.read_content.assert_called_once_with(
        changes["evidence_address"], actor="tester", max_bytes=1024 * 1024)
    receipt, = get_registry_store().documents("repo_registration_recoveries").values()
    assert receipt["source_record"]["resource_id"] == old["id"]
    assert receipt["actor"] == "tester"
    assert repos.register_repo_checkout(current)["id"] == old["id"]


@pytest.mark.parametrize("failure", ["hash", "concurrent", "wrong_replacement", "denied", "existing", "retired", "suppressed", "extra_checkout", "replacement_denied"])
def test_repo_recovery_conflicts_leave_all_records_intact(tmp_path, monkeypatch, failure):
    """证据、权限或目标CAS不符时，恢复不改任何登记。"""
    service, old, replacement, current, changes = setup_recovery(tmp_path, monkeypatch)
    if failure == "hash":
        changes["evidence_sha256"] = "0" * 64
    elif failure == "concurrent":
        repos.update_repo_policy(replacement["id"], push_enabled=False)
    elif failure == "wrong_replacement":
        changes["replacements"] = []
    elif failure == "denied":
        monkeypatch.setattr(permission_system, "allows", lambda *a, **kw: False)
    elif failure == "replacement_denied":
        monkeypatch.setattr(permission_system, "allows", lambda actor, instruction, args: instruction != "UnregisterResource")
    elif failure == "retired":
        store = get_registry_store()
        with store.writer("repo") as writer:
            writer.connection.execute(f'INSERT INTO "{store.database.schema}".retired_identities(resource_id) VALUES (%s)', (old["id"],))
    elif failure == "suppressed":
        from management_portal.resource_auto_registration import suppress_paths
        suppress_paths("repo", old["id"], [old["canonical_checkout"]])
    elif failure == "extra_checkout":
        extra = tmp_path / "unrelated"
        subprocess.run(["git", "init", "-q", str(extra)], check=True)
        repos.register_repo_checkout(extra, repo_id=replacement["id"])
        changed = get_registry_store().get("repo", replacement["id"])
        changes["replacements"][0]["expected_sha256"] = digest(canonical_record(replacement["id"], changed))
    else:
        with get_registry_store().writer("repo") as writer:
            writer.put("repo", old["id"], old)
    before = get_registry_store().documents("repo")
    with pytest.raises((ValueError, PermissionError)):
        service.update(old["id"], changes, actor="tester")
    assert get_registry_store().documents("repo") == before
    assert get_registry_store().documents("repo_registration_recoveries") == {}


def test_recovery_retry_does_not_rewrite_registration_or_duplicate_receipt(tmp_path, monkeypatch):
    """投影失败后同一恢复请求可重试，登记版本与恢复记录保持不变。"""
    service, old, replacement, current, changes = setup_recovery(tmp_path, monkeypatch)
    first = service.update(old["id"], changes, actor="tester")
    before = get_registry_store().snapshots("repo", "repo_registration_recoveries")
    again = service.update(old["id"], changes, actor="tester")
    assert again == first
    assert get_registry_store().snapshots("repo", "repo_registration_recoveries") == before
