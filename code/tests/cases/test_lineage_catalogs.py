from __future__ import annotations
from management_portal.society_service import business_fact_views

import json
from tests.cases.database_test_support import guard_queries
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from management_portal.society_service import resource_client
from management_portal.society_service.lineage_catalog import LineageCatalog
from management_portal.society_service.resident_catalog import ResidentCatalog
from management_portal.society_service.state_store import LineageDataStore
from management_portal.society_service import lineage_aggregate


LINEAGE_ID = "lineage_111111111111"
WORK_TABLES = (
    "event_source_progress", "operator_work", "pending_event_deliveries",
    "pending_resident_handoffs", "resident_active_turns", "lineage_run_records",
)


def _lineage_definition(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": "lineage-folder-v3",
        "schema_version": 1,
        "lineage_id": LINEAGE_ID,
        "lineage_membership": "formal",
        "project_id": "",
        "title": "Catalog test",
        "description": "",
        "leader_resident_id": "",
        "nodes": [],
        "program": {"schema": "tfi.hatg-program.v2", "components": [], "wires": []},
        "revision": 0,
        "layout_revision": 0,
        "created_at": "2026-08-16T00:00:00Z",
        "updated_at": "2026-08-16T00:00:00Z",
        "graph_updated_at": "2026-08-16T00:00:00Z",
        "layout_updated_at": "2026-08-16T00:00:00Z",
        "created_by": "sample_admin",
    }
    payload.update(overrides)
    payload.setdefault("principal_hierarchy", {"level": "lineage", "scope_id": payload["project_id"]})
    return payload


def _record(resident_id: str, *, lineage_id: str = LINEAGE_ID) -> dict[str, object]:
    from management_portal.society_service.lineage_storage import canonical_lineage_folder
    lineage_path = canonical_lineage_folder(lineage_id)
    workspace_path = lineage_path / "residents" / resident_id / "workspace"
    workspace_id = "workspace_" + resident_id.removeprefix("resident_")
    return {
        "schema": "tfi.resident.v6",
        "resident_id": resident_id,
        "lineage_id": lineage_id,
        "backend": "codex",
        "registration_state": "ready",
        "folder_schema": "lineage-folder-v3",
        "title": resident_id,
        "workspace_path": str(lineage_path),
        "owned_workspace_id": workspace_id,
        "default_cwd": str(workspace_path),
        "owned_workspace": {
            "workspace_id": workspace_id,
            "path": str(workspace_path),
        },
        "workspace_bindings": [],
    }


def test_stop_reads_only_access_facts_and_rejects_missing_members(monkeypatch):
    """停止成员执行只读访问信息，非成员、已删除成员或无调用者时拒绝"""
    from management_portal.society_service import lineage_domain, lineage_resident_controls

    store = LineageDataStore()
    catalog = LineageCatalog(store)
    resident_id = "resident_aaaaaaaaaaaa"
    catalog.upsert(_lineage_definition())
    store.put_resident_record(resident_id, _record(resident_id))
    monkeypatch.setattr(lineage_aggregate, "lineage_catalog_store", lambda: catalog)
    monkeypatch.setattr(catalog, "get_state", lambda *_: pytest.fail("Stop loaded the full graph/runtime"))
    cancelled = []
    monkeypatch.setattr(lineage_domain.resident_protocol, "cancel_resident_input",
                        lambda *args: cancelled.append(args) or {"cancellation_requested": True})
    payload = {"resident_id": resident_id, "message_id": "msg_exact"}
    assert lineage_resident_controls.cancel_lineage_resident_execution(LINEAGE_ID, payload, "sample_admin")["cancellation_requested"]
    assert cancelled == [(resident_id, "sample_admin", "msg_exact")]

    with pytest.raises(ValueError, match="Agent 节点"):
        lineage_resident_controls.cancel_lineage_resident_execution(
            LINEAGE_ID, {**payload, "resident_id": "resident_bbbbbbbbbbbb"}, "sample_admin")
    with pytest.raises(PermissionError):
        lineage_resident_controls.cancel_lineage_resident_execution(LINEAGE_ID, payload, "")
    store.database.write(lambda connection: connection.execute(
        "UPDATE residents SET deleted_at=%s WHERE resident_id=%s", ("2026-10-13T00:00:00Z", resident_id)))
    with pytest.raises(ValueError, match="Agent 节点"):
        lineage_resident_controls.cancel_lineage_resident_execution(LINEAGE_ID, payload, "sample_admin")
    assert len(cancelled) == 1


