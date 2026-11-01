"""Convert stored file locations after the offline HOME/staging/Lineage moves.

Only location fields are converted. Prose, message bodies, prompts, scripts and
opaque payloads are not rewritten. Dry runs are read-only, including schema
checks. Apply requires PostgreSQL alone and uses a single transaction.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from management_common.instance_manifest import AREAS
from management_common.secret_environment import secret_environment
from management_common.location_records import PATH_KEYS, PROSE_KEYS, path_key
from scripts.instance_storage_migration import (
    PrefixMap, Report, read_moves, parse_common, require_offline,
    require_protected, require_database_alone, write_report, instance_units,
)
from scripts.migrate_instance_staging import read_instance

SKIP_TABLES = {('agent', 'payloads'), ('agent', 'segments'), ('agent', 'chat_message_positions'),
               ('lineage', 'conversation_events'), ('lineage', 'family_relays'),
               ('resident_context', 'work_summaries'), ('resident_context', 'work_summary_layers')}


class Conversion:
    def __init__(self, instance, mapping):
        self.instance, self.mapping = instance, mapping
        self.counts = Counter()
        self.missing, self.unclassified = {}, {}

    def location(self, value, field, deleted=False):
        if not value.startswith('/') or '\n' in value or '\0' in value:
            return value
        mapped = value
        seen = set()
        while mapped not in seen:
            seen.add(mapped)
            updated = self.mapping.map(mapped)
            if updated is None or updated == mapped:
                break
            mapped = updated
        path = Path(mapped).resolve()
        try:
            reference = self.instance.reference(path)
        except ValueError:
            # Fixed runtime/secrets paths have no area encoding. Report their
            # owners for explicit handling instead of inventing an area.
            reference = str(path)
        inside = reference.partition(':')[0] in AREAS
        if deleted and not inside:
            return value
        if not path.exists() and not deleted:
            self.missing.setdefault(field, set()).add(value)
            # A recorded move establishes the new location even if the leaf has expired.
            if mapped == value:
                return value
        if path.is_relative_to(self.instance.root) and not inside and not deleted:
            self.unclassified.setdefault(field, set()).add(str(path))
        if reference != value:
            self.counts[field] += 1
        return reference

    def convert(self, value, field, key='', deleted=False):
        if isinstance(value, dict):
            deleted = deleted or bool(value.get('deleted_at')) or value.get('status') == 'deleted'
            return {k: v if k in PROSE_KEYS else self.convert(v, field + '.' + k, k, deleted)
                    for k, v in value.items()}
        if isinstance(value, list):
            return [self.convert(v, field + '.*', key, deleted) for v in value]
        if isinstance(value, str) and path_key(key):
            return self.location(value, field, deleted)
        return value


def assert_unused_index(instance):
    """Inspect definitions and live process environments, without exposing secrets."""
    for unit in instance_units(instance):
        result = subprocess.run(['systemctl', '--user', 'show', unit, '-p', 'Environment'],
                                capture_output=True, text=True, check=True)
        if re.search(r'PORTAL_RESOURCE_DATABASE_SCHEMA=["\x27]?resource_dev\b', result.stdout):
            raise RuntimeError('resource_dev is still selected by ' + unit)
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            values = (process / 'environ').read_bytes().split(b'\0')
        except (OSError, PermissionError):
            continue
        if b'PORTAL_RESOURCE_DATABASE_SCHEMA=resource_dev' in values:
            raise RuntimeError('resource_dev is selected by process ' + process.name)


def convert_database(conn, instance, mapping, *, apply):
    from psycopg import sql
    converter = Conversion(instance, mapping)
    columns = conn.execute("""SELECT n.nspname,c.relname,a.attname,t.typname
        FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_type t ON t.oid=a.atttypid
        WHERE a.attnum>0 AND NOT a.attisdropped AND c.relkind='r'
        AND n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema'
        AND t.typname IN ('text','varchar','json','jsonb') ORDER BY 1,2,3""").fetchall()
    by_table = {}
    for schema, table, column, kind in columns:
        if schema == 'resource_dev' or (schema, table) in SKIP_TABLES or column in PROSE_KEYS:
            continue
        if kind in ('json', 'jsonb') or column.endswith('_json') or path_key(column) or column in {
                'config', 'snapshot', 'result', 'receipt', 'attributes', 'metadata', 'outputs', 'value', 'payload'}:
            by_table.setdefault((schema, table), []).append((column, kind))
    if apply:
        from management_portal.resources.workspace_store import LOCATION_CHECK
        conn.execute('ALTER TABLE workspace.workspaces DROP CONSTRAINT IF EXISTS workspaces_absolute_path_check')
        conn.execute('ALTER TABLE workspace.workspaces DROP CONSTRAINT IF EXISTS workspaces_location_check')
        conn.execute('ALTER TABLE workspace.workspaces ADD CONSTRAINT workspaces_location_check CHECK (' + LOCATION_CHECK + ')')
    tables = {}
    for (schema, table), fields in by_table.items():
        table_id = sql.Identifier(schema, table)
        deleted_col = any(s == schema and t == table and col == 'deleted_at' for s, t, col, _ in columns)
        # deleted_at is a timestamp and therefore absent from the text-column list.
        deleted_col = bool(conn.execute('SELECT 1 FROM information_schema.columns WHERE table_schema=%s AND table_name=%s AND column_name=%s',
                                       (schema, table, 'deleted_at')).fetchone())
        where = sql.SQL(' OR ').join(sql.SQL('{}::text LIKE %s').format(sql.Identifier(name)) for name, _ in fields)
        query = sql.SQL('SELECT ctid::text,{},{} FROM {} WHERE {}').format(
            sql.SQL('deleted_at IS NOT NULL') if deleted_col else sql.SQL('false'),
            sql.SQL(',').join(sql.Identifier(name) for name, _ in fields), table_id, where)
        rows = conn.execute(query, ['%/%'] * len(fields)).fetchall()
        changed_rows = 0
        for row in rows:
            changes, values = [], []
            for (name, kind), raw in zip(fields, row[2:]):
                if raw is None:
                    continue
                parsed = raw
                json_text = kind not in ('json', 'jsonb') and isinstance(raw, str) and raw[:1] in '{['
                if json_text:
                    try:
                        parsed = json.loads(raw)
                    except ValueError:
                        continue
                field = f'{schema}.{table}.{name}'
                converted = converter.convert(parsed, field, name, row[1])
                if converted == parsed:
                    continue
                changes.append(sql.SQL('{}={}').format(sql.Identifier(name), sql.SQL('%s::' + kind)))
                values.append(json.dumps(converted, ensure_ascii=False) if json_text or kind in ('json', 'jsonb') else converted)
            if changes:
                changed_rows += 1
                if apply:
                    conn.execute(sql.SQL('UPDATE {} SET {} WHERE ctid=%s::tid').format(table_id, sql.SQL(',').join(changes)),
                                 [*values, row[0]])
        tables[f'{schema}.{table}'] = changed_rows
    if apply:
        conn.execute('DROP SCHEMA IF EXISTS resource_dev CASCADE')
    return {'tables': tables, 'fields': dict(converter.counts),
            'missing': {k: sorted(v) for k, v in converter.missing.items()},
            'unclassified': {k: sorted(v) for k, v in converter.unclassified.items()},
            'resource_dev': 'removed' if apply else 'planned'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parse_common(parser)
    parser.add_argument('--moves', type=Path, action='append', default=[], required=True)
    args = parser.parse_args(argv)
    instance, _, _ = read_instance(args.root)
    os.environ['PORTAL_INSTANCE_ROOT'] = str(instance.root)
    import psycopg
    mapping = PrefixMap.from_moves(read_moves(args.moves))
    report = Report('migrate_instance_database_paths', str(instance.root), args.apply)
    if args.apply:
        require_protected(args.protected_pid)
        require_offline(instance, running=('postgres',))
        assert_unused_index(instance)
    with psycopg.connect(secret_environment(instance, 'postgres.env')['PORTAL_DATABASE_URL']) as conn:
        conn.read_only = not args.apply
        conn.execute("SET lock_timeout='5s'")
        conn.execute("SET statement_timeout='300s'")
        if args.apply:
            require_database_alone(conn)
        report.sections = convert_database(conn, instance, mapping, apply=args.apply)
        write_report(args.report, report)
    print(json.dumps({'mode': 'apply' if args.apply else 'dry-run',
                      'converted': sum(report.sections['fields'].values()),
                      'unclassified_fields': len(report.sections['unclassified']),
                      'missing_fields': len(report.sections['missing'])}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
