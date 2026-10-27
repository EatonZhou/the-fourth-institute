"""Exercise local writer boundaries without running deployment or migrations."""
import importlib
import json
from contextlib import nullcontext

import pytest

from scripts import portal_dev_generation as generation


def test_generation_read_preserves_old_file(tmp_path):
    path = tmp_path / 'generations.json'
    old = {'schema': 'archive.portal-component-generations.v1', 'components': {'web': 'retained'}}
    path.write_text(json.dumps(old))
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    assert generation.read_component_build_ids(path) == old['components']
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


class ReachedMigration(Exception):
    pass


class MarkerConnection:
    """No database: stop at the first historical mutation or data migration."""
    def __init__(self, marker):
        self.marker = marker
        self.statements = []
        self.result = None

    def execute(self, query, params=None):
        text = str(query)
        self.statements.append(text)
        if any(token in text for token in ('ALTER TABLE', 'CREATE TABLE', 'DROP TABLE', 'UPDATE ', 'INSERT ', 'DELETE ', 'LOCK TABLE lineage_edges', 'LOCK TABLE resident_watchboards')):
            raise ReachedMigration(text)
        if 'SELECT value FROM lineage_meta' in text:
            self.result = {'value': self.marker}
        elif "to_regclass('resident_watchboards')" in text:
            self.result = {'name': None if self.marker.endswith('.v44') else 'resident_watchboards'}
        else:
            self.result = None
        return self

    def fetchone(self):
        return self.result

    def fetchall(self):
        return []


@pytest.mark.parametrize('name,entry', [
    ('application_targets_table', 'upgrade'), ('watchboard_storage', 'migrate'),
    ('wire_delivery_budgets', 'upgrade'), ('lineage_bindings', 'migrate'),
    ('lineage_automation_identity', 'upgrade'), ('family_resources', 'upgrade'),
])
@pytest.mark.parametrize('prefix', ['archive', 'tfi'])
def test_historical_marker_branches_preserve_same_version_and_reject_others(name, entry, prefix, monkeypatch):
    """历史迁移标记按完整标签校验，原值不被提前改写。"""
    module = importlib.import_module('scripts.migrate_' + name)
    from management_portal.society_service import state_store, wire_delivery_budget
    monkeypatch.setattr(state_store, 'lineage_data_schema_matches', lambda connection: True)
    monkeypatch.setattr(state_store, 'LINEAGE_DATA_SCHEMA', module.NEW_SCHEMA)
    monkeypatch.setattr(wire_delivery_budget, 'create_schema', lambda connection: (_ for _ in ()).throw(ReachedMigration()))
    function = getattr(module, entry)
    canonical = lambda value: prefix + '.' + value.split('.', 1)[1]
    current = MarkerConnection(canonical(module.NEW_SCHEMA))
    if prefix == 'tfi':
        function(current)
        # A valid predecessor reaches its original migration boundary.
        with pytest.raises(ReachedMigration):
            function(MarkerConnection(canonical(module.OLD_SCHEMA)))
    else:
        with pytest.raises(ValueError):
            function(current)
        predecessor = MarkerConnection(canonical(module.OLD_SCHEMA))
        with pytest.raises(ValueError):
            function(predecessor)
        assert predecessor.marker == canonical(module.OLD_SCHEMA)
    assert current.marker == canonical(module.NEW_SCHEMA)
    assert not any('UPDATE lineage_meta' in s for s in current.statements)
    for invalid in (canonical(module.OLD_SCHEMA) + '.0', 'unknown.lineage-data.v1', canonical(module.NEW_SCHEMA) + ' '):
        with pytest.raises(ValueError):
            function(MarkerConnection(invalid))


@pytest.mark.parametrize('prefix', ['archive', 'tfi'])
def test_domain_cutover_accepts_aliases_without_widening_numeric_versions(prefix, monkeypatch):
    """域切换拒绝退役文本标签，并独立校验数字版本。"""
    from scripts import migrate_domain_records as migration
    class Source:
        def __init__(self, wrong=False, namespace=prefix):
            self.statements = []
            self.markers = iter([(namespace + '.' + old.split('.', 1)[1] if old.startswith('tfi.') else ('99' if wrong else old),)
                                 for _, _, _, old, _ in migration.MARKERS])
        def execute(self, query, params=None):
            text = str(query)
            self.statements.append(text)
            if any(token in text for token in ('UPDATE ', 'INSERT ', 'DELETE ', 'ALTER TABLE', 'CREATE TABLE', 'DROP TABLE')):
                pytest.fail('source marker validation must finish before any mutation')
            if 'SELECT value FROM' in text:
                self.result = next(self.markers)
            else:
                self.result = (None,)
            return self
        def fetchone(self):
            return self.result
    monkeypatch.setattr(migration.paper, 'migrate', lambda conn: (_ for _ in ()).throw(ReachedMigration()))
    with monkeypatch.context() as patch:
        source = Source()
        patch.setattr(migration.psycopg, 'connect', lambda dsn: nullcontext(source))
        with pytest.raises(ReachedMigration if prefix == 'tfi' else ValueError):
            migration.migrate('unused')
        # Numeric validation is reached under canonical text markers, even in
        # the parameter which independently checks rejection of retired tags.
        patch.setattr(migration.psycopg, 'connect', lambda dsn: nullcontext(Source(wrong=True, namespace='tfi')))
        with pytest.raises(ValueError, match='Unexpected source schema marker'):
            migration.migrate('unused')


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
