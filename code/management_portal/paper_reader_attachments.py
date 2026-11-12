"""Opt-in single-Paper immutable attachment owner; no production entrypoints."""
from contextlib import contextmanager
from management_common.file_lock import lock_descriptor
import hashlib
import os
from pathlib import Path
import secrets
import stat

from management_common.domain_database import DomainDatabase
from management_common.file_durability import sync_directory
from management_common.file_permissions import create_private_file
from management_common.secure_paths import open_directory, unlink_child
from management_portal.paper_pdf import _has_pdf_header
from management_portal.paper_reader_annotations import Attachment, Conflict

DDL = '''
CREATE TABLE IF NOT EXISTS reader_attachment_heads (
 paper_id TEXT PRIMARY KEY, attachment_id TEXT UNIQUE NOT NULL,
 storage_key TEXT NOT NULL, active_version TEXT);
CREATE TABLE IF NOT EXISTS reader_attachment_versions (
 version_id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, sha256 TEXT NOT NULL,
 size BIGINT NOT NULL, relative_path TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reader_attachment_imports (
 operation_id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, actor TEXT NOT NULL,
 storage_key TEXT NOT NULL, expected_version TEXT, version_id TEXT NOT NULL,
 state TEXT NOT NULL, sha256 TEXT, size BIGINT, source_key TEXT NOT NULL);
'''


def purge_paper_records(connection, paper_id):
    """Remove only this owner's metadata; formal bundle cleanup owns the files."""
    for table in ('reader_attachment_versions', 'reader_attachment_imports', 'reader_attachment_heads'):
        connection.execute(f'DELETE FROM {table} WHERE paper_id=%s', (paper_id,))


