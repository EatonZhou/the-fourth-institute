"""The published packaging surface, independent of ordinary Workbench ACLs."""
from copy import deepcopy
import json

from .store import database


def context(actor, app_id='', *, original_build=False):
    if not actor:
        raise PermissionError('Login required')
    from management_portal.workbench_projections.store import Store, ProjectionError
    from management_portal.workbench_projections import tables
    owner = Store()
    with owner.connect() as connection:
        row = connection.execute("SELECT apps.* FROM apps JOIN native_entrypoints n ON n.app_id=apps.id WHERE n.name='development'").fetchone()
        if row is None or not row['published'] or (app_id and app_id != row['id']):
            raise ProjectionError('release.page', '发布工作台尚未就绪', 404)
        app = deepcopy(tables.manifest(connection, row, row['published']))
        app.update(revision=row['published'], published_revision=row['published'])
    manifest = app.get('manifest') or {}
    panel = next((p for p in manifest.get('sidebar', []) if p['id'] == 'packaging'), None)
    if panel is None:
        raise ProjectionError('release.page', '工作台缺少发布面板', 404)
    panel.pop('chat', None)
    manifest['sidebar'] = [panel]
    manifest.pop('chat', None)
    manifest['context'] = []
    app['bindings'] = {}
    app['html'] = ''
    app['panels'] = {k:v for k,v in app.get('panels', {}).items() if k == 'packaging'}
    app['application'] = {**(app.get('application') or {}), 'entries': {'packaging': app.get('application', {}).get('entries', {}).get('packaging')}}
    build = app.get('application_build') or {}
    entry = build.get('entries', {}).get('packaging')
    if not original_build:
        app['application_build'] = {**build, 'entries': {'packaging': entry},
            'files': {k:v for k,v in build.get('files', {}).items() if k == entry or not k.endswith('.html')}}
    app.update(release_scope='packaging', allows={'UpdateResource': False}, url='/development?view=packaging')
    return app


def state(actor, app_id, value=None):
    app = context(actor, app_id)
    from psycopg.types.json import Jsonb
    with database().transaction('preferences:' + actor) as connection:
        if value is not None:
            if not isinstance(value, dict) or len(json.dumps(value, ensure_ascii=False).encode()) > 480_000:
                raise ValueError('发布页面偏好必须是小于480KB的JSON对象')
            connection.execute('INSERT INTO page_preferences(actor,value) VALUES(%s,%s) ON CONFLICT(actor) DO UPDATE SET value=excluded.value', (actor, Jsonb(value)))
            return value
        row = connection.execute('SELECT value FROM page_preferences WHERE actor=%s', (actor,)).fetchone()
        if row:
            return row['value']
        # Recover only this actor's packaging panel after validating the published
        # entrypoint above. No ordinary Workbench read or other panel is exposed.
        from management_portal.workbench_projections.store import Store
        with Store().connect() as legacy:
            old = legacy.execute('SELECT data FROM preferences WHERE actor=%s AND app_id=%s',
                                 (actor, app.get('workbench_id') or app_id)).fetchone()
        container = json.loads(old['data']) if old else {}
        recovered = container.get('panels', {}).get('packaging', {})
        if not isinstance(recovered, dict):
            raise ValueError('原发布页面偏好必须是JSON对象')
        connection.execute('INSERT INTO page_preferences(actor,value) VALUES(%s,%s)', (actor, Jsonb(recovered)))
        return recovered


class PublishedPackaging:
    def get_revision(self, app_id, actor, revision):
        app = context(actor, app_id)
        if app['published_revision'] != revision:
            raise PermissionError('Release surface only exposes the current published revision')
        return app


def read_asset(path, actor):
    from management_portal.workbench_projections.application_http import _ASSET, _relative, _headers
    from management_portal.workbench_projections.application_build import read_artifact
    match = _ASSET.fullmatch(path.removeprefix('/api/workbenches/release-assets/'))
    if not match:
        raise ValueError('Invalid release asset address')
    app_id, revision, digest, encoded = match.groups()
    relative = _relative(encoded)
    app = context(actor, app_id, original_build=True)
    build = app['application_build']
    if (app['published_revision'] != int(revision) or build.get('artifact_hash') != digest
            or relative not in build.get('files', {})
            or (relative.endswith('.html') and relative != build.get('entries', {}).get('packaging'))):
        raise PermissionError('Asset is outside the published packaging surface')
    content, content_type = read_artifact(build, relative)
    return content, content_type, _headers(build['files'][relative]['sha256'])


def serve_asset(handler, path, actor):
    from management_portal.serve_management_portal import _serve_payload
    content, content_type, headers = read_asset(path, actor)
    return _serve_payload(handler, 200, content, content_type, cache_control='private, no-store', extra_headers=headers)