def test_lineage_listing_projection_matches_full_summary(tmp_path, monkeypatch):
    """Lineage 列表的轻量投影与完整摘要一致，不读布局和运行数据"""
    from management_portal.society_service import lineage_reads, lineage_state_domains

    store = LineageDataStore()
    catalog = LineageCatalog(store)
    first_resident = "resident_aaaaaaaaaaaa"
    second_resident = "resident_bbbbbbbbbbbb"
    empty_id = "lineage_222222222222"
    definition = _lineage_definition(
        leader_resident_id=first_resident,
        nodes=[
            {"id": first_resident, "status": "running", "role": {}},
            {"id": second_resident, "status": "", "role": {}},
        ],
        edges=[{"id": "edge_test", "source": first_resident, "target": second_resident, "type": "inheritance"}],
        program={"components": [
            {"id": "seed_active", "kind": "seed", "config": {}},
            {"id": "seed_closed", "kind": "seed", "config": {"status": "archived"}},
            {"id": "unneeded_body", "kind": "effect", "config": {"body": "x" * 100_000}},
        ], "wires": []},
    )
    catalog.upsert(_lineage_definition())
    for rid in (first_resident, second_resident):
        store.put_resident_record(rid, _record(rid))
    catalog.upsert(lineage_state_domains.definition_projection(definition))
    catalog.upsert(_lineage_definition(lineage_id=empty_id, title="Empty"))
    expected = {
        target: lineage_reads._lineage_summary(catalog.get(target))
        for target in (LINEAGE_ID, empty_id)
    }
    original_read = store.database.read
    original_transaction = store.database.read_transaction
    statements = []

    @contextmanager
    def listing_read():
        with original_read() as connection:
            yield guard_queries(
                connection,
                forbidden=(
                    "lineage_layout_positions",
                    *WORK_TABLES,
                ),
                statements=statements,
            )

    @contextmanager
    def listing_transaction():
        with original_transaction() as connection:
            yield guard_queries(
                connection,
                forbidden=(
                    "lineage_layout_positions",
                    *WORK_TABLES,
                ),
                statements=statements,
            )

    monkeypatch.setattr(store.database, "read", listing_read)
    monkeypatch.setattr(store.database, "read_transaction", listing_transaction)
    records = catalog.listing_records()
    assert len(json.dumps(records)) < 3000
    assert not any("nodes" in record or "program" in record for record in records)
    summaries = catalog.listing_graph_summaries([LINEAGE_ID, empty_id, LINEAGE_ID])
    actual = {
        record["lineage_id"]: lineage_reads._lineage_summary(
            record, graph_summary=summaries[record["lineage_id"]],
        )
        for record in records
    }
    assert actual == expected
    assert actual[LINEAGE_ID]["node_count"] == 2
    assert actual[LINEAGE_ID]["edge_count"] == 1
    assert actual[LINEAGE_ID]["active_seed_count"] == 1
    assert actual[LINEAGE_ID]["statuses"] == {"idle": 2}
    statements.clear()
    assert catalog.listing_graph_summaries([]) == {}
    assert statements == []
    assert len(catalog.listing_graph_summaries([
        f"lineage_{index:012x}" for index in range(410)
    ])) == 410


