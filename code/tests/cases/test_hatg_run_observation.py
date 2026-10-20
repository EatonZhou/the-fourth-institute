
"""Trigger Path observation reads graph facts and independent Executions.

:importance: P1
"""
import secrets
import pytest
from management_portal.hatg.catalog import instruction_catalog
from management_portal.society_service import trigger_paths as paths, trigger_path_observation as observation

AT = "2026-10-14T00:00:00Z"


def lineage():
    return "lineage_" + secrets.token_hex(6)


def occurrence(lid, index=0):
    event = {"id": f"event_{index}", "type": "manual.triggered", "occurred_at": AT, "payload": {}}
    identity = paths.begin_event(lid, "source", event, cause_kind="input", cause_id=f"input_{index}")
    event["payload"]["trigger_path_id"] = identity
    return identity, event


def test_empty_event_and_observation_permissions_contract():
    """只有事件步骤的触发路径可观测为已完成，触发路径查询指令只读且旧 Run 指令已移除"""
    lid = lineage()
    identity, event = occurrence(lid)
    paths.event_settled(identity, "source", event["id"])
    result = observation.observe(lid, {"trigger_path_id": identity})
    assert result["path"]["status"] == "completed"
    assert result["steps"]["items"][0]["kind"] == "event"
    assert result["executions"]["items"] == []
    for name in ("ListTriggerPaths", "GetTriggerPath"):
        assert instruction_catalog()[name].mutates is False
    for name in ("ListRuns", "GetRun", "GetInvocation", "GetTriggerPathStep"):
        assert name not in instruction_catalog()


@pytest.mark.parametrize("arguments", [{"limit": 0}, {"limit": 201}, {"offset": -1}])
def test_invalid_page(arguments):
    """触发路径观测的 limit 或 offset 越界时被拒绝

    :importance: P2"""
    with pytest.raises(ValueError):
        observation.observe(lineage(), arguments)


def test_step_paging_and_lineage_scope():
    """触发路径步骤按 limit 和 offset 分页，且只能在所属 Lineage 中查询"""
    lid = lineage()
    identity, event = occurrence(lid)
    for index in range(3):
        paths.admit_resident(lid, resident_id=f"resident_{index}", message_id=f"input_{index}",
                             trigger_path_id=identity, source_wire_id=f"wire_{index}", event=event)
    first = observation.observe(lid, {"trigger_path_id": identity, "limit": 1})
    second = observation.observe(lid, {"trigger_path_id": identity, "limit": 1, "offset": 1})
    assert first["steps"]["next_offset"] == 1
    assert first["steps"]["items"] != second["steps"]["items"]
    with pytest.raises(KeyError):
        observation.observe(lineage(), {"trigger_path_id": identity})


def test_resident_filter_precedes_paging():
    """按 Resident 筛选触发路径先于分页，总数只计筛选后的结果

    :importance: P2
    :design: decision_68215b3a825b
    :design-point:
    """
    lid = lineage()
    selected = ""
    for index in range(5):
        identity, event = occurrence(lid, index)
        resident = "daily" if index == 0 else "busy"
        paths.admit_resident(lid, resident_id=resident, message_id=f"input_{index}",
                             trigger_path_id=identity, source_wire_id="wire", event=event)
        if index == 0:
            selected = identity
    result = observation.observe(lid, {"resident_id": "daily", "limit": 1})
    assert result["total"] == 1 and result["next_offset"] is None
    assert result["paths"][0]["trigger_path_id"] == selected
    assert observation.observe(lid, {"limit": 2})["total"] == 5

