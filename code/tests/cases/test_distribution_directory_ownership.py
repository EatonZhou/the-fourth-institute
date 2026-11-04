"""The shipped SQL installs ownership facts without resource classification.

:module: delivery.services
:design: decision_4aa13bc21e8e
"""
from copy import deepcopy
from pathlib import Path
import subprocess

import psycopg
from psycopg.types.json import Jsonb
import pytest

from management_common.json_columns import json_value
from management_common.postgres_database import _portal_row_factory
from tests.cases.postgres_test_database import _binary, _postgres_environment
from tests.cases.test_distribution_application_flows import distribution_target_dsn


SEED = Path(__file__).resolve().parents[3] / 'instance/example_instance/seed/database/distribution.sql'


def _snapshot(connection):
    result = {}
    for table in ('lineage.lineages', 'lineage.residents', 'workspace.workspaces'):
        result[table] = [dict(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY 1')]
    result['registry'] = {
        (row['collection'], row['document_id']): json_value(row['document_json'])
        for row in connection.execute('SELECT collection,document_id,document_json '
                                      'FROM resource_registry.registry_documents')
    }
    return result


def _directories(collection, document):
    if collection == 'project_registry':
        return [item['directory'] for item in document.get('projects', []) if 'directory' in item]
    if collection in ('article', 'repo'):
        return document.get('roots' if collection == 'article' else 'checkouts', [])
    return []


@pytest.mark.parametrize('legacy', [False, True], ids=['fresh-install', 'legacy-upgrade'])
def test_public_seed_ownership_installs_and_upgrades_without_changing_records(
        distribution_target_dsn, monkeypatch, legacy):
    imported = subprocess.run(
        [str(_binary('psql')), distribution_target_dsn, '-X', '-v', 'ON_ERROR_STOP=1', '-f', str(SEED)],
        env=_postgres_environment(), capture_output=True, text=True)
    assert imported.returncode == 0, imported.stderr[-1500:]
    monkeypatch.delenv('PORTAL_DATABASE_NAMESPACE', raising=False)
    with psycopg.connect(distribution_target_dsn, row_factory=_portal_row_factory) as connection:
        baseline = _snapshot(connection)
        assert baseline['workspace.workspaces']
        assert all('origin' not in row for row in baseline['workspace.workspaces'])
        for table in ('lineage.lineages', 'lineage.residents'):
            assert baseline[table]
            assert all(set(json_value(row['directory_json'])) == {'owner_id'} for row in baseline[table])
        directories = [directory for (collection, _), document in baseline['registry'].items()
                       for directory in _directories(collection, document)]
        assert directories and all('origin' not in directory for directory in directories)
        if not legacy:
            return

        # Reconstruct both legacy classifications on the real shipped rows.
        # These fields used to affect deletion, but all other persisted facts
        # (including prose, message provenance, paths and owners) must survive.
        connection.execute("ALTER TABLE workspace.workspaces ADD COLUMN origin TEXT "
                           "NOT NULL DEFAULT 'created' CHECK(origin IN ('created','adopted'))")
        connection.execute("UPDATE workspace.workspaces SET origin='adopted' "
                           "WHERE workspace_id=(SELECT min(workspace_id) FROM workspace.workspaces)")
        for table, identity in (('lineages', 'lineage_id'), ('residents', 'resident_id')):
            connection.execute(f"UPDATE lineage.{table} SET directory_json=directory_json || "
                               "jsonb_build_object('origin', CASE WHEN " + identity +
                               f"=(SELECT min({identity}) FROM lineage.{table}) "
                               "THEN 'adopted' ELSE 'created' END)")
        changed_documents = 0
        for (collection, document_id), original in baseline['registry'].items():
            document = deepcopy(original)
            locations = _directories(collection, document)
            if not locations:
                continue
            for index, directory in enumerate(locations):
                directory['origin'] = 'created' if index % 2 else 'adopted'
            connection.execute('UPDATE resource_registry.registry_documents SET document_json=%s '
                               'WHERE collection=%s AND document_id=%s',
                               (Jsonb(document), collection, document_id))
            changed_documents += collection != 'project_registry'

        from scripts.migrate_directory_protocol import migrate_workspaces
        from management_portal.society_service.directory_owner import upgrade_ownership
        from management_portal.project_registry import upgrade_directory_ownership
        from management_portal.resources.directory_ownership_upgrade import upgrade_registry

        # Official owner hooks share one transaction; no runtime service or
        # filesystem migration is needed to retire these persisted fields.
        report = migrate_workspaces(connection, workspace_schema='workspace', lineage_schema='lineage')
        assert report['owned_workspaces'] == len(baseline['workspace.workspaces'])
        assert upgrade_ownership(connection) == {
            table: len(baseline['lineage.' + table]) for table in ('residents', 'lineages')}
        assert upgrade_directory_ownership(connection) > 0
        assert upgrade_registry(connection, 'resource_registry') == changed_documents
        assert _snapshot(connection) == baseline
        assert upgrade_ownership(connection) == {'residents': 0, 'lineages': 0}
        assert upgrade_directory_ownership(connection) == 0
        assert upgrade_registry(connection, 'resource_registry') == 0
        assert _snapshot(connection) == baseline
        connection.commit()
    with psycopg.connect(distribution_target_dsn, row_factory=_portal_row_factory) as connection:
        assert _snapshot(connection) == baseline
