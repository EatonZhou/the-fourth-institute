"""Recovery evidence never takes authority from a caller's exported JSON.

:module: resources.lifecycle
:importance: P1
"""
from copy import deepcopy
import json

import pytest

from management_portal.resources.registration import ResourceRegistrationStore


ARTICLE = 'article_111111111111'
RESIDENT = 'resident_222222222222'
ACTOR = 'resident_333333333333'
FIELDS = {'path': '/project/old/paper', 'project_ids': ['Project'],
          'primary_file': 'main.tex', 'title': 'Original'}
OPERATION = 'original-registration'
HASH = 'a' * 64
SELECTOR = {'resident_id': RESIDENT, 'ordinal': 224, 'tool_item_id': 'tool_original',
            'expected_hash': HASH, 'tool_index': 0}


def history_page():
    receipt = {'schema': 'tfi.hatg-instruction-result.v2', 'instruction': 'RegisterResource',
               'instruction_id': 'inst_res_original', 'status': 'committed', 'result': {
                   'resource_id': ARTICLE, 'resource': {'resource_id': ARTICLE, 'type': 'article',
                       'schema': 'tfi.article.v1', 'title': 'Original', 'project_ids': ['Project'],
                       'canonical_root': FIELDS['path'], 'primary_file': 'main.tex',
                       'created_at': '2026-11-09T19:50:30Z'}}}
    return {'resident_id': RESIDENT, 'identity': {'resident_id': RESIDENT, 'deleted_at': '2026-11-18T02:20:29Z'},
            'section': 'tool_evidence', 'items': [{
                'ordinal': 224, 'tool_index': 0, 'tool_item_id': 'tool_original',
                'content_sha256': HASH, 'format': 'canonical_tool', 'segment_type': 'tool',
                'status': 'completed', 'tool_name': 'mcp__portal__hatg_instruction',
                'input_text': json.dumps({'arguments': {'instruction': 'RegisterResource',
                    'arguments': {'resource_type': 'article', 'fields': FIELDS}, 'idempotency_key': OPERATION}}),
                'output_text': json.dumps({'output': [{'type': 'text', 'text': json.dumps(receipt)}]}),
                'input_omitted': False, 'output_omitted': False}]}


@pytest.fixture
def evidence(monkeypatch):
    from management_portal.resources import article_restore
    from management_portal.society_service import resident_deleted_history
    page = history_page()
    calls = []
    def read(resident_id, actor, **selectors):
        calls.append((resident_id, actor, selectors))
        return deepcopy(page)
    monkeypatch.setattr(resident_deleted_history, 'read_history', read)
    store = ResourceRegistrationStore()
    store.reserve('article', FIELDS, actor=RESIDENT, operation_id='RegisterResource:' + OPERATION, allocate=lambda: ARTICLE)
    store.complete(ARTICLE)
    store.forget(ARTICLE)
    return article_restore, page, calls


def read(evidence, **changes):
    module, _, _ = evidence
    return module.read_registration_preimage(article_id=ARTICLE, actor=ACTOR, **(SELECTOR | changes))


def test_original_service_result_and_deleted_ledger_agree(evidence):
    result = read(evidence)
    assert result['record']['resource_id'] == ARTICLE
    assert result['record']['created_at'] == '2026-11-09T19:50:30Z'
    assert 'created_by' not in result['record'] and 'acl' not in result['record']
    assert result['missing'] == ['created_by', 'acl']
    assert result['registration_actor'] == RESIDENT
    assert evidence[2] == [(RESIDENT, ACTOR, {'section': 'tool_evidence', **{k: v for k, v in SELECTOR.items() if k != 'resident_id'}})]


@pytest.mark.parametrize('field,value', [
    ('segment_type', 'message'), ('status', 'failed'), ('format', 'native_tool'),
    ('content_sha256', 'b' * 64), ('ordinal', 225), ('tool_item_id', 'other'),
    ('tool_name', 'Bash'), ('input_omitted', True), ('output_omitted', True),
])
def test_only_exact_unomitted_canonical_tool_is_evidence(evidence, field, value):
    evidence[1]['items'][0][field] = value
    with pytest.raises(ValueError):
        read(evidence)