class AttachmentOwner:
    def __init__(self, *, root, storage_key, authorize, checkpoint=lambda stage: None):
        # root must be a configured, dedicated object-data directory, never inferred.
        root = Path(root)
        if not root.is_absolute() or root.is_symlink():
            raise ValueError('explicit absolute non-symlink root required')
        if not storage_key or len(storage_key) > 128:
            raise ValueError('stable configured storage_key required')
        self.root = root.resolve()
        self.storage_key = storage_key
        self.authorize = authorize
        self.checkpoint = checkpoint
        self.database = DomainDatabase('paper', 'reader_attachments', DDL)

    def _auth(self, context, paper, write):
        actor = self.authorize(context, paper, write)
        if not isinstance(actor, str) or not actor:
            raise PermissionError('trusted Paper authorization required')
        return actor

    def _path(self, relative):
        path = self.root / relative
        if not path.resolve().is_relative_to(self.root) or path.is_symlink():
            raise PermissionError('attachment path escapes configured root')
        return path

    def _remove_stage(self, stage):
        # Published versions share this inode. Windows' read-only attribute
        # must remain intact on the version while deleting the staging name.
        try:
            with open_directory(stage.parent) as directory:
                unlink_child(directory, stage.name)
        except FileNotFoundError:
            pass

    @contextmanager
    def _operation(self, operation, *, create=True):
        if len(operation) != 32 or any(c not in '0123456789abcdef' for c in operation):
            raise ValueError('operation must be 32 lowercase hex characters')
        directory = self._path('locks')
        if create: directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(directory / operation, 'a+b' if create else 'r+b') as lock:
            lock_descriptor(lock.fileno())
            yield

    def _head(self, db, paper):
        row = db.execute('SELECT attachment_id,active_version,storage_key FROM reader_attachment_heads WHERE paper_id=%s FOR UPDATE', (paper,)).fetchone()
        if row and row[2] != self.storage_key:
            raise Conflict('configured storage key mismatch')
        return row

    def import_pdf(self, context, *, paper_id, source, expected_version, operation_id):
        actor = self._auth(context, paper_id, True)
        source_key = hashlib.sha256(os.fsencode(Path(source).resolve())).hexdigest()
        with self._operation(operation_id):
            with self.database.transaction() as db:
                existing = db.execute('SELECT paper_id,actor,storage_key,expected_version,source_key FROM reader_attachment_imports WHERE operation_id=%s', (operation_id,)).fetchone()
                if existing:
                    if tuple(existing) != (paper_id, actor, self.storage_key, expected_version, source_key):
                        raise Conflict('operation binding mismatch')
                else:
                    db.execute("INSERT INTO reader_attachment_imports(operation_id,paper_id,actor,storage_key,expected_version,version_id,state,source_key) VALUES (%s,%s,%s,%s,%s,%s,'copying',%s)", (operation_id, paper_id, actor, self.storage_key, expected_version, secrets.token_hex(16), source_key))
            if existing:
                return self._recover(context, operation_id)
            stage = self._path('staging/' + operation_id)
            stage.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                digest = hashlib.sha256(); size = 0
                source = Path(source)
                with source.open('rb') as incoming, create_private_file(stage) as outgoing:
                    before = os.fstat(incoming.fileno())
                    if not stat.S_ISREG(before.st_mode): raise ValueError('regular source required')
                    header = incoming.read(4)
                    if not _has_pdf_header(header): raise ValueError('PDF signature required')
                    incoming.seek(0)
                    while chunk := incoming.read(1024 * 1024):
                        size += len(chunk)
                        if size > 512 * 1024 * 1024: raise ValueError('PDF exceeds 512 MiB')
                        digest.update(chunk); outgoing.write(chunk)
                        self.checkpoint('chunk')
                    after = os.fstat(incoming.fileno()); current = source.stat()
                    signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
                    if signature(before) != signature(after) or signature(after) != signature(current):
                        raise Conflict('source changed during import')
                    outgoing.flush(); os.fsync(outgoing.fileno())
                sync_directory(stage.parent)
                with self.database.transaction() as db:
                    db.execute("UPDATE reader_attachment_imports SET state='prepared',sha256=%s,size=%s WHERE operation_id=%s", (digest.hexdigest(), size, operation_id))
                self.checkpoint('prepared')
            except Exception:
                # A prepared operation is recoverable without rereading the source.
                with self.database.read() as db:
                    state = db.execute('SELECT state FROM reader_attachment_imports WHERE operation_id=%s', (operation_id,)).fetchone()[0]
                if state == 'copying':
                    self._remove_stage(stage)
                    with self.database.transaction() as db:
                        db.execute("UPDATE reader_attachment_imports SET state='failed' WHERE operation_id=%s", (operation_id,))
                raise
            return self._recover(context, operation_id)

    def recover(self, context, operation_id):
        # Authorize before creating even the operation lock file.
        with self.database.read() as db:
            row = db.execute('SELECT paper_id,actor,storage_key FROM reader_attachment_imports WHERE operation_id=%s', (operation_id,)).fetchone()
        if not row: raise KeyError('unknown import')
        if self._auth(context, row[0], True) != row[1] or row[2] != self.storage_key:
            raise PermissionError('import context mismatch')
        with self._operation(operation_id):
            return self._recover(context, operation_id)

    def import_state(self, context, operation_id):
        with self.database.read() as db:
            row = db.execute('SELECT paper_id,actor,storage_key,state FROM reader_attachment_imports WHERE operation_id=%s', (operation_id,)).fetchone()
        if not row: return None
        if self._auth(context, row[0], True) != row[1] or row[2] != self.storage_key:
            raise PermissionError('import context mismatch')
        return row[3]

    def _recover(self, context, operation):
        with self.database.read() as db:
            row = db.execute('SELECT paper_id,actor,storage_key,expected_version,version_id,state,sha256,size FROM reader_attachment_imports WHERE operation_id=%s', (operation,)).fetchone()
        if not row: raise KeyError('unknown import')
        paper, actor, storage, expected, version, state, digest, size = row
        if self._auth(context, paper, True) != actor or storage != self.storage_key:
            raise PermissionError('import context mismatch')
        if state == 'done': return Attachment(paper, self._attachment_id(paper), version)
        stage = self._path('staging/' + operation)
        if state == 'copying':
            self._remove_stage(stage)
            with self.database.transaction() as db:
                db.execute("UPDATE reader_attachment_imports SET state='failed' WHERE operation_id=%s", (operation,))
            raise Conflict('incomplete copy; start a new explicit import')
        if state == 'failed': raise Conflict('failed copy; new operation required')
        relative = 'versions/' + version + '.pdf'
        final = self._path(relative); final.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not final.exists():
            os.chmod(stage, 0o400)
            os.link(stage, final)  # Exclusive publication; never overwrite a version.
            sync_directory(final.parent)
        if final.stat().st_size != size:
            raise Conflict('published version size mismatch; manual inspection required')
        self._remove_stage(stage); sync_directory(stage.parent)
        self.checkpoint('published')
        with self.database.transaction(key=paper) as db:
            head = self._head(db, paper)
            if (head[1] if head else None) != expected:
                raise Conflict('active version CAS conflict; prepared file retained')
            attachment = head[0] if head else secrets.token_hex(16)
            db.execute('INSERT INTO reader_attachment_versions VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING', (version, paper, digest, size, relative))
            db.execute('INSERT INTO reader_attachment_heads VALUES (%s,%s,%s,%s) ON CONFLICT(paper_id) DO UPDATE SET active_version=excluded.active_version', (paper, attachment, storage, version))
            self.checkpoint('before_commit')
            db.execute("UPDATE reader_attachment_imports SET state='done' WHERE operation_id=%s", (operation,))
        return Attachment(paper, attachment, version)

    def _attachment_id(self, paper):
        with self.database.read() as db:
            return db.execute('SELECT attachment_id FROM reader_attachment_heads WHERE paper_id=%s AND storage_key=%s', (paper, self.storage_key)).fetchone()[0]

    @contextmanager
    def active_write(self, context, selector):
        """Serialize active-version validation with every import/rollback publication."""
        self._auth(context, selector['paper_id'], True)
        with self.database.transaction(key=selector['paper_id']) as db:
            head = self._head(db, selector['paper_id'])
            if not head or (head[0], head[1]) != (selector['attachment_id'], selector['attachment_version']):
                raise Conflict('active PDF version changed; pending annotations retained')
            yield

    def current(self, context, paper_id):
        self._auth(context, paper_id, False)
        with self.database.read() as db:
            row = db.execute('SELECT attachment_id,active_version FROM reader_attachment_heads WHERE paper_id=%s AND storage_key=%s', (paper_id, self.storage_key)).fetchone()
        if not row: raise KeyError('no imported attachment')
        return self.resolve_attachment(context, dict(paper_id=paper_id, attachment_id=row[0], attachment_version=row[1]))

    def resolve_attachment(self, context, selector):
        self._auth(context, selector['paper_id'], False)
        with self.database.read() as db:
            row = db.execute('SELECT v.relative_path FROM reader_attachment_heads h JOIN reader_attachment_versions v ON v.paper_id=h.paper_id WHERE h.paper_id=%s AND h.attachment_id=%s AND h.storage_key=%s AND v.version_id=%s', (selector['paper_id'], selector['attachment_id'], self.storage_key, selector['attachment_version'])).fetchone()
        if not row: raise KeyError('attachment version not registered')
        if not self._path(row[0]).is_file(): raise FileNotFoundError('version bytes missing')
        return Attachment(**selector)

    def open_version(self, context, selector):
        self.resolve_attachment(context, selector)
        return self._path('versions/' + selector['attachment_version'] + '.pdf').open('rb')

    def rollback(self, context, *, paper_id, attachment_id, version, expected_version):
        self._auth(context, paper_id, True)
        descriptor = self.resolve_attachment(context, dict(paper_id=paper_id, attachment_id=attachment_id, attachment_version=version))
        with self.database.transaction(key=paper_id) as db:
            head = self._head(db, paper_id)
            if head[1] != expected_version: raise Conflict('active version CAS conflict')
            db.execute('UPDATE reader_attachment_heads SET active_version=%s WHERE paper_id=%s', (version, paper_id))
        return descriptor
