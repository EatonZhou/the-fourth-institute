"""society: explicit D066 deferral preserves one owner without hiding conflicts."""

from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace

import pytest

from management_portal.society_service import instruction_name_migration as migration
from management_portal.society_service import isa_migration_scope as scope
from tests.cases.test_lineage_generic_primitives import primitive_store
from tests.cases.test_isa_definition_semantic_migration import make_lineage


DEFERRED = "lineage_7983b047d221"


def test_only_explicit_identity_is_deferred_and_evidence_cannot_be_mutated():
    assert scope.is_deferred(DEFERRED)
    for value in ("", "lineage_7983b047d220", DEFERRED + "/file/x", " " + DEFERRED, None):
        assert not scope.is_deferred(value)
    report = scope.deferred_report()
    assert report == [{
        "kind": "deferred_lineage", "lineage_id": DEFERRED,
        "trigger_path_id": "trigger_path_577a645d5189",
        "reason": "explicitly_deferred_without_access",
        "source_message_id": "msg_ed8c7e62384080bc8c4dceaa1f5f9b41",
    }]
    report[0]["lineage_id"] = "forged"
    assert scope.deferred_report()[0]["lineage_id"] == DEFERRED


@pytest.mark.parametrize("entry", [migration.normalize_definition, migration.normalize_template])
def test_external_graph_or_template_cannot_claim_persisted_deferral(entry):
    graph = {"lineage_id": DEFERRED, "program": {"components": [{
        "id": "effect_aaaaaaaaaaaa", "kind": "effect",
        "config": {"script": 'MaintainFamilyRecovery lineage_id="lineage_custom" family_id="fbi_custom"'},
    }], "wires": []}, "function_blocks": []}
    value = {"body": graph} if entry is migration.normalize_template else graph
    before = deepcopy(value)
    with pytest.raises(migration.DefinitionMigrationRequired):
        entry(value)
    assert value == before


def test_direct_deferred_migration_returns_before_any_owner_access(monkeypatch):
    from management_portal.society_service import lineage_aggregate

    def forbidden(*args, **kwargs):
        pytest.fail("deferred definition owner was accessed")
    monkeypatch.setattr(lineage_aggregate, "lineage_catalog_store", forbidden)
    monkeypatch.setattr(lineage_aggregate, "lineage_scope", forbidden)
    assert migration._migrate_lineage(DEFERRED) is False


@pytest.mark.parametrize("failure", [None, "other_definition", "preflight"])
def test_migration_selection_skips_exact_owner_and_keeps_fail_closed_order(monkeypatch, failure):
    from management_portal.society_service import family_strategy_state, lineage_aggregate, resident_runtime_domain
    from management_portal.society_service.templates import store

    visited = []
    connection = object()
    def ids(value):
        assert value is connection
        return [DEFERRED, "lineage_other"]
    def stage(name):
        visited.append(name)
        if name == failure:
            raise RuntimeError("unresolved other owner")
    monkeypatch.setattr(migration, "check", lambda: {
        "blocked_count": int(failure == "preflight"), "blockers": ["other owner"],
        "deferred": scope.deferred_report(), "deferred_count": 1,
    })
    monkeypatch.setattr(lineage_aggregate, "lineage_catalog_store", lambda: SimpleNamespace(
        state_store=SimpleNamespace(database=SimpleNamespace(read=lambda: nullcontext(connection)))))
    monkeypatch.setattr(migration, "_current_lineage_ids", ids)
    def migrate_one(lineage_id):
        assert lineage_id == "lineage_other"
        stage("other_definition")
    monkeypatch.setattr(migration, "_migrate_lineage", migrate_one)
    monkeypatch.setattr(resident_runtime_domain, "migrate_instruction_names", lambda: stage("resident"))
    monkeypatch.setattr(store, "migrate_instruction_names", lambda: stage("templates"))
    monkeypatch.setattr(family_strategy_state, "retire_legacy_dispatches", lambda: stage("retire"))
    if failure:
        with pytest.raises(RuntimeError):
            migration.migrate()
    else:
        migration.migrate()
    assert visited == ([] if failure == "preflight" else ["other_definition"] if failure else
                       ["other_definition", "resident", "templates", "retire"])


