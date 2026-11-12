"""Durable report artifacts and explicit version-checked Knowledge publication."""
from datetime import datetime, timezone
import hashlib
import json
import uuid

from management_common.file_permissions import atomic_write_private_bytes, ensure_private_directory
from management_common.instance_manifest import current_instance
from scripts.provider_usage_native import record_path


def markdown(report):
    def cell(value):
        return str(value).replace('|', '\\|').replace('\n', ' ')

    lines = ['# Provider Usage Estimate', '', f"Generated: {report.get('generated_at', '')}",
             f"Interval: {report['since']} to {report['until']} (end exclusive)", '',
             'Existing local records only. No Provider usage refresh or model call.',
             'Native session history is primary for native runtimes, including sessions with no Portal execution match.',
             'Portal execution/session provenance: ' + report.get('portal_provenance', {}).get('status', 'not_supplied') + '.',
             'Recorded tokens are not proof of complete account consumption. Residuals are NOT added to totals.',
             '100% projections assume the same observed model mix; their whole-account error bound is unknown.', '',
             '| Provider | Account | Requests | Recorded tokens | Positive execution residual | Unlinked Portal executions |',
             '| --- | --- | ---: | ---: | ---: | ---: |']
    for a in report['accounts']:
        missing = sum(not c.get('evidence_source_present', True) for c in a.get('resident_comparisons', []))
        lines.append(f"| {cell(a['provider'])} | {cell(a['account_id'])} | {a['requests']} | {a['totals'].get('total_tokens', 0):,} | {a.get('unplaced_execution_tokens', 0):,} | {missing} |")
    lines += ['', '## Entitlement Attribution', '',
              'Account state supports inference only. Credits evidence confirms involvement, not a token funding split.',
              'Historical capacity fits have not been filtered by this classifier.', '',
              '| Provider / Account | Requests by evidence status and source |', '| --- | --- |']
    for a in report['accounts']:
        counts = a.get('quota_attribution', {}).get('requests_by_status_source', {})
        lines.append(f"| {cell(a['provider'])} / {cell(a['account_id'])} | " +
                     cell('; '.join(f'{k}: {v}' for k, v in sorted(counts.items())) or 'not analyzed') + ' |')
    lines += ['', '## Models and Periods', '', '| Provider / Account | Period kind | Period | Model | Tokens |',
              '| --- | --- | --- | --- | ---: |']
    for a in report['accounts']:
        for b in a['buckets']:
            lines.append(f"| {cell(a['provider'])} / {cell(a['account_id'])} | {b['period_kind']} | {cell(b['period'])} | {cell(b['model'])} | {b['usage'].get('total_tokens', 0):,} |")
    lines += ['', '## Weekly Full Cycles', '',
              'Each cycle starts full at its reset; consumption is 100% minus the lowest remaining percentage seen in it.',
              'Tokens run from the cycle start to that lowest sample. The 100% figure assumes the same model and token mix.', '',
              '"USD at 100%" restates the cycle as corrected API list-price value of the whole weekly limit (see Quota Pricing).', '',
              '| Provider / Account | Quota group | Cycle start | Lowest at | Used | Recorded tokens | Tokens at 100% | USD at 100% | Actual / predicted | Status |',
              '| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |']
    for a in report['accounts']:
        for c in a.get('weekly_cycles', []):
            full = c['tokens_at_100_percent_same_mix']
            weighted = c.get('weighted_capacity')
            equivalent = f"{weighted['adjusted_usd_at_100_percent']:,.0f}" if weighted else '-'
            ratio = f"{weighted['actual_over_predicted']:g}" if weighted else '-'
            status = c['status'] + (' (partial, fitted)' if c.get('fit_basis') == 'partial' else '') + \
                (' (outlier)' if c.get('fit_outlier') else '')
            lines.append(f"| {cell(a['provider'])} / {cell(a['account_id'])} | {cell(c['quota_group'])} | {c['cycle_start']} | {c['final_observed_at']} | {c['used_percentage_points']:g}% | {c['recorded_tokens']['total_tokens']:,} | {format(full['total_tokens'], ',') if full else '-'} | {equivalent} | {ratio} | {status} |")
    lines += ['', 'No measured cycle means insufficient evidence or no applicable weekly subscription quota, not zero capacity.',
              'Per-model typed tokens for each cycle, split by service tier, are retained in the JSON artifact.', '',
              '## Quota Pricing', '',
              'Requests are priced at API list price (uncached input, cached input and output separately), then corrected by',
              'a factor per model line and a fast-mode multiplier fitted over the cycles of one provider, policy era and plan.',
              'Exhausted cycles count, and so do cut-off ("partial") cycles that used enough and recorded almost nothing after',
              'their lowest sample. Cycles whose actual/predicted ratio is outside the listed inclusive bounds are left out',
              'and the fit repeated. Lines without enough data stay at list price. Groups with too few cycles keep the',
              'richest group\'s factors and fit only the scale.', '',
              '| Provider | Quota group | Policy era | Plan | USD at 100% | Line factors | Fast multiplier | Factors from | Unpriced models | Cycles (partial / external) | Outliers (kept ratio range) | Median relative error |',
              '| --- | --- | --- | --- | ---: | --- | --- | --- | --- | ---: | ---: | ---: |']
    for fit in report.get('quota_weights') or []:
        factors = ', '.join(f"{cell(k)} {v:g}" + ('' if k == fit['reference_line'] or k in fit['estimated_lines'] else ' (list)')
                            for k, v in fit['line_factors'].items())
        fast = f"{fit['fast_multiplier']:g}" if fit['fast_multiplier'] is not None else 'list price'
        unpriced = ', '.join(f'{cell(k)} {v:,}' for k, v in fit['unpriced_models'].items()) or '-'
        lines.append(f"| {cell(fit['provider'])} | {cell(fit['quota_group'])} | {cell(fit['policy_era'] or '-')} | {cell(fit['plan'])} | {fit['usd_at_100_percent']:,.0f} | {factors} | {fast} | {cell(fit['factors_from'])} | {unpriced} | {fit['cycles']} ({fit.get('partial_cycles', 0)} / {fit.get('external_usage_cycles', 0)}) | {fit.get('outlier_cycles', 0)} ([{fit.get('outlier_min_ratio', '-')}, {fit.get('outlier_max_ratio', '-')}]) | {fit['median_relative_error']:.0%} |")
    lines += ['', f"Prices: {(report.get('quota_weights') or [{}])[0].get('price_source', '-')}.", '',
              '## Remaining Evidence Gaps', '']
    for a in report['accounts']:
        lines.append(f"- {cell(a['provider'])} / {cell(a['account_id'])}: " + ', '.join(a['gaps']))
        if a.get('source_failures'):
            lines.append('  Source failure counts: ' + json.dumps(a['source_failures'], sort_keys=True))
    lines += ['', '## Method and Operations', '',
              'Native session history supplies native runtime totals. Portal records only enrich account routing and reconciliation.',
              'An unlinked Portal execution is not evidence that raw usage is absent and never removes native requests from totals.',
              'Direct API execution results use their recorded api_id, model and usage; harness execution totals are comparisons only.',
              'All paths use the selected instance deployment resolver or registered Provider homes.',
              'No schedule is installed or usage queried by this report. Legacy daily timers and historical prices are not asserted current.',
              'API cost is not recomputed using obsolete price constants. Other-machine consumption remains outside local coverage.', '']
    return '\n'.join(lines)


