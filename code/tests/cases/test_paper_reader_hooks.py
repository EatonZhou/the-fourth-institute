import json
from types import SimpleNamespace
from urllib.parse import urlsplit
import pytest

from management_portal import http_paper, http_auth, http_responses, http_request_body, paper_store, web_resources
from management_portal.paper_reader_service import production_service
from management_portal.resources.directory_registrations import register_owned, is_deleted


def setup_paper(tmp_path, monkeypatch):
    paper = 'paper_0123456789ab'
    bundle = tmp_path / 'bundle'; bundle.mkdir()
    (bundle / 'paper.pdf').write_bytes(b'%PDF-1.4\nsynthetic')
    (bundle / 'manifest.json').write_text(json.dumps({'paper_id': paper, 'title': 'Synthetic', 'files': {'pdf': 'paper.pdf'}}))
    register_owned('paper', paper, [bundle])
    store = paper_store.PaperStore(tmp_path)
    monkeypatch.setattr(paper_store, 'PAPER_STORE', store)
    monkeypatch.setattr(store, 'rebuild_fingerprint_index', lambda: {})
    monkeypatch.setattr(store, 'manifest_paths', lambda: pytest.fail('full scan'))
    monkeypatch.setattr(web_resources, '_require_resource', lambda p, a: None)
    return paper, bundle, store


def test_official_routes_to_owner_and_delete(tmp_path, monkeypatch):
    paper, bundle, store = setup_paper(tmp_path, monkeypatch)
    monkeypatch.setattr(http_auth, '_get_agent_api_auth', lambda r: {'type': 'human', 'id': 'user_fixture'})
    monkeypatch.setattr(http_responses, '_serve_json', lambda r, s, b: (s, b))
    monkeypatch.setattr(http_request_body, '_read_request_json', lambda r: r.payload)
    definitions = http_paper.routes(object())
    def call(method, path, payload=None, principal='user_fixture'):
        parsed = urlsplit(path)
        route = next(d for d in definitions if d.method == method and d.path == parsed.path)
        request = SimpleNamespace(headers={'X-Reader-Principal': principal, 'Content-Type': 'application/json'}, payload=payload)
        return route.handler(request, parsed, '')
    status, opened = call('POST', '/paper/api/reader/open', {'paper_id': paper})
    assert status == 200, opened
    doc = opened['document']
    batch = {**doc, 'operation_id': 'hook', 'expected_revision': '0', 'format': 'zotero-reader-v1', 'upserts': [], 'deletes': []}
    assert call('POST', '/paper/api/reader/annotations', batch)[0] == 200
    assert call('POST', '/paper/api/reader/open', {'paper_id': paper})[1]['revision'] == '1'
    assert call('POST', '/paper/api/reader/open', {'paper_id': paper}, 'user_other')[0] == 409
    (bundle / 'paper.pdf').write_bytes(b'%PDF-1.4\nchanged')
    assert call('POST', '/paper/api/reader/open', {'paper_id': paper})[1]['document'] != doc
    assert call('POST', '/paper/api/reader/annotations', {**batch, 'operation_id': 'stale'})[0] == 409
    result = store.delete_paper(paper, actor='user_fixture')
    assert result['paper_id'] == paper and is_deleted(paper)
    assert production_service().recover_pending_deletions() == []
    assert bundle.exists()  # existing cleanup queue alone owns physical removal


def test_unused_delete_never_initializes_reader(tmp_path, monkeypatch):
    paper, bundle, store = setup_paper(tmp_path, monkeypatch)
    service = production_service()
    def tables():
        with service.database.database.read_current() as db:
            return db.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema() AND tablename LIKE %s ORDER BY tablename", ('reader_%',)).fetchall()
    before = tables()
    assert not service.has_data(paper)
    assert store.delete_paper(paper)['paper_id'] == paper
    assert tables() == before
    assert not (bundle / 'reader-attachments').exists()


def test_official_delete_committed_cleanup_retry(tmp_path, monkeypatch):
    from management_portal import paper_reader_annotations
    paper, bundle, store = setup_paper(tmp_path, monkeypatch)
    service = production_service(); service.open('user_fixture', paper)
    original = paper_reader_annotations.purge_paper_records
    def fail(*args): raise RuntimeError('cleanup interrupted after unregister')
    monkeypatch.setattr(paper_reader_annotations, 'purge_paper_records', fail)
    with pytest.raises(RuntimeError, match='cleanup interrupted'):
        store.delete_paper(paper)
    assert is_deleted(paper)
    monkeypatch.setattr(paper_reader_annotations, 'purge_paper_records', original)
    assert production_service().recover_pending_deletions() == [{'paper_id': paper, 'state': 'done'}]
    assert bundle.exists()
