"""Canonical reads and rejected old formats leave stored evidence untouched.

:design: decision_15aab18c0ab3
"""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from knowledge_board_lan import knowledge_backend
from management_portal.permission_system.scheme.compiler import SchemeError, compile_scheme
from management_portal.permission_system.scheme.repository import builtin_documents, source_digest
from management_portal.resources.collaboration_registry import CollaborationDescriptorStore
from management_portal.resources.content_changes import ContentChangeStore
from management_portal.resources.domain_projection_evidence import EVIDENCE_COLLECTION, SOURCE_COLLECTION


RESIDENT = "resident_0123456789ab"
KNOWLEDGE = "thinking_0123456789ab"
TIME = "2026-10-19T00:00:00Z"


class ReadOnlyRows:
    """Storage boundary: reads return copies; any write is a test failure."""

    def __init__(self, rows):
        self.rows = copy.deepcopy(rows)

    def get(self, collection, resource_id):
        return copy.deepcopy(self.rows.get((collection, resource_id)))

    def writer(self, *args, **kwargs):
        raise AssertionError("a read must not open a registry writer")

    def put(self, *args, **kwargs):
        raise AssertionError("a read must not persist a normalized payload")


def descriptor(schema="archive.collaboration-descriptor.v2"):
    return {"schema": schema, "resource_id": RESIDENT, "title": "Existing name",
            "summary": "", "status": "deleted", "project_ids": [], "relations": [],
            "attributes": {}, "created_at": TIME, "updated_at": TIME, "deleted_at": TIME}


def invalid_tags(suffix):
    return [None, 7, True, {}, [], "", f"tfi.{suffix}.v999", f"archive.{suffix}.v999",
            "tfi.unrelated.v2", f"thirdparty.{suffix}.v2", f" tfi.{suffix}.v2"]


@pytest.mark.parametrize("namespace", ["archive", "tfi"])
def test_descriptor_get_accepts_both_tags_without_persisting(namespace):
    raw = descriptor(f"{namespace}.collaboration-descriptor.v2")
    registry = ReadOnlyRows({("resident", RESIDENT): raw})
    before = copy.deepcopy(registry.rows)
    store = CollaborationDescriptorStore(registry=registry)
    if namespace == "archive":
        with pytest.raises(ValueError, match="unsupported collaboration descriptor schema"):
            store.get(RESIDENT)
    else:
        assert store.get(RESIDENT) == descriptor("tfi.collaboration-descriptor.v2")
    assert registry.rows == before
    assert raw["schema"] == f"{namespace}.collaboration-descriptor.v2"


def test_descriptor_read_rejects_other_versions_types_and_invalid_payloads():
    store = CollaborationDescriptorStore(registry=ReadOnlyRows({}))
    for tag in invalid_tags("collaboration-descriptor"):
        raw = descriptor(tag)
        before = copy.deepcopy(raw)
        with pytest.raises(ValueError, match="schema"):
            store.normalize(raw)
        assert raw == before
    for patch in ({"status": "unknown"}, {"attributes": {"owner_service": "forged"}},
                  {"resource_id": KNOWLEDGE}, {"unexpected": True}):
        with pytest.raises(ValueError):
            store.normalize({**descriptor("tfi.collaboration-descriptor.v2"), **patch})


def retry_reader(tag, *, receipt_patch=None):
    current = descriptor("tfi.collaboration-descriptor.v2")
    receipt = {"domain_identity": "society-service", "incoming_descriptor": current}
    receipt.update(receipt_patch or {})
    rows = ReadOnlyRows({("resident", RESIDENT): current,
                         (SOURCE_COLLECTION, RESIDENT): {"schema": tag, "resource_id": RESIDENT,
                                                        "source_fingerprint": "historical"},
                         (EVIDENCE_COLLECTION, RESIDENT): receipt})
    store = CollaborationDescriptorStore(registry=rows)
    return store, rows


@pytest.mark.parametrize("namespace", ["archive", "tfi"])
def test_historical_tombstone_retry_preserves_source_and_receipt(namespace):
    store, rows = retry_reader(f"{namespace}.domain-tombstone-source.v1")
    before = copy.deepcopy(rows.rows)
    # A retired proof cannot authorize a retry, even for a canonical descriptor.
    with pytest.raises(ValueError, match="Stale domain tombstone"):
        store.tombstone(descriptor("tfi.collaboration-descriptor.v2"), rows,
                        source={"schema": "tfi.domain-tombstone-source.v2"})
    assert rows.rows == before


def test_historical_retry_rejects_bad_tag_or_unproven_receipt_without_writes():
    for tag in invalid_tags("domain-tombstone-source") + ["tfi.domain-tombstone-source.v2"]:
        store, rows = retry_reader(tag)
        before = copy.deepcopy(rows.rows)
        with pytest.raises(ValueError, match="Stale domain tombstone"):
            store.tombstone(descriptor("tfi.collaboration-descriptor.v2"), rows, source={})
        assert rows.rows == before
    for patch in ({"domain_identity": ""}, {"incoming_descriptor": {**descriptor(), "title": "Other"}}):
        store, rows = retry_reader("tfi.domain-tombstone-source.v1", receipt_patch=patch)
        before = copy.deepcopy(rows.rows)
        with pytest.raises(ValueError, match="Stale domain tombstone"):
            store.tombstone(descriptor("tfi.collaboration-descriptor.v2"), rows, source={})
        assert rows.rows == before


