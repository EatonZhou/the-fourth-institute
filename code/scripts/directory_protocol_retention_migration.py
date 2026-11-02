"""Offline migration, called with the supervisor's connection while writers stop."""
from management_common.portal_database import _database_schema
from management_common.json_columns import json_value


def migrate(connection):
    from management_common.directory_retention import DDL
    schema = _database_schema('directory_retention')
    connection.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    # Keep the caller's search_path intact; the migration never opens a connection.
    previous = connection.execute('SHOW search_path').fetchone()[0]
    try:
        connection.execute(f'SET LOCAL search_path TO "{schema}"')
        connection.executescript(DDL)
    finally:
        connection.execute('SELECT set_config(\'search_path\',%s,true)', (previous,))
    migrate_run_ownership(connection)


def migrate_run_ownership(connection):
    """Offline only: add run provenance to feature artifacts created by tests."""
    schema = _database_schema('directory_retention')
    exists = connection.execute('''SELECT 1 FROM information_schema.columns
        WHERE table_schema=%s AND table_name='artifacts' AND column_name='test_run_id' ''', (schema,)).fetchone()
    if not exists:
        connection.execute(f'ALTER TABLE "{schema}".artifacts ADD COLUMN test_run_id TEXT NOT NULL DEFAULT \'\'')


def backfill_run_ownership(connection):
    """Only recorded resource allocations prove an artifact owner's test run."""
    allocations = _table(connection, 'resource_lifecycle', 'allocations')
    scopes = _table(connection, 'resource_lifecycle', 'scopes')
    if not allocations or not scopes:
        return 0
    table = f'"{_database_schema("directory_retention")}".artifacts'
    return connection.execute(f'''UPDATE {table} artifact SET test_run_id=scope.run_id
        FROM {allocations} allocation JOIN {scopes} scope ON scope.id=allocation.scope_id
        WHERE artifact.owner_id=allocation.resource_id AND artifact.test_run_id=''
        AND scope.run_id<>'' ''').rowcount


def release_deleted_owners(connection):
    """Historical accepted uploads follow an already deleted owner's identity."""
    from management_common.directory_retention import RetentionStore
    store = RetentionStore(connection=connection)
    deleted = set()
    for schema, table, key, condition in (
        ('resource_registry', 'deleted_identities', 'resource_id', 'TRUE'),
        ('lineage', 'residents', 'resident_id', 'deleted_at IS NOT NULL'),
        ('lineage', 'lineages', 'lineage_id', 'deleted_at IS NOT NULL'),
        ('portal_identity', 'user_accounts', 'username', "status='deleted'"),
    ):
        qualified = _table(connection, schema, table)
        if qualified:
            deleted.update(row[0] for row in connection.execute(f'SELECT {key} FROM {qualified} WHERE {condition}').fetchall())
    artifacts = connection.execute(f"SELECT location,owner_id,bytes FROM {store.table} WHERE category='object_data'").fetchall()
    rooms = _table(connection, 'chatrooms', 'rooms')
    if rooms:
        live_rooms = {row[0] for row in connection.execute(f'SELECT chatroom_id FROM {rooms}').fetchall()}
        deleted.update(row['owner_id'] for row in artifacts
                       if row['owner_id'].startswith('chatroom_') and row['owner_id'] not in live_rooms)
    selected = [dict(row) for row in artifacts if row['owner_id'] in deleted]
    for owner in sorted({row['owner_id'] for row in selected}):
        store.release_owner(owner, connection=connection)
    return selected


