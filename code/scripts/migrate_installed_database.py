"""Explicit installed v65 database upgrade, with a full rollback preview by default.

Run using the installed code environment with PORTAL_INSTANCE_ROOT selected,
after stopping all instance services except PostgreSQL and verifying a backup.
No service control, file deletion or secret output is performed. An explicit
legacy Article preview can authorize verified copies into project areas; the
source files remain intact and database apply checks the reviewed preview.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psycopg import sql
from management_common.portal_database import _database_schema, portal_database_url
from management_common.postgres_database import PostgresConnection, _portal_row_factory
from scripts.instance_storage_migration import require_database_alone, require_offline


def preservation_facts(connection):
    """Hash protected business fields, without returning their contents."""
    facts = {}
    for schema, table, omitted in (
        ('lineage', 'residents', ['directory_json']),
        ('lineage', 'lineages', ['directory_json']),
        ('lineage', 'conversation_events', []),
        ('lineage', 'conversation_input_states', []),
        ('workspace', 'workspaces', ['origin', 'owner_id', 'operation_id']),
        ('execution_accounts', 'registrations', ['directory_origin', 'directory_owner']),
    ):
        qualified = sql.Identifier(_database_schema(schema), table)
        if connection.execute('SELECT to_regclass(%s)', (qualified.as_string(),)).fetchone()[0] is None:
            continue
        rows = connection.execute(sql.SQL(
            'SELECT (to_jsonb(t) - %s::text[])::text AS value FROM {} t ORDER BY value').format(qualified),
            (omitted,)).fetchall()
        digest = hashlib.sha256()
        for row in rows:
            value = row['value'].encode('utf-8')
            digest.update(len(value).to_bytes(8, 'big'))
            digest.update(value)
        facts[schema + '.' + table] = digest.hexdigest()
    return facts


def convert(connection, *, relocate_legacy_articles=False, expected_article_plan=None, require_article_targets=False):
    """One transaction for the ordered owner conversions and final validation."""
    from scripts.migrate_deleted_identities import migrate as identities
    from scripts.migrate_directory_protocol import migrate_schema as directories
    from scripts.migrate_family_chat_controls import convert as family
    from management_portal.society_service import state_store
    from management_portal.resources import registry_store
    from management_common.format_tags import schema_matches

    if state_store.LINEAGE_DATA_SCHEMA != 'tfi.lineage-data.v66':
        raise ValueError('This converter requires the v66 owner code')
    connection.execute("SET LOCAL lock_timeout='5s'")
    connection.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(_database_schema('lineage'))))
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':schema-init', 0))")
    version = connection.execute("SELECT value FROM lineage_meta WHERE key='schema' FOR UPDATE").fetchone()
    if not version or version['value'] not in ('tfi.lineage-data.v65', 'tfi.lineage-data.v66'):
        raise ValueError('Expected an installed LineageData v65 or v66 database')
    before = preservation_facts(connection)
    report = {'from_schema': version['value']}
    report['identities'] = identities(connection, _database_schema('resource_registry'), apply=True)
    if relocate_legacy_articles:
        from management_common.instance_manifest import current_instance
        from scripts import installed_article_migration as articles
        proposal = articles.plan(connection, current_instance())
        if expected_article_plan is not None and proposal != expected_article_plan:
            raise ValueError('Article migration differs from the reviewed preview')
        if require_article_targets:
            articles.verify_targets(proposal)
        report['legacy_articles'] = proposal
        report['relocated_resource_documents'] = articles.rewrite(connection, proposal)
    report['directories'] = directories(connection)
    report['family'] = family(connection)
    from scripts.directory_protocol_lifecycle_migration import validate_project_locations
    validate_project_locations(connection)
    # Family conversion validates the complete Lineage schema, not just its fields.
    schema = _database_schema('resource_registry')
    tables = {row[0] for row in connection.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema=%s AND table_type='BASE TABLE'",
        (schema,)).fetchall()}
    row = connection.execute(sql.SQL("SELECT value FROM {}.registry_meta WHERE key='schema'").format(
        sql.Identifier(schema))).fetchone()
    if tables != registry_store._TABLES or not row or not schema_matches(row[0], registry_store.SCHEMA_VERSION):
        raise ValueError('Converted resource registry does not match its owner schema')
    after = preservation_facts(connection)
    if before != after:
        raise ValueError('Protected instance business fields changed during conversion')
    report.update(to_schema=state_store.LINEAGE_DATA_SCHEMA, preservation=after)
    return report


def migrate(database, *, apply=False):
    from scripts.migrate_family_chat_controls import PreviewRollback
    result = {}
    try:
        with database.transaction() as connection:
            require_database_alone(connection)
            result = convert(connection)
            if not apply:
                raise PreviewRollback()
    except PreviewRollback:
        pass
    return {**result, 'committed': apply}


def main():
    import psycopg
    from management_common.instance_manifest import current_instance
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--relocate-legacy-articles', action='store_true')
    parser.add_argument('--expected-preview', type=Path)
    parser.add_argument('--prepare-article-copies', type=Path, metavar='PREVIEW_REPORT')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if args.prepare_article_copies and (args.apply or args.relocate_legacy_articles or args.expected_preview):
        parser.error('Copy preparation is separate from database preview/apply')
    if args.expected_preview and not (args.apply and args.relocate_legacy_articles):
        parser.error('--expected-preview requires --apply --relocate-legacy-articles')
    if args.apply and args.relocate_legacy_articles and not args.expected_preview:
        parser.error('Article relocation apply requires --expected-preview')
    expected = None
    if args.expected_preview or args.prepare_article_copies:
        expected = json.loads((args.expected_preview or args.prepare_article_copies).read_text(encoding='utf-8'))
        if (expected.get('committed') is not False or expected.get('to_schema') != 'tfi.lineage-data.v66'
                or not isinstance(expected.get('legacy_articles'), dict)):
            raise ValueError('A successful uncommitted Article migration preview is required')
    require_offline(current_instance(), running=('postgres',))
    with psycopg.connect(portal_database_url(), row_factory=_portal_row_factory) as raw:
        connection = PostgresConnection(raw)
        require_database_alone(connection)
        if expected and preservation_facts(connection) != expected['preservation']:
            raise ValueError('Protected business records changed after preview')
        if args.prepare_article_copies:
            from scripts import installed_article_migration as articles
            proposal = articles.plan(connection, current_instance())
            if proposal != expected['legacy_articles']:
                raise ValueError('Article copy plan differs from the reviewed preview')
            articles.prepare_copies(proposal)
            raw.rollback()
            args.report.write_text(json.dumps({'committed': False, 'article_copies_verified': True,
                'sources_retained': True, 'legacy_articles': proposal}, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({'article_copies_verified': True, 'database_changed': False, 'sources_retained': True}))
            return
        result = convert(connection, relocate_legacy_articles=args.relocate_legacy_articles,
                         expected_article_plan=expected['legacy_articles'] if expected else None,
                         require_article_targets=args.apply and args.relocate_legacy_articles)
        if not args.apply:
            raw.rollback()
    report = {**result, 'committed': args.apply}
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'committed': args.apply, 'schema': report['to_schema'], 'preservation': 'passed'}))


if __name__ == '__main__':
    main()
