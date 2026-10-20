"""
:importance: P3
:design: decision_3ece26686464
"""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from knowledge_board_lan import knowledge_backend as backend
from management_common.execution_association import execution_scope
from management_portal.resources.content_changes import ContentChangeStore
from management_portal.resources.errors import ResourceVersionConflict
from management_portal.resources.providers import knowledge
from management_portal.resources.service import ResourceService
from tests.cases.test_knowledge_versioning import KNOWLEDGE_ID, _frontmatter, _markdown


ACTOR = "resident_300dead2872a"


@pytest.fixture
def version_case(tmp_path, monkeypatch):
    primary = tmp_path / "item.md"
    original = _markdown(number=14)
    damaged = backend.stamp_knowledge_version(original, creating=True)
    primary.write_text(damaged)
    record = {"address": KNOWLEDGE_ID, "kb_id": KNOWLEDGE_ID, "type": "thinking",
              "project_ids": ["AI员工"], "path": str(primary), "selected_path": str(primary), "selected_file_id": "primary"}
    monkeypatch.setattr(backend, "resolve_item_address", lambda *a, **k: record)
    monkeypatch.setattr(backend, "resolve_item_record", lambda *a, **k: record)
    store = ContentChangeStore()
    def snapshot(text):
        return {"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest(),
                "version": backend.knowledge_version_record(text)}
    body = "# Body after the damaged first write\n"
    changes = [{"schema": "tfi.resource-content-change.v1", "change_id": cid,
                "resource_id": KNOWLEDGE_ID, "address": KNOWLEDGE_ID, "path": str(primary),
                "execution_id": "execution-original", "actor": ACTOR,
                "before": snapshot(before), "after": snapshot(after)}
               for cid, before, after in (("1" * 32, original, body), ("2" * 32, body, damaged))]
    for change in changes:
        store.append("execution-original", ACTOR, change)
    service = ResourceService()
    knowledge.install_mutation_provider(service)
    monkeypatch.setattr(service, "_notify_mutation", lambda **kwargs: None)
    return primary, original, damaged, store, service, changes


@pytest.mark.parametrize("text", ["# Only body\n", "---\n{}\n---\n# Empty header",
                                  "---\nversion: [\n---\n# Invalid YAML"])
def test_versioned_primary_rejects_header_loss_without_write_or_receipt(version_case, monkeypatch, text):
    """带版本的主文件更新丢失或损坏 front matter 时被拒绝，不写文件也不留回执

    :design-point:"""
    primary, original, _, store, service, _ = version_case
    primary.write_text(original)
    from management_portal.resources import content_changes
    monkeypatch.setattr(content_changes, "get_execution_service", lambda: type("Execution", (), {
        "status": lambda self, *a, **k: {"status": "running", "request": {"agent_id": ACTOR}}
    })())
    with execution_scope("execution-rejected"):
        with pytest.raises(ValueError, match="complete Markdown"):
            service.update(KNOWLEDGE_ID, {"text": text, "expected_version": 14}, actor=ACTOR)
    assert primary.read_text() == original
    assert not store.for_execution("execution-rejected", ACTOR)


def test_normal_primary_update_uses_server_version_and_creation_audit(version_case):
    """正常更新主文件时版本号由服务端递增，提交内容不能改写版本号和创建者

    :design-point:"""
    primary, original, _, _, service, _ = version_case
    primary.write_text(original)
    proposal = original.replace("number: 14", "number: 99999")
    proposal = proposal.replace('user: "sample_admin"', 'user: "forged"')
    updated = service.update(KNOWLEDGE_ID, {"text": proposal, "expected_version": 14}, actor=ACTOR)
    version = _frontmatter(updated["text"])["version"]
    assert version["number"] == 15
    assert version["created_by"] == _frontmatter(original)["version"]["created_by"]
    assert version["updated_by"] == {"user": "", "agent": ACTOR}
    # Omitting only the version block in otherwise complete Markdown is safe:
    # the writer still advances from its locked server-owned predecessor.
    proposal = backend._replace_frontmatter_block(updated["text"], "version", [])
    assert service.update(KNOWLEDGE_ID, {"text": proposal}, actor=ACTOR)["version"]["number"] == 16
    assert backend.stamp_knowledge_version("# Unversioned text\n") == "# Unversioned text\n"


