"""Router's view is complete; routing handoffs use the actual Family binding."""
from copy import deepcopy

import pytest

from management_portal.society_service import family_definition, family_roles
from management_portal.society_service.isa_definition_normalization import normalize_definition
from management_portal.society_service.templates import family_graph, builtins
from tests.cases.isa_test_support import ok, run
from tests.cases.test_family_familize import lineage, charters, future_creation_store  # noqa: F401
from tests.cases.test_family_router_script import _run


@pytest.mark.parametrize('status,state', [('unavailable','waiting_for_steward'),('quota_low','waiting_for_quota_review')])
def test_actual_route_arguments_reach_steward_in_direct_target_branches(lineage,status,state):
    body,parent,roles=family_graph.generate({})
    body=normalize_definition(body)
    by_key={e['key']:e['id'] for e in parent['elements']}
    wire=next(w for w in body['program']['wires'] if w['id']==by_key['route'])
    args={key:value['value'] for key,value in wire['config']['invoke_profiles']['effect']['arguments'].items()}
    script=next(c['config']['script'] for c in body['program']['components'] if c['id']==by_key['router'])
    event={'id':'event_1234567890abcdef','payload':{'resident_id':roles['facade'],
        'message_id':'msg_'+'1'*32,'direct_member_id':roles['member'],'relay_depth':0}}
    results={'GetRelayContext':{'request':{'author_id':'operator'}},'CheckRelayTarget':{'status':status,'reason':'test','remaining_percent':1},
        'DeliverMessage':{'message_id':'msg_'+'2'*32}}
    # The old installed profile omitted this binding. Reproduce its precise
    # failure without sending a live request or touching anyone's conversation.
    old=deepcopy(args);old.pop('steward')
    broken,calls=_run(script,{'arguments':old,'event':event},results)
    assert broken.status=='failed' or any(name=='DeliverMessage' and not value.get('resident_id') for name,value in calls)
    for name,delivery in calls:
        if name=='DeliverMessage' and not delivery.get('resident_id'):
            assert run('DeliverMessage',delivery).error
    result,calls=_run(script,{'arguments':args,'event':event},results)
    assert result.status=='succeeded',result.error
    assert result.outputs['state']==state
    delivery=next(value for name,value in calls if name=='DeliverMessage')
    assert delivery['resident_id']==roles['steward']
    assert delivery['business_ref']==f"family-review/{roles['facade']}/message/{event['payload']['message_id']}"
    assert _run(script,{'arguments':args,'event':event},results)[1]==calls


@pytest.mark.parametrize('composed',[False,True])
def test_old_installed_route_is_patched_in_place_without_rebuilding_runtime(lineage,composed):
    from management_portal.society_service.templates import role_recipes
    recipe={'role_templates':role_recipes.references()} if composed else {}
    if composed:
        ok('ListTemplates')
    body,parent,roles=family_graph.generate(recipe,resolve_roles=composed)
    body=normalize_definition(body)
    by_key={e['key']:e['id'] for e in family_definition.family_elements(body,parent)}
    wire=next(w for w in body['program']['wires'] if w['id']==by_key['route'])
    wire['config']['invoke_profiles']['effect']['arguments'].pop('steward')
    before=deepcopy(body)
    fixed=normalize_definition(body)
    assert body==before
    expected=deepcopy(before)
    route=next(w for w in expected['program']['wires'] if w['id']==by_key['route'])
    route['config']['invoke_profiles']['effect']['arguments']['steward']={'value':roles['steward']}
    assert fixed==expected
    assert normalize_definition(fixed)==fixed
    if composed:
        from management_portal.society_service.templates.family_reconcile import reconcile_in_body
        installed=deepcopy(before)
        current=next(block for block in installed['function_blocks'] if block['id']==parent['id'])
        reconcile_in_body(installed,current,'test')
        route=next(w for w in installed['program']['wires'] if w['id']==by_key['route'])
        assert route['config']['invoke_profiles']['effect']['arguments']['steward']=={'value':roles['steward']}
        assert {w['id'] for w in installed['program']['wires']}=={w['id'] for w in before['program']['wires']}


def test_router_projection_contains_the_real_relay_without_moving_runtime_ownership(lineage):
    router=ok('GetTemplate',{'template_id':builtins.template_id('family_role_router')})['template']
    source=ok('GetTemplate',{'template_id':builtins.template_id('family')})['template']
    before,parent,roles=family_graph.generate(source['body'],resolve_roles=True)
    saved=deepcopy(before)
    view=router['editor']['graph']['body'];meta=view['presentation']
    assert {'router','route_relay'} <= {c['key'] for c in view['components']}
    assert next(w for w in view['wires'] if w['key']=='relay')['source']=='router_resident'
    assert meta['owners']['route_relay']==meta['owners']['relay']=='router_resident'
    assert meta['owners']['facade_inbox']=='facade_resident'
    assert meta['expansion_owners']['facade_inbox']=='router_resident'
    event=next(c for c in view['components'] if c['key']=='facade_inbox')
    assert event['title']=='门面收到消息' and event['config']['event_type']=='resident.message_received'
    assert any(r['kind']=='watch' and r['source']=='facade_resident' and r['target']=='facade_inbox' for r in meta['interactions'])
    assert next(w for w in view['wires'] if w['key']=='route')['source']=='facade_inbox'
    assert {'route_relay','relay'} <= set(meta['residents']['router_resident']['duties'][0]['members'])
    assert any(r['source']=='route_relay' and r['target']=='member_resident' for r in meta['interactions'])
    assert any('格式无效' in r['label'] for r in meta['interactions'] if r['target']=='steward_resident')
    assert not ({'family','member_done'} & {b['key'] for b in meta['bindings'].values()})
    for local in ('facade_resident','router_resident','member_resident'):
        assert local in view['layout']
    family_meta=source['editor']['graph']['body']['presentation']
    assert family_meta['owners']['route_relay']==family_meta['owners']['relay']=='router_resident'
    # View reads and annotations never change the assembled structure or its
    # Family-owned singleton relay identities/definitions.
    scopes=[parent,*family_roles.role_blocks(before,parent)]
    for scope in family_roles.role_blocks(before,parent):
        scopes.extend(family_roles.duties(before,scope))
    family_graph.project_body(before,parent,roles,scopes,source=source)
    assert before==saved
    assert not family_roles.owner('route_relay') and not family_roles.owner('relay')
    assert sum(e.get('key')=='route_relay' for e in parent['elements'])==1
