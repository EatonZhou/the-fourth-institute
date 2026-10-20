"""
:module: resident.fork-handoff
:importance: P1
"""
from __future__ import annotations
from tests.cases.database_test_support import IntegrityError, connect
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from management_portal.society_service import lineage_state_domains
from management_portal.society_service.state_store import (
    LineageDataStore,
    RuntimeCASConflict,
)
from tests.cases.society_service_test_support import (
    install_resource_boundary, publish_registered_lineage_members,
)


LINEAGE_ID = "lineage_303030303030"


@pytest.fixture(autouse=True)
def object_resources(monkeypatch, tmp_path):
    monkeypatch.setenv("PORTAL_LINEAGE_FOLDER_ROOT", str(tmp_path / "lineages"))
    install_resource_boundary(monkeypatch)


def _definition(*, nodes: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema": "lineage-folder-v3",
        "schema_version": 1,
        "lineage_id": LINEAGE_ID,
        "lineage_membership": "formal",
        "project_id": "",
        "title": "Object state",
        "description": "",
        "leader_resident_id": "",
        "nodes": nodes,
        "program": {"components": [], "wires": []},
        "revision": 1,
        "layout_revision": 0,
        "created_at": "2026-09-01T00:00:00Z",
        "updated_at": "2026-09-01T00:00:00Z",
        "graph_updated_at": "2026-09-01T00:00:00Z",
        "layout_updated_at": "",
        "created_by": "sample_admin",
    }


def _store(
    tmp_path: Path,
    *,
    nodes: list[dict[str, object]] | None = None,
) -> LineageDataStore:
    store = LineageDataStore()
    if nodes is None:
        nodes = [
            {"id": "resident_111111111111", "x": 1, "y": 2},
            {"id": "resident_222222222222", "x": 3, "y": 4},
        ]
    store.put_lineage_definition(
        LINEAGE_ID,
        _definition(nodes=[]),
        {
            "schema": "tfi.lineage-runtime-state.v1",
            "lineage_id": LINEAGE_ID,
            "runtime_revision": 0,
        },
    )
    publish_registered_lineage_members(_definition(nodes=nodes), actor="sample_admin")
    store.put_lineage_definition(
        LINEAGE_ID,
        _definition(nodes=nodes),
        store.lineage_snapshot(LINEAGE_ID)[1],
    )
    # Formal membership materializes canonical graph nodes before the explicit
    # definition write. Establish the fixture's authored starting layout while
    # preserving the Lineage-wide layout revision.
    def set_initial_layout(connection: object) -> None:
        for node in nodes:
            connection.execute(
                'UPDATE lineage_layout_positions SET x=%s, y=%s WHERE lineage_id=%s AND object_id=%s',
                (node.get("x", 0), node.get("y", 0), LINEAGE_ID, node["id"]),
            )

    store.database.write(set_initial_layout)
    return store


def _position(store: LineageDataStore, object_id: str) -> tuple[float, float]:
    node = next(
        item
        for item in store.lineage_definition(LINEAGE_ID)["nodes"]
        if item["id"] == object_id
    )
    return float(node["x"]), float(node["y"])