def test_resource_update_recovers_retained_high_water_once_and_keeps_original_receipts(version_case, monkeypatch):
    """凭变更回执修复版本号回退时接续原最高版本，只能修复一次且原回执不变

    :design-point:"""
    primary, original, damaged, store, service, changes = version_case
    from management_portal.resources import content_changes
    monkeypatch.setattr(content_changes, "get_execution_service", lambda: type("Execution", (), {
        "status": lambda self, *a, **k: {"status": "running", "request": {"agent_id": ACTOR}}
    })())
    before = copy.deepcopy(store.for_execution("execution-original", ACTOR))
    payload = {"text": damaged, "expected_version": 1,
               "version_recovery_change_ids": [c["change_id"] for c in changes]}
    with execution_scope("execution-repair"):
        updated = service.update(KNOWLEDGE_ID, payload, actor=ACTOR)
    assert updated["version"]["number"] == 15
    assert _frontmatter(updated["text"])["version"]["created_by"] == _frontmatter(original)["version"]["created_by"]
    assert updated["text"].split("---", 2)[2] == damaged.split("---", 2)[2]
    prior_meta, new_meta = _frontmatter(damaged), _frontmatter(updated["text"])
    prior_meta.pop("version"); new_meta.pop("version")
    assert prior_meta == new_meta
    assert store.for_execution("execution-original", ACTOR) == before
    receipt, = store.for_execution("execution-repair", ACTOR)
    assert receipt["before"]["text"] == damaged
    assert receipt["after"]["text"] == primary.read_text()
    assert receipt["version_recovery_change_ids"] == payload["version_recovery_change_ids"]
    with pytest.raises(ResourceVersionConflict):
        service.update(KNOWLEDGE_ID, payload, actor=ACTOR)
    with pytest.raises(ValueError, match="proven version regression"):
        service.update(KNOWLEDGE_ID, {**payload, "expected_version": 15, "text": primary.read_text(),
                       "version_recovery_change_ids": payload["version_recovery_change_ids"] + [receipt["change_id"]]}, actor=ACTOR)


@pytest.mark.parametrize("defect", ["missing", "reversed", "duplicate", "broken_chain", "hash", "foreign",
                                    "version", "stale_content", "new_content", "no_cas", "too_many", "unauthenticated"])
def test_version_recovery_rejects_unproven_or_unscoped_changes_before_writing(version_case, defect):
    """版本修复的回执无法证明回退、超出范围或缺少 CAS 与身份时，写入前即拒绝

    :design-point:"""
    primary, _, damaged, store, service, changes = version_case
    payload = {"text": damaged, "expected_version": 1,
               "version_recovery_change_ids": [c["change_id"] for c in changes]}
    actor = ACTOR
    if defect == "missing": payload["version_recovery_change_ids"] = ["3" * 32]
    elif defect == "reversed": payload["version_recovery_change_ids"].reverse()
    elif defect == "duplicate": payload["version_recovery_change_ids"] *= 2
    elif defect == "new_content": payload["text"] += "unrelated change"
    elif defect == "no_cas": payload.pop("expected_version")
    elif defect == "too_many": payload["version_recovery_change_ids"] = [f"{i:032x}" for i in range(9)]
    elif defect == "unauthenticated": actor = ""
    elif defect == "stale_content":
        primary.write_text(damaged + "outside edit")
        payload["text"] = primary.read_text()
    else:
        # A malformed stored receipt must fail closed. Only this disposable
        # test database is modified; production immutable evidence is never edited.
        corrupted = copy.deepcopy(changes[1])
        if defect == "broken_chain":
            corrupted["before"]["text"] += "gap"
            corrupted["before"]["sha256"] = hashlib.sha256(corrupted["before"]["text"].encode()).hexdigest()
        elif defect == "hash": corrupted["after"]["sha256"] = "0" * 64
        elif defect == "foreign": corrupted["resource_id"] = "thinking_abcdef012345"
        elif defect == "version": corrupted["after"]["version"]["number"] = 99999
        store.database.write(lambda db: db.execute("UPDATE content_changes SET payload=%s WHERE change_id=%s",
                              (json.dumps(corrupted), corrupted["change_id"])))
    before = primary.read_text()
    with pytest.raises((ValueError, PermissionError)):
        service.update(KNOWLEDGE_ID, payload, actor=actor)
    assert primary.read_text() == before
