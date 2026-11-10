from dataclasses import asdict
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from management_portal.paper_reader_service import ReaderService
from management_portal.resources.registry_store import get_registry_store
from management_portal.resources.directory_registrations import is_deleted
from management_portal.http_paper_reader import routes


@pytest.fixture
def case(tmp_path):
    paper = 'paper_0123456789ab'
    source = tmp_path / 'paper.pdf'; source.write_bytes(b'%PDF-1.4\nsynthetic')
    registry = get_registry_store()
    with registry.writer('paper') as writer: writer.put('paper', paper, {'id': paper})
    def status(p):
        if is_deleted(p): return 'deleted'
        return 'active' if registry.get('paper', p) else 'unknown'
    def build():
        return ReaderService(locate=lambda p: (tmp_path, source), authorize=lambda a, p: None,
            deleted=is_deleted, lifecycle_state=status)
    def unregister():
        with registry.writer('paper') as writer:
            writer.delete('paper', paper)
            writer.connection.execute(f'INSERT INTO "{registry.database.schema}".deleted_identities VALUES (%s)', (paper,))
    return paper, source, build, unregister


def test_active_failure_and_crashed_prepare_unfreeze(case):
    paper, source, build, unregister = case
    service = build(); document = service.open('a', paper)['document']
    def fail(): raise RuntimeError('unregister failed before commit')
    with pytest.raises(RuntimeError): service.delete_with(paper, fail)
    assert service.read('a', document)['revision'] == '0'
    service.prepare_delete(paper)  # process dies before unregister: no live gate
    recovered = build()
    assert recovered.open('a', paper)['document'] == document
    assert recovered.recover_delete(paper) == 'none'
    assert source.exists()


def test_committed_unregister_lost_response_and_recovery(case, monkeypatch):
    paper, source, build, unregister = case
    service = build(); service.open('a', paper)
    service.prepare_delete(paper); unregister()  # process exits before finish
    recovered = build()
    from management_portal.paper_reader_annotations import purge_paper_records
    def fail(*args): raise RuntimeError('PG failure')
    monkeypatch.setattr('management_portal.paper_reader_annotations.purge_paper_records', fail)
    with pytest.raises(RuntimeError): recovered.recover_delete(paper)
    monkeypatch.setattr('management_portal.paper_reader_annotations.purge_paper_records', purge_paper_records)
    assert recovered.recover_pending_deletions() == [{'paper_id': paper, 'state': 'done'}]
    assert recovered.recover_delete(paper) == 'done'
    with recovered.database.read() as db:
        assert db.execute('SELECT count(*) FROM reader_attachment_heads WHERE paper_id=%s', (paper,)).fetchone()[0] == 0
    assert source.exists()  # no unlink outside the formal bundle cleanup owner


def test_live_deletion_gate_and_unknown_authority(case):
    paper, source, build, unregister = case
    service = build(); service.open('a', paper)
    entered, release = threading.Event(), threading.Event()
    def slow(): entered.set(); assert release.wait(5); unregister()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.delete_with, paper, slow)
        assert entered.wait(5)
        assert build().recover_delete(paper) == 'pending'
        release.set(); future.result()
    assert build().recover_delete(paper) == 'done'


def test_unknown_does_not_clear_or_unfreeze(case):
    paper, source, build, unregister = case
    service = build(); service.open('a', paper); service.prepare_delete(paper)
    service.lifecycle_state = lambda p: 'unknown'
    assert service.recover_delete(paper) == 'pending'
    def unavailable(p): raise OSError('registry unavailable')
    service.lifecycle_state = unavailable
    with pytest.raises(OSError): service.recover_delete(paper)
    with service.database.read() as db:
        assert db.execute('SELECT count(*) FROM reader_attachment_heads WHERE paper_id=%s', (paper,)).fetchone()[0] == 1


def test_unregister_committed_but_raised(case):
    paper, source, build, unregister = case
    service = build(); service.open('a', paper)
    def lost(): unregister(); raise RuntimeError('lost acknowledgement')
    with pytest.raises(RuntimeError): service.delete_with(paper, lost)
    assert service.recover_delete(paper) == 'done'


def test_csrf_and_methods(monkeypatch):
    monkeypatch.setattr('management_portal.http_auth._request_scheme', lambda r: 'https')
    def call(headers):
        definitions = routes(service=None, get_auth=lambda r: object(), authenticate=lambda a: 'a', read_json=lambda r: {}, respond=lambda r, status, body: status)
        return definitions[0].handler(SimpleNamespace(headers=headers), urlsplit('/paper/api/reader/open'), '')
    base = {'X-Reader-Principal': 'a', 'Host': 'portal.test', 'Content-Type': 'application/json'}
    assert call({**base, 'Sec-Fetch-Site': 'cross-site'}) == 403
    assert call({**base, 'Content-Type': 'text/plain'}) == 400
    assert call({**base, 'X-Reader-Principal': 'b'}) == 409
    assert call({**base, 'Origin': 'https://evil.test'}) == 403


