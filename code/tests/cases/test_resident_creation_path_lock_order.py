"""Resident creation and a row-first directory writer must not deadlock.

:module: resident.identity
"""
import threading
from management_common.directory_cleanup_store import get_directory_cleanup_store
from tests.cases.test_resident_folder_creation_contract import (
    future_creation_store, _new_session_payload, resident_domain, resident_catalog,
    lineage_resident_nodes,
)


def test_existing_lineage_creation_locks_row_before_paths(future_creation_store, monkeypatch):
    parent = resident_domain.register_resident(_new_session_payload('Parent'), 'sample_admin')
    lid = parent['lineage_id']
    store = resident_catalog.get_catalog().state_store
    queue = get_directory_cleanup_store()
    ready, definition_requested = threading.Event(), threading.Event()
    errors = []
    original = store.lock_lineage_definition
    def observe(identity):
        if identity == lid:
            definition_requested.set()
        return original(identity)
    monkeypatch.setattr(store, 'lock_lineage_definition', observe)
    def writer():
        try:
            with store.database.transaction() as connection:
                connection.execute('SELECT 1 FROM lineages WHERE lineage_id=%s FOR NO KEY UPDATE', (lid,))
                ready.set()
                assert definition_requested.wait(5)
                queue.lock(connection)
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=writer)
    thread.start()
    try:
        assert ready.wait(5)
        result = lineage_resident_nodes.create_lineage_resident(lid, _new_session_payload('Child'), 'sample_admin')
        assert result.get('resident', result)['lineage_id'] == lid
    finally:
        definition_requested.set()
        thread.join(10)
    assert not thread.is_alive()
    assert not errors
