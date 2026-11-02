"""Identity-proven deletion, with durable claims rather than stat-then-unlink.

The caller owns the decision to delete. This owner records and recovers the
filesystem effect for files, links and directories alike. The private sibling
is an operation location, not a backup or a user trash directory. Unknown
entries are restored without overwrite, or retained there with an error.

The journal and private operation namespace are trusted. This does not defend
against a malicious process with the same host UID editing those objects (or
against administrators). Public source names may change at every syscall.
"""
from __future__ import annotations

import hashlib
import json
import os
from uuid import uuid4

from . import secure_paths as fs
from .directory_paths import cleanup_location, cleanup_path, lexical_path, validate_removal, _remove_entry
from .portal_database import portal_database


def initialize_owned_deletion_schema(connection, schema):
    """Owner DDL for fresh databases and the parent's offline migration."""
    table = f'"{schema}".claims'
    connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (table + ':init',))
    connection.execute(f'''CREATE TABLE IF NOT EXISTS {table} (
        claim_id TEXT PRIMARY KEY, location TEXT NOT NULL,
        identity JSONB NOT NULL, parent_identity JSONB NOT NULL,
        private_name TEXT NOT NULL, private_identity JSONB,
        finished BOOLEAN NOT NULL DEFAULT FALSE
    )''')


def _identity(info):
    return [info.st_dev, info.st_ino]


def _entry(parent, name):
    try:
        return fs.lstat_child(parent, name)
    except FileNotFoundError:
        return None


