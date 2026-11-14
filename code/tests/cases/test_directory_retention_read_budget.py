"""Synthetic retention deletion budgets; no real owners or filesystem cleanup.

:design: decision_4aa13bc21e8e
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from management_common import directory_paths as paths
from management_common import directory_retention as retention
from management_common.instance_manifest import AREAS


def seed(store, connection, locations):
    connection.execute(
        f"INSERT INTO {store.table} (location,owner_id,category,created_at,last_access,bytes) "
        "SELECT location,'nested-other-owner','object_data',now(),now(),0 "
        "FROM unnest(%s::text[]) AS location", (locations,))


def remaining(store, connection):
    return {row['location'] for row in connection.execute(
        f'SELECT location FROM {store.table}').fetchall()}


class CountReads:
    def __init__(self, connection, table):
        self.connection, self.table = connection, table
        self.rows = 0

    def execute(self, sql, params=None):
        result = self.connection.execute(sql, params)
        if sql.startswith(f'SELECT location FROM {self.table}'):
            rows = result.fetchall()
            self.rows += len(rows)
            return SimpleNamespace(fetchall=lambda: rows)
        return result


@pytest.fixture
def location_map(tmp_path, monkeypatch):
    mapping = {area: tmp_path / area for area in AREAS}
    instance = SimpleNamespace(area=mapping.__getitem__)
    monkeypatch.setattr(paths, 'current_instance', lambda: instance)
    # Only present after the optimization. Keep the regression runnable on
    # the original product, with the very same synthetic instance mapping.
    monkeypatch.setattr(retention, 'current_instance', lambda: instance, raising=False)
    monkeypatch.setattr(paths, '_directory_case_sensitive', lambda _: True)
    return mapping


def test_unregister_tree_reads_only_candidates(location_map, monkeypatch, record_property):
    store = retention.RetentionStore()
    root = location_map['features'] / 'target'
    selected = ['features:target', 'features:target/child', str(root / 'absolute')]
    unrelated = [f'features:other-{i}/child' for i in range(300)]
    unrelated += ['features:target-sibling/child', 'papers:target/child']
    parsed, manifests, policies = [], [], []
    original_path, instance = retention.cleanup_path, paths.current_instance()

    def parse(location, **kwargs):
        parsed.append(location)
        return original_path(location, **kwargs)

    def manifest():
        manifests.append(True)
        return instance

    monkeypatch.setattr(retention, 'cleanup_path', parse)
    monkeypatch.setattr(paths, 'current_instance', manifest)
    monkeypatch.setattr(retention, 'current_instance', manifest, raising=False)
    monkeypatch.setattr(paths, '_directory_case_sensitive', lambda p: policies.append(p) or True)
    with store.database.transaction() as connection:
        seed(store, connection, selected + unrelated)
        counted = CountReads(connection, store.table)
        assert store.unregister_tree([root, root / 'child'], connection=counted) == 3
        assert remaining(store, connection) == set(unrelated)
    counts = dict(returned_rows=counted.rows, parsed_locations=len(parsed),
                  instance_reads=len(manifests), case_probes=len(policies),
                  distinct_case_parents=len(set(policies)))
    for key, value in counts.items():
        record_property(key, value)
    print(counts)
    assert counted.rows == 3
    assert len(parsed) == 3
    assert len(manifests) == 1
    assert len(policies) == len(set(policies))


@pytest.mark.parametrize('sensitive', [False, True])
def test_case_aliases_unicode_and_prefix_siblings(location_map, monkeypatch, sensitive):
    store = retention.RetentionStore()
    parent = location_map['features']
    root = parent / 'Straße'
    aliases = ['features:STRASSE/child', str(parent / 'STRASSE' / 'absolute')]
    exact = 'features:Straße/child'
    sibling = 'features:Straßex/child'
    monkeypatch.setattr(paths, '_directory_case_sensitive', lambda p: sensitive if p == parent else True)
    with store.database.transaction() as connection:
        seed(store, connection, [exact, sibling, *aliases])
        assert store.unregister_tree([root], connection=connection) == (1 if sensitive else 3)
        assert remaining(store, connection) == {sibling, *(aliases if sensitive else [])}


def test_mapping_is_fresh_per_decision_and_absolute_history_is_retained(location_map):
    store = retention.RetentionStore()
    old = location_map['features']
    new = old.parent / 'moved'
    location_map['features'] = new
    with store.database.transaction() as connection:
        seed(store, connection, ['features:owner', str(old / 'owner')])
        assert store.unregister_tree([old / 'owner'], connection=connection) == 1
        assert remaining(store, connection) == {'features:owner'}
        location_map['features'] = old
        assert store.unregister_tree([old / 'owner'], connection=connection) == 1


def test_outer_root_covers_multiple_areas_but_not_other_instance(location_map):
    store = retention.RetentionStore()
    outer = location_map['features'].parent
    other = str(outer.parent / 'other-instance' / 'data')
    with store.database.transaction() as connection:
        seed(store, connection, ['features:owner/x', 'papers:other-owner/x', other])
        # Pure membership only; caller's validate_removal still rejects deleting
        # an actual instance/area root before it reaches this store method.
        assert store.unregister_tree([outer], connection=connection) == 2
        assert remaining(store, connection) == {other}


def test_absolute_legacy_spelling_and_sql_metacharacters(location_map):
    store = retention.RetentionStore()
    root = location_map['features'] / 'owner_%'
    rows = [str(root) + '//child', str(root.parent) + '/./owner_%/dot',
            str(root) + '/trailing/', 'features:owner_%/child', 'features:owner_AX/child']
    with store.database.transaction() as connection:
        seed(store, connection, rows)
        assert store.unregister_tree([root], connection=connection) == 4
        assert remaining(store, connection) == {'features:owner_AX/child'}


def test_symlink_membership_remains_lexical(location_map):
    store = retention.RetentionStore()
    parent = location_map['features']
    parent.mkdir()
    target = parent.parent / 'outside'
    target.mkdir()
    alias = parent / 'alias'
    alias.symlink_to(target, target_is_directory=True)
    with store.database.transaction() as connection:
        seed(store, connection, ['features:alias/child', str(target / 'child')])
        assert store.unregister_tree([alias], connection=connection) == 1
        assert remaining(store, connection) == {str(target / 'child')}
    assert alias.is_symlink() and target.exists()


def test_unregister_rollback_and_decision_lock(location_map, monkeypatch):
    store = retention.RetentionStore()
    root = location_map['features'] / 'owner'
    with store.database.transaction() as connection:
        seed(store, connection, ['features:owner/child'])
    lock_calls = []
    original_lock = store.queue.lock

    def lock(connection):
        lock_calls.append(connection)
        return original_lock(connection)

    monkeypatch.setattr(store.queue, 'lock', lock)
    with pytest.raises(RuntimeError, match='abort'):
        with store.database.transaction() as connection:
            assert store.unregister_tree([root], connection=connection) == 1
            assert lock_calls == [connection]
            raise RuntimeError('abort')
    with store.database.read() as connection:
        assert remaining(store, connection) == {'features:owner/child'}


def test_unknown_case_policy_fails_for_matches_not_unrelated_rows(location_map, monkeypatch):
    store = retention.RetentionStore()
    root = location_map['features'] / 'owner'

    def unknown(_):
        raise OSError('unknown native case policy')

    monkeypatch.setattr(paths, '_directory_case_sensitive', unknown)
    with store.database.transaction() as connection:
        seed(store, connection, ['features:unrelated/x'])
        assert store.unregister_tree([root], connection=connection) == 0
        seed(store, connection, ['features:owner/x'])
        with pytest.raises(OSError, match='unknown native'):
            store.unregister_tree([root], connection=connection)
        assert 'features:owner/x' in remaining(store, connection)
