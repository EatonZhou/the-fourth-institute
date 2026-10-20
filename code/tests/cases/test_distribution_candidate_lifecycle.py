from contextlib import nullcontext
from copy import deepcopy
import json

import pytest

from management_portal.distribution import candidates
from management_portal.distribution.candidate_records import current_records
from management_portal.distribution.resource_dependencies import resolve
from tests.cases.test_distribution_resource_dependencies import Source as DependencySource, selected, KNOWLEDGE


class Source:
    def __init__(self, records, tables):
        self.records, self.tables, self.queries = records, tables, []

    def rows(self, schema, table, *, predicates):
        return [r for r in self.tables.get((schema, table), [])
                if any(r.get(k) in ids for k, ids in predicates.items())]

    def read_index(self, **filters):
        self.queries.append(filters)
        assert filters, 'Candidate reads must bound the index query'
        records = {k: v for k, v in self.records.items()
                   if k in filters.get('resource_ids', self.records)
                   and (not filters.get('resource_type') or v['resource_type'] == filters['resource_type'])
                   and (not filters.get('query') or filters['query'] in (k+' '+v['title']).lower())}
        return current_records(self, records)


@pytest.mark.parametrize('json_text', [False, True])
def test_deleted_missing_and_retired_are_consistent_before_pagination(monkeypatch, json_text):
    ids = ['lineage_'+c*12 for c in 'abcdef']
    records = {rid: {'resource_id':rid, 'resource_type':'lineage', 'title':rid} for rid in ids}
    source = Source(records, {
        ('lineage','lineages'):[{'lineage_id':rid, 'deleted_at':'2026-01-01' if n==1 else None}
                              for n,rid in enumerate(ids) if n!=2],
        ('resource_registry','retired_identities'):[{'resource_id':ids[3]}],
        ('resource_registry','registry_documents'):[{'collection':'lineage','document_id':ids[4],
                                                    'document_json':json.dumps({'status':'deleted'}) if json_text else {'status':'deleted'}}]})
    monkeypatch.setattr(candidates, 'read_snapshot', lambda: nullcontext(source))
    page = candidates.candidate('SearchResources', {'resource_type':'lineage','limit':1})
    assert page['total']==2 and page['items'][0]['resource_id']==ids[0] and page['next_cursor']=='1'
    last = candidates.candidate('SearchResources', {'resource_type':'lineage','limit':1,'cursor':'1'})
    assert last['total']==2 and last['items'][0]['resource_id']==ids[5] and last['next_cursor']==''
    results = candidates.candidate('resolve_many', {'addresses':ids})['results']
    assert [r['ok'] for r in results]==[True,False,False,False,False,True]
    for rid in ids[1:5]:
        for op,args in [('ResolveResource',{'address':rid}),('GetLineage',{'lineage_id':rid}),('read',{'address':rid})]:
            with pytest.raises(KeyError): candidates.candidate(op,args)
    assert candidates.candidate('resolve',{'address':ids[0]})['resource']['resource_id']==ids[0]
    assert source.queries[-1]=={'resource_ids':[ids[0]]}
    assert records[ids[1]].get('attributes') is None  # No source index repair.
    source.tables['lineage','lineages'][0]['deleted_at']='2026-02-01'
    with pytest.raises(KeyError): candidates.candidate('resolve',{'address':ids[0]})


def test_registered_file_disappearance_and_workspace_authority(tmp_path):
    path=tmp_path/'idea.md';path.write_text('idea')
    records={'idea_x':{'resource_type':'idea','source':{'path':str(path)}},
             'workspace_x':{'resource_type':'workspace','source':{'path':str(tmp_path)}}}
    source=Source(records,{})
    assert set(current_records(source,records))=={'idea_x'}
    path.unlink()
    assert current_records(source,records)=={}


def test_missing_required_lineage_is_a_gap_not_a_selectable_structure():
    source=DependencySource()
    source.data['lineage','lineages']=[{**r,'deleted_at':'2026-01-01'} if r['lineage_id']==KNOWLEDGE else r
                                     for r in source.data['lineage','lineages']]
    result=resolve(selected('decision'),source)
    assert not result['valid']
    assert any(g['id']==KNOWLEDGE and 'missing' in g['reason'] for g in result['gaps'])


def test_project_registry_and_repo_canonical_root_are_authoritative(tmp_path):
    records = {'live': {'resource_type':'project'}, 'removed': {'resource_type':'project'},
               'repo_x': {'resource_type':'repo', 'source':{'path':str(tmp_path/'old')},
                          'attributes':{'canonical_checkout':str(tmp_path)}}}
    source = Source(records, {('resource_registry','registry_documents'):[
        {'collection':'project_registry','document_id':'projects',
         'document_json':json.dumps({'projects':[{'id':'live'}]})}]})
    assert set(current_records(source,records)) == {'live','repo_x'}
