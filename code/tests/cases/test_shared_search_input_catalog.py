from __future__ import annotations

from copy import deepcopy
import json

import pytest

from management_portal import semantic_search_store, shared_search_adapter as adapter
from tests.cases.test_shared_search_adapter import make_surface_jobs


JOB = {'surface': 'paper', 'query': '多智能体协作与任务规划'}


def records(count, text='multi-agent task planning'):
    return [{'paper_id': f'paper_{index:012x}', 'title': f'Paper {index}', 'abstract': text}
            for index in range(count)]


def content(prompt):
    return json.loads(prompt.split('\n', 1)[1])


@pytest.mark.parametrize('count', [0, 193, 3419])
def test_complete_catalog_reaches_one_input_without_count_or_size_ceiling(count):
    """完整候选目录以单次输入提交且不受数量与大小限制"""
    catalog = records(count)
    for index, row in enumerate(catalog):
        row['abstract'] = ('multi-agent planning and collaboration. ' * 24 if index % 3 == 0
                           else '规划与协作🧠' * 120 if index % 3 == 1 else 'short')
    original = deepcopy(catalog)
    prompts = adapter.candidate_prompts(JOB, catalog)
    assert len(prompts) == 1
    assert content(prompts[0]) == {**JOB, 'candidates': original}
    assert catalog == original
    if count:
        assert len(prompts[0].encode('utf-8')) > 96_000


def test_large_single_candidate_preserves_long_fields_lists_and_unicode():
    """超大单个候选完整保留长文本、列表与中文字符"""
    row = {'paper_id': 'paper_0123456789ab', 'abstract': '🧠中文' * 20_000,
           'title': '"quoted" \\ title\n换行',
           'tags': ['research' * 100 + str(index) for index in range(75)]}
    assert len(json.dumps(row, ensure_ascii=False).encode('utf-8')) > 96_000
    prompts = adapter.candidate_prompts(JOB, [row])
    assert len(prompts) == 1
    assert content(prompts[0])['candidates'] == [row]


def test_query_overhead_does_not_trigger_catalog_rejection_or_truncation():
    """查询自身过长时不拒绝或截断候选输入"""
    job = {**JOB, 'query': 'x' * 96_000 + '\n"中文"🧠'}
    assert content(adapter.candidate_prompts(job, [])[0]) == {**job, 'candidates': []}


def test_catalog_still_filters_unapproved_fields_types_and_duplicate_ids():
    """候选目录过滤未批准字段、错误类型和重复标识"""
    catalog = [{'paper_id': 'paper_0123456789ab', 'title': 'allowed', 'year': 2026,
                'content': {'instruction': 'not a string'}, 'summary': True,
                'tags': ['keep', 42], 'private_token': 'not sent'},
               {'paper_id': 'paper_0123456789ab', 'title': 'duplicate'}, {'title': 'no ID'}]
    assert content(adapter.candidate_prompts(JOB, catalog)[0])['candidates'] == [
        {'paper_id': 'paper_0123456789ab', 'title': 'allowed', 'year': '2026', 'tags': ['keep']}]


def test_snapshot_freezes_complete_input_without_budget_metadata(tmp_path, monkeypatch):
    """快照冻结完整输入且不再保存预算元数据"""
    jobs = make_surface_jobs('paper', tmp_path, monkeypatch)
    catalog = records(110, 'research ' * 100)
    jobs.candidates['alice'] = catalog
    job = jobs.create('alice')
    payload = adapter._input_snapshot(job, catalog)
    assert 'input_budget' not in payload
    assert len(payload['prompts']) == 1
    before = semantic_search_store.stored_run_input(job['search_id'])
    assert before == payload
    monkeypatch.setattr(adapter, 'candidate_prompts', lambda *_: pytest.fail('frozen plan was replanned'))
    recovered = adapter._input_snapshot(job, catalog + records(1, 'new'))
    assert recovered == payload and semantic_search_store.stored_run_input(job['search_id']) == before


def test_frozen_multibatch_input_remains_valid_with_historical_budget_metadata():
    """历史预算元数据下的冻结多批次输入仍可读取"""
    catalog = records(3)
    prompts = [adapter.candidate_prompt(JOB, catalog[:2]), adapter.candidate_prompt(JOB, catalog[2:])]
    payload = {'catalog': catalog, 'prompts': prompts, 'input_budget': {
        'schema': 'shared_search_input_budget.v2', 'max_characters': 96_000,
        'max_utf8_bytes': 96_000, 'max_candidates': 96}}
    assert adapter._snapshot_batches(JOB, payload) == [catalog[:2], catalog[2:]]
    assert payload['prompts'] == prompts


@pytest.mark.parametrize('change', ['omit', 'alter', 'query', 'surface'])
def test_frozen_input_still_rejects_incomplete_or_changed_candidates(change):
    """冻结输入拒绝候选遗漏、内容变更或查询条件变化"""
    catalog = records(3)
    altered = deepcopy(catalog)
    job = dict(JOB)
    if change == 'omit':
        altered.pop()
    elif change == 'alter':
        altered[0]['abstract'] = 'changed'
    else:
        job[change] = 'knowledge' if change == 'surface' else 'changed query'
    prompt = adapter.candidate_prompt(job, altered)
    with pytest.raises(ValueError, match='complete candidate catalog|query or candidates changed'):
        adapter._snapshot_batches(JOB, {'catalog': catalog, 'prompts': [prompt]})