class OwnedPathDeletionStore:
    def __init__(self):
        # Different transaction owner from Creation/Move. Intent must commit
        # before rename even when a caller later rolls back its own transaction.
        self.db = portal_database(schema='directory_deletion')
        self.table = f'"{self.db.schema}".claims'
        with self.db.read() as connection:
            ready = connection.execute('SELECT to_regclass(%s)', (self.table,)).fetchone()[0]
        if ready is None:
            self.db.write(lambda conn: initialize_owned_deletion_schema(conn, self.db.schema))

    def resume(self, path, identity):
        """Recover an existing deletion without claiming a new public entry."""
        return self.remove(path, identity, _pending_only=True)

    def remove(self, path, identity, *, _pending_only=False):
        path = lexical_path(path)
        identity = list(identity)
        if len(identity) != 2 or not all(isinstance(value, int) for value in identity):
            raise ValueError('Deletion requires a device/inode identity')
        location = cleanup_location(path)
        key = hashlib.sha256(json.dumps([location, identity]).encode()).hexdigest()
        lock = int.from_bytes(hashlib.sha256(('owned-delete:' + key).encode()).digest()[:8], 'big', signed=True)
        with self.db.advisory_lock(lock):
            with self.db.read() as conn:
                row = conn.execute(f'SELECT * FROM {self.table} WHERE claim_id=%s', (key,)).fetchone()
                row = dict(row) if row is not None else None
            if row is None and _pending_only:
                return
            # Missing source is success only if there is no outstanding claim.
            if row is None and not os.path.lexists(path):
                return
            with fs.open_directory(path.parent) as parent:
                validate_removal(path)
                if row is None:
                    before = _entry(parent, path.name)
                    if before is None:
                        return
                    if _identity(before) != identity:
                        raise FileExistsError('Directory entry ownership changed during cleanup: ' + str(path))
                    row = dict(claim_id=key, location=location, identity=identity,
                               parent_identity=_identity(os.fstat(parent)),
                               private_name='.tfi-delete-' + uuid4().hex,
                               private_identity=None, finished=False)
                    self.db.write(lambda conn: conn.execute(f'''INSERT INTO {self.table}
                        (claim_id,location,identity,parent_identity,private_name)
                        VALUES (%s,%s,%s::jsonb,%s::jsonb,%s)''',
                        (key, location, json.dumps(identity), json.dumps(row['parent_identity']), row['private_name'])))
                if _identity(os.fstat(parent)) != row['parent_identity']:
                    raise FileExistsError('Cleanup parent was replaced; claim retained: ' + str(path.parent))
                self._remove(parent, path.name, row)

    def _remove(self, parent, name, row):
        private = row['private_name']
        if row['private_identity'] is None:
            # The mkdir -> journal gap cannot prove ownership after a crash.
            # Keep an existing unproven directory, even if empty, and report it.
            if _entry(parent, private) is not None:
                raise FileExistsError('Unproven cleanup operation directory retained: ' + private)
            with fs.private_child(parent, private, create=True) as claim:
                fs.sync_directory(parent)
                row['private_identity'] = _identity(os.fstat(claim))
                self.db.write(lambda conn: conn.execute(
                    f'UPDATE {self.table} SET private_identity=%s::jsonb WHERE claim_id=%s',
                    (json.dumps(row['private_identity']), row['claim_id'])))
        if not row['finished'] or _entry(parent, private) is not None:
            with fs.private_child(parent, private) as claim:
                if _identity(os.fstat(claim)) != row['private_identity']:
                    raise FileExistsError('Cleanup operation directory replaced; retained: ' + private)
                if not row['finished']:
                    self._claim_and_remove(parent, name, claim, row)
                    fs.sync_directory(claim)
                    self.db.write(lambda conn: conn.execute(
                        f'UPDATE {self.table} SET finished=TRUE WHERE claim_id=%s', (row['claim_id'],)))
            # No child handles remain (also required by Windows). Only remove
            # this empty, private operation directory, never unknown contents.
            fs.rmdir_child(parent, private)
            fs.sync_directory(parent)
        self.db.write(lambda conn: conn.execute(f'DELETE FROM {self.table} WHERE claim_id=%s', (row['claim_id'],)))

    def _claim_and_remove(self, parent, name, claim, row):
        claimed = _entry(claim, 'entry')
        if claimed is None:
            try:
                fs.rename_child_no_replace(parent, name, claim, 'entry')
            except FileNotFoundError:
                return
            fs.sync_directory(parent)
            fs.sync_directory(claim)
            claimed = fs.lstat_child(claim, 'entry')
        if _identity(claimed) != row['identity']:
            try:
                fs.rename_child_no_replace(claim, 'entry', parent, name)
            except OSError as error:
                raise FileExistsError('Unknown entry retained in ' + row['private_name'] + '/entry; restore failed: ' + str(error)) from error
            fs.sync_directory(parent)
            fs.sync_directory(claim)
            raise FileExistsError('Unknown entry restored; cleanup remains pending: ' + name)
        # Identity checking and deletion now occur in the private namespace.
        # Ordinary unqualified tree cleanup still has no per-child DB records.
        _remove_entry(claim, 'entry', identity=row['identity'])

    def recover(self):
        """Recover unreserved claims; a journal alone cannot override a live owner.

        Owner-directed remove/resume retain their caller's authorization. This
        autonomous entry has no such authorization and must honor every path
        reservation, including the claimed path's descendants and ancestors.
        Hold the registration lock across observation and filesystem effect.
        """
        from .directory_cleanup_store import get_directory_cleanup_store
        from .directory_paths import overlaps
        queue = get_directory_cleanup_store()
        with self.db.read() as conn:
            rows = conn.execute(f'SELECT location,identity FROM {self.table} ORDER BY claim_id').fetchall()
        errors = []
        for row in rows:
            try:
                path = cleanup_path(row['location'])
                with queue.database.transaction() as conn:
                    queue.lock(conn)
                    reservations = conn.execute(f'SELECT location FROM {queue.reservations}').fetchall()
                    if any(overlaps(path, cleanup_path(item['location'])) for item in reservations):
                        raise FileExistsError('Deletion recovery path is reserved: ' + str(path))
                    self.resume(path, row['identity'])
            except (OSError, ValueError) as error:
                errors.append(str(error))
        return errors
