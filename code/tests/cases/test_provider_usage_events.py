"""
:module: spec.design-governance
:design: decision_8b510cbd9c58, decision_4527448ad07f
"""
from __future__ import annotations
import json
import tempfile
import unittest
from datetime import datetime, timezone
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

    def test_snapshot_uses_isa_and_retains_legacy_schema(self):
        """codex 每日额度快照经 ListProviderAccounts 收集账号并保持原有格式

        :design-point:"""
        from scripts import codex_daily_limit_snapshot as snapshot
        def isa(name, **args):
            self.assertEqual(name,'ListProviderAccounts')
            return {'default_routes':{'codex':'one'},'accounts':[{'provider':'codex','backend_route_id':'one','rate_limits':[]}]}
        default, accounts = snapshot.collect_codex_accounts(isa_call=isa)
        self.assertEqual(default,'one'); self.assertEqual(list(accounts),['one'])

    def test_isa_export_retains_existing_quota_parser_semantics(self):
        """经 ISA 导出的会话用量事件与原始文件得出相同的周额度解析结果

        :design-point:"""
        from scripts import codex_weekly_quota_usage as weekly
        from management_portal.execution.provider_usage_events import read_page
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); original=root/'original'; original.mkdir()
            rows=[{'type':'session_meta','timestamp':'2026-10-01T00:00:00Z','payload':{'id':'s','model_provider':'openai'}},
                  {'type':'turn_context','timestamp':'2026-10-01T00:00:01Z','payload':{'model':'gpt-6'}},
                  {'type':'event_msg','timestamp':'2026-10-01T00:00:02Z','payload':{'type':'token_count','info':{'last_token_usage':{'input_tokens':5,'total_tokens':7}},'rate_limits':{'plan_type':'pro','primary':{'window_minutes':10080,'used_percent':2,'resets_at':1791244800}}}}]
            (original/'rollout.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
            start='2026-10-01T00:00:00Z'; end='2026-10-02T00:00:00Z'
            def isa(name, **args):
                self.assertEqual(name,'GetProviderUsageEvents');args['limit']=1
                return read_page(original,**args)
            sources=weekly.export_isa_sessions(root/'export',account_ids=['one'],since=start,until=end,isa_call=isa)
            options=dict(since_utc=datetime.fromisoformat(start.replace('Z','+00:00')),until_utc=datetime.fromisoformat(end.replace('Z','+00:00')),plan_type_filter='',max_reset_lag_seconds=608400)
            native=weekly.collect_events(original,**options)
            projected=weekly.collect_events(sources[0][1],**options)
            self.assertEqual([(e.session_id,e.usage,e.reset_at) for e in native[0]],[(e.session_id,e.usage,e.reset_at) for e in projected[0]])
            self.assertEqual(native[3],projected[3])

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
        with patch.object(isa,'_accounts_snapshot',return_value={'accounts':[{'account_id':'codex_one'}]}),patch('management_portal.execution.accounts.default_execution_accounts',return_value=SimpleNamespace(adapters={'codex':SimpleNamespace(accounts=lambda: [object()])},resolve=lambda provider:SimpleNamespace(route_id='one'))):
            result=isa.list_provider_accounts(SimpleNamespace(argument=lambda name:None, caller="CASE-A"))
        self.assertEqual(result['default_routes'], {'codex': 'one'})
        matches = [row for row in result['accounts'] if row['account_id'] == 'codex_one']
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]['registration']['route_id'], 'one')
        self.assertEqual(matches[0]['kind'], 'subscription_login')
