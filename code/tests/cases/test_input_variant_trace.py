import json
from types import SimpleNamespace
from management_portal.execution.input_variant_trace import trace,trace_invocation
from management_portal.execution.input_variants import input_variant_result
from management_portal.execution.backend_contracts import BackendResult

def test_trace_never_contains_body_or_material_path(capsys):
    secret='PRIVATE PAPER TEXT'
    path='/private/snapshot.txt'
    row=trace('adapter_send',business_ref='job-one',role='resident-one',prompt=secret+' '+path,
              spec={'materials':[{'path':path}]},decision={'selected':'path','policy':{'reason':'allowed'}})
    captured=capsys.readouterr().out
    assert secret not in captured and path not in captured
    assert json.loads(captured)['contains_snapshot_path'] is True
    assert row['prompt_chars']==len(secret+' '+path)

def test_absent_variant_is_visible_in_receipt():
    call=SimpleNamespace(request=SimpleNamespace(metadata={}),user_prompt='full')
    result=input_variant_result(call,None,BackendResult(output={'message':'body'}))
    assert result.output['input_variant']['reason']=='input_variants_missing'