@pytest.mark.parametrize("allowed", [True, False])
def test_lineage_list_checks_actor_once_before_graph_summary(tmp_path, monkeypatch, allowed):
    """Lineage 列表每次请求只检查一次管理身份

    批量构建 Lineage 图摘要时复用本请求的管理身份检查，同时保留各对象的可见性与删除约束。

    :design: decision_71aa039e3211
    :design-point:
    """
    from management_portal.society_service import lineage_reads

    catalog = LineageCatalog(LineageDataStore())
    hidden_id = "lineage_222222222222"
    catalog.upsert(_lineage_definition())
    catalog.upsert(_lineage_definition(lineage_id=hidden_id))
    visible_ids = [LINEAGE_ID, hidden_id] if allowed else []
    authorizations = []
    calls = []
    original_summaries = catalog.listing_graph_summaries

    def forbidden_snapshot():
        raise AssertionError("Listing must not load complete Lineage definitions")

    def canonical_views_stub(records, *, id_field, actor):
        return list(records)

    def authorize(actor):
        authorizations.append(actor)
        return allowed

    def selected_summaries(lineage_ids):
        calls.append(list(lineage_ids))
        return original_summaries(lineage_ids)

    monkeypatch.setattr(catalog, "snapshot", forbidden_snapshot)
    monkeypatch.setattr(catalog, "listing_graph_summaries", selected_summaries)
    monkeypatch.setattr(lineage_aggregate, "lineage_catalog_store", lambda: catalog)
    monkeypatch.setattr(business_fact_views, "canonical_views", canonical_views_stub)
    monkeypatch.setattr(
        lineage_reads,
        "_actor_can_manage_lineages",
        authorize,
    )
    result = lineage_reads.list_lineages("sample_admin")
    assert [record["lineage_id"] for record in result["lineages"]] == visible_ids
    assert calls == [visible_ids]
    assert authorizations == ["sample_admin"]
    assert all(record["allows"]["UpdateLineage"] is True for record in result["lineages"])


@pytest.mark.parametrize("graph_size", [0, 1000])
def test_lineage_listing_metadata_does_not_read_graph(tmp_path, monkeypatch, graph_size):
    """Lineage 列表元数据一条查询读出，无论图多大都不读节点和组件"""
    store = LineageDataStore()
    catalog = LineageCatalog(store)
    catalog.upsert(_lineage_definition(program={"components": [
        {"id": f"event_{i}", "kind": "event_source", "config": {"event_type": "manual.triggered"}}
        for i in range(graph_size)], "wires": []}))
    read = store.database.read
    statements = []
    @contextmanager
    def guarded():
        with read() as c:
            yield guard_queries(c, statements=statements, forbidden=("lineage_nodes", "lineage_components", *WORK_TABLES))
    monkeypatch.setattr(store.database, "read", guarded)
    rows = catalog.listing_records()
    assert len(rows) == 1 and rows[0]["lineage_id"] == LINEAGE_ID
    assert not ({"owner_ids", "runtime_principal_id", "program", "nodes"} & rows[0].keys())
    assert len(statements) == 1


def test_lineage_access_records_do_not_load_component_bodies(tmp_path, monkeypatch):
    """读取 Lineage 访问记录不加载组件、连线和运行数据"""
    store = LineageDataStore()
    catalog = LineageCatalog(store)
    catalog.upsert(_lineage_definition(program={"components": [
        {"id": "event_large", "kind": "event_source", "config": {"body": "x" * 100_000}}], "wires": []}))
    read = store.database.read
    @contextmanager
    def guarded():
        with read() as c:
            yield guard_queries(c, forbidden=("lineage_components", "lineage_edges", *WORK_TABLES))
    monkeypatch.setattr(store.database, "read", guarded)
    records = catalog.get_access_records([LINEAGE_ID])
    assert len(json.dumps(records)) < 2000
    assert records[0]["lineage_node_ids"] == []
    assert "owner_ids" not in records[0]
    assert catalog.get_access_records(["lineage_ffffffffffff"]) == []


