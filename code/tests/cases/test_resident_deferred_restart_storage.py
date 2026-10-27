""":module: spec.design-governance"""
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from management_portal.society_service import (
    resident_catalog, resident_conversation_stream, resident_deferred_restart,
    resident_lifecycle_domain, resident_workspace_domain,
)
from management_portal.society_service.state_store import LineageDataStore
from tests.cases.test_lineage_catalogs import _lineage_definition, _record


@pytest.mark.parametrize("fail_save", [False, True])
def test_deferred_restart_and_intake_commit_together(monkeypatch, fail_save):
    """延后重启会话与关闭接收一起保存，保存失败时两者都不生效，取消后重新开放接收"""
    store = LineageDataStore()
    lineage = _lineage_definition()
    store.put_lineage_definition(lineage["lineage_id"], lineage, {})
    record = _record("resident_aaaaaaaaaaaa")
    store.put_resident_record(record["resident_id"], record)
    rid = record["resident_id"]
    monkeypatch.setattr(resident_catalog, "get_catalog", lambda: SimpleNamespace(state_store=store))
    monkeypatch.setattr(resident_lifecycle_domain, "state_lock", nullcontext)
    monkeypatch.setattr(resident_workspace_domain, "find_resident", store.resident_record)
    repository = resident_conversation_stream.get_repository()
    monkeypatch.setattr(repository, "current_state", lambda *_: pytest.fail("Execution RPC inside state lock"))

    def save(value, **options):
        if fail_save and value.get("session_restart"):
            raise RuntimeError("save failed")
        store.put_resident_record(rid, value, **options)

    monkeypatch.setattr(resident_workspace_domain, "save_resident_record", save)
    source = {"provider": "codex", "account_id": "one"}
    intent = {"target_provider": "claude", "target_account": "", "context_strategy": "compact"}
    if fail_save:
        with pytest.raises(RuntimeError, match="save failed"):
            resident_deferred_restart.schedule(record, "alice", {}, source, intent)
        assert store.resident_record(rid)["intake_open"] is True
        assert not store.resident_record(rid).get("session_restart")
    else:
        receipt = resident_deferred_restart.schedule(record, "alice", {}, source, intent)
        saved = store.resident_record(rid)
        assert saved["intake_open"] is False
        assert saved["session_restart"] == receipt
        resident_deferred_restart.cancel(saved, receipt["restart_id"])
        saved = store.resident_record(rid)
        assert saved["intake_open"] is True
        assert saved["session_restart"]["status"] == "cancelled"
        assert "deferred" not in saved["session_restart"]
