"""Audit retained provenance, optionally reparse only a previous report's unlocated sessions.

No provider processes, model calls, remote usage queries, or full token-index rebuild.
File presence is not proof of complete token coverage. Direct API terminal usage is
reported separately from native request logs.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import uuid

from scripts.provider_usage_native import expand_sources, record_path
from scripts.provider_weekly_usage import moment, report_account


def coverage(manifest, baseline=None):
    counts, months, unresolved, comparisons = defaultdict(Counter), defaultdict(Counter), [], []
    diagnostics, conflicts = [], []
    previous = {r['execution_id'] for a in (baseline or {}).get('accounts', [])
                for r in a.get('resident_comparisons', []) if not r['evidence_source_present']}
    for account in manifest['accounts']:
        provider = account['provider']
        gaps = set()
        sources = list(expand_sources(account.get('sources', []), gaps))
        if any(s.get('provenance_scan_failed') for s in sources):
            gaps.add('native_provenance_metadata_unreadable')
        if gaps:
            diagnostics.append({'provider': provider, 'account_id': account['account_id'], 'discovery_gaps': sorted(gaps)})
        located = {s['execution_session_id'] for s in sources if record_path(s['path']).is_file()}
        terminal = {r['request_id'] for r in account.get('execution_requests', [])}
        selected = []
        for r in account.get('resident_records', []):
            c = counts[provider]
            month = (r.get('started_at') or 'unknown')[:7]
            m = months[(provider, month)]
            c['executions_with_usage'] += 1
            m['executions_with_usage'] += 1
            status = ('execution_result_only' if r['execution_id'] in terminal or r.get('evidence_kind') == 'execution_result' else
                      'native_source_located' if r['session_id'] in located else 'source_unlocated')
            c[status] += 1
            m[status] += 1
            if r.get('native_link_basis', 'provider_session') != 'provider_session':
                c['native_link_recovered'] += 1
            if r.get('native_link_conflict'):
                c['native_link_conflict'] += 1
                conflicts.append({'provider': provider, 'account_id': account['account_id'],
                                  'execution_id': r['execution_id'], 'session_id': r['session_id'],
                                  'source_present': r['session_id'] in located})
            if status == 'source_unlocated':
                reason = ('conflicting_native_links' if r.get('native_link_conflict') else
                          'logical_placeholder_without_proven_native_link' if r['session_id'].startswith('execution-') else
                          'known_session_source_not_located_in_configured_roots')
                unresolved.append({'provider': provider, 'account_id': account['account_id'], 'reason': reason, **r})
            if r['execution_id'] in previous:
                selected.append(r)
        if not selected:
            continue
        sessions = {r['session_id'] for r in selected}
        scoped = {**account, 'resident_records': selected, 'quota_files': [], 'quota_observations': [],
                  'sources': [s for s in sources if s['execution_session_id'] in sessions],
                  'execution_requests': [r for r in account.get('execution_requests', [])
                                         if r['request_id'] in previous]}
        result = report_account(scoped, moment(baseline['since']), moment(baseline['until']),
                                baseline.get('timezone', 'Asia/Hong_Kong'))
        if result['source_failures']:
            diagnostics.append({'provider': provider, 'account_id': account['account_id'],
                                'scoped_source_failures': result['source_failures']})
        for r in result['resident_comparisons']:
            comparisons.append({'provider': provider, 'account_id': account['account_id'], **r})
    summary = Counter()
    for r in comparisons:
        summary['previously_unlocated'] += 1
        summary['now_located' if r['evidence_source_present'] else 'still_unlocated'] += 1
        if r['evidence_source_present']:
            summary['tokens_equal' if r['difference'] == 0 else 'tokens_differ'] += 1
    return {'schema': 'tfi.provider-usage-coverage.v1',
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'provider_usage_refresh_requested': False,
            'scope': 'native_file_presence_and_direct_api_terminal_evidence; raw token reconciliation only for baseline unlocated sessions',
            'providers': dict(counts),
            'months': [{'provider': p, 'month': m, **c} for (p, m), c in sorted(months.items())],
            'unlocated_executions': unresolved, 'baseline_summary': dict(summary),
            'source_diagnostics': diagnostics,
            'conflicting_links': conflicts,
            'baseline_comparisons': comparisons,
            'baseline_execution_ids_not_found': sorted(previous - {r['execution_id'] for r in comparisons})}


def markdown(result):
    lines = ['# Retained Usage Evidence Coverage', '',
             f"Window: {result.get('since', '')} through {result.get('until', '')}.", '',
             'This is source-presence coverage, not a complete token or billing audit.',
             'Unlocated does not mean deleted or additional uncounted consumption.',
             'No Provider usage refresh or model call was performed.', '',
             '| Provider | Executions with usage | Native source located | API terminal result only | Source unlocated |',
             '| --- | ---: | ---: | ---: | ---: |']
    for provider, counts in sorted(result['providers'].items()):
        values = [counts.get(key, 0) for key in ('executions_with_usage', 'native_source_located',
                                                'execution_result_only', 'source_unlocated')]
        lines.append('| ' + ' | '.join([provider, *map(str, values)]) + ' |')
    c = result.get('baseline_summary', {})
    lines += ['', '## Previous Unlocated Executions', '',
              f"Rechecked: {c.get('previously_unlocated', 0)}; located: {c.get('now_located', 0)}; "
              f"equal token totals: {c.get('tokens_equal', 0)}; differing totals: {c.get('tokens_differ', 0)}; "
              f"still unlocated: {c.get('still_unlocated', 0)}.", '',
              'The JSON retains per-month coverage, individual unresolved reasons, and scoped token comparisons.',
              f"Conflicting native linkage metadata: {len(result.get('conflicting_links', []))} executions; "
              'existing formal session identities are preserved rather than overwritten.',
              'All other historical rows were checked for source presence only, not token agreement.',
              'Other-machine consumption, unreported retries, and unobserved quota/redemption history remain outside this proof.', '']
    return '\n'.join(lines)


def write_coverage(result, output_root='features:provider_usage'):
    from management_common.file_permissions import atomic_write_private_bytes, ensure_private_directory
    from management_common.instance_manifest import current_instance
    root = record_path(output_root)
    ensure_private_directory(root)
    stem = 'coverage-' + moment(result['generated_at']).strftime('%Y%m%dT%H%M%SZ')
    artifacts = {}
    for suffix, content in [('json', json.dumps(result, indent=2, ensure_ascii=False) + '\n'), ('md', markdown(result))]:
        target = root / (stem + '.' + suffix)
        atomic_write_private_bytes(target, content.encode())
        atomic_write_private_bytes(root / ('latest-coverage.' + suffix), content.encode())
        artifacts[suffix] = current_instance().reference(target)
    return artifacts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resident-id', required=True)
    parser.add_argument('--since', required=True)
    parser.add_argument('--until', required=True)
    parser.add_argument('--baseline-report', help='Explicit scoped raw recheck, not a full historical reparse')
    parser.add_argument('--output-root', default='features:provider_usage')
    parser.add_argument('--cache-root', default='features:provider_usage/provenance-cache')
    parser.add_argument('--rebuild', action='store_true', help='Explicitly initialize/rebuild the private provenance index')
    args = parser.parse_args(argv)
    start, end = moment(args.since), moment(args.until)
    if start >= end:
        parser.error('invalid window')
    from scripts.resident_hatg_instruction import execute
    from scripts.provider_usage_local import local_manifest
    accounts = execute(args.resident_id, {'schema': 'tfi.hatg-instruction.v2',
        'instruction': 'ListProviderAccounts', 'arguments': {'refresh': False},
        'idempotency_key': 'usage-coverage:' + uuid.uuid4().hex},
        request_timeout=30, max_attempts=1)['result'].get('accounts') or []
    baseline = json.loads(record_path(args.baseline_report).read_text()) if args.baseline_report else None
    from contextlib import closing
    from scripts.provider_usage_incremental import IncrementalCache
    with closing(IncrementalCache(args.cache_root, rebuild=args.rebuild)) as cache:
        result = coverage(local_manifest(accounts, start, end, cache=cache), baseline)
    result.update(since=args.since, until=args.until)
    artifacts = write_coverage(result, args.output_root)
    print(json.dumps({'artifacts': artifacts, 'providers': result['providers'],
                      'baseline_summary': result['baseline_summary']}, indent=2))


if __name__ == '__main__':
    main()
