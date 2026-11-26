"""The same bounded state plan must be checked before and during cutover."""
from copy import deepcopy

import pytest

from management_common.json_columns import json_text
from management_portal.society_service import family_strategy_state as migration
from management_portal.society_service import lineage_graph_state as state
from management_portal.society_service import lineage_aggregate
from tests.cases.test_lineage_generic_primitives import primitive_store
from tests.cases.test_isa_definition_semantic_migration import make_lineage


def test_many_small_cells_remain_bounded_by_count_value_and_document_bytes():
    cells = {f"family.{i}.review": {"cursor": i} for i in range(300)}
    assert state._normalize({"cells": cells})["cells"] == cells
    with pytest.raises(ValueError, match="4096"):
        state._normalize({"cells": {str(i): None for i in range(4097)}})
    with pytest.raises(ValueError, match="64 KiB"):
        state._normalize({"cells": {"large": "x" * (64 * 1024)}})
    with pytest.raises(ValueError, match="2 MiB"):
        state._normalize({"cells": {str(i): "x" * 60000 for i in range(36)}})


def test_readonly_capacity_preflight_matches_atomic_writer(primitive_store, monkeypatch):
    lineage_id = make_lineage('output status="unchanged"')
    database = lineage_aggregate.lineage_catalog_store().state_store.database
    initial = state.empty_state()
    initial["cells"] = {f"original.{i}": i for i in range(128)}
    with database.transaction() as connection:
        connection.execute("INSERT INTO lineage_shared_state(lineage_id,cells_json) VALUES (%s,%s) "
                           "ON CONFLICT(lineage_id) DO UPDATE SET cells_json=excluded.cells_json",
                           (lineage_id, json_text(initial)))
    cells = {f"family.{i}.review": {"cursor": i} for i in range(300)}
    monkeypatch.setattr(migration, "_family_cells", lambda connection, lineage_id: deepcopy(cells))
    with database.readonly_snapshot() as connection:
        assert connection.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"] == "on"
        assert migration.check_family_state(connection, lineage_id) == []
    with database.transaction() as connection:
        assert len(migration.migrate_family_state(connection, lineage_id)) == 300
    stored = state.read_state(lineage_id)
    assert stored["cells"] == initial["cells"] | cells
    with database.transaction() as connection:
        assert migration.migrate_family_state(connection, lineage_id) == []
    cells.update({f"extra.{i}": i for i in range(4096)})
    with database.readonly_snapshot() as connection:
        issues = migration.check_family_state(connection, lineage_id)
        assert issues[0]["kind"] == "family_state"
        assert issues[0]["lineage_id"] == lineage_id
        assert "4096" in issues[0]["reason"]
    with pytest.raises(ValueError, match="4096"):
        with database.transaction() as connection:
            migration.migrate_family_state(connection, lineage_id)
    assert state.read_state(lineage_id) == stored
    from management_portal.society_service import instruction_name_migration as contracts
    monkeypatch.setattr(contracts, "_has_original_family_script", lambda definition: True)
    report = contracts.check()
    assert any(issue["kind"] == "family_state" and issue["lineage_id"] == lineage_id
               for issue in report["blockers"])