def test_lineage_catalog_persists_and_validates_definitions_contracts(tmp_path, subtests, isolated_portal_case):
    """Lineage 目录校验并保存定义和运行时状态、索引 Resident 观测者，Resident 记录按快照读取"""
    with subtests.test(contract='test_lineage_catalog_persists_and_validates_definitions'), isolated_portal_case() as case_path:
        tmp_path = case_path
        catalog = LineageCatalog(
            LineageDataStore(),
        )
        definition = _lineage_definition()
        catalog.upsert(definition)

        expected = {**definition, "program": {**definition["program"], "schema": "tfi.hatg-program.v2"}}
        assert catalog.get(LINEAGE_ID) == expected
        assert LineageCatalog(
            LineageDataStore(),
        ).snapshot() == [expected]

        invalid = _lineage_definition()
        invalid.pop("lineage_membership")
        with pytest.raises(ValueError, match="missing required fields"):
            catalog.upsert(invalid)
        with pytest.raises(ValueError, match="unsupported fields"):
            catalog.upsert(_lineage_definition(unexpected_projection="visible"))
    with subtests.test(contract='test_lineage_catalog_indexes_program_observers'), isolated_portal_case() as case_path:
        tmp_path = case_path
        resident_id = "resident_aaaaaaaaaaaa"
        event_source_id = "event_source_external_done"
        catalog = LineageCatalog(
            LineageDataStore(),
        )
        catalog.upsert(_lineage_definition())
        catalog.state_store.put_resident_record(resident_id, _record(resident_id))
        catalog.upsert(_lineage_definition(program={
            "components": [{
                "id": event_source_id,
                "kind": "event_source",
                "config": {"event_type": "resident.work_finished"},
            }],
            "wires": [{
                "id": "wire_external_done_watch",
                "kind": "watch",
                "source_port": "watch",
                "source": event_source_id,
                "target": resident_id,
            }],
        }))

        assert catalog.program_resident_observers(
            resident_id,
            "resident.work_finished",
        ) == {LINEAGE_ID: [event_source_id]}
        assert catalog.program_resident_observers(
            resident_id,
            "resident.work_failed",
        ) == {}
    with subtests.test(contract='test_lineage_runtime_is_initialized_once_and_persists_independently'), isolated_portal_case() as case_path:
        tmp_path = case_path
        catalog = LineageCatalog(
            LineageDataStore(),
        )
        catalog.upsert(_lineage_definition())
        assert catalog.get_state(LINEAGE_ID)[1] == {
            "schema": "tfi.lineage-runtime-state.v1",
            "lineage_id": LINEAGE_ID,
            "runtime_revision": 0,
            "nodes": {}, "edges": {}, "updated_at": "",
        }
        runtime = {
            "schema": "tfi.lineage-runtime-state.v1",
            "lineage_id": LINEAGE_ID,
            "runtime_revision": 3,
            "nodes": {}, "edges": {}, "updated_at": "",
        }
        catalog.put_runtime(LINEAGE_ID, runtime)
        updated_definition = _lineage_definition()
        updated_definition["title"] = "Updated title"
        catalog.upsert(updated_definition)

        reopened = LineageCatalog(
            LineageDataStore(),
        )
        assert reopened.get_state(LINEAGE_ID)[1] == {**runtime, "schema": "tfi.lineage-runtime-state.v1"}
        reopened.remove(LINEAGE_ID, deleted_by="sample_admin")
        with pytest.raises(KeyError):
            reopened.get(LINEAGE_ID)
    with subtests.test(contract='test_lineage_catalog_reads_definition_and_runtime_as_one_snapshot'), isolated_portal_case() as case_path:
        tmp_path = case_path
        catalog = LineageCatalog(
            LineageDataStore(),
        )
        definition = _lineage_definition()
        catalog.upsert(definition)
        runtime = {
            "schema": "tfi.lineage-runtime-state.v1",
            "lineage_id": LINEAGE_ID,
            "runtime_revision": 4,
            "nodes": {}, "edges": {}, "updated_at": "",
        }
        catalog.put_runtime(LINEAGE_ID, runtime)

        snapshot_definition, snapshot_runtime = catalog.get_state(LINEAGE_ID)

        assert snapshot_definition == {**definition, "program": {**definition["program"], "schema": "tfi.hatg-program.v2"}}
        assert snapshot_runtime == {**runtime, "schema": "tfi.lineage-runtime-state.v1"}
    with subtests.test(contract='test_resident_catalog_persists_validated_records'), isolated_portal_case() as case_path:
        tmp_path = case_path
        state_store = LineageDataStore()
        lineages = LineageCatalog(state_store)
        for lineage_id in (LINEAGE_ID, "lineage_222222222222"):
            lineages.upsert(_lineage_definition(lineage_id=lineage_id))
        catalog = ResidentCatalog(
            tmp_path,
            state_store,
        )
        first = _record("resident_111111111111")
        second = _record(
            "resident_222222222222",
            lineage_id="lineage_222222222222",
        )
        catalog.upsert(first)
        catalog.upsert(second)

        reopened = ResidentCatalog(
            tmp_path,
            LineageDataStore(),
        )
        stored_first = reopened.get(first["resident_id"])
        assert {key: stored_first[key] for key in first} == {**first, "schema": "tfi.resident.v6"}
        assert stored_first['execution_settings']
        assert reopened.for_lineage(LINEAGE_ID) == [stored_first]
        assert reopened.target(second["resident_id"]).lineage_id == second["lineage_id"]
    with subtests.test(contract='test_resident_catalog_reads_family_as_one_detached_snapshot'), isolated_portal_case() as case_path:
        tmp_path = case_path
        state_store = LineageDataStore()
        lineages = LineageCatalog(state_store)
        for lineage_id in (LINEAGE_ID, "lineage_333333333333"):
            lineages.upsert(_lineage_definition(lineage_id=lineage_id))
        catalog = ResidentCatalog(
            tmp_path,
            state_store,
        )
        first = _record("resident_111111111111")
        sibling = _record("resident_222222222222")
        unrelated = _record(
            "resident_333333333333",
            lineage_id="lineage_333333333333",
        )
        for record in (first, sibling, unrelated):
            catalog.upsert(record)

        anchor = catalog.get(first["resident_id"])
        family = catalog.for_lineage(first["lineage_id"])
        anchor["title"] = "detached"
        family[0]["title"] = "detached"

        assert {item["resident_id"] for item in family} == {
            first["resident_id"],
            sibling["resident_id"],
        }
        assert catalog.get(first["resident_id"])["title"] == first["title"]