def test_watch_cursor_includes_execution_beyond_visible_page():
    """触发路径游标随当前页之外的执行变化而前进"""
    from management_portal.execution.agent_data_store import get_agent_data_store
    from management_portal.society_service import trigger_path_storage as storage
    lid = lineage()
    identity, event = occurrence(lid)
    agent = get_agent_data_store()
    agent.ensure_agent("resident-pagination")
    for index in range(201):
        eid = f"execution-page-{index:03}"
        agent.reserve_execution(execution_id=eid, agent_id="resident-pagination",
                                actor_ref="fixture", provider="codex", created_at=AT)
        with paths._store().database.transaction() as connection:
            step = storage.admit_step(connection, trigger_path_id=identity, kind="resident",
                                      node_id="resident-pagination", activation_key=eid, created_at=AT,
                                      input_message_id=f"message-{index}")
            storage.link_execution(connection, step, eid)
            storage.transition_step(connection, step, status="completed", at=AT)
    first = observation.observe(lid, {"trigger_path_id": identity, "limit": 200})
    assert len(first["executions"]["items"]) == 200
    assert first["executions"]["next_offset"] == 200
    agent.queue_execution("execution-page-200")
    changed = observation.observe(lid, {"trigger_path_id": identity, "limit": 200})
    assert changed["path"]["cursor"] > first["path"]["cursor"]
    final = observation.observe(lid, {"trigger_path_id": identity, "offset": 200, "limit": 200})
    assert [e["execution_id"] for e in final["executions"]["items"]] == ["execution-page-200"]


def test_failed_path_notification_only_after_commit(monkeypatch):
    """触发路径失败通知只在事务提交后发出，事务回滚时不发"""
    lid = lineage()
    identity, event = occurrence(lid)
    notifications = []
    def notify(path_id):
        # Callback can read the committed state from a separate connection.
        assert paths.get(lid, path_id)["status"] == "failed"
        notifications.append(path_id)
    monkeypatch.setattr(paths, "_notify_failed_path", notify)
    with pytest.raises(RuntimeError, match="rollback"):
        with paths._store().database.transaction():
            paths.event_settled(identity, "source", event["id"], error="example")
            assert notifications == []
            raise RuntimeError("rollback")
    assert notifications == []
    assert paths.get(lid, identity)["status"] == "active"
    with paths._store().database.transaction():
        paths.event_settled(identity, "source", event["id"], error="example")
        assert notifications == []
    assert notifications == [identity]


def test_effect_observation_reads_current_owner(monkeypatch):
    """Effect 步骤观测读取 effect_work 的当前状态，状态变化时游标前进"""
    import json
    lid = lineage()
    identity, event = occurrence(lid)
    path, step = paths.effect_started(lid, "activation", {
        "trigger_path_id": identity, "effect_id": "effect", "started_at": AT,
    })
    from management_portal.society_service.state_store import get_lineage_data_store
    get_lineage_data_store().put_lineage_definition(lid, {"lineage_id": lid, "title": "Effect fixture", "nodes": [], "edges": [], "program": {}}, {"lineage_id": lid})
    current = {"trigger_path_id": identity, "status": "running", "calls": {"c1": {"status": "executing", "script_run_id": "script"}}}
    with paths._store().database.transaction() as connection:
        connection.execute("INSERT INTO effect_work VALUES (%s,%s,%s)", (lid, "activation", json.dumps(current)))
    from management_portal.society_service import effect_disposition
    monkeypatch.setattr(effect_disposition, "can_resolve", lambda *_: True)
    def read():
        result = observation.observe(lid, {"trigger_path_id": identity})
        return result["path"]["cursor"], next(s for s in result["steps"]["items"] if s["step_id"] == step)

    cursor, observed = read()
    assert observed["current"] == current
    assert observed["resolution_required"]

    with paths._store().effect_work.transaction(lid, "activation") as state:
        state["calls"]["c1"]["status"] = "completed"
    updated_cursor, updated = read()
    assert updated_cursor > cursor
    assert updated["current"]["calls"]["c1"]["status"] == "completed"
    with paths._store().effect_work.transaction(lid, "activation"):
        pass
    assert observation.observe(lid, {"trigger_path_id": identity})["path"]["cursor"] == updated_cursor
