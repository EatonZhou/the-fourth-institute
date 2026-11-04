"""Existing storage is consumed by each feature, not gated as one schema."""
import pytest
from psycopg.errors import UndefinedColumn, UndefinedTable

from management_portal.society_service import resident_catalog, state_store, resident_reporting
from management_portal.society_service import resident_conversation_stream
from tests.cases.test_wall04_residual_20260919 import team, instruction  # noqa: F401
from tests.cases.test_cross_owner_storage_integration import owners, ACTOR  # noqa: F401


@pytest.mark.parametrize("value", [False, [], "", {"initiative":"off"}])
def test_optional_default_does_not_mask_corrupt_policy(value):
    with pytest.raises(ValueError):
        resident_reporting.configured({"reporting":value})


@pytest.mark.parametrize("metadata", ["row_missing", "different", "table_missing"])
def test_existing_residents_and_conversation_work_without_optional_column_or_metadata(team, metadata):
    catalog = resident_catalog.get_catalog()
    db = catalog.state_store.database
    rid = team.target["resident_id"]
    repo = resident_conversation_stream.get_repository()
    mid = "msg_startup_compatibility"
    repo.append_message(rid, {"message_id":mid,"role":"user","author_id":ACTOR,
        "content_parts":[{"type":"text","text":"persisted body"}]})
    repo.set_input_state(rid, mid, "completed")
    try:
        with db.transaction() as c:
            c.execute("ALTER TABLE resident_execution_configuration DROP COLUMN reporting_json")
            if metadata == "table_missing":
                c.execute("ALTER TABLE lineage_meta RENAME TO startup_saved_meta")
            elif metadata == "different":
                c.execute("UPDATE lineage_meta SET value='other-implementation' WHERE key='schema'")
            else:
                c.execute("DELETE FROM lineage_meta WHERE key='schema'")
        reopened = state_store.LineageDataStore(db)
        assert reopened.health()["schema"] == ("other-implementation" if metadata == "different" else "")
        assert any(item["resident_id"] == rid for item in reopened.residents.all_residents())
        listed = instruction(team,"SearchResidents",{"resident_ids":[rid]})
        assert listed.status == "committed", listed.error
        assert listed.result["residents"][0]["resident_id"] == rid
        detail = instruction(team,"GetResident",{"resident_id":rid})
        assert detail.status == "committed", detail.error
        settings = instruction(team,"GetResidentSettings",{"resident_id":rid})
        assert settings.status == "committed", settings.error
        assert settings.result["settings"]["reporting"] == {"initiative":"off","quality":"medium"}
        ordinary = instruction(team,"UpdateResidentSettings",{
            "resident_id":rid,"changes":{"allow_subagents":True}})
        assert ordinary.status == "committed", ordinary.error
        snapshot = instruction(team,"GetConversation",{"resident_id":rid,"input_message_id":mid})
        assert snapshot.status == "committed", snapshot.error
        assert snapshot.result["messages"][0]["message_id"] == mid
        # Optional configuration can be read without upgrading. An explicit
        # write still has its real local column requirement and never claims success.
        with pytest.raises(UndefinedColumn, match="reporting_json"):
            resident_reporting.update(rid, ACTOR, {"reporting":{"initiative":"high","quality":"high"}})
        assert instruction(team,"GetConversation",{"resident_id":rid,"input_message_id":mid}).status == "committed"
    finally:
        with db.transaction() as c:
            c.execute("ALTER TABLE resident_execution_configuration ADD COLUMN reporting_json JSONB")
            if metadata == "table_missing":
                c.execute("ALTER TABLE startup_saved_meta RENAME TO lineage_meta")
            c.execute("INSERT INTO lineage_meta(key,value) VALUES('schema',%s) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (state_store.LINEAGE_DATA_SCHEMA,))


def test_missing_core_table_fails_only_when_accessed(team):
    db = resident_catalog.get_catalog().state_store.database
    try:
        db.write(lambda c:c.execute("ALTER TABLE residents RENAME TO startup_saved_residents"))
        reopened = state_store.LineageDataStore(db)
        with pytest.raises(UndefinedTable, match="residents"):
            reopened.residents.all_residents()
    finally:
        db.write(lambda c:c.execute("ALTER TABLE startup_saved_residents RENAME TO residents"))


def test_runtime_ready_without_descriptive_agent_metadata(team):
    from management_portal.runtime_status import portal_runtime_status
    db = resident_conversation_stream.get_repository().agent_data.database
    try:
        db.write(lambda c:c.execute("ALTER TABLE agent_store_meta RENAME TO startup_saved_agent_meta"))
        status = portal_runtime_status()
        assert status["ready"] is True and status["ok"] is True
        assert status["agent_data"]["actual_schema"] == ""
        assert status["agent_data"]["compatible"] is False
    finally:
        db.write(lambda c:c.execute("ALTER TABLE startup_saved_agent_meta RENAME TO agent_store_meta"))


def test_health_keeps_actual_metadata_read_errors_visible(team):
    from management_portal.runtime_status import portal_runtime_status
    catalog = resident_catalog.get_catalog()
    agent = resident_conversation_stream.get_repository().agent_data.database
    try:
        catalog.state_store.database.write(lambda c:c.execute("ALTER TABLE lineage_meta RENAME COLUMN value TO broken_value"))
        with pytest.raises(UndefinedColumn, match="value"):
            catalog.state_store.health()
        agent.write(lambda c:c.execute("ALTER TABLE agent_store_meta RENAME COLUMN value TO broken_value"))
        status = portal_runtime_status()
        assert status["ok"] is False and status["ready"] is False
        assert status["state"] == "unavailable" and "value" in status["detail"]
    finally:
        catalog.state_store.database.write(lambda c:c.execute("ALTER TABLE lineage_meta RENAME COLUMN broken_value TO value"))
        agent.write(lambda c:c.execute("ALTER TABLE agent_store_meta RENAME COLUMN broken_value TO value"))