def test_resident_catalog_rejects_noncanonical_records(tmp_path: Path) -> None:
    """Resident 目录拒绝旧 schema、持久化的 Agent 会话字段和经符号链接的默认 Workspace"""
    catalog = ResidentCatalog(
        tmp_path,
        LineageDataStore(),
    )
    invalid_schema = _record("resident_111111111111")
    invalid_schema["schema"] = "invalid"
    with pytest.raises(ValueError, match="current contract"):
        catalog.upsert(invalid_schema)

    bound_session = _record("resident_222222222222")
    bound_session["backend"] = "codex"
    bound_session["backend_binding"] = {
        "backend": "codex",
        "binding_role": "main",
        "execution_binding_id": "resident:resident_222222222222",
        "codex_thread_id": "thread-bound",
    }
    with pytest.raises(ValueError, match="persisted Agent session fields"):
        catalog.upsert(bound_session)

    alias_root = tmp_path / "lineage-alias"
    canonical_root = tmp_path / "lineage"
    canonical_root.mkdir()
    alias_root.symlink_to(canonical_root, target_is_directory=True)
    aliased_binding = _record("resident_333333333333")
    aliased_binding["workspace_path"] = str(canonical_root)
    alias_workspace = alias_root / "residents/resident_333333333333/workspace"
    aliased_binding["default_cwd"] = str(alias_workspace)
    aliased_binding["owned_workspace"]["path"] = str(alias_workspace)
    with pytest.raises(ValueError, match="default Workspace Binding"):
        catalog.upsert(aliased_binding)


def test_resident_catalog_rejects_fork_as_resident_identity(tmp_path, subtests, isolated_portal_case):
    """Resident 目录拒绝 fork_id、forks 等 Fork 身份字段和表示 Fork 的状态值"""
    for field, value, message in [
        ("fork_id", "resident_333333333333", "forbidden Fork identity fields"),
        ("forks", [], "forbidden Fork identity fields"),
        ("view", "fork", "forbidden Fork identity fields"),
        ("status", "budded", "status cannot encode the Fork operation"),
        (
            "session_state",
            "budded",
            "session_state cannot encode the Fork operation",
        ),
    ]:
        with subtests.test(field=field, value=value, message=message), isolated_portal_case() as case_path:
            tmp_path = case_path
            catalog = ResidentCatalog(
                tmp_path,
                LineageDataStore(),
            )
            record = _record("resident_333333333333")
            record[field] = value

            with pytest.raises(ValueError, match=message):
                catalog.upsert(record)


