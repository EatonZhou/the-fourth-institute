from dataclasses import asdict
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from management_portal.paper_reader_attachments import AttachmentOwner
from management_portal.paper_reader_annotations import AnnotationStore, Conflict


@pytest.fixture
def setup(tmp_path):
    context = object()
    def authorize(c, paper, write):
        if c is not context or paper != 'paper_fixture': raise PermissionError('denied')
        return 'test-account'
    owner = AttachmentOwner(root=tmp_path / 'owned', storage_key='test-storage', authorize=authorize)
    source = tmp_path / 'input.pdf'; source.write_bytes(b'%PDF-1.4\nfirst synthetic content')
    return context, owner, source


def ingest(c, owner, source, expected=None):
    return owner.import_pdf(c, paper_id='paper_fixture', source=source, expected_version=expected, operation_id=secrets.token_hex(16))


def test_import_replace_rollback_and_annotations(setup):
    c, owner, source = setup
    first = ingest(c, owner, source)
    store = AnnotationStore(authenticate=lambda context: owner._auth(context, 'paper_fixture', False), resolve_attachment=owner.resolve_attachment)
    payload = dict(**asdict(first), operation_id='save', expected_revision='0', format='zotero-reader-v1', upserts=[dict(id='A', type='note', position=dict(pageIndex=0, rects=[[1, 2, 3, 4]]))], deletes=[])
    store.commit(c, payload)
    source.write_bytes(b'%PDF-1.4\nsecond synthetic content')
    second = ingest(c, owner, source, first.attachment_version)
    assert owner.current(c, 'paper_fixture') == second
    assert first.attachment_id == second.attachment_id
    with owner.open_version(c, asdict(first)) as stream: assert b'first' in stream.read()
    with owner.open_version(c, asdict(second)) as stream: assert b'second' in stream.read()
    assert store.read(c, asdict(second))['annotations'] == []
    assert store.read(c, asdict(first))['annotations'][0]['id'] == 'A'
    owner.rollback(c, paper_id='paper_fixture', attachment_id=first.attachment_id, version=first.attachment_version, expected_version=second.attachment_version)
    assert owner.current(c, 'paper_fixture') == first
    with pytest.raises(Conflict):
        owner.rollback(c, paper_id='paper_fixture', attachment_id=first.attachment_id, version=second.attachment_version, expected_version=second.attachment_version)
    with pytest.raises(PermissionError): owner.resolve_attachment(object(), asdict(first))


@pytest.mark.parametrize('failure', ['prepared', 'published', 'before_commit'])
def test_recover_after_midflight_failure(setup, failure):
    c, owner, source = setup
    operation = secrets.token_hex(16)
    def checkpoint(stage):
        if stage == failure: raise RuntimeError('injected failure')
    owner.checkpoint = checkpoint
    with pytest.raises(RuntimeError):
        owner.import_pdf(c, paper_id='paper_fixture', source=source, expected_version=None, operation_id=operation)
    original = source.read_bytes(); source.unlink()
    # Reconstruct the owner to demonstrate no process-local recovery dependency.
    owner = AttachmentOwner(root=owner.root, storage_key=owner.storage_key, authorize=owner.authorize)
    result = owner.recover(c, operation)
    assert owner.recover(c, operation) == result
    with owner.open_version(c, asdict(result)) as stream: assert stream.read() == original
    with owner.database.read() as db:
        assert db.execute('SELECT count(*) FROM reader_attachment_versions').fetchone()[0] == 1


def test_copy_failure_and_source_change(setup):
    c, owner, source = setup
    def mutate(stage):
        if stage == 'chunk': source.write_bytes(b'%PDF-1.4\nchanged')
    owner.checkpoint = mutate
    with pytest.raises(Conflict, match='source changed'): ingest(c, owner, source)
    assert not list((owner.root / 'staging').iterdir())
    with owner.database.read() as db:
        assert db.execute('SELECT count(*) FROM reader_attachment_heads').fetchone()[0] == 0
    owner.checkpoint = lambda stage: (_ for _ in ()).throw(RuntimeError('copy interrupted'))
    operation = secrets.token_hex(16)
    with pytest.raises(RuntimeError):
        owner.import_pdf(c, paper_id='paper_fixture', source=source, expected_version=None, operation_id=operation)
    with pytest.raises(Conflict, match='failed copy'): owner.recover(c, operation)


def test_recovery_of_interrupted_copy(setup):
    c, owner, source = setup
    operation = secrets.token_hex(16)
    owner.checkpoint = lambda stage: (_ for _ in ()).throw(SystemExit('process interruption'))
    with pytest.raises(SystemExit):
        owner.import_pdf(c, paper_id='paper_fixture', source=source, expected_version=None, operation_id=operation)
    with pytest.raises(PermissionError): owner.recover(object(), operation)
    owner.checkpoint = lambda stage: None
    with pytest.raises(Conflict, match='incomplete copy'): owner.recover(c, operation)
    assert not list((owner.root / 'staging').iterdir())
    assert ingest(c, owner, source)


def test_concurrent_active_cas(setup):
    c, owner, source = setup
    first = ingest(c, owner, source)
    barrier = threading.Barrier(2)
    owner.checkpoint = lambda stage: barrier.wait(5) if stage == 'prepared' else None
    def replace(_):
        try: return ingest(c, owner, source, first.attachment_version)
        except Conflict: return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(replace, range(2)))
    assert sum(result is not None for result in results) == 1
    with owner.open_version(c, asdict(first)) as stream: assert stream.read() == source.read_bytes()


def test_import_operation_binding_and_non_pdf(setup):
    c, owner, source = setup
    operation = secrets.token_hex(16)
    first = owner.import_pdf(c, paper_id='paper_fixture', source=source, expected_version=None, operation_id=operation)
    assert owner.import_pdf(c, paper_id='paper_fixture', source=source, expected_version=None, operation_id=operation) == first
    other = source.with_name('other.pdf'); other.write_bytes(b'%PDF-other')
    with pytest.raises(Conflict, match='binding mismatch'):
        owner.import_pdf(c, paper_id='paper_fixture', source=other, expected_version=None, operation_id=operation)
    other.write_bytes(b'<html>not PDF</html>')
    with pytest.raises(ValueError, match='PDF signature'): ingest(c, owner, other, first.attachment_version)
