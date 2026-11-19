"""The offline protocol entry owns its guarded writes and atomic index refresh."""
import pytest

from tests.cases.directory_fixture_support import directory_owners  # noqa: F401


@pytest.mark.usefixtures('directory_owners')
@pytest.mark.parametrize('fail_after_write', [False, True])
def test_protocol_refresh_and_failure_rollback_share_authority_transaction(tmp_path, monkeypatch, fail_after_write):
    from management_common import directory_dependencies as deps
    from management_portal.resources.workspace_store import get_workspace_store
    from scripts import migrate_directory_protocol as protocol
    store = get_workspace_store()
    root = tmp_path / 'workspace'
    root.mkdir()
    wid = 'workspace_000000009831'
    owner = 'resident_000000009832'
    store.ensure(wid, str(root), owner_id=wid)

    def legacy_writes(connection):
        # This real source guard rejects an unversioned caller. The script
        # must enter the protocol before any legacy owner SQL is performed.
        connection.execute(f'UPDATE {store.table} SET owner_id=%s WHERE workspace_id=%s', (owner, wid))
        if fail_after_write:
            raise RuntimeError('injected legacy migration failure')
        return {'source_rows': 1}

    monkeypatch.setattr(protocol, '_migrate_schema', legacy_writes)
    if fail_after_write:
        with pytest.raises(RuntimeError, match='injected legacy migration failure'), store.database.transaction() as connection:
            protocol.migrate_schema(connection)
    else:
        with store.database.transaction() as connection:
            report = protocol.migrate_schema(connection)
            assert report['directory_dependencies']['schema'] == 'identity-directory-dependencies.v1'
            assert connection.execute("SELECT current_setting('tfi.directory_dependency_migration',true)").fetchone()[0] in (None, '')
    with store.database.read() as connection:
        deps.require_ready(connection)
        expected = wid if fail_after_write else owner
        assert store.rows([wid], connection=connection)[wid]['owner_id'] == expected
        assert connection.execute(
            f"SELECT owner_id FROM {deps.schema()}.directory_dependencies WHERE kind='workspace' AND object_id=%s",
            (wid,),
        ).fetchone()[0] == expected
