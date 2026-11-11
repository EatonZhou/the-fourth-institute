"""Real context normalization and final Execution dispatch; no model/DB."""
import json
from types import SimpleNamespace
from dataclasses import replace
from unittest.mock import Mock
import pytest
from management_portal import agent_context_selection as selection, paper_resident
from management_portal.society_service.resident_intake_domain import resolve_context_refs
from management_portal.paper_agent_materials import variants_for_message
from management_portal.execution.requests import build_agent_turn_request
from management_portal.execution.backend_contracts import BackendInvocation, BackendContext, BackendResult
from management_portal.execution.backend_services import AgentSessionService
from management_portal.execution.session_manager import RuntimeSession

@pytest.mark.parametrize('provider',['codex','kimi'])
@pytest.mark.parametrize('scope',['paper:deep-read','paper:review','paper:exp','paper:registration-metadata'])
def test_verified_context_survives_real_normalizer_to_actual_dispatch(monkeypatch,scope,provider):
    full='FULL PAPER BODY '*2000
    snapshot={'type':'paper.task-material','full_prompt':full,'input_variants':{'version':1,'path_prompt':'Read /authorized/sha.txt; return required product','materials':[{'identity':scope+':job','sha256':'a'*64,'path':'/authorized/sha.txt'}]}}
    # The protocol creates this only after its authenticated owner RPC.
    from management_portal.society_service import resident_protocol as protocol
    from management_portal import isa_client
    resolved=[]
    monkeypatch.setattr(isa_client,'call',lambda *a,**kw:{'snapshot':snapshot})
    monkeypatch.setattr(protocol.resident_input_scope,'permits',lambda *a:False)
    monkeypatch.setattr(protocol,'_reads_resident',lambda *a:False)
    def intake(rid,actor,body,refs,**kw):
        resolved.append(resolve_context_refs(refs,actor,body))
        return {'resident_id':rid,'message_id':kw['message_id']}
    monkeypatch.setattr(protocol.resident_intake_domain,'deliver_resident_message_content',intake)
    protocol.deliver_resident_input({'resident_id':'role','message':{'message_id':'msg_'+('a'*32),'role':'user','content_parts':[
        {'type':'text','text':'TASK\n'+full},{'type':'context_reference','context':{'structured_reference':{'type':'paper.task-material','version':1,'key':'task-one'}}}]}},'reader')
    refs=resolved[0]
    assert refs['verified_material_snapshot']==snapshot
    prompt='TASK\n'+full
    spec=variants_for_message(refs['verified_material_snapshot'],prompt)
    from management_portal.society_service import resident_execution_domain as domain
    from management_portal.society_service import resident_access, resident_hatg_credentials
    monkeypatch.setattr(domain.resident_input_scope,'get',lambda *a:{'actor':'reader'})
    monkeypatch.setattr(domain.resident_backend,'execution_backend',lambda *a:provider)
    monkeypatch.setattr(domain.resident_backend,'execution_binding_id',lambda *a:'old-binding')
    monkeypatch.setattr(domain.resident_backend,'execution_account',lambda *a:{'backend_route_id':'kimi-code'})
    monkeypatch.setattr(domain.resident_lineage,'lineage_id',lambda *a:'lineage-test')
    monkeypatch.setattr(resident_hatg_credentials,'ensure_resident_credential',lambda *a,**kw:None)
    monkeypatch.setattr(domain.resident_runtime_domain,'resident_working_directory',lambda *a:'/authorized')
    monkeypatch.setattr(domain.resident_runtime_domain,'reconcile_resident_codex_runtime_configuration',lambda r:r)
    monkeypatch.setattr(resident_access,'has_resident_workspace_authority',lambda *a:True)
    monkeypatch.setattr(domain.resident_execution_engine_domain,'resident_execution_request_backend',lambda *a:provider)
    monkeypatch.setattr(domain.resident_execution_engine_domain,'resident_execution_runtime_options',lambda *a,**kw:({'tools':['Read'],'permission_mode':'yolo'} if provider=='kimi' else {'sandbox':'read-only'}))
    monkeypatch.setattr(domain.resident_conversation_stream,'get_repository',lambda:SimpleNamespace(message=lambda *a:{}))
    monkeypatch.setattr(domain,'resident_system_instructions',lambda *a,**kw:None)
    req=domain._build_resident_execution_request_unrestricted({'resident_id':'role'}, {'actor':'reader','raw_message':prompt,'context_refs':refs,'execution_id':'test-'+scope,'user_message_id':'msg_'+('a'*32)})
    assert req.metadata['input_variants']==spec
    sent=[]
    class Adapter:
        def execute(self,call,context):
            sent.append(call.user_prompt)
            return BackendResult(output={'message':'done'},usage={})
    class Harness:
        def prepare_invocation(self,call,provider):return call
        def should_run(self,call):return False
    monkeypatch.setattr('management_portal.execution.resident_capability_assembly.prepare',lambda call,ctx:call)
    monkeypatch.setattr('management_portal.execution.resident_capability_assembly.observe_delivery',lambda *a,**k:None)
    ctx=BackendContext(emit_event=Mock(),cancellation_requested=lambda:False,set_interrupt_target=Mock(),input_variant_observer=lambda d:None)
    call=BackendInvocation(req,RuntimeSession('stateless','','',True),prompt,'system',True,True)
    result=AgentSessionService(providers={provider:Adapter()},harness=Harness()).execute(call,ctx)
    assert sent==['TASK\n'+spec['path_prompt'].removeprefix('TASK\n')]
    assert 'FULL PAPER BODY' not in sent[0] and '/authorized/sha.txt' in sent[0]
    assert result.output['input_variant']['selected']=='path'