def backfill_workbench_builds(connection, *, artifact_root, now):
    """Import only hashes proven by version receipts; never scan unknown folders.

    The first later revision which excludes a build from current/previous is
    its replacement clock. Currently served publication remains protected.
    """
    import json
    import re
    from datetime import timedelta
    from pathlib import Path
    from management_common.directory_paths import cleanup_location
    workbenches = _database_schema('workbenches')
    retention = _database_schema('directory_retention')
    if not connection.execute('SELECT to_regclass(%s)', (f'"{workbenches}".application_packages',)).fetchone()[0]:
        return 0
    rows = connection.execute(f'''SELECT p.app_id,p.revision,p.build,a.revision AS current,a.published,
        v.updated_at, (SELECT v2.updated_at FROM "{workbenches}".versions v2
                      WHERE v2.app_id=p.app_id AND v2.revision=p.revision+2) AS replaced_at
        FROM "{workbenches}".application_packages p
        JOIN "{workbenches}".apps a ON a.id=p.app_id
        JOIN "{workbenches}".versions v ON v.app_id=p.app_id AND v.revision=p.revision''').fetchall()
    builds = {}
    for row in rows:
        build = json.loads(row['build'])
        digest = build.get('artifact_hash', '')
        if build.get('status') != 'ready' or not re.fullmatch('[0-9a-f]{64}', digest):
            continue
        protected = row['revision'] in {row['current'], row['current']-1, row['published']}
        if not protected and row['replaced_at'] is None:
            # No replacement receipt: preserve it for offline investigation.
            continue
        deadline = None if protected else row['replaced_at'] + timedelta(days=7)
        if digest not in builds:
            builds[digest] = [row['updated_at'], deadline]
        else:
            builds[digest][0] = min(builds[digest][0], row['updated_at'])
            previous = builds[digest][1]
            builds[digest][1] = None if previous is None or deadline is None else max(previous, deadline)
    for digest, (created, deadline) in builds.items():
        connection.execute(f'''INSERT INTO "{retention}".artifacts
            (location,owner_id,origin,category,created_at,eligible_at,last_access,bytes,cache_group)
            VALUES (%s,%s,'created','workbench_build',%s,%s,%s,0,'') ON CONFLICT(location) DO NOTHING''',
            (cleanup_location(Path(artifact_root) / digest), 'build:'+digest, created, deadline, now))
    return len(builds)


def _table(connection, schema, name):
    table = f'"{_database_schema(schema)}".{name}'
    return table if connection.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0] else None


def upload_consumption_evidence(connection):
    """Read accepted Mailbox envelopes and canonical stored message facts.

    Never treat execution preparation, composer state, target or ready as use.
    Return the earliest proven owner; ties between owners remain unresolved.
    """
    import json
    evidence = {}
    mailbox = _table(connection, 'lineage', 'resident_mailbox')
    segments = _table(connection, 'agent', 'segments')
    events = _table(connection, 'lineage', 'conversation_events')

    def remember(owner, at, message):
        if isinstance(message, str):
            message = json.loads(message)
        if message.get('role') != 'user' or at is None:
            return
        for part in message.get('content_parts', message.get('parts', [])):
            if isinstance(part, dict) and part.get('upload_id'):
                evidence.setdefault(part['upload_id'], []).append((at, owner))

    if mailbox:
        for row in connection.execute(f'SELECT resident_id,accepted_at,message_json FROM {mailbox}').fetchall():
            remember(row['resident_id'], row['accepted_at'], row['message_json'])
    if segments:
        for row in connection.execute(f'''SELECT created_by_agent_id,started_at,metadata_json
                FROM {segments} WHERE segment_type='input' AND metadata_json::jsonb->>'role'='user'
                AND metadata_json::text LIKE %s ''', ('%"upload_id"%',)).fetchall():
            owner = row['created_by_agent_id']
            message = json_value(row['metadata_json']).get('message', {})
            at = row['started_at']
            if owner.startswith('resident_'):
                # A projected segment alone can precede admission failure.
                accepted = connection.execute(f'''SELECT occurred_at FROM {events}
                    WHERE resident_id=%s AND subject_id=%s AND event_type='conversation.message.appended'
                    ORDER BY cursor LIMIT 1''', (owner, message.get('message_id'))).fetchone() if events else None
                if not accepted:
                    continue
                at = accepted['occurred_at']
            remember(owner, at, message)
    owners, conflicts = {}, []
    for identifier, uses in evidence.items():
        first = min(at for at, _owner in uses)
        candidates = {owner for at, owner in uses if at == first}
        if len(candidates) == 1:
            owners[identifier] = candidates.pop()
        else:
            conflicts.append(identifier)
    return {'owners': owners, 'conflicts': conflicts, 'complete': bool(mailbox and segments and events)}


