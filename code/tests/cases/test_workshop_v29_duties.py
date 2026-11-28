"""v29 task classification: organization changes and forwarded notices differ."""
import pytest
from tests.cases.test_family_familize import charters, future_creation_store, lineage  # noqa: F401
from management_portal.society_service import family_facade, family_tasks
from management_portal.society_service.templates import role_recipes


def test_steward_defaults_have_separate_info_forward_contract():
    duties={row['key']:row for row in role_recipes.definition('steward')['duties']}
    assert set(duties)=={'queue','summary','review','organization','info_forward','recruit','route_review','recovery','quota'}
    assert duties['info_forward']['title']=='门面收到告知时，判断它与哪些成员有关，只向这些成员定向转告'
    assert '名册' in duties['organization']['guide']
    assert '背景' not in duties['organization']['title']


def test_customized_steward_source_adds_info_without_replacing_organization_text():
    from copy import deepcopy
    current=role_recipes.definition('steward');old=deepcopy(current)
    old['duties']=[d for d in old['duties'] if d['key']!='info_forward']
    org=next(d for d in old['duties'] if d['key']=='organization')
    org.update(title='我的初始化职责',guide='自定义组织指南',prompt='自定义组织提示 ${facts}')
    upgraded=role_recipes.add_info_forward(old,current)
    assert next(d for d in upgraded['duties'] if d['key']=='organization')==org
    assert next(d for d in upgraded['duties'] if d['key']=='info_forward')==next(d for d in current['duties'] if d['key']=='info_forward')
    assert role_recipes.add_info_forward(upgraded,current)==upgraded


@pytest.mark.parametrize('trigger',[None,'unknown','route'])
def test_steward_delivery_rejects_missing_or_wrong_duty_before_delivery(monkeypatch,trigger):
    from management_portal.society_service import resident_record_domain
    monkeypatch.setattr(resident_record_domain,'find_resident',lambda _:None)
    with pytest.raises(ValueError,match='task_trigger'):
        family_facade.tell_steward('resident_'+'1'*12,'resident_'+'2'*12,'actor','note',**({'task_trigger':trigger} if trigger else {}))


def test_forwarded_info_explicitly_selects_info_forward_and_preserves_identity(monkeypatch):
    calls=[]
    monkeypatch.setattr(family_facade,'family_steward_id',lambda _:'resident_'+'2'*12)
    monkeypatch.setattr(family_facade,'tell_steward',lambda *a,**kw:calls.append((a,kw)) or {})
    facade={'resident_id':'resident_'+'1'*12};message={'message_id':'msg_'+'3'*32,'content_parts':[{'type':'text','text':'Release时间改为周五'}]}
    family_facade.forward_info(facade,message,'author')
    args,options=calls[0]
    assert options['task_trigger']=='info_forward'
    assert options['task_facts']['content']=='Release时间改为周五'
    assert options['message_id']==family_facade.info_forward_message_id(facade['resident_id'],message['message_id'])
    assert args[:3]==(facade['resident_id'],'resident_'+'2'*12,'author')


def test_existing_role_upgrade_is_additive_and_does_not_restore_withdrawn_duty():
    from copy import deepcopy
    from management_portal.society_service import family_duty_upgrade, family_role_composition, family_roles
    source={'template_id':role_recipes.references()['steward'],'version':5,'body':role_recipes.definition('steward')}
    source['body']['duties']=[d for d in source['body']['duties'] if d['key']!='info_forward']
    parent={'id':'fbi_'+'1'*12,'elements':[],'family':{'steward':'resident_'+'2'*12}}
    body={'function_blocks':[parent],'program':{'components':[],'wires':[]}}
    role,_=family_role_composition.install(body,parent,'steward',source,'author')
    org=next(d for d in family_roles.duties(body,role) if d['duty']['parameters']['family_task']=='organization')
    org['duty']['memory']['prompt']='用户编辑过的组织指南'
    before=deepcopy(body)
    family_duty_upgrade.upgrade(body)
    assert role['role']['source']==before['function_blocks'][1]['role']['source']
    assert org['duty']['memory']['prompt']=='用户编辑过的组织指南'
    assert body['program']==before['program']
    added=next(d for d in family_roles.duties(body,role) if d['duty']['parameters']['family_task']=='info_forward')
    assert len(body['function_blocks'])==len(before['function_blocks'])+1
    upgraded=deepcopy(body);family_duty_upgrade.upgrade(body);assert body==upgraded
    body['function_blocks'].remove(added)
    role['role']['duties'].pop('info_forward')
    role['elements']=[e for e in role['elements'] if e['id']!=added['id']]
    withdrawn=deepcopy(body);family_duty_upgrade.upgrade(body);assert body==withdrawn


