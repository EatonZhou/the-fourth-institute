"""Feature writes, clocks and capacity eviction against isolated PostgreSQL."""
from types import SimpleNamespace
import json

from management_common.directory_retention import RetentionStore
from tests.cases.test_retention_consumers import artifact


def test_run_finish_removes_background_artifact_owned_by_this_run(tmp_path):
    from management_common.resource_lifecycle import LifecycleStore, lifecycle_scope
    from management_portal.test_framework.run_cleanup import finish_run
    run_id = 'testrun_0123456789abcdef'
    scope = LifecycleStore().open(str(tmp_path), actor='alice', run_id=run_id, case_id='retention')
    path = tmp_path / 'background'
    path.mkdir()
    with lifecycle_scope(scope, actor='alice'):
        RetentionStore().declare(path, category='background', owner_id='collector-test')
    result = finish_run(run_id)
    assert result['status'] == 'complete'
    assert not path.exists()


def test_article_compile_cache_and_pdf_keep_article_owner(tmp_path, monkeypatch):
    from management_portal.article_build import _ArticleBuild
    build = object.__new__(_ArticleBuild)
    build.article_id = 'article_111111111111'
    build.cache_root, build.output = tmp_path / 'cache', tmp_path / 'article.pdf'
    def compile(_timeout):
        build.cache_root.mkdir()
        (build.cache_root / 'article.aux').write_bytes(b'cache')
        build.output.write_bytes(b'%PDF fake build')
        return 'compiled'
    monkeypatch.setattr(build, '_compile_owned_cache', compile)
    assert build._compile(10) == 'compiled'
    assert artifact(build.cache_root)['owner_id'] == build.article_id
    assert artifact(build.output)['bytes'] == build.output.stat().st_size
    retention = RetentionStore()
    assert retention.sweep(cache_capacities={'article': 0})['queued'] == 2
    retention.queue.run_pending()
    assert not build.cache_root.exists() and not build.output.exists()


def test_paper_pdf_and_text_cache_writes_are_measured_and_evictable(tmp_path, monkeypatch):
    from io import BytesIO
    from management_portal import paper_store, paper_pdf
    monkeypatch.setattr(paper_store, 'DATA_ROOT', tmp_path)
    monkeypatch.setattr(paper_store.urllib.request, 'urlopen', lambda *a, **k: BytesIO(b'%PDF' + b'a'*21000))
    pdf = paper_store._local_pdf_for_paper({'id': 'paper_111111111111', 'pdf_path': 'https://invalid.example/paper.pdf'})
    monkeypatch.setattr(paper_pdf, '_poppler_command', lambda name: name)
    calls = []
    def extract(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=b'paper text '*150, stderr=b'')
    monkeypatch.setattr(paper_pdf.process_runtime, 'run_process', extract)
    text = tmp_path / 'text.txt'
    for _ in range(2):
        assert paper_pdf._extract_pdf_text_file(pdf, cache_path=text, owner_id='paper_111111111111', raw=True)
    assert len(calls) == 1 and '-raw' in calls[0]
    assert artifact(text)['owner_id'] == 'paper_111111111111'
    retention = RetentionStore()
    assert retention.sweep(cache_capacities={'paper_pdf': 0, 'paper_text': 0})['queued'] == 2
    retention.queue.run_pending()
    assert not text.exists() and not pdf.exists()


def test_ccf_result_cache_tracks_reads_and_capacity(tmp_path, monkeypatch):
    from management_portal import paper_ccf_enrichment as ccf
    monkeypatch.setattr(ccf, 'CCF_ENRICHMENT_CACHE_ROOT', tmp_path)
    paper = {'title': 'test paper'}
    path = ccf._ccf_enrichment_cache_path(paper)
    ccf._write_ccf_enrichment_state(path, {'status': 'done', 'summary_zh': 'summary'})
    first = artifact(path)
    assert first['bytes'] > 0
    assert ccf._read_ccf_enrichment(paper)['status'] == 'done'
    assert artifact(path)['last_access'] >= first['last_access']
    retention = RetentionStore()
    assert retention.sweep(cache_capacities={'paper_ccf': 0})['queued'] == 1
    assert ccf._read_ccf_enrichment(paper) is None  # Pending means a cache miss.
    retention.queue.run_pending()
    assert not path.exists()


def test_ccf_interrupted_runtime_requests_require_process_evidence(tmp_path, monkeypatch):
    from management_portal import paper_ccf_enrichment as ccf, business_tasks
    monkeypatch.setattr(ccf, 'CCF_ENRICHMENT_CACHE_ROOT', tmp_path)
    monkeypatch.setattr(business_tasks, 'process_is_alive', lambda owner: owner['pid'] == 1)
    retention = RetentionStore()
    for name, owner in (('live', {'pid': 1}), ('dead', {'pid': 2}), ('unknown', None)):
        path = tmp_path / (name + '.request.json')
        path.write_text(json.dumps({'process_owner': owner}))
        retention.declare(path, category='runtime_tmp', owner_id='paper-ccf:' + name)
    report = ccf.cleanup_interrupted_requests()
    assert report['queued'] == [str(tmp_path / 'dead.request.json')]
    assert report['unresolved'] == [str(tmp_path / 'unknown.request.json')]
    retention.queue.run_pending()
    assert not (tmp_path / 'dead.request.json').exists()
    assert (tmp_path / 'live.request.json').exists()