@pytest.mark.parametrize('fault', ['not_committed', 'other_id', 'different_project', 'different_path', 'different_primary'])
def test_receipt_is_checked_against_original_request(evidence, fault):
    item = evidence[1]['items'][0]
    wrapper = json.loads(item['output_text'])
    receipt = json.loads(wrapper['output'][0]['text'])
    if fault == 'not_committed':
        receipt['status'] = 'failed'
    elif fault == 'other_id':
        receipt['result']['resource_id'] = 'article_444444444444'
    else:
        key, value = {'different_project': ('project_ids', ['Another']),
                      'different_path': ('canonical_root', '/elsewhere'),
                      'different_primary': ('primary_file', 'different.tex')}[fault]
        receipt['result']['resource'][key] = value
    wrapper['output'][0]['text'] = json.dumps(receipt)
    item['output_text'] = json.dumps(wrapper)
    with pytest.raises(ValueError):
        read(evidence)


def test_history_permission_failure_is_not_replaced_by_article_authority(evidence, monkeypatch):
    from management_portal.society_service import resident_deleted_history
    def denied(*args, **kwargs):
        raise PermissionError('current history/AMA permission denied')
    monkeypatch.setattr(resident_deleted_history, 'read_history', denied)
    with pytest.raises(PermissionError, match='history/AMA'):
        read(evidence)


@pytest.mark.parametrize('fault', ['missing', 'actor', 'payload', 'type', 'resource', 'unfinished', 'not_deleted'])
def test_registration_ledger_must_independently_match(evidence, fault):
    store = ResourceRegistrationStore()
    table = 'resource_registration_operations'
    # Isolated fixture DB: corrupt one field to model independent conflicting proof.
    with store._database.transaction() as conn:
        if fault == 'missing':
            conn.execute(f'DELETE FROM {table}')
        else:
            field, value = {'actor': ('actor', ACTOR), 'payload': ('fingerprint', 'f' * 64),
                'type': ('resource_type', 'repo'), 'resource': ('resource_id', 'article_444444444444'),
                'unfinished': ('completed', False), 'not_deleted': ('deleted', False)}[fault]
            conn.execute(f'UPDATE {table} SET {field}=%s', (value,))
    with pytest.raises((KeyError, ValueError)):
        read(evidence)


def test_exported_client_preimage_is_not_an_input(evidence):
    with pytest.raises(TypeError):
        read(evidence, registration_preimage=history_page())


def test_current_article_registration_can_have_no_generic_ledger(tmp_path):
    """Exercise the real Article provider; missing ledger is a supported legacy fact."""
    from management_portal.resources import ResourceService
    from management_portal.resources.providers import article
    from management_portal.article_registration import unregister_article
    from tests.cases.registry_support import write_projects

    project = tmp_path / 'project'
    manuscript = project / 'paper'
    manuscript.mkdir(parents=True)
    (manuscript / 'main.tex').write_text(
        '\\documentclass{article}\n\\title{Legacy Registration}\n'
        '\\begin{document}Original\\end{document}\n')
    write_projects({'projects': [{'id': 'RecoveryProject', 'path': str(project), 'status': 'active'}]})
    fields = {'path': str(manuscript), 'project_ids': ['RecoveryProject'], 'primary_file': 'main.tex'}
    from types import SimpleNamespace
    projected = []
    service = ResourceService(index_provider=SimpleNamespace(
        project_registered=lambda kind, record: projected.append((kind, record['resource_id']))))
    article.install_mutation_provider(service)
    record = service.register('article', fields, actor=RESIDENT,
                              operation_id='RegisterResource:legacy-article')
    resource_id = record['resource_id']
    assert resource_id.startswith('article_')
    assert projected == [('article', resource_id)]
    unregister_article(resource_id, actor=ACTOR)
    with pytest.raises(KeyError, match='registration ledger is missing'):
        ResourceRegistrationStore().verify_deleted('article', fields, actor=RESIDENT,
            operation_id='RegisterResource:legacy-article', resource_id=resource_id)