def write_report(report, output_root='features:provider_usage'):
    root = record_path(output_root)
    ensure_private_directory(root)
    stem = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    instance = current_instance()
    artifacts = {}
    hashes = {}
    for suffix, data in (('json', json.dumps(report, ensure_ascii=False, indent=2) + '\n'), ('md', markdown(report))):
        path = root / (stem + '.' + suffix)
        payload = data.encode('utf-8')
        atomic_write_private_bytes(path, payload)
        atomic_write_private_bytes(root / ('latest.' + suffix), payload)
        artifacts[suffix] = instance.reference(path)
        hashes[suffix] = hashlib.sha256(payload).hexdigest()
    manifest = {'schema': 'tfi.provider-usage-artifacts.v1', 'generated_at': report.get('generated_at'),
                'artifacts': artifacts, 'sha256': hashes, 'provider_usage_refresh_requested': False}
    atomic_write_private_bytes(root / 'latest-manifest.json', (json.dumps(manifest, indent=2) + '\n').encode())
    return artifacts


def publish_report(call, resource_id, report, artifacts):
    import yaml
    current = call('ReadResource', address=resource_id)
    text = current.get('text', '')
    parts = text.split('---', 2) if text.startswith('---\n') else []
    if len(parts) != 3 or current.get('version') is None:
        raise ValueError('Knowledge resource lacks versioned front matter')
    metadata = yaml.safe_load(parts[1])
    if not isinstance(metadata, dict) or metadata.get('kb_id') != resource_id:
        raise ValueError('Knowledge resource identity mismatch')
    metadata.setdefault('links', {})['source'] = [
        'scripts/provider_weekly_usage.py', 'scripts/provider_weekly_usage.md', *artifacts.values()]
    body = markdown(report) + '\n## Artifacts\n\n' + '\n'.join(f'- `{ref}`' for ref in artifacts.values()) + '\n'
    replacement = '---\n' + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + '---\n' + body
    updated = call('UpdateResource', resource_id=resource_id,
                   changes={'text': replacement, 'expected_version': current['version']})
    return {'resource_id': resource_id, 'version': updated.get('version')}
