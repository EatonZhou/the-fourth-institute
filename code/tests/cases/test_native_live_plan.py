"""Native plans stay live through persistence, Resident Activity and the real Chat.

:module: workbench.chat
:importance: P1
"""
import copy
import subprocess
import json
from pathlib import Path
import tempfile
import unittest

from management_common.provider_turn_artifacts import TurnArtifacts
from management_portal.execution import event_projection as projection
from management_portal.society_service import resident_directory_domain
from management_portal.society_service import resident_conversation_stream as stream
from management_portal.resources.portal import portal_route_script


class NativePlanProjection(unittest.TestCase):
    def test_durable_updates_and_final_card_keep_identity(self):
        """计划逐次更新经过持久化和真实界面，最终记录替换实时卡片且保留原 Diff 与审查入口"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'activity.jsonl'
            reader = projection.ExecutionActivityProjection('execution-plan-fixture', runtime_limit=2)
            reader.path = path
            collector = TurnArtifacts()
            snapshots = []
            base = {'threadId': 'thread-plan', 'turnId': 'turn-plan'}
            sequence = 0

            def emit(method, params, expected_activities=1):
                nonlocal sequence
                sequence += 1
                event = {'type': 'provider_event', 'execution_id': reader.execution_id, 'sequence': sequence,
                         'created_at': f'2026-10-25T00:00:{sequence:02d}.000Z',
                         'details': {'method': method, 'params': params}}
                self.assertEqual(projection.append_execution_activity(path, [event]), expected_activities)
                return reader.read()

            for status in ['pending', 'inProgress', 'completed']:
                params = {**base, 'plan': [{'step': 'Check <fixture>', 'status': status}], 'explanation': 'Only this turn'}
                collector.observe('turn/plan/updated', params)
                result = emit('turn/plan/updated', params)
                self.assertEqual(result['outputs'], [], 'live plans never append mutable chat facts')
                plans = [r['native_plan'] for r in result['runtime_details'] if r.get('native_plan')]
                self.assertEqual(len(plans), 1)
                self.assertEqual(plans[0]['plan'][0]['status'], status)
                self.assertNotIn('diff', plans[0])
                snapshots.append(result)
                reader.acknowledge()
            self.assertEqual(len({x['runtime_details'][0]['native_plan']['message_id'] for x in snapshots}), 1)
            # A long run must not evict its plan merely because tools emit rows.
            active = reader.read()['runtime_details'][0]
            self.assertIn(active, projection.last_runtime_details([active, *[{'kind': 'tool', 'status': 'completed'} for _ in range(10)]], 2))
            reader.acknowledge()
            collector.observe('turn/diff/updated', {**base, 'diff': '+actual change'})
            final = collector.observe('turn/completed', {**base, 'turn': {'id': 'turn-plan', 'status': 'completed'}})
            result = emit('item/completed', final, expected_activities=2)
            self.assertFalse(any(r.get('native_plan') and r['status'] == 'running' for r in result['runtime_details']))
            self.assertEqual(result['outputs'][0]['output_id'], active['native_plan']['message_id'])
            self.assertEqual(result['outputs'][0]['content_parts'][0]['diff'], '+actual change')
            final_message = {'id': result['outputs'][0]['output_id'], 'role': 'agent', 'content_parts': result['outputs'][0]['content_parts']}
            reader.acknowledge()
            result = emit('turn/completed', {**base, 'turn': {'id': 'turn-plan', 'status': 'completed'}}, expected_activities=2)
            self.assertFalse(any(r.get('native_plan') and r['status'] == 'running' for r in result['runtime_details']))
            reader.acknowledge()
            reopened = projection.ExecutionActivityProjection(reader.execution_id, runtime_limit=2)
            reopened.path = path
            self.assertFalse(any(r.get('native_plan') and r['status'] == 'running' for r in reopened.read()['runtime_details']))
            wire = []
            for snapshot in snapshots:
                projected = resident_directory_domain._runtime_activity_for_execution('resident_planfixture', {
                    'execution_id': reader.execution_id, 'submitted_at': '2026-10-27T00:00:00Z',
                    'user_message_id': 'msg_input',
                }, transcript_projection=snapshot)
                activities = stream.protocol_activities('resident_planfixture', projected['details'])
                self.assertEqual(activities[0]['native_plan'], snapshot['runtime_details'][0]['native_plan'])
                self.assertEqual(activities[0]['message_id'], 'msg_input')
                activities[0]['native_plan']['plan'][0]['status'] = 'changed-copy'
                self.assertNotEqual(projected['details'][0]['native_plan']['plan'][0]['status'], 'changed-copy')
                wire.append({'runtime_details': stream.protocol_activities('resident_planfixture', projected['details'])})
            row = wire[0]['runtime_details'][0]
            for field, value in [('execution_id', 'execution-other'), ('thread_id', ''), ('message_id', ''), ('diff', '+final')]:
                invalid = copy.deepcopy(row)
                invalid['native_plan'][field] = value
                with self.subTest(field=field), self.assertRaises(ValueError):
                    stream.protocol_activity(invalid)
            self.assertNotIn('native_plan', stream.protocol_activity({**row, 'status': 'completed'}))
            with self.assertRaises(ValueError):
                stream.protocol_activities('resident_wrong', [row])
            browser = subprocess.run(
                ['node', str(Path(__file__).with_name('javascript') / 'native_plan_live.cjs')],
                input=json.dumps({'routeScript': portal_route_script().decode(), 'snapshots': wire, 'finalMessage': final_message}),
                text=True, capture_output=True, timeout=60,
            )
            self.assertEqual(browser.returncode, 0, browser.stdout + browser.stderr)


    def test_no_native_card_without_proven_identity(self):
        """缺少原 Execution、Thread 或 Turn 身份的活动不生成原生计划卡片"""
        detail = projection.runtime_details_from_objects([(1, {'method': 'turn/plan/updated', 'params': {'plan': [{'step': 'unknown', 'status': 'pending'}]}})])
        self.assertEqual(len(detail), 1)
        self.assertNotIn('native_plan', detail[0])

    def test_turn_and_execution_scope_and_cancel_end(self):
        """取消只移除对应 Execution 和 Turn 的实时计划，不影响并发执行与其他轮次"""
        def event(sequence, execution, turn, method='turn/plan/updated'):
            return (sequence, {'type': 'provider_event', 'execution_id': execution, 'details': {'method': method,
                'params': {'threadId': 'thread-scope', 'turnId': turn, 'turn': {'id': turn, 'status': 'interrupted'},
                           'plan': [{'step': execution + turn, 'status': 'pending'}]}}})
        rows = [event(1,'execution-a','turn-a'), event(2,'execution-b','turn-a'), event(3,'execution-a','turn-b'),
                event(4,'execution-a','turn-a','turn/completed')]
        plans = [x['native_plan'] for x in projection.runtime_details_from_objects(rows) if x.get('native_plan')]
        self.assertEqual({(x['execution_id'],x['turn_id']) for x in plans}, {('execution-b','turn-a'),('execution-a','turn-b')})

if __name__ == '__main__':
    unittest.main()
