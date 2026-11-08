"""PDF bytes are checked before publication, without changing Creation semantics.

:design: decision_528bf9985705
"""
import io
from unittest import mock

import pytest

from management_portal import paper_registration, paper_store
from management_portal.resources.registration import ResourceRegistrationStore
from tests.cases.test_paper_entry_idempotency import intents


@pytest.fixture
def store(tmp_path, monkeypatch):
    result = paper_store.PaperStore(tmp_path / 'papers')
    result.ensure()
    monkeypatch.setattr(paper_store, 'ALLOWED_FILE_ROOTS', [tmp_path])
    monkeypatch.setattr(paper_store, 'PAPER_STORE', result)
    monkeypatch.setattr(paper_registration, 'PAPER_STORE', result)
    monkeypatch.setattr(paper_store.time, 'sleep', lambda _: None)
    return result


@pytest.mark.parametrize('entry', ['public', 'store'])
@pytest.mark.parametrize('data', [b'<!DOCTYPE html><title>Sign in</title>', b''])
def test_invalid_local_pdf_is_rejected_before_creation(store, tmp_path, entry, data):
    source = tmp_path / 'input.pdf'
    source.write_bytes(data)
    callback = paper_registration.register_paper if entry == 'public' else store.register
    with (
        mock.patch.object(ResourceRegistrationStore, 'reserve', wraps=ResourceRegistrationStore().reserve) as reserve,
        mock.patch.object(paper_registration, '_start_summary_task') as summary,
        mock.patch.object(paper_registration, '_start_assets_task') as assets,
        mock.patch.object(paper_registration, 'get_paper_detail', side_effect=lambda paper_id: {'id': paper_id}),
    ):
        with pytest.raises(ValueError, match='does not look like a PDF'):
            callback({'title': 'Invalid PDF', 'pdf_path': str(source)}, 'alice')
        reserve.assert_not_called()
        summary.assert_not_called()
        assets.assert_not_called()
    assert not list(store.root.rglob('manifest.json'))
    assert not list(store.root.rglob('paper.pdf'))


@pytest.mark.parametrize('failure, error', [
    (b'<!DOCTYPE html><title>Access denied</title>', 'remote PDF did not return PDF bytes'),
    (b'', 'empty response'),
    (OSError('upstream fixture unavailable'), 'upstream fixture unavailable'),
])
def test_remote_failure_keeps_missing_identity_and_error(store, failure, error):
    def response(*args, **kwargs):
        if isinstance(failure, Exception):
            raise failure
        return io.BytesIO(failure)

    payload = {'title': 'Remote failure', 'pdf_path': 'https://example.test/paper.pdf',
               'status': 'registered'}
    with mock.patch.object(paper_store.urllib.request, 'urlopen', side_effect=response) as download:
        paper = store.register(payload, 'alice', operation_id='pdf-ingestion')
        assert paper['status'] == 'pdf-missing'
        path, manifest = store.manifest_for_paper_id(paper['id'])
        assert manifest['source']['pdf_url'] == payload['pdf_path']
        assert manifest['source']['pdf_download_error'] == 'download failed after 3 attempts: ' + error
        assert not (path.parent / 'paper.pdf').exists()
        assert not (path.parent / 'paper.pdf.download').exists()
        assert download.call_count == 3
        row, = intents()
        assert row['completed'] and row['actor'] == 'alice'
        assert store.register(payload, 'alice', operation_id='pdf-ingestion')['id'] == paper['id']
        assert download.call_count == 3
        assert len(intents()) == 1


@pytest.mark.parametrize('remote', [False, True])
def test_pdf_and_plain_analysis_remain_accepted(store, tmp_path, remote):
    data = b'%PDF-1.7\nfixture'
    analysis = tmp_path / 'analysis.md'
    analysis.write_text('# Plain analysis\n')
    source = tmp_path / 'input.pdf'
    source.write_bytes(data)
    with mock.patch.object(paper_store.urllib.request, 'urlopen', return_value=io.BytesIO(data)):
        paper = store.register({'title': 'Valid PDF', 'pdf_path': 'https://example.test/file' if remote else str(source),
                                'analysis_path': str(analysis)}, 'alice')
    path, _ = store.manifest_for_paper_id(paper['id'])
    assert (path.parent / 'paper.pdf').read_bytes() == data
    assert (path.parent / 'analysis.md').read_bytes() == analysis.read_bytes()
    assert paper['created_by'] == 'alice'


def test_copied_local_bytes_are_validated_before_publication(store, tmp_path):
    source = tmp_path / 'input.pdf'
    source.write_bytes(b'%PDF-1.7\nfixture')

    def changed_source(src, target):
        target.write_bytes(b'<html>source changed during copy</html>')

    with mock.patch.object(paper_store.shutil, 'copy2', side_effect=changed_source):
        with pytest.raises(ValueError, match='does not look like a PDF'):
            store.register({'title': 'Changing source', 'pdf_path': str(source)}, 'alice')
    assert not list(store.root.rglob('paper.pdf'))
    assert not any(row['completed'] for row in intents())
