#!/usr/bin/env python3
"""Automatically link explicitly supplied existing native files, entirely offline.

Example: python scripts/provider_usage_attribution_report.py --manifest FILE
  --since 2026-11-01T00:00:00Z --until 2026-11-13T00:00:00Z --output-root DIR
Manifest is the existing provider-weekly-usage accounts/sources contract. Optional
quota_observations/entitlement_policies are existing exports, never live queries.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import shutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from management_common.file_permissions import atomic_write_private_bytes, ensure_private_directory
from scripts.provider_usage_native import record_path
from scripts.provider_weekly_usage import moment, report_account


def run(manifest, since, until, output_root):
    """Publish one immutable run directory only after every artifact is complete."""
    if since >= until:
        raise ValueError('since must precede until')
    if not isinstance(manifest, dict) or not isinstance(manifest.get('accounts'), list):
        raise ValueError('Manifest requires accounts list')
    seen = set()
    for account in manifest['accounts']:
        if not isinstance(account, dict) or not isinstance(account.get('provider'), str):
            raise ValueError('Invalid account')
        if account['provider'] not in {'codex', 'claude', 'kimi', 'antigravity'}:
            continue
        if not isinstance(account.get('account_id'), str) or not account['account_id']:
            raise ValueError('Account identity required')
        key = (account['provider'], account['account_id'])
        if key in seen:
            raise ValueError('Duplicate account in manifest')
        seen.add(key)
        if not isinstance(account.get('sources', []), list):
            raise ValueError('Account sources must be a list')
    if not seen:
        raise ValueError('No subscription accounts supplied')
    root = record_path(output_root)
    ensure_private_directory(root.parent)
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ValueError('Output directory must be absent or empty; use a new run directory')
    staging = Path(tempfile.mkdtemp(prefix='.attribution-run-', dir=root.parent))
    try:
        result = _write(manifest, since, until, staging)
        os.rename(staging, root)
        return result
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _write(manifest, since, until, root):
    ensure_private_directory(root)
    fd, temporary = tempfile.mkstemp(prefix='.attribution-', dir=root)
    summary = {'schema': 'tfi.entitlement-attribution.v1', 'generated_at': datetime.now(timezone.utc).isoformat(),
               'since': since.isoformat(), 'until': until.isoformat(), 'usage_queried': False,
               'model_called': False, 'coverage': manifest.get('coverage', 'supplied_sources_only'), 'accounts': []}
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as ledger:
            for account in manifest['accounts']:
                if account['provider'] not in {'codex', 'claude', 'kimi', 'antigravity'}:
                    continue
                def save(event, attribution):
                    ledger.write(json.dumps({'provider': account['provider'], 'account_id': account['account_id'],
                        'session': event['session'], 'request_id': event['id'], 'at': event['at'],
                        'model': event['model'], 'usage': event['usage'], 'attribution': attribution},
                        ensure_ascii=False) + '\n')
                report = report_account(account, since, until, 'Asia/Hong_Kong', attribution_sink=save)
                summary['accounts'].append({k: report[k] for k in
                    ('provider', 'account_id', 'source_files', 'requests', 'totals',
                     'source_failures', 'gaps', 'quota_attribution')})
        os.replace(temporary, root / 'requests.jsonl')
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    summary['complete'] = all(a['requests'] > 0 and not a['source_failures'] for a in summary['accounts'])
    summary['status'] = 'complete' if summary['complete'] else 'partial_or_empty'
    atomic_write_private_bytes(root / 'summary.json', (json.dumps(summary, ensure_ascii=False, indent=2)+'\n').encode())
    lines = ['# 逐请求额度来源离线关联', '',
             '只读取明确提供的已有会话及额度历史；未查 usage、未调用模型。',
             '范围：' + str(summary['coverage']), '读取状态：' + summary['status'], '',
             '| Provider | Account | 请求数 | 证据分类（请求数） |', '| --- | --- | ---: | --- |']
    for a in summary['accounts']:
        counts = a['quota_attribution']['requests_by_status_source']
        lines.append(f"| {a['provider']} | {a['account_id']} | {a['requests']} | " +
                     '; '.join(f'{k}: {v}' for k, v in sorted(counts.items())) + ' |')
    lines += ['', 'confirmed:credits_involved 只确认该请求涉及 credits，不证明其全部 token 都由 credits 支付。',
              'inferred 只表示有限时效的状态推定；unknown 保留缺证、过期、冲突和切换边界。',
              '请求 token 只计一次；额度窗口可以有多个。未按余额差分摊 token，也未改变旧容量拟合的筛选规则。',
              '逐请求证据见 requests.jsonl，覆盖及失败见 summary.json。', '']
    atomic_write_private_bytes(root / 'summary.md', '\n'.join(lines).encode())
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--since', required=True)
    parser.add_argument('--until', required=True)
    parser.add_argument('--output-root', required=True)
    args = parser.parse_args()
    manifest = json.loads(record_path(args.manifest).read_text())
    result = run(manifest, moment(args.since), moment(args.until), args.output_root)
    print(json.dumps({'accounts': len(result['accounts']), 'requests': sum(a['requests'] for a in result['accounts']),
                      'status': result['status'], 'usage_queried': False, 'output_root': args.output_root}))
    if not result['complete']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
