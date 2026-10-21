""":design: decision_71aa039e3211, decision_4adcc1ff2f2c"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from management_portal.society_service import lineage_event_delivery as delivery

from management_portal.society_service import program_work_storage as work
from management_portal.society_service.state_store import get_lineage_data_store


@pytest.fixture
def database():
    store = get_lineage_data_store()
    store.put_lineage_definition("lineage_aabbccddeeff", {
        "lineage_id": "lineage_aabbccddeeff", "title": "Work persistence",
        "nodes": [], "edges": [], "program": {},
    }, {"lineage_id": "lineage_aabbccddeeff"})
    return store.database


LINEAGE = "lineage_aabbccddeeff"


def test_permanent_identity_failures_are_durable_but_not_recovery_candidates(database):
    """永久身份失败保留证据并退出恢复候选

    :design-point:
    """
    from management_common.principal import OperationIdentity
    event = {"id": "legacy", "type": "manual.triggered"}
    with work.transaction(database, LINEAGE) as state:
        state["event_wires"] = {"wire": {"pending_events": [{"claim_token": "old", "event": event}]}}
        state["expired_deliveries"] = [{"wire_id": "expired", "cursor": "old", "event": event}]
        state["operators"] = {"operator": {"pending_deliveries": {"input": {"event": event}}}}
    assert delivery.block_unrecoverable_events(database, LINEAGE) == 3
    assert delivery.block_unrecoverable_events(database, LINEAGE) == 0
    with database.read() as c:
        assert work.recovery_lineage_ids(c) == set()
        saved = work.read(c, LINEAGE)
    assert saved["expired_deliveries"][0]["event"] == event
    assert saved["expired_deliveries"][0]["recovery_blocked"]["requires_reissue"]
    # A fresh, explicitly authorized occurrence remains recoverable.
    with work.transaction(database, LINEAGE) as state:
        state["event_wires"]["wire"]["pending_events"].append({
            "claim_token": "new", "event": {**event, "id": "new", "identity": OperationIdentity.for_actor("sample_admin").to_dict()},
        })
    assert delivery.block_unrecoverable_events(database, LINEAGE) == 0
    with database.read() as c:
        assert work.recovery_lineage_ids(c) == {LINEAGE}


def test_work_survives_reopen_without_rewriting_other_sources(database):
    """工作状态持久保存且修改单个源不重写其他源

    :module: spec.design-governance
    :design-point:
    """
    value = {
        "event_sources": {"source-a": {"consumed_cursors": ["one"]}, "source-b": {"consumed_cursors": ["two"]}},
        "event_wires": {"wire": {"pending_events": [{"claim_token": "claim", "event": {"id": "event", "payload": "中文"}}]}},
        "operators": {"operator": {"round": 3}},
        "delivery_profiles": {"message-a": {"isolated": True}},
        "expired_deliveries": [{"wire_id": "wire", "cursor": "expired"}],
    }
    with work.transaction(database, LINEAGE) as state:
        state.update(deepcopy(value))
    with database.read_transaction() as connection:
        assert work.read(connection, LINEAGE) == value
        before = {row["source_id"]: row["xmin"] for row in connection.execute(
            "SELECT source_id,xmin FROM event_source_progress WHERE lineage_id=%s", (LINEAGE,),
        ).fetchall()}
    with work.transaction(database, LINEAGE) as state:
        state["event_sources"]["source-a"]["consumed_cursors"].append("three")
    with database.read_transaction() as connection:
        after = {row["source_id"]: row["xmin"] for row in connection.execute(
            "SELECT source_id,xmin FROM event_source_progress WHERE lineage_id=%s", (LINEAGE,),
        ).fetchall()}
        assert before["source-b"] == after["source-b"]
        assert before["source-a"] != after["source-a"]


def test_failed_transition_keeps_delivery_and_checkpoint(database):
    """工作状态转换失败时回滚游标更新

    :module: spec.design-governance
    :design-point:
    """
    with work.transaction(database, LINEAGE) as state:
        state["event_sources"] = {"source": {"cursor": "before"}}
    with pytest.raises(RuntimeError, match="failed delivery"):
        with work.transaction(database, LINEAGE) as state:
            state["event_sources"]["source"]["cursor"] = "after"
            raise RuntimeError("failed delivery")
    with database.read_transaction() as connection:
        assert work.read(connection, LINEAGE)["event_sources"]["source"]["cursor"] == "before"


def test_concurrent_progress_preserves_all_updates(database):
    """并发推进保留全部计数更新

    :module: spec.design-governance
    :design-point:
    """
    def advance(_):
        with work.transaction(database, LINEAGE) as state:
            source = state.setdefault("event_sources", {}).setdefault("source", {"count": 0})
            source["count"] += 1
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(advance, range(16)))
    with database.read_transaction() as connection:
        assert work.read(connection, LINEAGE)["event_sources"]["source"]["count"] == 16


@pytest.mark.parametrize("other", ["event_sources", "event_wires", "operators"])
def test_objects_of_one_lineage_progress_without_waiting_for_each_other(database, other):
    """同一 Lineage 的不同运行对象互不等待

    一个事务持有某个事件源时，同一 Lineage 中另一个事件源、连线或算子的推进不等待它提交，两边的修改都保留。

    :design-point:
    """
    import threading
    with work.transaction(database, LINEAGE) as state:
        state["event_sources"] = {"held": {"count": 0}, "other": {"count": 0}}
        state["event_wires"] = {"other": {"pending_events": [{"claim_token": "claim", "event": {}}]}}
        state["operators"] = {"other": {"count": 0}}
    entered, release = threading.Event(), threading.Event()

    def hold():
        with work.transaction(database, LINEAGE) as state:
            state["event_sources"]["held"]["count"] += 1
            entered.set()
            assert release.wait(10)

    def advance_other():
        with work.transaction(database, LINEAGE) as state:
            if other == "event_wires":
                state["event_wires"]["other"]["pending_events"].append({"claim_token": "next", "event": {}})
            else:
                state[other]["other"]["count"] += 1

    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    assert entered.wait(10)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(advance_other).result(timeout=3)
    finally:
        release.set()
        holder.join(10)
    with database.read_transaction() as connection:
        saved = work.read(connection, LINEAGE)
    assert saved["event_sources"]["held"]["count"] == 1
    if other == "event_wires":
        assert [item["claim_token"] for item in saved["event_wires"]["other"]["pending_events"]] == ["claim", "next"]
    else:
        assert saved[other]["other"]["count"] == 1


def test_a_transaction_reads_only_the_objects_it_touches(database):
    """Program 工作事务只读取用到的对象

    修改一个事件源的事务只读取并锁定这个事件源，不加载同一 Lineage 的其他事件源、连线和算子。

    :design-point:
    """
    with work.transaction(database, LINEAGE) as state:
        state["event_sources"] = {f"source-{index}": {"count": index} for index in range(5)}
    with work.transaction(database, LINEAGE) as state:
        state["event_sources"]["source-3"]["count"] += 1
        assert state["event_sources"].loaded == {"source-3"}
        assert not state["event_wires"].loaded and not state["operators"].loaded
    with database.read_transaction() as connection:
        assert work.read(connection, LINEAGE)["event_sources"]["source-3"]["count"] == 4


def test_definition_lock_allows_independent_delivery_and_mailbox_progress(database):
    """定义锁允许投递与 Mailbox 独立推进

    :design-point:"""
    store = get_lineage_data_store()

    def advance():
        with work.transaction(database, LINEAGE) as state:
            state["event_sources"] = {"source": {"cursor": "accepted"}}
        with store.resident_mailbox_progress.transaction(LINEAGE, "resident_test") as state:
            state["cursor"] = "accepted"

    with ThreadPoolExecutor(max_workers=1) as pool:
        with database.transaction():
            store.lock_lineage_definition(LINEAGE)
            pool.submit(advance).result(timeout=3)
    assert store.resident_mailbox_progress.get(LINEAGE, "resident_test")["cursor"] == "accepted"
