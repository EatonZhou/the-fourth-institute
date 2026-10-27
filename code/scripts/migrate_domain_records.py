"""Atomic Paper, API route, Chatroom and absolute-time cutover.

Rehearsal restores a verified schema archive into disposable PostgreSQL.
Apply requires stopped Portal writers and a new backup directory. Runtime
owners have no compatibility path for the retired storage layouts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from management_common.format_tags import schema_matches

import psycopg
from psycopg import sql

from scripts import migrate_database_instants as times
from scripts import migrate_paper_records as paper
from scripts import migrate_model_api_records as apis
from scripts import migrate_chatroom_relations as chatrooms
from scripts.postgres_archive import _archive, _postgres_environment
from tests.cases.postgres_test_database import _binary, start_disposable_cluster


SCHEMAS = sorted({name.split('.')[0] for name in times.COLUMNS} | {'paper'})
MARKERS = (
    ('agent', 'agent_store_meta', 'schema', 'tfi.agent-data.v16', 'tfi.agent-data.v17'),
    ('lineage', 'lineage_meta', 'schema', 'tfi.lineage-data.v48', 'tfi.lineage-data.v49'),
    ('execution_runtime', 'execution_store_meta', 'schema_version', '18', '19'),
)


def migrate(dsn):
    with psycopg.connect(dsn) as connection:
        connection.execute("SET LOCAL jit=off")
        connection.execute("SET LOCAL timezone='UTC'")
        for owner, table, key, old, new in MARKERS:
            value = connection.execute(sql.SQL('SELECT value FROM {} WHERE key=%s FOR UPDATE').format(
                sql.Identifier(owner, table)), (key,)).fetchone()
            if value is None or (not schema_matches(value[0], old) if old.startswith("tfi.") else value[0] != old):
                raise ValueError(f'Unexpected source schema marker: {owner}.{table}')
        telepus_present = connection.execute("SELECT to_regclass('telepus.meta')").fetchone()[0] is not None
        if telepus_present:
            marker = connection.execute("SELECT value FROM telepus.meta WHERE key='schema_version' FOR UPDATE").fetchone()
            if marker != ('4',):
                raise ValueError('Unexpected source schema marker: telepus.meta')
        result = {'paper': paper.migrate(connection), 'model_apis': apis.migrate(connection),
                  'chatrooms': chatrooms.migrate(connection), 'times': times.migrate(connection)}
        for owner, table, key, old, new in MARKERS:
            connection.execute(sql.SQL('UPDATE {} SET value=%s WHERE key=%s').format(
                sql.Identifier(owner, table)), (new, key))
        if telepus_present:
            connection.execute("UPDATE telepus.meta SET value='5' WHERE key='schema_version'")
        return result


def source_url():
    from management_common.portal_database import portal_database_url
    if not os.getenv('PORTAL_DATABASE_URL'):
        from management_common.postgres_configuration import postgres_env_file
        for line in postgres_env_file().read_text().splitlines():
            key, sep, value = line.partition('=')
            if sep and key.strip() == 'PORTAL_DATABASE_URL':
                os.environ[key.strip()] = value.strip().strip('\"\'')
    return portal_database_url()


def cutover(*, rehearsal=False, backup_dir=None, source_archive=None):
    source = source_url()
    if rehearsal:
        if source_archive:
            archive = Path(source_archive).resolve(strict=True)
        else:
            folder = Path(backup_dir).resolve()
            folder.mkdir(parents=True, exist_ok=False, mode=0o700)
            archive = folder / 'source.dump'
            _archive(source, archive, SCHEMAS)
        target = start_disposable_cluster()
        environment = _postgres_environment(target)
        subprocess.run([str(_binary('pg_restore')), '--dbname', environment['PGDATABASE'],
                        '--jobs=2', '--no-owner', '--no-acl', '--exit-on-error', str(archive)],
                       check=True, env=environment, capture_output=True)
        return {'mode': 'isolated-rehearsal', 'archive': str(archive), **migrate(target)}
    from scripts.portal_source_layout import INSTANCE_LAYOUT
    for instance in ('dev', 'release'):
        units = INSTANCE_LAYOUT[instance]['component_units']
        for component in ('web', 'lineage', 'resource', 'execution-service', 'execution-worker'):
            unit = units[component]
            state = subprocess.run(['systemctl', '--user', 'show', unit, '--property=ActiveState', '--value'],
                                   check=True, capture_output=True, text=True).stdout.strip()
            if state not in {'inactive', 'failed'}:
                raise RuntimeError(f'Schema consumer must be stopped: {unit} ({state})')
    for unit in ('telepus-breakglass-worker.service', 'telepus-portal-refresh.service', 'telepus-telegram-router.service'):
        state = subprocess.run(['systemctl', '--user', 'show', unit, '--property=ActiveState', '--value'],
                               check=True, capture_output=True, text=True).stdout.strip()
        if state not in {'inactive', 'failed'}:
            raise RuntimeError(f'Schema consumer must be stopped: {unit} ({state})')
    folder = Path(backup_dir).resolve()
    folder.mkdir(parents=True, exist_ok=False, mode=0o700)
    archive = folder / 'domain-records.dump'
    _archive(source, archive, SCHEMAS)
    with archive.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    manifest = {'archive': str(archive), 'sha256': digest, 'committed': False,
                'created_at': datetime.now(timezone.utc).isoformat(), 'schemas': SCHEMAS}
    path = folder / 'manifest.json'
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    path.chmod(0o600)
    result = migrate(source)
    manifest.update(result, committed=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--rehearse', action='store_true')
    mode.add_argument('--apply', action='store_true')
    parser.add_argument('--backup-dir')
    parser.add_argument('--archive', help='Reuse a completed source archive for an isolated rehearsal')
    args = parser.parse_args()
    if args.apply and not args.backup_dir:
        parser.error('--apply requires a new --backup-dir')
    if args.rehearse and not (args.backup_dir or args.archive):
        parser.error('--rehearse requires --backup-dir or --archive')
    if args.apply and args.archive:
        parser.error('--archive is only accepted for isolated rehearsal')
    print(json.dumps(cutover(rehearsal=args.rehearse, backup_dir=args.backup_dir,
                            source_archive=args.archive), ensure_ascii=False))