def test_client_json_cannot_forge_verified_context():
    r=resolve_context_refs({'verified_material_snapshot':{'type':'paper.task-material'}},'reader','body')
    assert 'verified_material_snapshot' not in r

@pytest.mark.parametrize('scope',['paper:deep-read','paper:exp'])
def test_markdown_protocol_unwraps_json_and_keeps_fulltext_evidence(scope):
    content=json.dumps({'markdown':'## 一段话先看懂\nSection 2 facts','source_evidence':'依据本次prompt提供的论文提取全文，Section 2'})
    body,usage=paper_resident.normalize_task_result(content,{'material_snapshot':{'sha256':'a'*64}},markdown=True)
    assert body.startswith('## ')
    assert usage['source_evidence'].endswith('Section 2')
    assert usage['material_read_status']=='not_proven'

@pytest.mark.parametrize('markdown',[True,False])
def test_plain_output_and_structured_role_schema_retained(markdown):
    content='## Evidence\nSection 2 findings' if markdown else json.dumps({'title':'Paper','score':4,'source_evidence':'Section 2'})
    body,usage=paper_resident.normalize_task_result(content,{},markdown=markdown)
    assert body==content
    if not markdown:assert json.loads(body)['score']==4

def test_read_failure_is_not_saved_as_product():
    with pytest.raises(ValueError,match='无法读取'):
        paper_resident.normalize_task_result('PAPER_MATERIAL_READ_FAILED',{'material_snapshot':{}},markdown=True)


def test_actual_failed_json_product_revalidates_without_model():
    from management_portal.paper_reading_jobs import DEEP_READ_SECTIONS, _require_artifact_sections
    original=json.dumps({'markdown':'\n'.join('## '+h+'\nSection 2 evidence' for h in DEEP_READ_SECTIONS),'source_evidence':'仅依据prompt全文 Section 2'})
    body,usage=paper_resident.normalize_task_result(original,{'material_snapshot':{'sha256':'a'*64}},markdown=True)
    _require_artifact_sections(body,DEEP_READ_SECTIONS,'Deep Read')
    assert usage['material_read_status']=='not_proven'
