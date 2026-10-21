"""
:importance: P1
:design: decision_71aa039e3211
"""
from concurrent.futures import ThreadPoolExecutor
import secrets

import pytest

from management_common.portal_database import portal_database
from management_portal.society_service import trigger_path_storage as paths


AT = "2026-10-14T00:00:00Z"


@pytest.fixture
def database():
    database = portal_database(schema="path_test_" + secrets.token_hex(4))
    database.write(paths.create_schema)
    yield database
    database.write(lambda connection: connection.execute(
        "DROP TABLE trigger_path_execution_links,trigger_path_step_edges,trigger_path_steps,trigger_paths"
    ))
    database.close()


def begin(connection, occurrence="event-one", **changes):
    values = dict(lineage_id="lineage-one", root_kind="event", occurrence_id=occurrence,
                  root_node_id="event-source", cause_kind="input", cause_id="message-one",
                  occurred_at=AT, input={"prompt": "inspect"})
    return paths.begin(connection, **(values | changes))


def resident(connection, path, node="resident-one", activation="delivery-one", predecessors=()):
    return paths.admit_step(connection, trigger_path_id=path, node_id=node, kind="resident",
                            activation_key=activation, created_at=AT, input_message_id=activation,
                            predecessors=predecessors)


def test_occurrence_identity_and_conflicting_redelivery(database):
    """同一事件并发开启只得到一条触发路径，原因不同的重投被拒绝"""
    with ThreadPoolExecutor(max_workers=4) as pool:
        identities = list(pool.map(lambda _: database.write(begin), range(12)))
    assert len(set(identities)) == 1
    with database.transaction() as connection:
        second = begin(connection, "event-two")
        assert second != identities[0]
        effect = begin(connection, "event-one", root_kind="effect", root_node_id="effect-one")
        assert effect != identities[0]
    with pytest.raises(ValueError, match="Conflicting"):
        database.write(lambda connection: begin(connection, cause_id="different-input"))


def test_fanout_fanin_and_resident_boundary(database):
    """触发路径支持分叉与汇合并止于 Resident 步骤，关联执行完成后路径才结束"""
    with database.transaction() as connection:
        path = begin(connection)
        first = paths.admit_step(connection, trigger_path_id=path, kind="operator", node_id="split",
                                activation_key="one", created_at=AT)
        second = paths.admit_step(connection, trigger_path_id=path, kind="effect", node_id="side-effect",
                                 activation_key="one", created_at=AT, predecessors=[(first, "wire-one")])
        leaf = resident(connection, path, predecessors=[(first, "wire-two"), (second, "wire-three")])
        paths.link_execution(connection, leaf, "execution-one")
        for step in (first, second, leaf):
            paths.transition_step(connection, step, status="completed", at=AT)
        assert paths.finish(connection, path, at=AT, execution_statuses={}) == "active"
        assert paths.finish(connection, path, at=AT, execution_statuses={"execution-one": "running"}) == "active"
        assert len(paths.read(connection, path)["edges"]) == 3
        next_path = begin(connection, "completion-event", cause_kind="execution", cause_id="execution-one")
        resident(connection, next_path, "resident-two")
        assert paths.finish(connection, path, at=AT, execution_statuses={"execution-one": "completed"}) == "completed"
        assert paths.read(connection, next_path)["status"] == "active"
    with pytest.raises(ValueError, match="terminal Trigger Path"):
        database.write(lambda connection: resident(connection, path, "resident-three"))
    with pytest.raises(ValueError, match="stops at a Resident"):
        with database.transaction() as connection:
            another = begin(connection, "another")
            leaf = resident(connection, another)
            resident(connection, another, "illegal-descendant", predecessors=[(leaf, "wire")])


def test_shared_execution_and_path_cancellation(database):
    """多条路径共享同一执行时各自结束，取消一条路径不影响另一条"""
    with database.transaction() as connection:
        first = begin(connection, "first")
        second = begin(connection, "second")
        for path in (first, second):
            step = resident(connection, path)
            paths.link_execution(connection, step, "shared-execution")
            paths.link_execution(connection, step, "shared-execution")
            paths.transition_step(connection, step, status="completed", at=AT)
        paths.cancel(connection, first, at=AT)
        assert paths.read(connection, first)["status"] == "cancelled"
        assert paths.finish(connection, second, at=AT, execution_statuses={"shared-execution": "running"}) == "active"
        assert paths.finish(connection, second, at=AT, execution_statuses={"shared-execution": "completed"}) == "completed"
        assert connection.execute("SELECT count(*) FROM trigger_path_execution_links").fetchone()[0] == 2


def test_empty_trigger_and_effect_failure_are_terminal(database):
    """没有步骤的触发路径直接完成，Effect 失败使路径失败且步骤不能重开"""
    with database.transaction() as connection:
        empty = begin(connection, "empty")
        assert paths.finish(connection, empty, at=AT, execution_statuses={}) == "completed"
        effect = begin(connection, "activation-one", root_kind="effect", root_node_id="effect-one")
        step = paths.admit_step(connection, trigger_path_id=effect, kind="effect", node_id="effect-one",
                               activation_key="activation-one", created_at=AT)
        paths.transition_step(connection, step, status="running", at=AT)
        paths.transition_step(connection, step, status="failed", at=AT, result={"instruction_id": "instruction-one"})
        assert paths.finish(connection, effect, at=AT, execution_statuses={}) == "failed"
    with pytest.raises(ValueError, match="reopened"):
        database.write(lambda connection: paths.transition_step(connection, step, status="running", at=AT))


def test_resident_handoff_does_not_duplicate_execution_result(database):
    """Resident 步骤须关联执行才能完成，且不复制执行结果"""
    with database.transaction() as connection:
        path = begin(connection)
        step = resident(connection, path)
    with pytest.raises(ValueError, match="requires an Execution"):
        database.write(lambda connection: paths.transition_step(connection, step, status="completed", at=AT))
    database.write(lambda connection: paths.link_execution(connection, step, "execution-one"))
    with pytest.raises(ValueError, match="Agent execution history"):
        database.write(lambda connection: paths.transition_step(connection, step, status="completed", at=AT, result={"reply": "copy"}))
