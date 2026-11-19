"""Explicit directory setup for isolated lifecycle tests, never delete-time repair."""
from contextlib import contextmanager

import pytest

from management_common import directory_dependencies as dependencies


@pytest.fixture
def directory_owners(isolate_portal_databases):
    """Install the actual owners and migrate before the test's lifecycle actions."""
    return install_directory_owners()


def install_directory_owners():
    """Use again only when a subtest explicitly creates a new isolated database."""
    from management_common.directory_retention import RetentionStore
    from management_portal.resources.registry_store import RegistryStore
    from management_portal.resources.directory_identity_lifecycle import migrate_dependencies
    from management_portal.society_service.state_store import get_lineage_data_store

    RegistryStore()
    RetentionStore()
    state = get_lineage_data_store()
    with state.database.transaction() as connection:
        migrate_dependencies(connection)
    return state


@contextmanager
def resident_identity_write(connection, resident_id):
    """Construct a historical/unit identity and refresh only its owner atomically."""
    from management_portal.society_service.directory_owner import record_resident_directory

    dependencies.prepare_write(connection)
    yield
    row = connection.execute(
        'SELECT deleted_at FROM residents WHERE resident_id=%s', (resident_id,),
    ).fetchone()
    if row is None or row['deleted_at']:
        dependencies.replace(connection, 'identity', resident_id, [])
    else:
        member = connection.execute(
            "SELECT lineage_id FROM lineage_nodes WHERE node_id=%s AND node_type='resident'",
            (resident_id,),
        ).fetchone()
        if member is None:
            raise AssertionError('Live fixture Resident requires canonical Lineage membership')
        record_resident_directory(connection, resident_id, member['lineage_id'])
