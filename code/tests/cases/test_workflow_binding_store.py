from __future__ import annotations

import copy
import json

import pytest

from management_portal.workflow_bindings_store import WorkflowBindingConflict, WorkflowBindingStore


def binding():
    return {"lineage_id": "lineage_test", "resident_id": "resident_test", "name": "feature", "steps": {}}


def test_workflow_store_keeps_only_ids_and_rejects_stale_writers(tmp_path):
    """工作流绑定只保存对象 ID，持旧版本的写入被拒绝"""
    store = WorkflowBindingStore()
    state = binding()
    state["steps"] = {"event": "event_source_test", "effect": "effect_test",
                      "target": "wire_target", "invoke": "wire_event"}
    store.save(state)
    stale = copy.deepcopy(state)
    store.save(state)
    with pytest.raises(WorkflowBindingConflict):
        store.save(stale)
    assert store.get("lineage_test", "resident_test", "feature") == state
    assert len(json.dumps(state).encode()) < 1024
    state["steps"]["event"] = {"id": "event_source_test", "lineage": {}}
    with pytest.raises(ValueError, match="object IDs"):
        store.save(state)


def test_a_saved_step_survives_a_lost_response(tmp_path):
    """工作流绑定在响应丢失后保留已保存的步骤

    在 Lineage 锁内保存一个步骤后即使调用方没有收到响应，其他连接读到的绑定也已包含这个步骤。
    """
    store = WorkflowBindingStore()
    state = binding()
    with pytest.raises(TimeoutError):
        with store.lineage_lock("lineage_test"):
            state["steps"]["event"] = "event_source_test"
            store.save(state)
            raise TimeoutError("response lost")
    observed = WorkflowBindingStore().get("lineage_test", "resident_test", "feature")
    assert observed == state