def test_family_organization_projection_has_its_own_duties_without_changing_execution(lineage,charters):
    from copy import deepcopy
    from tests.cases.isa_test_support import ok
    from management_portal.society_service.templates import family_graph
    from management_portal.society_service import family_roles
    source=next(t for t in ok('ListTemplates')['templates'] if t.get('builtin_key')=='family')
    body,parent,roles=family_graph.generate(source['body'],resolve_roles=True)
    scopes=[parent,*family_roles.role_blocks(body,parent)]
    for role in family_roles.role_blocks(body,parent):scopes.extend(family_roles.duties(body,role))
    before=deepcopy(body)
    projection=family_graph.project_body(body,parent,roles,scopes,source=source)
    assert body==before
    meta=projection['body']['presentation']
    assert {d['key'] for d in meta['organization']['duties']}=={'summary_registration','role_intake_assurance'}
    assert meta['owners']['summary_check']=='binding_platform'
    assert any(i['source']=='summary_check' and '归纳' in i['label'] for i in meta['interactions'])
    assert all(d['trigger'] for r in meta['residents'].values() for d in r['duties'])
    steward=next(t for t in ok('ListTemplates')['templates'] if t.get('builtin_key')=='family_role_steward')
    assert len(steward['body']['duties'])==9


def test_builtin_duty_and_content_groups_are_consistent(lineage):
    from tests.cases.isa_test_support import ok
    from management_portal.society_service.templates.marketplace import group_for
    rows={t['builtin_key']:t for t in ok('ListTemplates')['templates'] if t.get('builtin_key')}
    assert group_for(rows['agent_loop'])=='organization'
    for key in ('leader_pulse','scheduled_work','fixed_reminder'):
        assert group_for(rows[key])=='duty'
        assert rows[key]['body']['duty']['description']
    assert next(p for p in rows['scheduled_work']['parameters'] if p['name']=='calendar_time')['type']=='time'
    from management_portal.society_service.templates.values import normalize_parameters
    assert normalize_parameters([{'name':'at','type':'time','default':'21:30'}])[0]['default']=='21:30'
    with pytest.raises(ValueError):normalize_parameters([{'name':'at','type':'time','default':'24:00'}])


def test_existing_family_reconciliation_preserves_guides_ids_and_withdrawal(lineage,charters):
    from copy import deepcopy
    from tests.cases.isa_test_support import ok
    from tests.cases.test_family_familize import resident,graph
    from management_portal.society_service import lineage_graph_edits,family_roles,instruction_name_migration
    from management_portal.society_service.templates import familize
    family=ok('Familize',{'lineage_id':lineage,'resident_ids':[resident(lineage,'Migration member')]})
    def before_upgrade(body):
        parent=next(b for b in body['function_blocks'] if b['id']==family['family_id'])
        role=next(r for r in family_roles.role_blocks(body,parent) if r['role']['key']=='steward')
        removed=role['role']['duties'].pop('info_forward');role['role'].pop('contracts',None)
        role['elements']=[e for e in role['elements'] if e['id']!=removed]
        body['function_blocks']=[b for b in body['function_blocks'] if b['id']!=removed]
        org=next(d for d in family_roles.duties(body,role) if d['duty']['parameters']['family_task']=='organization')
        org['duty']['memory']['prompt']='保留我的初始化交接指南'
        org['duty']['parameters']['prompt']='自定义交接 ${facts} ${guide_path} ${guide_version}'
    lineage_graph_edits.commit_lineage_edit(lineage,'sample_admin',before_upgrade,reason='Simulate pre-info-forward Family')
    before=graph(lineage);ids={n['id'] for n in before['nodes']};program=deepcopy(before['program'])
    instruction_name_migration._migrate_lineage(lineage)
    after=graph(lineage);parent=next(b for b in after['function_blocks'] if b['id']==family['family_id'])
    assert after['program']==program
    assert {n['id'] for n in after['nodes']}==ids
    configured=family_tasks.configured(after,parent)
    assert configured['organization']['guide']=='保留我的初始化交接指南'
    assert configured['organization']['prompt'].startswith('自定义交接')
    assert 'info_forward' in configured
    steward=next(r for r in family_roles.role_blocks(after,parent) if r['role']['key']=='steward')
    identity=steward['role']['duties']['info_forward']
    ok('DeleteFunctionBlock',{'lineage_id':lineage,'function_block_id':identity})
    familize.sync_generated_structure(lineage);instruction_name_migration._migrate_lineage(lineage)
    final=graph(lineage);parent=next(b for b in final['function_blocks'] if b['id']==family['family_id'])
    assert 'info_forward' not in family_tasks.configured(final,parent)