def test_layout_updates_for_different_objects_do_not_overwrite_each_other_contracts(tmp_path, subtests, isolated_portal_case):
    """不同对象的布局更新互不覆盖且批量失败整体回滚"""
    with subtests.test(contract='test_layout_updates_for_different_objects_do_not_overwrite_each_other'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = _store(tmp_path)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    store.upsert_layout_positions,
                    LINEAGE_ID,
                    [{"object_id": object_id, "x": x, "y": y}],
                    updated_at=f"2026-09-01T00:00:0{index}Z",
                )
                for index, (object_id, x, y) in enumerate(
                    (("resident_111111111111", 10, 20), ("resident_222222222222", 30, 40)),
                    start=1,
                )
            ]
            [future.result() for future in futures]

        assert _position(store, "resident_111111111111") == (10, 20)
        assert _position(store, "resident_222222222222") == (30, 40)
    with subtests.test(contract='test_layout_update_is_last_write_wins_per_object'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = _store(tmp_path)

        store.upsert_layout_positions(
            LINEAGE_ID,
            [{"object_id": "resident_111111111111", "x": 10, "y": 20}],
            updated_at="2026-09-01T00:00:01Z",
        )
        store.upsert_layout_positions(
            LINEAGE_ID,
            [{"object_id": "resident_111111111111", "x": 50, "y": 60}],
            updated_at="2026-09-01T00:00:02Z",
        )

        assert _position(store, "resident_111111111111") == (50, 60)
        with connect(store.database) as connection:
            revision = connection.execute(
                "SELECT layout_revision FROM lineages WHERE lineage_id=%s",
                (LINEAGE_ID,),
            ).fetchone()[0]
        assert revision == 2
    with subtests.test(contract='test_layout_batch_rolls_back_as_one_transaction'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = _store(tmp_path)
        with connect(store.database) as connection:
            connection.execute(
                """
                CREATE FUNCTION reject_node_b_layout_update() RETURNS trigger AS $$
                BEGIN
                    IF NEW.object_id = 'resident_222222222222' THEN
                        RAISE EXCEPTION 'reject resident_222222222222' USING ERRCODE = '23514';
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql
                """
            )
            connection.execute(
                """
                CREATE TRIGGER reject_node_b_layout
                BEFORE UPDATE ON lineage_layout_positions
                FOR EACH ROW EXECUTE FUNCTION reject_node_b_layout_update()
                """
            )

        with pytest.raises(IntegrityError, match="reject resident_222222222222"):
            store.upsert_layout_positions(
                LINEAGE_ID,
                [
                    {"object_id": "resident_111111111111", "x": 10, "y": 20},
                    {"object_id": "resident_222222222222", "x": 30, "y": 40},
                ],
                updated_at="2026-09-01T00:00:01Z",
            )

        assert _position(store, "resident_111111111111") == (1, 2)
        assert _position(store, "resident_222222222222") == (3, 4)
        with connect(store.database) as connection:
            connection.execute(
                "DROP TRIGGER reject_node_b_layout ON lineage_layout_positions"
            )
            connection.execute("DROP FUNCTION reject_node_b_layout_update()")
    with subtests.test(contract='test_deleted_identity_rejects_layout_and_reuse'), isolated_portal_case() as case_path:
        store = _store(case_path)
        store.remove_residents([{'resident_id': 'resident_111111111111', 'deleted_at': '2026-10-11T00:00:00Z', 'deleted_by': 'sample_admin'}])
        result = store.upsert_layout_positions(LINEAGE_ID, [{'object_id': 'resident_111111111111', 'x': 10, 'y': 20}], updated_at='')
        assert result['applied'] == []
        assert result['missing_object_ids'] == ['resident_111111111111']
        with pytest.raises(ValueError, match='Deleted graph IDs'):
            store.put_lineage_definition(LINEAGE_ID, _definition(nodes=[{'id': 'resident_111111111111'}]), store.lineage_snapshot(LINEAGE_ID)[1])






def test_lineage_timer_lease_has_one_holder_until_it_expires_contracts(tmp_path, subtests, isolated_portal_case):
    """Lineage 定时租约在到期前仅允许同一持有者续租"""
    with subtests.test(contract='test_lineage_timer_lease_has_one_holder_until_it_expires'), isolated_portal_case() as case_path:
        store = _store(case_path)
        assert store.hold_lineage_timer_lease("release:a", ttl_seconds=60) is True
        assert store.hold_lineage_timer_lease("dev:b", ttl_seconds=60) is False
        assert store.hold_lineage_timer_lease("release:a", ttl_seconds=0.01) is True  # renewed, short
        import time as _time
        _time.sleep(0.05)
        assert store.hold_lineage_timer_lease("dev:b", ttl_seconds=60) is True  # took over after expiry
        assert store.hold_lineage_timer_lease("release:a", ttl_seconds=60) is False


def test_a_timer_lease_taken_in_one_case_is_gone_in_the_next(isolated_portal_case):
    """Lineage 定时租约不跨测试用例残留

    测试隔离清空数据时保留 lineage_meta 的 schema 版本行，但删除其中的 Lineage 定时租约；上一个用例取得的租约不能让下一个用例拿不到租约。

    lineage_meta keeps its schema row between cases; the lease beside it must not survive.

    :module: operations.test-framework
    :importance: P2
    :aspects: functional, performance
    """
    with isolated_portal_case() as case_path:
        assert _store(case_path).hold_lineage_timer_lease("dev:other", ttl_seconds=60) is True
    with isolated_portal_case() as case_path:
        assert _store(case_path).hold_lineage_timer_lease("release:a", ttl_seconds=60) is True
