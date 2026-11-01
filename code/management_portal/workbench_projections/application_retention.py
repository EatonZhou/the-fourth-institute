"""Build lifetime is derived from authoritative workbench revisions.

Current and previous revisions (plus a still-served published revision) protect
shared content hashes. Losing the last protected reference starts seven days.
"""
import json
from datetime import timedelta

from management_common.directory_retention import RetentionStore, utc_now
from management_common.directory_paths import cleanup_location, cleanup_path
from management_common.portal_database import _database_schema


def reconcile(connection, *, now=None, deleted_hashes=()):
    from .application_build import _artifact_root
    store = RetentionStore()
    store.queue.lock(connection)
    now = now or utc_now()
    schema = _database_schema('workbenches')
    rows = connection.execute(f'''SELECT p.build,p.revision,a.revision AS current,a.published
        FROM "{schema}".application_packages p JOIN "{schema}".apps a ON a.id=p.app_id''').fetchall()
    protected = set()
    for row in rows:
        build = json.loads(row['build'])
        if row['revision'] in {row['current'], row['current'] - 1, row['published']}:
            if build.get('status') == 'ready':
                protected.add(cleanup_location(_artifact_root() / build['artifact_hash']))
    deleted = {cleanup_location(_artifact_root() / digest) for digest in deleted_hashes}
    artifacts = connection.execute(f"SELECT * FROM {store.table} WHERE category='workbench_build' FOR UPDATE").fetchall()
    for row in artifacts:
        location = row['location']
        if location in protected:
            store.queue.assert_available(cleanup_path(location), connection=connection)
            connection.execute(f'UPDATE {store.table} SET eligible_at=NULL WHERE location=%s', (location,))
        elif location in deleted:
            store.release_path(cleanup_path(location), connection=connection)
        elif row['eligible_at'] is None:
            connection.execute(f'UPDATE {store.table} SET eligible_at=%s WHERE location=%s',
                               (now + timedelta(days=7), location))


def register_build(digest, *, connection, now=None):
    from .application_build import _artifact_root
    store = RetentionStore()
    now = now or utc_now()
    store.queue.lock(connection)
    location = cleanup_location(_artifact_root() / digest)
    exists = connection.execute(f'SELECT 1 FROM {store.table} WHERE location=%s', (location,)).fetchone()
    store.declare(_artifact_root() / digest, category='workbench_build', owner_id='build:' + digest,
                  created_at=now, connection=connection)
    # An uncommitted build has no app reference; its staging lease lasts 7 days.
    if not exists:
        connection.execute(f'UPDATE {store.table} SET eligible_at=%s WHERE location=%s',
                           (now + timedelta(days=7), location))
