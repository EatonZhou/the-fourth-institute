"""Additive dispatch intent migration preserves accepted inputs and is atomic."""
import pytest

from management_portal.society_service import resident_conversation_stream, resident_input_queue_domain
from management_portal.society_service.state_store import get_lineage_data_store
from scripts.migrate_family_task_dispatches import OLD_SCHEMA, NEW_SCHEMA, upgrade
from tests.cases.test_family_tasks import charters, configured_family, future_creation_store, lineage  # noqa: F401


def test_dispatch_intent_migration_preserves_messages_and_replays(configured_family):
    _, block = configured_family
    target = block["family"]["steward"]
    repository = resident_conversation_stream.get_repository()
    message = resident_input_queue_domain.build_message("user", "original", block["family"]["facade"])
    repository.accept_user_message(target, message)
    before = repository.message(target, message["message_id"])
    database = get_lineage_data_store().database

    def old_schema(connection):
        connection.execute("DROP TABLE family_task_dispatches")
        connection.execute("UPDATE lineage_meta SET value=%s WHERE key='schema'", (OLD_SCHEMA,))
    database.write(old_schema)
    assert database.write(upgrade) == {"previous_schema": OLD_SCHEMA, "schema": NEW_SCHEMA}
    assert database.write(upgrade) == {"previous_schema": NEW_SCHEMA, "schema": NEW_SCHEMA}
    assert repository.message(target, message["message_id"]) == before


def test_dispatch_intent_migration_rolls_back_on_incomplete_schema(configured_family):
    database = get_lineage_data_store().database

    def incompatible(connection):
        connection.execute("DROP TABLE family_task_dispatches")
        connection.execute("UPDATE lineage_meta SET value=%s WHERE key='schema'", (OLD_SCHEMA,))
        connection.execute("CREATE TABLE unexpected_dispatch_test (id TEXT)")
    database.write(incompatible)
    with pytest.raises(ValueError, match="current contract"):
        database.write(upgrade)
    with database.read() as connection:
        assert connection.execute("SELECT value FROM lineage_meta WHERE key='schema'").fetchone()["value"] == OLD_SCHEMA
        assert connection.execute("SELECT to_regclass('family_task_dispatches') AS present").fetchone()["present"] is None
