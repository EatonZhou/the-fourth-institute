"""Effect candidates are inert, reviewed by identity and bound to exact content."""
from copy import deepcopy
import pytest
from tests.cases.isa_test_support import ok, run
from tests.cases.test_isa_lineage_lifecycle import future_creation_store, graph, lineage, resident  # noqa: F401


def component(lid, eid):
    return next(c for c in graph(lid)['program']['components'] if c['id'] == eid)


def test_candidate_review_cancellation_and_runtime_boundary(lineage):
    from management_portal.society_service.lineage_program_compilation import compile_program
    from management_portal.society_service.lineage_program_effect_rules import effect_definition
    reviewer = resident(lineage, 'Reviewer')
    eid = ok('CreateComponent', {'lineage_id':lineage,'kind':'effect','config':{
        'script_draft':{'description':'Return a value','script':'output result="first"','confirm_by':reviewer}}})['component_id']
    record=component(lineage,eid)
    assert record['config']['script'] == ''
    plan=compile_program(graph(lineage)['program'],graph(lineage)['nodes'])
    with pytest.raises(ValueError, match='尚未编写'):
        effect_definition(plan,eid)
    args={'lineage_id':lineage,'effect_id':eid,'decision':'confirm','draft_hash':record['config']['script_draft']['hash']}
    before=graph(lineage)
    assert run('ReviewComponentScript',args,version=before['revision']).error['code']=='instruction.forbidden'
    assert graph(lineage)==before
    assert run('UpdateComponent',{'lineage_id':lineage,'component_id':eid,'changes':{'config':{'script':'output result="bypass"'}}}).error
    ok('ReviewComponentScript',args,version=before['revision'],actor=reviewer)
    assert component(lineage,eid)['config']['script']=='output result="first"'
    # Changing a reviewed executable cannot silently reuse its confirmation.
    assert run('UpdateComponent',{'lineage_id':lineage,'component_id':eid,'changes':{'script':'output result="bypass"'}}).error
    ok('UpdateComponent',{'lineage_id':lineage,'component_id':eid,'changes':{'config':{
        **component(lineage,eid)['config'],'script_draft':{'description':'Second version','script':'output result="second"'}}}})
    current=component(lineage,eid)
    assert current['config']['script']=='output result="first"'
    assert effect_definition(compile_program(graph(lineage)['program'],graph(lineage)['nodes']),eid)['script']=='output result="first"'
    assert run('ReviewComponentScript',args,version=graph(lineage)['revision'],actor=reviewer).error['code']=='instruction.conflict'
    cancel={**args,'decision':'cancel','draft_hash':current['config']['script_draft']['hash']}
    ok('ReviewComponentScript',cancel,version=graph(lineage)['revision'])
    assert component(lineage,eid)['config']['script']=='output result="first"'
    assert 'script_draft' not in component(lineage,eid)['config']


def test_template_confirm_publish_install_and_apply(lineage):
    spec={'kind':'structure','title':'Reviewed duty','parameters':[],'body':{
        'roles':[{'key':'target','from':'target'}],
        'components':[{'key':'work','kind':'effect','config':{'script_draft':{
            'description':'Return a value without sending messages','script':'output result="ready"'}}}],
        'wires':[], 'function_block':True}}
    template=ok('CreateTemplate',{'template':spec})['template']
    tid=template['template_id'];draft=template['body']['components'][0]['config']['script_draft']
    assert template['body']['components'][0]['config']['script']==''
    assert run('PublishTemplate',{'template_id':tid}).error
    args={'template_id':tid,'effect_id':'work','draft_hash':draft['hash'],'decision':'confirm'}
    assert run('ReviewTemplateScript',args,version=template['version'],actor='another_user').error
    edited=deepcopy(template['body']);edited['components'][0]['config']['script_draft']['script']='output result="updated"'
    template=ok('UpdateTemplate',{'template_id':tid,'body':edited})['template']
    assert run('ReviewTemplateScript',args,version=template['version']).error['code']=='instruction.conflict'
    args['draft_hash']=template['body']['components'][0]['config']['script_draft']['hash']
    approved=ok('ReviewTemplateScript',args,version=template['version'])['template']
    assert approved['body']['components'][0]['config']['script']=='output result="updated"'
    replacement=deepcopy(approved['body']);replacement['components'][0]['config']={}
    assert '确认' in run('UpdateTemplate',{'template_id':tid,'body':replacement}).error['message']
    store=ok('PublishTemplate',{'template_id':tid})['item']
    installed=ok('InstallStoreTemplate',{'store_id':store['store_id']},actor='store-reader')['template']
    assert installed['body']['components'][0]['config']==approved['body']['components'][0]['config']
    holder=resident(lineage,'Holder')
    result=ok('ApplyTemplate',{'lineage_id':lineage,'template_id':installed['template_id'],'target':{'kind':'resident','resident_ids':[holder]}})
    assert result['function_block_ids']
    effect=next(c for c in graph(lineage)['program']['components'] if c['kind']=='effect')
    assert effect['config']['script']=='output result="updated"'
    assert 'script_draft' not in effect['config']


def test_invalid_candidate_and_stale_resource_do_not_promote(lineage):
    eid=ok('CreateComponent',{'lineage_id':lineage,'kind':'effect','config':{'script_draft':{
        'description':'Invalid candidate','script':'UnknownInstruction value="x"'}}})['component_id']
    before=graph(lineage);draft=component(lineage,eid)['config']['script_draft']
    args={'lineage_id':lineage,'effect_id':eid,'draft_hash':draft['hash'],'decision':'confirm'}
    assert run('ReviewComponentScript',args,version=before['revision']).error
    assert graph(lineage)==before
    assert run('ReviewComponentScript',{**args,'decision':'cancel'},version=before['revision']-1).error['code']=='instruction.conflict'
    assert graph(lineage)==before


def test_duty_preview_keeps_candidate_inert_and_requester_stable(lineage):
    holder=resident(lineage,'Holder')
    duty=ok('CreateDuty',{'lineage_id':lineage,'holder_id':holder,'description':'Candidate duty','prompt':'Prepare a result'})['function_block']['id']
    before=graph(lineage)
    args={'lineage_id':lineage,'function_block_id':duty,'operations':[{
        'op':'create','kind':'component','ref':'candidate','value':{'kind':'effect',
        'config':{'script':'output result="must not run"','script_draft':{'description':'Return a result','script':'output result="review me"'}}}}]}
    preview=ok('ApplyLineageChanges',{**args,'dry_run':True},version=before['revision'])
    assert graph(lineage)==before
    candidate=preview['preview']['elements'][0]['after']['config']
    assert candidate['script']=='' and candidate['script_draft']['hash']
    ok('ApplyLineageChanges',args,version=before['revision'])
    effect=next(c for c in graph(lineage)['program']['components'] if c['kind']=='effect')
    assert effect['config']==candidate
    config=deepcopy(effect['config']);config['script_draft']['confirm_by']='another_user'
    assert run('UpdateComponent',{'lineage_id':lineage,'component_id':effect['id'],'changes':{'config':config}},actor=resident(lineage,'Assistant')).error
