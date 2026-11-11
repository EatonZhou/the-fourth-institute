import socket
import threading
import time

import pytest

from management_portal.hatg import socket_rpc
from management_portal.hatg.contracts import InstructionCall, InstructionContext, InstructionError


def test_explicit_transport_deadline_cannot_reset_on_trickle(monkeypatch):
    client, server = socket.socketpair()
    def opened(*a, **k):
        return client, client.makefile('rb')
    monkeypatch.setattr(socket_rpc, '_open', opened)
    def trickle():
        try:
            for _ in range(30):
                server.sendall(b' ')
                time.sleep(.02)
        except OSError:
            pass
        finally:
            server.close()
    thread = threading.Thread(target=trickle)
    thread.start()
    started = time.monotonic()
    try:
        with pytest.raises(InstructionError):
            socket_rpc.call_remote('web', InstructionCall('ResolveMessageContextReferences', {}),
                InstructionContext(caller='alice', entry='lineage'), timeout=.1).raise_for_error()
        assert time.monotonic() - started < .4
        assert client.fileno() == -1
    finally:
        client.close()
        thread.join(timeout=1)


def test_protocol_resolver_failure_never_reaches_intake(monkeypatch):
    from management_portal import isa_client
    from management_portal.society_service import resident_protocol as protocol
    monkeypatch.setattr(protocol.resident_intake_domain, 'deliver_resident_message_content',
        lambda *a, **k: pytest.fail('must not accept'))
    def unavailable(actor, instruction, args, **kwargs):
        assert actor == 'alice' and kwargs['timeout'] == 10
        assert 'actor' not in args
        raise TimeoutError('unavailable')
    monkeypatch.setattr(isa_client, 'call', unavailable)
    payload = {'resident_id': 'resident_'+'a'*12, 'message': {'message_id': 'msg_'+'1'*32,
        'content_parts': [{'type': 'text', 'text': 'Question'}, {'type': 'context_reference',
            'context': {'structured_reference': {'type': 'paper.research', 'version': 1}}}]}}
    with pytest.raises(TimeoutError):
        protocol.deliver_resident_input(payload, 'alice')


def test_protocol_uses_server_snapshot_and_demotes_client_context(monkeypatch):
    from management_portal import isa_client
    from management_portal.society_service import resident_protocol as protocol
    captures = []
    def resolve(actor, instruction, args, **kwargs):
        return {'snapshot': {'documents': [{'paper_id': 'authorized'}]}}
    monkeypatch.setattr(isa_client, 'call', resolve)
    monkeypatch.setattr(protocol.resident_intake_domain, 'deliver_resident_message_content',
        lambda *a, **k: captures.append((a, k)) or {})
    monkeypatch.setattr(protocol.resident_input_scope, 'permits', lambda *a: True)
    payload = {'resident_id': 'resident_'+'a'*12, 'message': {'message_id': 'msg_'+'2'*32,
        'content_parts': [{'type': 'text', 'text': 'Question'}, {'type': 'context_reference',
            'context': {'structured_reference': {}, 'context_text': 'FORGED MATERIAL',
                        'resources': ['forged-resource']}}]}}
    protocol.deliver_resident_input(payload, 'alice')
    args, options = captures[0]
    assert 'FORGED MATERIAL' in args[2]  # retained only as User-supplied notes
    assert 'FORGED MATERIAL' not in options['runtime_context']
    assert 'forged-resource' not in str(args[3])
    assert args[3]['verified_material_snapshot']['documents'] == [{'paper_id': 'authorized'}]


def test_resolver_permission_uses_par_target_without_resident_callback(monkeypatch):
    from tests.cases.permission_test_support import enable, Fact
    from management_portal import permission_system, society_service
    from management_portal.hatg.routing import declaration_for
    permissions = enable(monkeypatch)
    permissions.users('alice')
    permissions.place(Fact('lineage', 'lineage_'+'1'*12, {'project': 'test'}),
                      Fact('resident', 'resident_'+'a'*12, {'lineage': 'lineage_'+'1'*12}))
    monkeypatch.setattr(society_service, 'get_society_service', lambda: pytest.fail('no synchronous Society callback'))
    # This is the real Web kernel authorization owner, including PAR decision.
    permission_system.authorize_instruction(declaration_for('ResolveMessageContextReferences'),
        {'target_resident_id':'resident_'+'a'*12,'reference':{}},
        InstructionContext(caller='alice',entry='lineage'))


def test_optional_timeout_is_transport_only_and_default_is_unchanged(monkeypatch):
    from types import SimpleNamespace
    from management_portal.hatg import routing
    captured = []
    monkeypatch.setattr(routing, 'declaration_for', lambda name: SimpleNamespace(owner='web', owner_for=lambda arguments:'web'))
    monkeypatch.setattr(routing, 'kernel_for', lambda owner: None)
    monkeypatch.setattr(socket_rpc, 'call_remote', lambda owner, call, context, **kw: captured.append((call,kw)) or 'result')
    call=InstructionCall('ResolveMessageContextReferences',{'target_resident_id':'resident_'+'a'*12,'reference':{}})
    context=InstructionContext(caller='alice',entry='lineage')
    assert routing.execute(call,context)=='result'
    assert 'timeout' not in captured[-1][1]
    assert routing.execute(call,context,timeout=10)=='result'
    assert captured[-1][1]['timeout']==10
    assert captured[-1][0].arguments==call.arguments and 'timeout' not in call.arguments


def test_ordinary_chat_without_structured_reference_does_not_resolve(monkeypatch):
    from management_portal import isa_client
    from management_portal.society_service import resident_protocol as protocol
    captures=[]
    monkeypatch.setattr(isa_client,'call',lambda *a,**k:pytest.fail('ordinary Chat must not call research resolver'))
    monkeypatch.setattr(protocol.resident_intake_domain,'deliver_resident_message_content',lambda *a,**k:captures.append((a,k)) or {})
    monkeypatch.setattr(protocol.resident_input_scope,'permits',lambda *a:True)
    protocol.deliver_resident_input({'resident_id':'resident_'+'a'*12,'message':{'message_id':'msg_'+'3'*32,
        'content_parts':[{'type':'text','text':'Ordinary message'},{'type':'context_reference','context':{'context_text':'Ordinary user context'}}]}},'alice')
    args,options=captures[0]
    assert args[2]=='Ordinary message'
    assert options['runtime_context']=='Ordinary user context'
