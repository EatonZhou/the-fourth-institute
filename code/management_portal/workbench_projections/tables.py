"""Workbench identity, versioned page definitions and business relationships.

``apps.actor`` is the workbench's creator; who may change a workbench is the
permission system's answer, not a stored list."""
from __future__ import annotations
import json
from management_common.database_time import instant, iso_time, decode_times

DDL = """
CREATE TABLE IF NOT EXISTS apps (
 id TEXT PRIMARY KEY, actor TEXT NOT NULL, builtin TEXT NOT NULL,
 revision BIGINT NOT NULL, published BIGINT, created_at TIMESTAMPTZ NOT NULL,
 updated_at TIMESTAMPTZ NOT NULL, navigation TEXT NOT NULL,
 UNIQUE(actor,builtin)
);
CREATE TABLE IF NOT EXISTS versions (
 app_id TEXT NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
 revision INTEGER NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL,
 html TEXT NOT NULL, path TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
 PRIMARY KEY(app_id,revision)
);
CREATE TABLE IF NOT EXISTS page_packages (
 app_id TEXT NOT NULL, revision INTEGER NOT NULL,
 manifest TEXT NOT NULL, panels TEXT NOT NULL,
 PRIMARY KEY(app_id,revision),
 FOREIGN KEY(app_id,revision) REFERENCES versions ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS application_packages (
 app_id TEXT NOT NULL, revision INTEGER NOT NULL,
 application TEXT NOT NULL, build TEXT NOT NULL,
 PRIMARY KEY(app_id,revision),
 FOREIGN KEY(app_id,revision) REFERENCES versions ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS projects (
 app_id TEXT NOT NULL, revision INTEGER NOT NULL, project_id TEXT NOT NULL,
 PRIMARY KEY(app_id,revision,project_id),
 FOREIGN KEY(app_id,revision) REFERENCES versions ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS bindings (
 app_id TEXT NOT NULL, revision INTEGER NOT NULL, alias TEXT NOT NULL,
 lineage_id TEXT NOT NULL, PRIMARY KEY(app_id,revision,alias),
 FOREIGN KEY(app_id,revision) REFERENCES versions ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS binding_targets (
 app_id TEXT NOT NULL, revision INTEGER NOT NULL, alias TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('residents','events')), name TEXT NOT NULL,
 target_id TEXT NOT NULL, PRIMARY KEY(app_id,revision,alias,kind,name),
 FOREIGN KEY(app_id,revision,alias) REFERENCES bindings ON DELETE CASCADE
);
"""


def save_version(connection, app_id, revision, value):
    allowed = {'workbench_id','builtin','title','description','html','path','updated_at',
               'created_at','created_by','project_ids','navigation','bindings','manifest','panels',
               'application','application_build'}
    if set(value) - allowed:
        raise ValueError('Unknown workbench definition fields: ' + ','.join(sorted(set(value) - allowed)))
    connection.execute('INSERT INTO versions VALUES (%s,%s,%s,%s,%s,%s,%s)',
                       (app_id, revision, *(value.get(k, '') for k in ('title','description','html','path')),
                        instant(value['updated_at'])))
    if value.get('manifest') is not None:
        connection.execute('INSERT INTO page_packages VALUES (%s,%s,%s,%s)',
                           (app_id, revision, json.dumps(value['manifest'], ensure_ascii=False),
                            json.dumps(value['panels'], ensure_ascii=False)))
    if value.get('application') is not None:
        connection.execute('INSERT INTO application_packages VALUES (%s,%s,%s,%s)',
                           (app_id, revision, json.dumps(value['application'], ensure_ascii=False),
                            json.dumps(value['application_build'], ensure_ascii=False)))
    for project in dict.fromkeys(value.get('project_ids', [])):
        connection.execute('INSERT INTO projects VALUES (%s,%s,%s)', (app_id, revision, project))
    for alias, binding in value.get('bindings', {}).items():
        if set(binding) - {'lineage_id','residents','events'}:
            raise ValueError('Unknown workbench binding fields')
        connection.execute('INSERT INTO bindings VALUES (%s,%s,%s,%s)', (app_id,revision,alias,binding['lineage_id']))
        for kind in ('residents','events'):
            for name, target in binding.get(kind, {}).items():
                connection.execute('INSERT INTO binding_targets VALUES (%s,%s,%s,%s,%s,%s)', (app_id,revision,alias,kind,name,target))


def manifest(connection, row, revision=None):
    app_id = row['id']
    revision = row['revision'] if revision is None else revision
    page = connection.execute('SELECT title,description,html,path,updated_at FROM versions WHERE app_id=%s AND revision=%s', (app_id,revision)).fetchone()
    if page is None:
        raise ValueError('Workbench page version is missing')
    bindings = {r['alias']: {'lineage_id': r['lineage_id'], 'residents': {}, 'events': {}}
                for r in connection.execute('SELECT alias,lineage_id FROM bindings WHERE app_id=%s AND revision=%s ORDER BY alias', (app_id,revision))}
    for target in connection.execute('SELECT alias,kind,name,target_id FROM binding_targets WHERE app_id=%s AND revision=%s', (app_id,revision)):
        bindings[target['alias']][target['kind']][target['name']] = target['target_id']
    package = connection.execute('SELECT manifest,panels FROM page_packages WHERE app_id=%s AND revision=%s', (app_id, revision)).fetchone()
    application = connection.execute('SELECT application,build FROM application_packages WHERE app_id=%s AND revision=%s', (app_id, revision)).fetchone()
    return {**decode_times(page, ('updated_at',)), **({key: json.loads(package[key]) for key in ('manifest', 'panels')} if package else {}),
            **({'application': json.loads(application['application']), 'application_build': json.loads(application['build'])} if application else {}),
            'workbench_id': app_id, 'builtin': '' if row['builtin'] == app_id else row['builtin'],
            'created_at': iso_time(row['created_at']), 'created_by': row['actor'], 'navigation': row['navigation'],
            'project_ids': [r[0] for r in connection.execute('SELECT project_id FROM projects WHERE app_id=%s AND revision=%s ORDER BY project_id', (app_id,revision))],
            'bindings': bindings}
