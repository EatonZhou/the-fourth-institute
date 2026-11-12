"""Paper-bound Reader assembly; imports only on explicit open, never scans."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import secrets
import threading
from contextlib import contextmanager
from management_common.file_lock import lock_descriptor, try_lock_descriptor
from management_common.instance_locations import stored_location, located_path

from management_common.domain_database import DomainDatabase
from management_portal.paper_reader_annotations import AnnotationStore, Conflict
from management_portal.paper_reader_attachments import AttachmentOwner

DDL = '''CREATE TABLE IF NOT EXISTS reader_document_sources (
 paper_id TEXT PRIMARY KEY, signature TEXT NOT NULL, operation_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reader_deletion_intents (
 paper_id TEXT PRIMARY KEY, state TEXT NOT NULL, bundle_location TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS reader_open_intents (
 paper_id TEXT PRIMARY KEY, signature TEXT NOT NULL, operation_id TEXT NOT NULL,
 expected_version TEXT);'''


class ReaderService:
    def __init__(self, *, locate, authorize, deleted, lock=None, lifecycle_state=None, locate_bundle=None):
        self.locate, self.authorize, self.deleted = locate, authorize, deleted
        self.database = DomainDatabase('paper', 'reader_service', DDL)
        self.lock = lock or threading.RLock()
        self.lifecycle_state = lifecycle_state
        self.locate_bundle = locate_bundle

    def _owner(self, actor, paper):
        self.authorize(actor, paper)
        with self.database.read() as db:
            if db.execute('SELECT 1 FROM reader_deletion_intents WHERE paper_id=%s', (paper,)).fetchone():
                raise Conflict('Paper Reader deletion in progress')
        if self.deleted(paper): raise KeyError('Paper deleted')
        bundle, source = (self.locate_bundle(paper), None) if self.locate_bundle else self.locate(paper)
        return AttachmentOwner(root=Path(bundle) / 'reader-attachments', storage_key='paper-bundle:' + paper,
            authorize=lambda context, target, write: self._authorized(context, target)), Path(source) if source is not None else None

    def _authorized(self, actor, paper):
        self.authorize(actor, paper)
        if self.deleted(paper): raise KeyError('Paper deleted')
        return actor

    @staticmethod
    def _signature(source):
        s = source.stat()
        return json.dumps([str(source.resolve()), s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns])

    def open(self, actor, paper):
        with self.lock, self._lifecycle_gate(paper):
            return self._open(actor, paper)

    @contextmanager
    def _lifecycle_gate(self, paper):
        # Raw database access deliberately avoids Reader lazy DDL for unused Papers.
        key = int.from_bytes(hashlib.sha256((self.database.database.schema + ':paper-reader-lifecycle:' + paper).encode()).digest()[:8], 'big', signed=True)
        # Existing dedicated session-lock facility: never wrap durable intent writes
        # in an outer transaction that could roll them back after unregister commits.
        with self.database.database.advisory_lock(key):
            yield

    def has_data(self, paper):
        with self.database.database.read_current() as db:
            for table in ('reader_document_sources', 'reader_open_intents', 'reader_deletion_intents'):
                if db.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0] is not None:
                    if db.execute(f'SELECT 1 FROM {table} WHERE paper_id=%s', (paper,)).fetchone(): return True
        return False

    def delete_if_used(self, paper, unregister):
        with self.lock, self._lifecycle_gate(paper):
            if not self.has_data(paper): return unregister()
            return self.delete_with(paper, unregister)

    def _open(self, actor, paper):
        self.authorize(actor, paper)
        # POST open may release an abandoned gate only after authoritative recovery.
        self.recover_delete(paper)
        owner, source = self._owner(actor, paper)
        if source is None: source = Path(self.locate(paper)[1])
        # Short, single-Paper coordination; no directory scan or per-read PDF hash.
        with owner._operation(hashlib.sha256(('open:' + paper).encode()).hexdigest()[:32]):
            self._owner(actor, paper)
            signature = self._signature(source)
            with self.database.read() as db:
                old = db.execute('SELECT signature,operation_id FROM reader_document_sources WHERE paper_id=%s', (paper,)).fetchone()
            if old and old[0] == signature:
                document = owner.current(actor, paper)
            else:
                try: expected = owner.current(actor, paper).attachment_version
                except KeyError: expected = None
                with self.database.read() as db:
                    intent = db.execute('SELECT signature,operation_id,expected_version FROM reader_open_intents WHERE paper_id=%s', (paper,)).fetchone()
                if intent and intent[0] == signature and owner.import_state(actor, intent[1]) != 'failed':
                    operation, expected = intent[1], intent[2]
                else:
                    operation = secrets.token_hex(16)
                    with self.database.transaction(key=paper) as db:
                        db.execute('INSERT INTO reader_open_intents VALUES (%s,%s,%s,%s) ON CONFLICT(paper_id) DO UPDATE SET signature=excluded.signature,operation_id=excluded.operation_id,expected_version=excluded.expected_version', (paper, signature, operation, expected))
                document = owner.import_pdf(actor, paper_id=paper, source=source, expected_version=expected, operation_id=operation)
                if signature != self._signature(source): raise Conflict('PDF changed while opening')
                self._owner(actor, paper)  # Do not publish source pointer after formal deletion.
                with self.database.transaction(key=paper) as db:
                    db.execute('INSERT INTO reader_document_sources VALUES (%s,%s,%s) ON CONFLICT(paper_id) DO UPDATE SET signature=excluded.signature,operation_id=excluded.operation_id', (paper, signature, operation))
                    db.execute('DELETE FROM reader_open_intents WHERE paper_id=%s', (paper,))
        return {**self.read(actor, asdict(document)), 'principal': actor}

    def _annotations(self, actor, document):
        owner, _ = self._owner(actor, document['paper_id'])
        # Require active version for writes; historical bytes/annotations remain available.
        return owner, AnnotationStore(authenticate=lambda context: context,
            resolve_attachment=lambda context, selector: owner.resolve_attachment(context, selector))

    def read(self, actor, document):
        with self._guard(actor, document['paper_id']):
            _, store = self._annotations(actor, document)
            return store.read(actor, document)

    @contextmanager
    def _guard(self, actor, paper):
        owner, _ = self._owner(actor, paper)
        with owner._operation(hashlib.sha256(('open:' + paper).encode()).hexdigest()[:32], create=False):
            self._owner(actor, paper)
            yield

    def commit(self, actor, batch):
        with self.lock, self._guard(actor, batch['paper_id']):
            return self._commit(actor, batch)

    def _commit(self, actor, batch):
        owner, store = self._annotations(actor, batch)
        with owner.active_write(actor, batch):
            return store.commit(actor, batch)

    def pdf(self, actor, document):
        owner, _ = self._annotations(actor, document)
        return owner.open_version(actor, document)

    def prepare_delete(self, paper):
        with self.lock:
            return self._prepare_delete(paper)

    def _prepare_delete(self, paper):
        # Called by authorized Paper deletion owner BEFORE unregistering the object.
        bundle = self.locate_bundle(paper) if self.locate_bundle else self.locate(paper)[0]
        owner = AttachmentOwner(root=Path(bundle) / 'reader-attachments', storage_key='paper-bundle:' + paper, authorize=self._authorized)
        with owner._operation(hashlib.sha256(('open:' + paper).encode()).hexdigest()[:32]):
            with self.database.transaction(key=paper) as db:
                db.execute("INSERT INTO reader_deletion_intents VALUES (%s,'prepared',%s) ON CONFLICT(paper_id) DO UPDATE SET state='prepared',bundle_location=excluded.bundle_location", (paper, stored_location(Path(bundle))))

    def delete_with(self, paper, unregister):
        """Formal deletion owner calls this after authorization; hold gate across unregister."""
        self.prepare_delete(paper)
        with self.database.read() as db:
            location = db.execute('SELECT bundle_location FROM reader_deletion_intents WHERE paper_id=%s', (paper,)).fetchone()[0]
        path = Path(located_path(location)) / 'reader-attachments/locks' / hashlib.sha256(('open:' + paper).encode()).hexdigest()[:32]
        try:
            with path.open('a+b') as gate:
                lock_descriptor(gate.fileno())
                # Reassert after acquiring gate: a prior abandoned attempt may have recovered.
                with self.database.transaction(key=paper) as db:
                    db.execute("INSERT INTO reader_deletion_intents VALUES (%s,'prepared',%s) ON CONFLICT(paper_id) DO UPDATE SET state='prepared'", (paper, location))
                result = unregister()
        except BaseException:
            self.recover_delete(paper)  # Read authority, never infer outcome from the exception.
            raise
        self.recover_delete(paper)
        return result

    def recover_delete(self, paper):
        """Single-ID recovery for the existing maintenance owner; no private sweeper."""
        with self.database.read() as db:
            row = db.execute('SELECT state,bundle_location FROM reader_deletion_intents WHERE paper_id=%s', (paper,)).fetchone()
        if not row or row[0] == 'done': return 'done' if row else 'none'
        state = self.lifecycle_state(paper) if self.lifecycle_state else ('deleted' if self.deleted(paper) else 'unknown')
        if state == 'deleted':
            self.finish_delete(paper)
            return 'done'
        if state != 'active': return 'pending'
        path = Path(located_path(row[1])) / 'reader-attachments/locks' / hashlib.sha256(('open:' + paper).encode()).hexdigest()[:32]
        try:
            with path.open('r+b') as gate:
                if not try_lock_descriptor(gate.fileno()): return 'pending'
                # Recheck authority after obtaining the same gate as unregister.
                if self.lifecycle_state(paper) != 'active': return 'pending'
                with self.database.transaction(key=paper) as db:
                    db.execute("DELETE FROM reader_deletion_intents WHERE paper_id=%s AND state='prepared'", (paper,))
            return 'active'
        except FileNotFoundError:
            return 'pending'

    def recover_pending_deletions(self, limit=100):
        """Bounded database intents for the existing maintenance caller, not a new worker."""
        if type(limit) is not int or not 1 <= limit <= 1000: raise ValueError('invalid recovery limit')
        with self.database.database.read_current() as db:
            if db.execute("SELECT to_regclass('reader_deletion_intents')").fetchone()[0] is None: return []
        with self.database.read() as db:
            papers = db.execute("SELECT paper_id FROM reader_deletion_intents WHERE state='prepared' ORDER BY paper_id LIMIT %s", (limit,)).fetchall()
        results = []
        for row in papers:
            try: results.append({'paper_id': row[0], 'state': self.recover_delete(row[0])})
            except Exception as error: results.append({'paper_id': row[0], 'state': 'pending', 'error': type(error).__name__})
        return results

    def finish_delete(self, paper):
        with self.lock:
            return self._finish_delete(paper)

    def _finish_delete(self, paper):
        if not self.deleted(paper): raise Conflict('Paper not formally unregistered')
        with self.database.transaction(key=paper) as db:
            if not db.execute('SELECT 1 FROM reader_deletion_intents WHERE paper_id=%s', (paper,)).fetchone():
                raise Conflict('missing durable Reader deletion intent')
            # Table owners initialize their own definitions; no shared schema edits.
            AnnotationStore(authenticate=lambda c: c, resolve_attachment=lambda c, s: None).database.ensure()
            from management_portal.paper_reader_attachments import DDL as ATTACHMENT_DDL
            DomainDatabase('paper', 'reader_attachments', ATTACHMENT_DDL).ensure()
            from management_portal.paper_reader_annotations import purge_paper_records as purge_annotations
            from management_portal.paper_reader_attachments import purge_paper_records as purge_attachments
            purge_annotations(db, paper)
            purge_attachments(db, paper)
            for table in ('reader_document_sources', 'reader_open_intents'):
                db.execute(f'DELETE FROM {table} WHERE paper_id=%s', (paper,))
            db.execute("UPDATE reader_deletion_intents SET state='done' WHERE paper_id=%s", (paper,))
        # Files are inside the registered Paper bundle: existing formal directory cleanup owns them.


def production_service():
    from management_portal import paper_store, web_resources
    from management_portal.resources.directory_registrations import is_deleted
    def lifecycle_state(paper_id):
        from management_portal.resources.registry_store import get_registry_store
        if is_deleted(paper_id): return 'deleted'
        return 'active' if get_registry_store().get('paper', paper_id) else 'unknown'
    def registered_paper(paper_id):
        found = paper_store.PAPER_STORE.manifest_for_paper_id(paper_id, allow_scan=False, registered_only=True)
        if not found: raise KeyError(paper_id)
        path, manifest = found
        return paper_store.PAPER_STORE.manifest_to_paper(manifest, path)
    def bundle_path(paper):
        bundle = Path(str(paper.get('_store_root') or paper.get('folder') or '')).resolve()
        if not str(paper.get('_store_root') or paper.get('folder') or ''):
            raise ValueError('authoritative Paper bundle missing')
        return bundle
    def locate_bundle(paper_id):
        return bundle_path(registered_paper(paper_id))
    def locate(paper_id):
        paper = registered_paper(paper_id)
        # Resolve the registered manifest's PDF directly; resolve_asset would look up
        # the Paper again through the legacy scan-enabled helper.
        source = paper_store.PAPER_STORE.paper_file(paper, 'pdf')
        if not source or not source.is_file(): raise FileNotFoundError('pdf')
        return bundle_path(paper), source
    return ReaderService(locate=locate, locate_bundle=locate_bundle, authorize=lambda actor, paper: web_resources._require_resource(paper, actor), deleted=is_deleted, lifecycle_state=lifecycle_state)
