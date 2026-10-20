"""Article compiler settings and latest build result, separate from registration."""
from __future__ import annotations

import json
import os
from functools import lru_cache

from management_common.domain_database import DomainDatabase
from management_common.database_time import instant, iso_time


SETTINGS_SCHEMA = 'tfi.article-build-settings.v3'
SETTING_FIELDS = ('compiler', 'primary_file', 'use_project_latexmkrc', 'shell_escape',
                  'auto_compile', 'compile_mode', 'syntax_check', 'error_handling', 'updated_at')
DDL = """
CREATE TABLE IF NOT EXISTS build_settings (
    article_id TEXT PRIMARY KEY, compiler TEXT NOT NULL, primary_file TEXT NOT NULL,
    use_project_latexmkrc BOOLEAN NOT NULL, shell_escape BOOLEAN NOT NULL,
    auto_compile BOOLEAN NOT NULL, compile_mode TEXT NOT NULL, syntax_check BOOLEAN NOT NULL,
    error_handling TEXT NOT NULL, updated_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS build_reports (
    article_id TEXT PRIMARY KEY, status TEXT NOT NULL, log TEXT NOT NULL,
    report_json JSONB NOT NULL
);
"""


@lru_cache(maxsize=8)
def _database(dsn, namespace):
    return DomainDatabase('article', 'build', DDL)


def database():
    return _database(os.getenv('PORTAL_DATABASE_URL'), os.getenv('PORTAL_DATABASE_NAMESPACE'))


def settings(article_id):
    with database().read() as connection:
        row = connection.execute('SELECT * FROM build_settings WHERE article_id=%s', (article_id,)).fetchone()
        return {'id': article_id, 'schema': SETTINGS_SCHEMA,
                **{field: iso_time(row[field]) if field == 'updated_at' else row[field]
                   for field in SETTING_FIELDS}} if row else None


def save_settings(article_id, value):
    with database().transaction() as connection:
        connection.execute('INSERT INTO build_settings VALUES (' + ','.join(['%s'] * 10) +
                           ') ON CONFLICT(article_id) DO UPDATE SET ' + ','.join(field + '=excluded.' + field for field in SETTING_FIELDS),
                           (article_id, *(instant(value[field]) if field == 'updated_at' else value[field]
                                          for field in SETTING_FIELDS)))


def report(article_id):
    with database().read() as connection:
        row = connection.execute('SELECT status,log,report_json FROM build_reports WHERE article_id=%s', (article_id,)).fetchone()
        return {'id': article_id, 'schema': 'tfi.article-build.v1', **row[2], 'status': row[0], 'log': row[1]} if row else None


def save_report(article_id, value):
    details = {key: item for key, item in value.items() if key not in {'id', 'schema', 'status', 'log', 'pdf_available', 'pdf_size'}}
    with database().transaction() as connection:
        connection.execute('''INSERT INTO build_reports VALUES (%s,%s,%s,%s::jsonb)
            ON CONFLICT(article_id) DO UPDATE SET status=excluded.status,log=excluded.log,report_json=excluded.report_json''',
            (article_id, value.get('status', 'idle'), value.get('log', ''), json.dumps(details)))
