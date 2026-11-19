"""Figure commit promotion maintains directory dependencies in the same TX.

:design: decision_4aa13bc21e8e
"""
import pytest

from management_common import directory_dependencies as deps
from management_common.directory_paths import cleanup_location
from management_common.directory_retention import RetentionStore
from management_portal import paper_figures
from management_portal.resources.directory_identity_lifecycle import migrate_dependencies
from management_portal.resources.registry_store import get_registry_store


def prepared_generation(tmp_path):
    store = RetentionStore()
    get_registry_store()._ensure()
    with store.database.transaction() as connection:
        migrate_dependencies(connection, identities=[])
    paper = {'id': 'paper_000000000083', '_store_root': str(tmp_path)}
    generation = tmp_path / 'figures' / 'extracted' / 'run-current'
    generation.mkdir(parents=True)
    store.declare(generation, category='runtime_tmp',
                  owner_id=paper_figures._figure_artifact_owner(paper['id']))
    return store, paper, generation


def snapshot(store, generation):
    location = cleanup_location(generation)
    with store.database.read() as connection:
        return (
            dict(connection.execute(f'SELECT * FROM {store.table} WHERE location=%s', (location,)).fetchone()),
            [dict(row) for row in connection.execute(
                f'SELECT * FROM {deps.schema()}.directory_dependencies '
                "WHERE kind='artifact' AND object_id=%s ORDER BY slot", (location,)).fetchall()],
            [dict(row) for row in connection.execute(
                f'SELECT * FROM {deps.schema()}.directory_dependency_dirty').fetchall()],
        )


@pytest.mark.parametrize('with_output', [False, True])
def test_figure_commit_promotion_keeps_dependency_index_ready(tmp_path, with_output):
    store, paper, generation = prepared_generation(tmp_path)
    if with_output:
        (generation / 'figure.png').write_bytes(b'figure output')
    paper_figures._prepare_figure_commit(store, paper, generation, [])
    row, entries, dirty = snapshot(store, generation)
    assert row['category'] == 'object_data'
    assert entries and entries[0]['owner_id'] == row['owner_id']
    assert entries[0]['location'] == cleanup_location(generation)
    assert entries[0]['disposition'] == 'owned'
    assert dirty == []
    with store.database.read() as connection:
        deps.require_ready(connection)


def test_figure_commit_refresh_failure_rolls_back_promotion(tmp_path, monkeypatch):
    store, paper, generation = prepared_generation(tmp_path)
    before = snapshot(store, generation)
    from management_common import directory_retention
    refresh = directory_retention.refresh_artifact

    def fail_after_refresh(*args, **kwargs):
        refresh(*args, **kwargs)
        raise RuntimeError('injected after dependency refresh')

    monkeypatch.setattr(directory_retention, 'refresh_artifact', fail_after_refresh)
    with pytest.raises(RuntimeError, match='injected after dependency refresh'):
        paper_figures._prepare_figure_commit(store, paper, generation, [])
    assert snapshot(store, generation) == before