def _insert_artifact(connection, path, *, owner_id, category, created_at,
                     finished_at=None, cache_group='', size=0):
    from datetime import timedelta
    from management_common.database_time import instant
    from management_common.directory_paths import cleanup_location
    from management_common.directory_retention import POLICIES
    created = instant(created_at)
    if created is None:
        raise ValueError('missing creation time')
    policy = POLICIES[category]
    start = created if category == 'unused_upload' else instant(finished_at)
    deadline = start + timedelta(days=policy['days']) if start is not None and policy['days'] is not None else None
    table = f'"{_database_schema("directory_retention")}".artifacts'
    connection.execute(f'''INSERT INTO {table}
        (location,owner_id,origin,category,created_at,eligible_at,last_access,bytes,cache_group)
        VALUES (%s,%s,'created',%s,%s,%s,%s,%s,%s) ON CONFLICT(location) DO NOTHING''',
        (cleanup_location(path.absolute()), owner_id, category, created, deadline, created, size, cache_group))


def backfill_uploads(connection, *, upload_root, unused_upload_ids=()):
    """Creation receipts set seven days; durable consumers prove ownership.

    The owner supplies IDs proved unused by the complete offline inventory.
    Missing structured references alone cannot disprove legacy path-based use.
    Missing creation records, ambiguous first use and unavailable history are
    report-only. No ready/target/mtime inference can certify an unused upload.
    """
    import json
    import re
    from pathlib import Path
    from management_common.database_time import instant
    evidence = upload_consumption_evidence(connection)
    proven_unused = set(unused_upload_ids)
    root = Path(upload_root)
    report = {'registered': 0, 'skipped': []}
    if not root.exists():
        return report
    for directory in root.iterdir():
        if not re.fullmatch(r'upload_[0-9a-f]{24}', directory.name):
            continue
        try:
            if directory.is_symlink() or not directory.is_dir() or (directory / 'meta.json').is_symlink():
                raise ValueError('not an owned regular upload directory')
            meta = json.loads((directory / 'meta.json').read_text(encoding='utf-8'))
            if not isinstance(meta, dict):
                raise ValueError('invalid creation receipt')
            created = instant(meta.get('created_at'))
            if meta['upload_id'] != directory.name or created is None:
                raise ValueError('incomplete creation evidence')
            owner = evidence['owners'].get(directory.name)
            if directory.name in evidence['conflicts']:
                raise ValueError('ambiguous first consumer')
            if not owner and (not evidence['complete'] or directory.name not in proven_unused):
                raise ValueError('consumption history cannot establish unused status')
        except (OSError, ValueError, KeyError, TypeError):
            report['skipped'].append(directory.name)
            continue
        _insert_artifact(connection, directory, owner_id=owner or directory.name,
                         category='object_data' if owner else 'unused_upload', created_at=created)
        report['registered'] += 1
    return report


