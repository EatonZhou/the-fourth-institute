from copy import deepcopy
from tests.cases.isa_test_support import ok
from tests.cases.test_family_familize import charters, future_creation_store, lineage, graph, resident
from management_portal.society_service.templates import family_graph, builtins, structure_layout


def test_role_menus_and_bindings_are_derived_from_the_source_without_runtime_ids(lineage):
    sources=[t for t in ok('ListTemplates')['templates'] if t['kind']=='role']
    for source in sources:
        before=deepcopy(source['body'])
        detail=ok('GetTemplate',{'template_id':source['template_id']})['template']
        view=detail['editor']['graph']['body'];meta=view['presentation']
        assert meta['level']=='role'
        assert set(meta['residents'])=={source['body']['role_key']+'_resident'}
        owner=next(iter(meta['residents'].values()))
        assert {d['key'] for d in owner['duties']}=={d['key'] for d in source['body']['duties']}
        assert [m['prompt'] for m in owner['memories']]==[m['prompt'] for m in source['body']['memories']]
        assert owner['source']['template_id']==source['template_id']
        assert {b['key'] for b in meta['bindings'].values()}==set(source['body']['bindings'])
        assert all(b['direction'] in {'in','out','both'} for b in meta['bindings'].values())
        assert any(b['key']=='members' and b['cardinality']=='many' for b in meta['bindings'].values())
        assert set(meta['residents']).isdisjoint(meta['bindings'])
        assert not any(r['from']=='lineage_leader' for r in view['roles'])
        assert ok('GetTemplate',{'template_id':source['template_id']})['template']['body']==before


def test_family_placement_uses_the_same_composed_graph_and_external_member_anchor(lineage,charters):
    source=ok('GetTemplate',{'template_id':builtins.template_id('family')})['template']
    view=source['editor']['graph']['body'];meta=view['presentation']
    assert set(meta['residents'])=={'facade_resident','router_resident','steward_resident'}
    for key in ('router','steward'):
        assert meta['residents'][key+'_resident']['source']['template_id']==source['body']['role_templates'][key]
    assert meta['bindings']['member_resident']['cardinality']=='many'
    member=resident(lineage,'Bound existing member')
    before=deepcopy(graph(lineage));point={'x':63,'y':42}
    result=ok('ApplyTemplate',{'lineage_id':lineage,'template_id':source['template_id'],
        'target':{'kind':'resident','resident_ids':[member],**point}})
    after=graph(lineage);parent=next(b for b in after['function_blocks'] if b.get('family'))
    expected=structure_layout.placed(view,point)
    assert parent['family']['members']==[member]
    assert next(n for n in after['nodes'] if n['id']==member)==next(n for n in before['nodes'] if n['id']==member)
    for key in ('facade','router','steward'):
        node=next(n for n in after['nodes'] if n['id']==parent['family'][key])
        assert {axis:node[axis] for axis in ('x','y')}==expected[key+'_resident']
    assert len(after['nodes'])==len(before['nodes'])+3


def test_duty_projection_keeps_primitives_and_explicit_external_bindings(lineage):
    source=ok('GetTemplate',{'template_id':builtins.template_id('family_review')})['template']
    view=source['editor']['graph']['body'];meta=view['presentation']
    assert meta['level']=='duty' and not meta['residents'] and not meta['owners']
    assert view['components'] and view['wires']
    assert meta['bindings']['steward_resident']['key']=='target'
    assert next(r for r in view['roles'] if r['key']=='steward_resident')['from']=='target'
