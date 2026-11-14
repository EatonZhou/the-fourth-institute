"""
:module: spec.design-governance
:design: decision_8b510cbd9c58, decision_4527448ad07f
"""
from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path

class TestProviderUsageEvents(unittest.TestCase):
    def test_pages_preserve_usage_and_strip_message_content(self):
        """用量事件分页读取保留 token 用量与额度，去掉消息正文

        :design-point:"""
        from management_portal.execution.provider_usage_events import read_page
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rows = [
                {"timestamp":"2026-10-01T00:00:00Z", "type":"session_meta", "payload":{"id":"session", "model_provider":"openai", "instructions":"SECRET"}},
                {"timestamp":"2026-10-01T00:01:00Z", "type":"turn_context", "payload":{"model":"gpt-6", "cwd":"SECRET"}},
                {"timestamp":"2026-10-01T00:02:00Z", "type":"event_msg", "payload":{"type":"user_message", "message":"SECRET"}},
                {"timestamp":"2026-10-01T00:03:00Z", "type":"event_msg", "payload":{"type":"token_count", "info":{"last_token_usage":{"input_tokens":5,"total_tokens":7,"secret":"SECRET"}}, "rate_limits":{"plan_type":"pro","primary":{"window_minutes":10080,"used_percent":2,"resets_at":1791244800,"secret":"SECRET"}}}},
                {"timestamp":"2026-10-01T00:04:00Z", "type":"response_item", "payload":{"content":"SECRET"}},
            ]
            (root/'rollout.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
            cursor = ''; records = []; pages = 0
            while True:
                result = read_page(root, account_id='one', since='2026-10-01T00:00:00Z', until='2026-10-02T00:00:00Z', cursor=cursor, limit=2)
                records.extend(result['records']); pages += 1
                cursor = result['next_cursor']
                if not cursor: break
                self.assertLess(pages, 10)
            self.assertEqual(len(records),4)
            self.assertNotIn('SECRET',json.dumps(records))
            self.assertEqual(records[-1]['record']['payload']['info']['last_token_usage']['total_tokens'],7)
            self.assertGreater(pages,1)

    def test_rejects_invalid_window_cursor_and_symlinks(self):
        """用量事件读取拒绝倒置时间窗与无效游标，并跳过符号链接文件

        :design-point:"""
        from management_portal.execution.provider_usage_events import read_page
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'sessions'; root.mkdir()
            outside = Path(folder)/'secret.jsonl'; outside.write_text('{}\n')
            (root/'escape.jsonl').symlink_to(outside)
            args = dict(account_id='one', since='2026-10-01T00:00:00Z', until='2026-10-02T00:00:00Z')
            self.assertEqual(read_page(root,**args)['records'],[])
            with self.assertRaises(ValueError): read_page(root, **args, cursor='bad')
            with self.assertRaises(ValueError): read_page(root,account_id='one',since=args['until'],until=args['since'])

    def test_handler_only_reads_registered_account_directory(self):
        """GetProviderUsageEvents 只读取已登记账号的会话目录，未知账号被拒绝

        :design-point:"""
        from types import SimpleNamespace
        from unittest.mock import patch
        from management_portal.execution import provider_usage_events as source
        from management_portal.hatg.contracts import InstructionError
        class Registry:
            def get(self, account):
                if account != 'one': raise ValueError('unknown')
                return SimpleNamespace(codex_home=Path('/registered/one'))
        arguments={'account_id':'one','since':'2026-10-01T00:00:00Z','until':'2026-10-02T00:00:00Z'}
        with patch('management_portal.execution.account_adapters.CodexRoutes',Registry),patch.object(source,'read_page',return_value={'records':[]}) as read:
            result=source.get_provider_usage_events(SimpleNamespace(argument=arguments.get))
            self.assertEqual(result,{'records':[]})
            self.assertEqual(read.call_args.args[0],Path('/registered/one/sessions'))
            arguments['account_id']='../../credentials'
            with self.assertRaises(InstructionError):
                source.get_provider_usage_events(SimpleNamespace(argument=arguments.get))

    def test_account_list_exposes_registry_default_route(self):
        """ListProviderAccounts 返回账号注册表给出的各 Provider 默认路线

        :design-point:"""
        from types import SimpleNamespace
        from unittest.mock import patch
        from management_portal.execution import isa
        row = {'account_id': 'codex_one', 'route_id': 'one', 'provider': 'codex',
               'kind': 'subscription_login', 'enabled': True, 'version': 1}
        def snapshot(refresh, *, account_facts=None):
            account_facts.update(registrations=[row], login_states={'codex_one': 'logged_in'})
            return {'accounts': [{**row, 'login_state': 'logged_in'}]}
        with patch.object(isa,'_accounts_snapshot',side_effect=snapshot), \
             patch('management_portal.execution.account_registrations.registrations', return_value=[row]), \
             patch('management_portal.permission_system.allows', return_value=False), \
             patch('management_portal.execution.accounts.default_execution_accounts',return_value=SimpleNamespace(adapters={'codex':SimpleNamespace(accounts=lambda: [object()])},resolve=lambda provider, route_id, **kw:SimpleNamespace(route_id=route_id))):
            result=isa.list_provider_accounts(SimpleNamespace(argument=lambda name:None, caller="CASE-A"))
        self.assertEqual(result['default_routes'], {'codex': 'one'})
        matches = [row for row in result['accounts'] if row['account_id'] == 'codex_one']
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]['registration']['route_id'], 'one')
        self.assertEqual(matches[0]['kind'], 'subscription_login')
def test_export_retains_scope_speed_and_identity_without_private_fields():
    from datetime import datetime, timezone
    from management_portal.execution.provider_usage_events import public_record
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    end = datetime(2026, 11, 3, tzinfo=timezone.utc)
    row = {'timestamp': '2026-11-02T00:00:00Z', 'type': 'event_msg', 'ordinal': 3,
           'payload': {'type': 'token_count', 'info': {'total_token_usage': {'total_tokens': 42},
           'private': 'SECRET'}, 'rate_limits': {'limit_id': 'codex_bengalfox',
           'limit_name': 'gpt-5.3-codex-spark', 'credits': {'has_credits': True, 'access_token': 'SECRET'}}}}
    result = public_record(row, start, end)
    assert result['ordinal'] == 3
    assert result['payload']['rate_limits']['limit_id'] == 'codex_bengalfox'
    assert result['payload']['rate_limits']['credits'] == {'has_credits': True}
    assert 'SECRET' not in str(result)
    row['payload'] = {'type': 'thread_settings_applied', 'thread_settings': {'service_tier': 'priority', 'private': 'SECRET'}}
    assert public_record(row, start, end)['payload']['thread_settings'] == {'service_tier': 'priority'}