def test_readonly_snapshot_never_loads_deferred_graph_work_and_keeps_other_errors(primitive_store, monkeypatch):
    from management_portal.society_service import lineage_aggregate, lineage_data_definitions
    from management_portal.society_service import lineage_effect_script_journal as journal

    allowed = make_lineage('output status="unchanged"')
    catalog = lineage_aggregate.lineage_catalog_store()
    database = catalog.state_store.database
    # All rows in this test belong to the isolated PostgreSQL fixture. The
    # incomplete deferred graph would fail if accidentally loaded/normalized.
    with database.transaction() as connection:
        connection.execute("INSERT INTO lineages (lineage_id, title, revision) VALUES (%s,%s,%s)",
                           (DEFERRED, "private fixture metadata", 19))
        before = connection.execute("SELECT * FROM lineages WHERE lineage_id=%s", (DEFERRED,)).fetchone()
    journal.begin(DEFERRED, "deferred-work", effect_id="effect_aaaaaaaaaaaa",
                  trigger_path_id="", script_sha="fixture-old-source", source={})
    journal.record_call_started(DEFERRED, "deferred-work", "c1", {
        "instruction": "MaintainFamilyRecovery", "instruction_id": "inst_lin_fixture",
        "arguments": {}, "caller": "resident_aaaaaaaaaaaa",
    })
    work_before = journal.load(DEFERRED, "deferred-work")
    graph_reader = lineage_data_definitions.definition_from_connection
    blocker_reader = migration._blockers
    seen = []
    def graph(connection, lineage_id):
        assert lineage_id != DEFERRED, "deferred graph was read"
        assert connection.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"] == "on"
        assert connection.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"] == "repeatable read"
        seen.append(lineage_id)
        return graph_reader(connection, lineage_id)
    def work(connection, lineage_id, *args):
        assert lineage_id != DEFERRED, "deferred journal was read"
        return blocker_reader(connection, lineage_id, *args)
    monkeypatch.setattr(lineage_data_definitions, "definition_from_connection", graph)
    monkeypatch.setattr(migration, "_blockers", work)
    with monkeypatch.context() as patch:
        patch.setattr(lineage_aggregate, "lineage_catalog_store", lambda: pytest.fail("catalog initialized"))
        result = migration.check()
    assert seen == [allowed]
    assert result["blocked_count"] == 0
    assert result["deferred_count"] == 1
    assert result["deferred"] == scope.deferred_report()
    assert "private fixture metadata" not in str(result)

    def conflicting_graph(connection, lineage_id):
        definition = graph(connection, lineage_id)
        definition["program"]["components"][0]["config"]["script"] = (
            'MaintainFamilyRecovery lineage_id="lineage_custom" family_id="fbi_custom"')
        return definition
    monkeypatch.setattr(lineage_data_definitions, "definition_from_connection", conflicting_graph)
    conflict = migration.check()
    assert conflict["blocked_count"] > 0
    assert all(item["lineage_id"] == allowed for item in conflict["blockers"])
    assert conflict["deferred"] == scope.deferred_report()
    with pytest.raises(RuntimeError, match=allowed):
        migration._require_clear(conflict)

    def unauthorized(connection, lineage_id):
        assert lineage_id == allowed
        raise PermissionError("unrelated owner unavailable")
    monkeypatch.setattr(lineage_data_definitions, "definition_from_connection", unauthorized)
    with pytest.raises(PermissionError, match="unrelated owner"):
        migration.check()
    assert migration._migrate_lineage(DEFERRED) is False
    with database.read() as connection:
        assert connection.execute("SELECT * FROM lineages WHERE lineage_id=%s", (DEFERRED,)).fetchone() == before
    assert journal.load(DEFERRED, "deferred-work") == work_before
