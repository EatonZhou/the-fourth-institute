"""Real PostgreSQL retained-data migration and failure atomicity."""
import uuid
import pytest

from management_common.portal_database import portal_database
from management_portal.society_service.state_store import create_lineage_data_schema
from scripts import tfi_release_schema as migration


@pytest.fixture
def database():
    db = portal_database(schema="mig_" + uuid.uuid4().hex[:12])
    db.write(create_lineage_data_schema)
    yield db


def origin(c, number):
    if number < 65:
        c.execute("DROP TABLE family_chat_preferences,family_chat_inputs")
    if number < 64:
        c.execute("ALTER TABLE families DROP COLUMN auto_migrate_priority")
    if number < 63:
        # Historical b00/v60 storage is TEXT, not the later JSONB convention.
        c.execute("ALTER TABLE lineages ADD COLUMN run_as_principal_json TEXT")
    if number < 62:
        c.execute("ALTER TABLE families DROP COLUMN auto_migrate")
    if number < 61:
        c.execute("DROP TABLE family_task_dispatches")
    c.execute("UPDATE lineage_meta SET value=%s WHERE key='schema'", (f"tfi.lineage-data.v{number}",))
    c.execute("INSERT INTO lineages(lineage_id,title) VALUES ('kept','local title')")
    if number < 63:
        c.execute("UPDATE lineages SET run_as_principal_json='{}'")


@pytest.mark.parametrize("number", range(60, 66))
def test_chain_preserves_rows_and_archives_retired_configuration(database, number):
    database.write(lambda c: origin(c, number))
    result = database.write(migration.upgrade)
    assert result["previous_schema"] == f"tfi.lineage-data.v{number}"
    with database.read() as c:
        assert c.execute("SELECT title FROM lineages WHERE lineage_id='kept'").fetchone()[0] == 'local title'
        if number < 63:
            assert c.execute("SELECT value FROM tfi_migration_archive.lineage_run_as "
                             "WHERE source_schema=current_schema() AND lineage_id='kept'").fetchone()[0] == '{}'
    assert database.write(migration.upgrade)["applied"] is False


def test_unknown_structure_is_refused_without_mutation(database):
    database.write(lambda c: origin(c, 60))
    database.write(lambda c: c.execute("ALTER TABLE families ADD COLUMN unexplained TEXT"))
    with pytest.raises(ValueError, match="structure"):
        database.write(migration.upgrade)
    with database.read() as c:
        assert c.execute("SELECT value FROM lineage_meta WHERE key='schema'").fetchone()[0].endswith('v60')


def test_late_failure_rolls_back_schema_and_business_rows(database, monkeypatch):
    database.write(lambda c: origin(c, 60))
    from management_portal.society_service import family_chat_storage
    def fail(c):
        raise RuntimeError('injected migration failure')
    monkeypatch.setattr(family_chat_storage, 'create_schema', fail)
    with pytest.raises(RuntimeError, match='injected'):
        database.write(migration.upgrade)
    with database.read() as c:
        assert migration.inspect(c)['previous_schema'].endswith('v60')
        assert c.execute("SELECT title,run_as_principal_json FROM lineages").fetchone()[0] == 'local title'
