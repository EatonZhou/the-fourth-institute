"""Definition commits bound database round trips without relaxing atomicity."""
import copy
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from management_portal.society_service import (
    graph_storage,
    lineage_graph_edits,
    lineage_resident_nodes,
    lineage_state,
    resident_domain,
)
from management_portal.society_service.state_store import get_lineage_data_store
from management_portal.society_service import lineage_aggregate
from tests.cases.test_resident_folder_creation_contract import (  # noqa: F401
    future_creation_store, _new_session_payload,
)


def test_definition_resolves_members_once_including_prepared_identity(future_creation_store, monkeypatch):
    """规范化 Lineage 定义时一次批量解析全部成员，预先准备的身份不再查询"""
    parent = resident_domain.register_resident(_new_session_payload("Batch owner"), "sample_admin")
    child = lineage_resident_nodes.create_lineage_resident(
        parent["lineage_id"], _new_session_payload("Existing member"), "sample_admin",
    )["resident"]
    before = lineage_aggregate.read_lineage_state(parent["lineage_id"])
    candidate = copy.deepcopy(before)
    pending = "resident_abcdefabcdef"
    candidate["nodes"].append({"id": pending, "title": "Prepared identity"})
    resolve = lineage_state._resident_targets
    calls = []

    def observed(ids):
        calls.append(tuple(ids))
        return resolve(ids)

    monkeypatch.setattr(lineage_state, "_resident_targets", observed)
    normalized = lineage_state._normalize_lineage_shape(
        candidate, before, "sample_admin", resident_references={pending: {
            "resident_id": pending, "lineage_id": parent["lineage_id"], "title": "Prepared identity",
        }},
    )
    assert {n["id"] for n in normalized["nodes"]} == {parent["resident_id"], child["resident_id"], pending}
    assert len(calls) == 1, "one batched authority read per definition, not one full workspace projection per member"
    assert set(calls[0]) == {parent["resident_id"], child["resident_id"]}


def test_graph_commit_batches_writes_and_preserves_unchanged_rows(future_creation_store):
    """图提交的写入次数不随组件数增长，未改动的组件行保持原样"""
    parent = resident_domain.register_resident(_new_session_payload("Graph batch"), "sample_admin")
    store = get_lineage_data_store()
    lineage_id = parent["lineage_id"]
    candidate = store.lineage_definition(lineage_id)
    components = [{"id": f"event_source_{i:012x}", "kind": "event_source",
                   "title": f"Source {i}", "config": {"event_type": "manual.triggered"}}
                  for i in range(64)]
    candidate["program"]["components"] = components
    with store.database.transaction() as connection:
        graph_storage.put(connection, lineage_id, candidate)
    with store.database.read() as connection:
        before = {r["node_id"]: r["token"] for r in connection.execute(
            "SELECT node_id,ctid::text token FROM lineage_components WHERE node_id=ANY(%s)",
            ([n["id"] for n in components],),
        ).fetchall()}
    candidate["program"]["components"][0]["title"] = "Only this row changes"
    round_trips = []

    class ObservedConnection:
        def execute(self, sql, args=()):
            if sql.lstrip().upper().startswith("INSERT"):
                round_trips.append(sql)
            return connection.execute(sql, args)

        def executemany(self, sql, args):
            round_trips.append(sql)
            return connection.executemany(sql, args)

    with store.database.transaction() as connection:
        graph_storage.put(ObservedConnection(), lineage_id, candidate)
        actual = graph_storage.read_many(connection, [lineage_id])[lineage_id]
        assert {n["id"]: n["title"] for n in actual["program"]["components"]} == {
            n["id"]: n["title"] for n in candidate["program"]["components"]}
        after = {r["node_id"]: r["token"] for r in connection.execute(
            "SELECT node_id,ctid::text token FROM lineage_components WHERE node_id=ANY(%s)",
            ([n["id"] for n in components],),
        ).fetchall()}
        assert after[components[0]["id"]] != before[components[0]["id"]]
        assert all(after[n["id"]] == before[n["id"]] for n in components[1:])
        assert len(round_trips) <= 8, "graph commit write round trips must not grow with component count"


def test_concurrent_creation_merges_and_failed_delete_keeps_identity(future_creation_store, monkeypatch):
    """并发创建的成员都合并进图，删除时图提交失败则 Resident 身份保留"""
    parent = resident_domain.register_resident(_new_session_payload("Concurrent owner"), "sample_admin")
    lineage_id = parent["lineage_id"]
    append = lineage_resident_nodes._append_registered_resident_node_locked
    ready = threading.Barrier(2)

    def append_after_both_read(lineage, record, actor, **kwargs):
        ready.wait(timeout=10)
        return append(lineage, record, actor, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(lineage_resident_nodes, "_append_registered_resident_node_locked", append_after_both_read)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(lineage_resident_nodes.create_lineage_resident, lineage_id,
                                   _new_session_payload(title), "sample_admin") for title in ("A", "B")]
            children = [f.result(timeout=20)["resident"]["resident_id"] for f in futures]
    store = get_lineage_data_store()
    assert {n["id"] for n in store.lineage_definition(lineage_id)["nodes"]} == {parent["resident_id"], *children}
    persist = lineage_state._persist_lineage_state

    def fail_graph_commit(previous, lineage, reason, **kwargs):
        if reason == "批量删除 HATG 对象及 Resident 身份":
            raise RuntimeError("injected graph commit failure")
        return persist(previous, lineage, reason, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(lineage_state, "_persist_lineage_state", fail_graph_commit)
        with pytest.raises(RuntimeError, match="injected graph commit failure"):
            lineage_graph_edits.delete_lineage_graph_objects(lineage_id, {"resident_ids": [children[0]]}, "sample_admin")
    assert not store.resident_record(children[0]).get("deleted_at")
    assert children[0] in {n["id"] for n in store.lineage_definition(lineage_id)["nodes"]}
    lineage_graph_edits.delete_lineage_graph_objects(lineage_id, {"resident_ids": [children[0]]}, "sample_admin")
    assert store.resident_record(children[0], include_deleted=True)["deleted_at"]
    assert {n["id"] for n in store.lineage_definition(lineage_id)["nodes"]} == {parent["resident_id"], children[1]}
    with store.database.read() as connection:
        retained = connection.execute("SELECT lineage_id FROM lineage_nodes WHERE node_id=%s", (children[0],)).fetchone()
        assert retained["lineage_id"] == lineage_id, "historical membership is retained; it is not an active graph reference"