def test_read_never_imports_or_creates_scope(case):
    paper, source, build, unregister = case
    service = build()
    fake = dict(paper_id=paper, attachment_id='not-imported', attachment_version='not-imported')
    with pytest.raises(FileNotFoundError): service.read('a', fake)
    assert not (source.parent / 'reader-attachments').exists()
    document = service.open('a', paper)['document']
    assert service.read('a', document)['annotations'] == []
    with service.database.read() as db:
        assert db.execute('SELECT count(*) FROM reader_annotation_scopes').fetchone()[0] == 0


def test_delete_and_historical_read_without_original_pdf(case):
    paper, source, build, unregister = case
    service = build(); document = service.open('a', paper)['document']
    service.locate_bundle = lambda p: source.parent
    def no_source(p): raise FileNotFoundError('original PDF removed')
    service.locate = no_source
    source.unlink()
    assert service.read('a', document)['annotations'] == []
    service.delete_with(paper, unregister)
    assert service.recover_delete(paper) == 'done'


def test_commit_serializes_active_replacement(case, monkeypatch):
    from management_portal.paper_reader_annotations import AnnotationStore, Conflict
    paper, source, build, _ = case
    service = build(); first = service.open('a', paper)['document']
    source.write_bytes(b'%PDF-1.4\nreplacement')
    second = service.open('a', paper)['document']
    owner, _ = service._annotations('a', second)
    entered, release, replacing = threading.Event(), threading.Event(), threading.Event()
    original = AnnotationStore.commit
    def slow(store, actor, payload):
        entered.set(); assert release.wait(10)
        return original(store, actor, payload)
    monkeypatch.setattr(AnnotationStore, 'commit', slow)
    batch = {**second, 'operation_id': 'race', 'expected_revision': '0',
             'format': 'zotero-reader-v1', 'upserts': [], 'deletes': []}
    def replace():
        replacing.set()
        return owner.rollback('a', paper_id=paper, attachment_id=first['attachment_id'],
                              version=first['attachment_version'], expected_version=second['attachment_version'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        saving = pool.submit(service.commit, 'a', batch)
        assert entered.wait(5)
        replacement = pool.submit(replace)
        assert replacing.wait(5)
        # Confirm the competing transaction is blocked on the authoritative DB lock.
        import time
        deadline = time.monotonic() + 5
        blocked = False
        while time.monotonic() < deadline:
            with owner.database.read() as db:
                blocked = bool(db.execute("SELECT 1 FROM pg_stat_activity WHERE wait_event_type='Lock' AND query LIKE %s LIMIT 1", ('SELECT pg_advisory_xact_lock%',)).fetchone())
            if blocked: break
        release.set()
        assert blocked
        assert saving.result()['revision'] == '1'
        replacement.result()
    with pytest.raises(Conflict, match='active PDF version changed'):
        service.commit('a', {**batch, 'operation_id': 'after-replacement', 'expected_revision': '1'})
    assert service.read('a', second)['revision'] == '1'


def test_production_lookup_is_registered_only(tmp_path, monkeypatch):
    from management_portal import paper_store, paper_detail
    from management_portal.paper_reader_service import production_service
    pdf = tmp_path / 'paper.pdf'; pdf.write_bytes(b'%PDF-1.4')
    calls = []
    def lookup(paper, **options):
        calls.append(options)
        return tmp_path / 'manifest.json', {'paper_id': paper}
    def forbidden(*args, **kwargs): raise AssertionError('legacy lookup/scan')
    monkeypatch.setattr(paper_store.PAPER_STORE, 'manifest_for_paper_id', lookup)
    monkeypatch.setattr(paper_store.PAPER_STORE, 'manifest_to_paper', lambda m, p: {'_store_root': str(tmp_path), '_files': {'pdf': 'paper.pdf'}})
    monkeypatch.setattr(paper_store, '_paper_by_id', forbidden)
    monkeypatch.setattr(paper_detail, 'resolve_asset', forbidden)
    monkeypatch.setattr(paper_store.PAPER_STORE, 'manifest_paths', forbidden)
    service = production_service()
    assert service.locate('paper_0123456789ab') == (tmp_path, pdf)
    assert service.locate_bundle('paper_0123456789ab') == tmp_path
    assert calls == [{'allow_scan': False, 'registered_only': True}] * 2