def test_permission_scheme_alias_preserves_rules_and_raw_source_digest():
    old = {**builtin_documents()["default"], "schema": "archive.permission-scheme.v2"}
    new = {**copy.deepcopy(old), "schema": "tfi.permission-scheme.v2"}
    old_bytes = json.dumps(old, ensure_ascii=False, sort_keys=True)
    new_bytes = json.dumps(new, ensure_ascii=False, sort_keys=True)
    with pytest.raises(SchemeError) as error:
        compile_scheme(old)
    assert error.value.errors[0]["path"] == "schema"
    canonical = compile_scheme(new)
    reference = compile_scheme(builtin_documents()["default"])
    for field in ("trees", "patterns", "fixed", "rules", "instructions", "user_categories", "rules_digest"):
        assert getattr(canonical, field) == getattr(reference, field)
    assert dict(canonical.source) == new
    assert canonical.digest == hashlib.sha256(new_bytes.encode()).hexdigest()[:16]
    # The copy-source digest excludes display identity, but includes the schema.
    for source in (old, new):
        material = {key: value for key, value in source.items() if key not in ("scheme_id", "title")}
        expected = hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
        assert source_digest(source) == expected
    assert source_digest(new) != source_digest(old)
    assert json.dumps(old, ensure_ascii=False, sort_keys=True) == old_bytes
    assert json.dumps(new, ensure_ascii=False, sort_keys=True) == new_bytes


def test_permission_scheme_rejects_wrong_tag_and_keeps_field_validation():
    old = {**builtin_documents()["default"], "schema": "archive.permission-scheme.v2"}
    for tag in invalid_tags("permission-scheme") + ["tfi.permission-scheme.v1"]:
        raw = {**copy.deepcopy(old), "schema": tag}
        before = copy.deepcopy(raw)
        with pytest.raises(SchemeError) as error:
            compile_scheme(raw)
        assert error.value.errors[0]["path"] == "schema"
        assert raw == before
    with pytest.raises(SchemeError) as error:
        compile_scheme({**old, "schema": "tfi.permission-scheme.v2", "trees": {}})
    assert error.value.errors[0]["path"] == "trees"


def recovery_evidence(tmp_path, tag, *, corrupt=False):
    path = tmp_path / "knowledge.md"
    original = f"---\nkb_id: {KNOWLEDGE}\nversion:\n  number: 14\n---\n# Original\n"
    current = "# Current content with lost version\n"
    path.write_text(current)
    def snapshot(text):
        return {"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest(),
                "version": knowledge_backend.knowledge_version_record(text)}
    change_id = "1" * 32
    change = {"schema": tag, "change_id": change_id, "actor": RESIDENT, "execution_id": "isolated",
              "resource_id": KNOWLEDGE, "address": KNOWLEDGE, "path": str(path),
              "before": snapshot(original), "after": snapshot(current)}
    if corrupt:
        change["before"]["sha256"] = "0" * 64
    store = ContentChangeStore()
    store.append("isolated", RESIDENT, change)
    return path, original, current, store, [change_id]


@pytest.mark.parametrize("namespace", ["archive", "tfi"])
def test_recovery_reads_evidence_without_changing_receipt_or_content(tmp_path, namespace):
    path, original, current, store, ids = recovery_evidence(tmp_path, f"{namespace}.resource-content-change.v1")
    retained = store.for_changes(ids)
    stamp = path.stat().st_mtime_ns
    if namespace == "archive":
        with pytest.raises(ValueError, match="different scope or order"):
            knowledge_backend._knowledge_version_recovery_source(KNOWLEDGE, path, current, ids)
    else:
        assert knowledge_backend._knowledge_version_recovery_source(KNOWLEDGE, path, current, ids) == original
    assert store.for_changes(ids) == retained
    assert path.read_text() == current
    assert path.stat().st_mtime_ns == stamp


@pytest.mark.parametrize("tag", ["tfi.resource-content-change.v2", "archive.resource-content-change.v2",
                                 "tfi.unrelated.v1", None, {}, 7])
def test_recovery_rejects_bad_schema_without_changing_evidence(tmp_path, tag):
    path, _, current, store, ids = recovery_evidence(tmp_path, tag)
    retained = store.for_changes(ids)
    with pytest.raises(ValueError, match="different scope or order"):
        knowledge_backend._knowledge_version_recovery_source(KNOWLEDGE, path, current, ids)
    assert store.for_changes(ids) == retained
    assert path.read_text() == current


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ


@pytest.mark.parametrize("namespace", ["archive", "tfi"])
def test_recovery_alias_cannot_bypass_snapshot_hash(tmp_path, namespace):
    path, _, current, store, ids = recovery_evidence(tmp_path, f"{namespace}.resource-content-change.v1", corrupt=True)
    retained = store.for_changes(ids)
    # The canonical case must reach hash validation; the alias fails earlier.
    reason = "different scope or order" if namespace == "archive" else "snapshot hash is invalid"
    with pytest.raises(ValueError, match=reason):
        knowledge_backend._knowledge_version_recovery_source(KNOWLEDGE, path, current, ids)
    assert store.for_changes(ids) == retained
    assert path.read_text() == current