def backfill_known_artifacts(connection, *, article_build_root=None, ccf_root=None,
                             collector_root=None, provider_receipts=(), archive_receipts=()):
    """Import only owner records with explicit clocks; no filesystem-age guesses.

    Provider/archive owners must export receipts containing path, owner_id,
    created_at, finished_at and origin. This function never connects to owners
    or reads account homes. Missing receipts remain the owner's inventory job.
    """
    import json
    import re
    from pathlib import Path
    from hashlib import sha256
    from management_common.file_retention import _size
    from management_common.directory_paths import cleanup_location
    report = {'registered': [], 'unresolved': []}

    def enroll(path, **fields):
        path = Path(path)
        try:
            if path.is_symlink() or not path.exists():
                raise ValueError('not an owned existing path')
            _insert_artifact(connection, path, size=_size(path), **fields)
        except (ValueError, OSError, TypeError) as error:
            report['unresolved'].append({'path': str(path), 'reason': str(error)})
        else:
            report['registered'].append(str(path))

    articles = _table(connection, 'article', 'build_reports')
    if article_build_root is not None and articles:
        root = Path(article_build_root)
        for row in connection.execute(f'SELECT article_id,report_json FROM {articles}').fetchall():
            identifier = row['article_id']
            if not re.fullmatch(r'article_[0-9a-f]{12}', identifier):
                continue
            for path in (root / (identifier + '.pdf'), root / 'cache' / identifier):
                if path.exists():
                    enroll(path, owner_id=identifier, category='cache', cache_group='article',
                           created_at=json_value(row['report_json']).get('compiled_at'))
    if ccf_root is not None:
        root = Path(ccf_root)
        if root.is_dir() and not root.is_symlink():
            for path in root.rglob('*.json'):
                # A request is runtime work, not a rebuildable cache entry.
                if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != root.parent):
                    continue
                try:
                    receipt = json.loads(path.read_text(encoding='utf-8'))
                    at = receipt.get('fetched_at')
                    if at is None and path.parent.name == '_enrichment' and receipt.get('status') in {'done', 'error'}:
                        at = receipt.get('updated_at')
                    if path.name.endswith('.request.json') or '.request-' in path.name:
                        raise ValueError('request has no proven termination receipt')
                    if at is None:
                        raise ValueError('cache has no owner fetch timestamp')
                    owner = 'cache:' + sha256(cleanup_location(path.absolute()).encode()).hexdigest()
                    enroll(path, owner_id=owner, category='cache', cache_group='paper_ccf', created_at=at)
                except (ValueError, OSError, TypeError, AttributeError) as error:
                    report['unresolved'].append({'path': str(path), 'reason': str(error)})
    collectors = _table(connection, 'paper', 'collector_jobs')
    if collector_root is not None and collectors:
        for row in connection.execute(f'SELECT job_id,created_at,status FROM {collectors}').fetchall():
            if not re.fullmatch(r'collector-[0-9a-f]{12}', row['job_id']):
                continue
            path = Path(collector_root) / row['job_id']
            if path.exists():
                report['unresolved'].append({'path': str(path), 'reason': 'legacy collector has no finished_at'})
    for category, receipts in (('provider_session', provider_receipts), ('background', archive_receipts)):
        for receipt in receipts:
            if receipt.get('origin') != 'created' or not receipt.get('finished_at'):
                report['unresolved'].append({'path': str(receipt.get('path', '')), 'reason': 'missing owned termination receipt'})
                continue
            enroll(receipt['path'], owner_id=receipt['owner_id'], category=category,
                   created_at=receipt.get('created_at'), finished_at=receipt['finished_at'])
    return report


def backfill_paper_drafts(connection, *, draft_root):
    """The old Paper uploader deleted consumed drafts; surviving receipts prove
    creation of unused drafts. Never use ready/updated_at/file mtime as a clock.
    Existing consumption registrations win over this idempotent import.
    """
    import json
    import re
    from pathlib import Path
    report = {'registered': [], 'unresolved': []}
    root = Path(draft_root)
    if not root.is_dir() or root.is_symlink():
        return report
    for path in root.iterdir():
        if not re.fullmatch(r'paper-upload-[0-9a-f]{12}', path.name):
            continue
        try:
            if path.is_symlink() or (path / 'draft.json').is_symlink():
                raise ValueError('not an owned regular draft')
            receipt = json.loads((path / 'draft.json').read_text(encoding='utf-8'))
            if receipt.get('upload_id') != path.name:
                raise ValueError('draft receipt identity mismatch')
            _insert_artifact(connection, path, owner_id=path.name, category='unused_upload',
                             created_at=receipt.get('created_at'))
        except (OSError, ValueError, TypeError, AttributeError) as error:
            report['unresolved'].append({'path': str(path), 'reason': str(error)})
        else:
            report['registered'].append(str(path))
    return report
