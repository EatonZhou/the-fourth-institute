"""4503 synthetic indexed papers, temporary PostgreSQL and real text-preview I/O."""
import json
import time

from management_portal import paper_library, paper_search, paper_store
from management_portal.resource_index_store import IndexStore
from management_portal.resource_index_records import ChangeSet, resource_record


def test_structured_search_avoids_rejected_document_reads(tmp_path, monkeypatch):
    analysis = tmp_path / 'synthetic-analysis.md'
    analysis.write_text(('synthetic data only\n' * 250) + 'NEEDLE中文', encoding='utf-8')
    store = IndexStore()
    records = [resource_record(resource_id=f'paper_{i:012x}', resource_type='paper',
        adapter='paper', title=f'Title {i:06}', attributes={
            'year': '2026' if i < 3 else '2025', 'venue': 'Synthetic',
            'has_pdf': False, 'has_analysis': True, 'has_summary': False,
            'analysis_path': str(analysis)}) for i in range(4503)]
    store.apply(ChangeSet(adapter='paper', upserts=tuple(records)))
    # ResourceIndex's existing authorization phase stays before pagination/filtering.
    acl_batches = []
    def hidden(actor, rows):
        assert actor == 'fixture_reader'
        acl_batches.append(len(rows))
        return {records[0]['resource_id']}
    monkeypatch.setattr('management_portal.society_service.lineage_access_policy.hidden_private_ids', hidden)
    monkeypatch.setattr('management_portal.resource_index_store.readable_flags', lambda rows, actor: [True] * len(rows))
    queries = []
    original_query = store.query
    def query(**kwargs):
        value = original_query(**kwargs)
        queries.append(len(value['resources']))
        return value
    monkeypatch.setattr(store, 'query', query)
    monkeypatch.setattr(paper_search, 'get_resource_service', lambda: store)
    monkeypatch.setattr(paper_store, 'ALLOWED_FILE_ROOTS', (tmp_path,))
    monkeypatch.setattr(paper_library.paper_favorites, 'favorite_ids', lambda actor: [])
    def forbidden(*a, **k): raise AssertionError('unexpected manifest/map scan')
    monkeypatch.setattr(paper_store.PaperStore, 'manifest_paths', forbidden)
    monkeypatch.setattr(paper_store.PaperStore, 'manifest_map', forbidden)
    reads = []
    original_preview = paper_search._read_text_preview
    def preview(path, limit):
        text = original_preview(path, limit=limit)
        reads.append(len(text.encode('utf-8')))
        return text
    monkeypatch.setattr(paper_search, '_read_text_preview', preview)
    start = time.perf_counter()
    value = paper_library.list_library({'q': 'needle中文', 'year': '2026',
        'compact': '1', 'rows_only': '1', 'limit': '1', 'offset': '1'}, actor='fixture_reader')
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert [p['id'] for p in value['papers']] == [records[2]['resource_id']]
    assert sum(queries) == 4502  # Full catalog projection remains; do not call this SQL pushdown.
    assert sum(acl_batches) >= 4503
    print(json.dumps({'indexed': 4503, 'query_calls': len(queries), 'index_rows': sum(queries),
        'text_reads': len(reads), 'text_utf8_bytes': sum(reads), 'elapsed_ms': round(elapsed_ms, 3)}))
    assert len(reads) == 2


def test_text_casefold_and_unfiltered_search_semantics():
    flags = dict(has_pdf=False, has_analysis=True, has_summary=False)
    papers = [dict(flags, id='first', title='Straße 中文', year='2026', venue='期刊', tags=['GPU']),
              dict(flags, id='second', title='STRASSE 中文', year='2025', venue='OTHER', tags=['CPU'])]
    assert [p['id'] for p in paper_search._filter_papers(papers, {'q':'strasse 中文'})] == ['first', 'second']
    assert [p['id'] for p in paper_search._filter_papers(papers, {'q':'strasse', 'year':'2026','tag':'gpu'})] == ['first']
    assert paper_search._filter_papers(papers, {'q':'missing','year':'2026'}) == []
    assert paper_search._filter_papers(papers, {'year':'2025','venue':'other','analysis':'ready'}) == [papers[1]]
