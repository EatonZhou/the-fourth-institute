import unittest
from types import SimpleNamespace
from unittest.mock import patch, Mock

from management_portal import workflow_delivery_policy as policy, paper_projection
from management_portal.hatg.contracts import InstructionContext
from management_portal.society_service import resident_protocol
from management_portal.society_service.isa import conversation
import importlib.util
import sys
from pathlib import Path


# The installed Society package deliberately redirects to the configured shared
# deployment. Load the candidate files explicitly for this isolated checkout test.
def candidate(name, relative):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).parents[2]/'management_portal'/relative)
    module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module
    spec.loader.exec_module(module)
    return module

resident_protocol=candidate('management_portal.society_service.resident_protocol','society_service/resident_protocol.py')
conversation=candidate('management_portal.society_service.isa.conversation','society_service/isa/conversation.py')
import management_portal.society_service as society_package
from management_portal.society_service import outward_messages
society_package.resident_protocol=resident_protocol


class PaperRoleDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.rows = [dict(resident_id='resident_' + str(i), name=name, lineage_id='lineage_test',
                          effect_id='effect_' + str(i), event_source_id='event_' + str(i))
                     for i, name in enumerate(paper_projection.ROLES)]
        self.read = self.enterContext(patch.object(policy, '_read', return_value=self.rows))
        self.enterContext(patch.object(policy.portal_features, 'contribution', side_effect=
                                      lambda name: paper_projection.definitions if name=='workbench.workflows.paper' else None))

    def test_all_nine_user_direct_and_fresh_metadata_cannot_bypass(self):
        for row in self.rows:
            with self.assertRaisesRegex(PermissionError, '此角色只处理论文页'):
                policy.require_delivery(row['resident_id'], 'lukai')
            inv = SimpleNamespace(caller='lukai', context=InstructionContext(caller='lukai', entry='web'))
            with patch.object(resident_protocol, 'ResidentDeliveryContext') as constructor:
                with self.assertRaises(PermissionError):
                    conversation.deliver(inv, row['resident_id'], {'role':'user', 'metadata':{'business_context':{'fresh':True}}})
                constructor.assert_not_called()  # Rejected before profiles, messages or queue records.

    def test_other_resident_and_other_application_are_unchanged(self):
        policy.require_delivery('resident_ordinary', 'lukai')
        self.rows.append(dict(resident_id='resident_otherapp', name='other:worker'))
        policy.require_delivery('resident_otherapp', 'lukai')
        self.assertEqual(policy.disabled_reason('resident_ordinary'), '')

    def test_registered_workflow_with_human_initiator_still_delivers(self):
        ctx=InstructionContext(caller='lukai',entry='lineage',recovery_key='activation',trigger_path_id='path')
        journal={'status':'running','effect_id':'effect_0','trigger_path_id':'path'}
        with patch('management_portal.society_service.lineage_effect_script_journal.load',return_value=journal):
            policy.require_delivery('resident_0','lukai',ctx)
            for wrong in ({**journal,'effect_id':'unregistered'}, {**journal,'trigger_path_id':'other'}, {**journal,'status':'done'}):
                with patch('management_portal.society_service.lineage_effect_script_journal.load',return_value=wrong):
                    with self.assertRaises(PermissionError):policy.require_delivery('resident_0','lukai',ctx)
        with self.assertRaises(PermissionError):
            policy.require_delivery('resident_0','lukai',InstructionContext(caller='lukai',entry='lineage',trigger_path_id='path'))

    def test_published_role_history_is_policy_without_workflow_row(self):
        self.rows[:] = [dict(resident_id='resident_summary',name='paper:summary',application='paper',lineage_id='lineage_test')]
        self.assertEqual(policy.disabled_reason('resident_summary'),paper_projection.DIRECT_CHAT_DISABLED_REASON)
        with self.assertRaises(PermissionError):policy.require_delivery('resident_summary','lukai')

    def test_batched_controls_and_stop_permission_preserved(self):
        with policy.projection_scope([r['resident_id'] for r in self.rows]):
            for row in self.rows:
                allowed,reason=resident_protocol._send_problem({'resident_id':row['resident_id']},'lukai',allowed=True)
                self.assertTrue(allowed)
                self.assertEqual(reason,paper_projection.DIRECT_CHAT_DISABLED_REASON)
        self.read.assert_called_once()

    def test_final_delivery_boundary_also_rejects(self):
        with patch.object(resident_protocol.resident_intake_domain,'deliver_resident_message_content') as enqueue:
            with self.assertRaises(PermissionError):
                resident_protocol.deliver_resident_input({'resident_id':'resident_0','message':{}},'lukai')
            enqueue.assert_not_called()

    def test_actual_isa_workflow_reaches_outward_delivery(self):
        ctx=InstructionContext(caller='lukai',entry='lineage',recovery_key='activation',trigger_path_id='path')
        inv=SimpleNamespace(caller='lukai',context=ctx,idempotency_key='test',argument=lambda key:None)
        journal={'status':'running','effect_id':'effect_0','trigger_path_id':'path'}
        with patch('management_portal.society_service.lineage_effect_script_journal.load',return_value=journal), \
                patch.object(conversation,'lineage_of_resident',return_value='lineage_test'), \
                patch.object(outward_messages,'deliver_to_resident',return_value={'message_id':'msg_test'}) as send:
            conversation.deliver(inv,'resident_0',{'content_parts':[{'type':'text','text':'task'}]})
            self.assertIs(send.call_args.kwargs['delivery_context'].workflow_context,ctx)

    def test_conversation_controls_disabled_with_exact_hint(self):
        from management_portal.society_service import domain_ports
        caps=SimpleNamespace(supports_active_turn_input=True,supports_media=True,local_tools=True)
        with patch.object(resident_protocol.family_facade,'is_facade',return_value=False), \
                patch.object(domain_ports,'resident_domain_port',SimpleNamespace(quota_switch_state=lambda *a,**kw:{})), \
                patch.object(resident_protocol.resident_execution_provider,'provider_for',return_value=SimpleNamespace(capabilities=caps)):
            for resident_id in ['resident_0','resident_ordinary']:
                target=SimpleNamespace(record={'resident_id':resident_id})
                controls=resident_protocol._conversation_controls(target,'lukai',{},allowed=True)
                self.assertEqual(controls['can_send'],resident_id=='resident_ordinary')
                self.assertEqual(controls['input_read_only'],resident_id=='resident_0')
                if resident_id=='resident_0':
                    self.assertEqual(controls['disabled_reason'],paper_projection.DIRECT_CHAT_DISABLED_REASON)
                    self.assertEqual(controls['send_hint'],paper_projection.DIRECT_CHAT_DISABLED_REASON)

    def test_history_only_summary_installs_before_real_submission_and_admission(self):
        """Real install/submit/trigger/admission; only ISA graph/storage and model sink are in memory."""
        from contextlib import nullcontext
        from copy import deepcopy
        from management_portal import lineage_workflows as flows, resident_handoff
        from management_portal.workbench_projections import native
        from management_portal.society_service import lineage_program_resident_inputs, resident_conversation_stream

        rid='resident_d510733a002b'
        identity={'lineage_id':'lineage_summary','resident_id':rid}
        history={**identity,'name':'paper:summary','application':'paper'}
        self.rows[:]=[history]
        states={}
        graph={'nodes':[{'id':rid}],'program':{'components':[],'wires':[]},'revision':0}
        sequence=[]

        class MemoryStore:
            def get(_self,lineage_id,resident_id,name):
                return deepcopy(states.get((lineage_id,resident_id,name)))
            def follow_moved_resident(_self,*args): return None
            def lineage_lock(_self,*args): return nullcontext()
            def save(_self,state):
                state['version']=state.get('version',0)+1
                states[tuple(state[k] for k in ('lineage_id','resident_id','name'))]=deepcopy(state)
                self.rows[:]=[history]+[{**identity,'name':s['name'],'effect_id':s['steps'].get('effect',''),
                                        'event_source_id':s['steps'].get('event','')} for s in states.values()]
                sequence.append('save:'+','.join(sorted(state['steps'])))

        def fake_isa(actor,instruction,arguments,**kwargs):
            self.assertEqual(actor,'lukai')
            if instruction=='CreateComponent':
                component={**arguments,'id':'component_'+str(len(graph['program']['components']))}
                graph['program']['components'].append(component)
                return {'component_id':component['id']}
            if instruction=='CreateWire':
                wire={**arguments,'id':'wire_'+str(len(graph['program']['wires']))}
                graph['program']['wires'].append(wire)
                return {'wire_id':wire['id']}
            self.assertEqual(instruction,'TriggerEvent')
            registered=next(iter(states.values()))
            self.assertEqual(set(registered['steps']),{'event','effect','target','invoke'})
            self.assertEqual(arguments['event_source_id'],registered['steps']['event'])
            sequence.append('trigger')
            ctx=InstructionContext(caller=actor,entry='lineage',recovery_key='real-effect-boundary',trigger_path_id='path_summary')
            journal={'effect_id':registered['steps']['effect'],'trigger_path_id':'path_summary','status':'running'}
            inv=SimpleNamespace(caller=actor,context=ctx,idempotency_key='summary_delivery',
                                argument=lambda key:{'context_policy':'fresh'}.get(key))
            with patch('management_portal.society_service.lineage_effect_script_journal.load',return_value=journal):
                result=conversation.deliver(inv,rid,{'role':'user','content_parts':[{'type':'text','text':arguments['prompt']}]})
            return {'event_id':'event_summary','trigger_path_id':'path_summary','delivery':{'results':[{
                'target_id':registered['steps']['effect'],'status':'executed','typed_outputs':result}]}}

        def outward(payload,actor,*,delivery_context,**kwargs):
            sequence.append('admission')
            return resident_protocol.deliver_resident_input(payload,actor,delivery_context=delivery_context)

        # Initially direct human input is forbidden, with no live workflow binding.
        with self.assertRaises(PermissionError):policy.require_delivery(rid,'lukai')
        with patch.object(flows,'get_workflow_binding_store',return_value=MemoryStore()), \
                patch.object(flows,'lineage_graph',return_value=graph), \
                patch.object(flows.isa_client,'call',side_effect=fake_isa), \
                patch.object(native,'binding',return_value=identity), \
                patch.object(resident_handoff,'ensure_invocation_resident',return_value=identity), \
                patch.object(conversation,'lineage_of_resident',return_value=identity['lineage_id']), \
                patch.object(resident_conversation_stream,'get_repository',return_value=SimpleNamespace(message=lambda *a:None)), \
                patch.object(lineage_program_resident_inputs,'record_delivery_profile') as fresh, \
                patch.object(outward_messages,'deliver_to_resident',side_effect=outward), \
                patch.object(resident_protocol,'_with_conversation_controls',side_effect=lambda result,*a,**k:result), \
                patch.object(resident_protocol.resident_intake_domain,'deliver_resident_message_content',return_value={'message_id':'msg_summaryaccepted'}) as enqueue:
            result=native.submit('paper',actor='lukai',resident_scope='paper:summary',message='Summarize this input',
                                 message_id='msg_summaryrequest',workflow=paper_projection._workflow('paper:summary'))
            self.assertEqual(result['submission_state'],'submitted')
            fresh.assert_called_once()
            enqueue.assert_called_once()
            self.assertLess(sequence.index('save:effect,event,invoke,target'),sequence.index('trigger'))
            self.assertLess(sequence.index('trigger'),sequence.index('admission'))
            # The next task reuses its persisted workflow; install creates no duplicate graph objects.
            count=len(graph['program']['components'])
            native.submit('paper',actor='lukai',resident_scope='paper:summary',message='Next summary input',
                          message_id='msg_summaryrequest2',workflow=paper_projection._workflow('paper:summary'))
            self.assertEqual(len(graph['program']['components']),count)
            self.assertEqual(enqueue.call_count,2)
        with self.assertRaises(PermissionError):policy.require_delivery(rid,'lukai')


if __name__=='__main__': unittest.main()
