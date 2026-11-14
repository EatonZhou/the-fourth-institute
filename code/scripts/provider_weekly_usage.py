#!/usr/bin/env python3
"""Read existing usage only; no prompts, remote usage refreshes or ledger writes.
Portal runs additionally notify administrators of reset cards expiring in 7 days.

Offline: --manifest FILE --since ISO --until ISO
Portal: --resident-id ID --since ISO --until ISO [--max-pages 100]
Manifest accounts contain provider, account_id, sources[{format,path,session_id}],
optional quota_observations and resident_records. Paths are explicitly supplied
authorized exports. --local-records additionally reads registered native homes
and existing database provenance with local operator access. Output is JSON.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import uuid
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from management_portal.execution.scheduler_usage import normalize_token_usage
from management_portal.execution.provider_turn_usage import anthropic_request_usage, _kimi_request_usage, _CAMEL_KEYS
from management_portal.codex_app_server_client import codex_token_usage_delta
from scripts.provider_usage_native import expand_sources, opencode_rows, antigravity_rows, quota_rows, record_path
from scripts.provider_usage_quota_scope import scope_quota_rows
from scripts.provider_usage_events_import import provider_events
from scripts.provider_usage_attribution import (codex_evidence, claude_evidence, request_metadata,
                                               merge_evidence, Attributor, summarize)


def scoped_quota_file(path):
    """Normalized quota rows of one snapshot file, with named extra limits scoped to their model."""
    payload = json.loads(path.read_text())
    return list(scope_quota_rows(quota_rows(payload), payload))

NATIVE_PROVIDERS = frozenset(('codex', 'claude', 'kimi', 'dsh', 'opencode', 'antigravity'))
NATIVE_FORMATS = NATIVE_PROVIDERS | {'opencode_sqlite'}
from scripts.provider_usage_cycles import cycle_bounds, normalize_quota_endpoints, reset_evidence


def moment(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, timezone.utc)
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('timestamps must include a timezone')
    return result.astimezone(timezone.utc)


def iso(value):
    return moment(value).isoformat().replace('+00:00', 'Z')


def add(left, right):
    for key, value in right.items():
        left[key] = left.get(key, 0) + value


def rows(path, gaps, wanted=(), cursor=None):
    with record_path(path).open('rb') as stream:
        stream.seek(cursor.get('offset', 0) if cursor is not None else 0)
        for raw in stream:
            if cursor is not None:
                cursor['bytes_read'] = cursor.get('bytes_read', 0) + len(raw)
                if not raw.endswith(b'\n'):
                    gaps.add('pending_partial_json_line')
                    break
                cursor['offset'] = stream.tell()
            line = raw.decode('utf-8')
            if wanted and not any(marker in line for marker in wanted):
                continue
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    yield row
                else:
                    gaps.add('invalid_record')
            except json.JSONDecodeError:
                gaps.add('invalid_or_incomplete_json_line')


class NativeSource:
    """Yield content-free request observations, retaining provenance internally."""

    def __init__(self, source, gaps):
        self.source, self.gaps = source, gaps

    def event(self, identity, at, usage, model='', session='', quotas=(), service_tier=None, quota_evidence=()):
        if identity is None or identity == '':
            self.gaps.add('missing_request_identity')
            return None
        try:
            at = iso(at)
        except (ValueError, TypeError, OverflowError):
            self.gaps.add('missing_request_time')
            return None
        if not isinstance(usage, dict) or not usage:
            self.gaps.add('missing_token_usage')
            return None
        normalized = normalize_token_usage(usage)
        if not normalized:
            self.gaps.add('missing_token_usage')
            return None
        result = {'id': str(identity), 'at': at, 'usage': normalized,
                  'model': str(model or 'unknown'), 'session': str(session), 'quotas': list(quotas),
                  'execution_session': self.source.get('execution_session_id') or str(session)}
        if service_tier:
            result['service_tier'] = str(service_tier)
        if quota_evidence:
            result['quota_evidence'] = list(quota_evidence)
        return result


class CodexSource(NativeSource):
    """Codex rollout reader.

    ``service_tier`` is the thread's speed setting ("priority" = fast mode, which
    consumes the weekly limit faster than "default"). The App Server records it in
    ``thread_settings_applied``; sessions that never set it run at the default tier
    and their events carry no ``service_tier``.
    """

    def read(self):
        session = self.source.get('session_id', '')
        model, forked, started, metadata_seen = '', False, None, False
        cursor = self.source.get('_cursor')
        state = cursor.setdefault('parser', {}) if cursor is not None else {}
        session, model = state.get('session', session), state.get('model', model)
        forked, metadata_seen = state.get('forked', False), state.get('metadata_seen', False)
        started = moment(state['started']) if state.get('started') else None
        tier = state.get('service_tier')
        wanted = ('"session_meta"', '"turn_context"', '"token_count"', '"thread_settings_applied"')
        for row in rows(self.source['path'], self.gaps, wanted, cursor):
            p = row.get('payload') or {}
            if row.get('type') == 'event_msg' and p.get('type') == 'thread_settings_applied':
                settings = p.get('thread_settings') or {}
                if 'service_tier' in settings:
                    tier = settings.get('service_tier') or None
                continue
            if row.get('type') == 'session_meta':
                if metadata_seen:
                    continue  # Imported parent metadata must not change physical session identity.
                metadata_seen = True
                session = p.get('id') or session
                forked = bool(p.get('forked_from_id'))
                started = moment(row['timestamp']) if row.get('timestamp') else None
            elif row.get('type') == 'turn_context':
                model = p.get('model') or ''
                if 'service_tier' in p:
                    tier = p.get('service_tier') or None
            elif row.get('type') == 'event_msg' and p.get('type') == 'token_count':
                # Match the legacy report's exclusion of fork-import bursts.
                if forked and started and (moment(row['timestamp']) - started).total_seconds() <= 1:
                    self.gaps.add('fork_import_records_excluded')
                    continue
                info = p.get('info') or {}
                usage = info.get('last_token_usage')
                total = info.get('total_token_usage')
                evidence = codex_evidence(p.get('rate_limits'), row['timestamp'])
                quotas = []
                for sample in evidence:
                    for window in sample['windows']:
                        if window.get('window_duration_mins') == 10080:
                            used = window.get('used_percent')
                            quotas.append({**window, 'at': row['timestamp'],
                                           'remaining_percent': 100-used if type(used) in (int, float) else None})
                native_id, counted = codex_token_usage_delta({
                    'last': {_CAMEL_KEYS[k]: v for k, v in (usage or {}).items() if k in _CAMEL_KEYS},
                    'total': {_CAMEL_KEYS[k]: v for k, v in (total or {}).items() if k in _CAMEL_KEYS},
                    'modelContextWindow': info.get('model_context_window'),
                })
                if not native_id:
                    if quotas:
                        yield {'quota_only': True, 'at': iso(row.get('timestamp')), 'quotas': quotas}
                    continue  # Compaction context estimates are not consumed tokens.
                if total:
                    identity = native_id
                elif row.get('ordinal') is not None:
                    identity = str(row['ordinal'])
                else:
                    # No canonical identity survives some sanitized exports.
                    self.gaps.add('codex_identity_fallback_timestamp_usage')
                    identity = json.dumps([row.get('timestamp'), usage], sort_keys=True)
                yield self.event(identity, row.get('timestamp'), counted, model, session, quotas, tier, evidence)
        state.update(session=session, model=model, forked=forked, metadata_seen=metadata_seen,
                     started=iso(started) if started else None, service_tier=tier)


class ClaudeSource(NativeSource):
    def read(self):
        cursor = self.source.get('_cursor')
        state = cursor.setdefault('parser', {}) if cursor is not None else {}
        current = state.get('quota_evidence', [])
        for row in rows(self.source['path'], self.gaps, ('"usage"', '"rate_limit_info"', '"quotaLimits"'), cursor):
            msg = row.get('message') or {}
            error = row.get('apiErrorParams') or {}
            info = row.get('rate_limit_info') or row.get('quotaLimits') or (error.get('rate_limit_info') if isinstance(error, dict) else None)
            if isinstance(info, dict) and row.get('timestamp'):
                current = claude_evidence(info, row['timestamp'])
            if row.get('type') == 'assistant' and isinstance(msg.get('usage'), dict):
                yield self.event(msg.get('id') or row.get('requestId'), row.get('timestamp'),
                                 anthropic_request_usage(msg['usage']), msg.get('model'),
                                 row.get('sessionId') or self.source.get('session_id', ''),
                                 quota_evidence=current + request_metadata('claude', msg['usage'], row.get('timestamp')))
        state['quota_evidence'] = current


class KimiSource(NativeSource):
    def read(self):
        cursor = self.source.get('_cursor')
        occurrences = cursor.setdefault('parser', {}).setdefault('occurrences', {}) if cursor is not None else {}
        agent = Path(self.source['path']).parent.name
        for row in rows(self.source['path'], self.gaps, ('"usage.record"',), cursor):
            if row.get('type') == 'usage.record':
                digest = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
                occurrences[digest] = occurrences.get(digest, 0) + 1
                identity = row.get('requestId') or row.get('id') or f'{agent}:{digest}:{occurrences[digest]}'
                if not (row.get('requestId') or row.get('id')):
                    self.gaps.add('kimi_no_upstream_request_id_local_occurrence_identity')
                if not row.get('time'):
                    self.gaps.add('missing_request_time')
                    continue
                yield self.event(identity, row.get('time', 0) / 1000,
                                 _kimi_request_usage(row.get('usage') or {}), row.get('model'),
                                 self.source.get('session_id', ''),
                                 quota_evidence=request_metadata('kimi', row, row.get('time', 0) / 1000))


class DshSource(NativeSource):
    def read(self):
        model = ''
        cursor = self.source.get('_cursor')
        state = cursor.setdefault('parser', {}) if cursor is not None else {}
        model = state.get('model', '')
        for row in rows(self.source['path'], self.gaps, ('"request/context"', '"assistant/message"'), cursor):
            data = row.get('data') or {}
            if row.get('type') == 'request/context':
                model = data.get('model') or ''
            elif row.get('type') == 'assistant/message' and isinstance(data.get('usage'), dict):
                u = data['usage']
                yield self.event(row.get('seq'), row.get('time', 0) / 1000,
                                 {'input_tokens': u.get('inputTokens', 0) + u.get('cacheReadTokens', 0) + u.get('cacheWriteTokens', 0),
                                  'cached_input_tokens': u.get('cacheReadTokens', 0),
                                  'output_tokens': u.get('outputTokens', 0),
                                  'reasoning_output_tokens': u.get('reasoningTokens', 0)},
                                 model, self.source.get('session_id', ''))
        state['model'] = model


class OpenCodeSqliteSource(NativeSource):
    def read(self):
        for row in opencode_rows(self.source, self.gaps):
            yield self.event(row['id'], row['at'], row['usage'], row['model'], row['session'])


class OpenCodeSource(NativeSource):
    def read(self):
        payload = json.loads(record_path(self.source['path']).read_text())
        for row in payload.get('requests') or []:
            yield self.event(row.get('id'), row.get('at_ms', 0) / 1000, row.get('usage'),
                             row.get('model'), self.source.get('session_id', ''))


class AntigravitySource(NativeSource):
    def read(self):
        for row in antigravity_rows(self.source, self.gaps):
            yield self.event(row['id'], row['at'], row['usage'], row['model'], row['session'],
                             quota_evidence=row.get('quota_evidence', ()))


class RequestExportSource(NativeSource):
    def read(self):
        # Explicit exports of existing API/session request records, not estimates.
        for row in rows(self.source['path'], self.gaps, cursor=self.source.get('_cursor')):
            yield self.event(row.get('request_id'), row.get('occurred_at'), row.get('usage'),
                             row.get('model'), row.get('session_id') or self.source.get('session_id', ''))


SOURCES = {'codex': CodexSource, 'claude': ClaudeSource, 'kimi': KimiSource,
           'dsh': DshSource, 'opencode': OpenCodeSource, 'antigravity': AntigravitySource,
           'requests': RequestExportSource, 'opencode_sqlite': OpenCodeSqliteSource}


def report_account(account, since, until, timezone_name, cache=None, provider_events=(), plan_periods=(),
                   attribution_sink=None):
    gaps, events, formats, failures, evidence_sessions = set(), {}, {}, {}, set()
    sources = account.get('sources') or []
    has_native_format = any(s['format'] in NATIVE_FORMATS for s in sources)
    native_primary = (account['provider'] in NATIVE_PROVIDERS or has_native_format
                      or account.get('account_kind') == 'subscription_login')
    execution_requests = account.get('execution_requests') or []
    if native_primary and execution_requests:
        gaps.add('portal_execution_requests_excluded_from_native_totals')
    for record in [] if native_primary else execution_requests:
        event = NativeSource({}, gaps).event(record.get('request_id'), record.get('occurred_at'),
            record.get('usage'), record.get('model'), record.get('session_id', ''))
        if event and event['session'] and since <= moment(event['at']) < until:
            event['execution_result'] = True
            events[(event['session'], event['id'])] = event
            evidence_sessions.add(event['execution_session'])
            formats[event['session']] = 'execution_result'
            gaps.add('execution_result_usage_only_unreported_retries_unknown')
    quotas = [q for q in account.get('quota_observations') or []
              if not q.get('account_id') or q['account_id'] in (account['account_id'], account.get('route_id'))]
    for path in account.get('quota_files') or []:
        try:
            # Bump the kind whenever quota_rows output changes; the cache keys only on file stamps.
            snapshots = cache.metadata(path, 'quota-v3', scoped_quota_file) if cache else scoped_quota_file(record_path(path))
            quotas.extend({**q, 'evidence_source': q.get('evidence_source', 'daily_snapshot')} for q in snapshots
                          if not q.get('account_id') or q['account_id'] in (account['account_id'], account.get('route_id')))
        except (OSError, ValueError, TypeError):
            gaps.add('quota_history_file_unreadable')
    if account['provider'] == 'codex':
        quotas = [{**q, 'quota_group': q.get('quota_group') or 'codex'} for q in quotas]
    scanned = 0
    expanded = expand_sources(sources, gaps, cache)
    if cache and callable(getattr(cache, 'prefetch', None)):
        expanded = cache.prefetch(s for s in expanded
                                  if not has_native_format or s['format'] in NATIVE_FORMATS)
        if has_native_format and any(s['format'] not in NATIVE_FORMATS for s in sources):
            gaps.add('non_native_export_excluded_from_native_totals')
    for source in expanded:
        if has_native_format and source['format'] not in NATIVE_FORMATS:
            gaps.add('non_native_export_excluded_from_native_totals')
            continue
        if source.get('provenance_scan_failed'):
            gaps.add('native_provenance_metadata_unreadable')
        try:
            scanned += 1
            if Path(source['path']).is_file():
                evidence_sessions.add(source.get('execution_session_id') or source.get('session_id', ''))
            reader_type = SOURCES[source['format']]
            incoming = cache.read(source, reader_type, gaps, since, until) if cache else reader_type(source, gaps).read()
            for event in incoming:
                if not event:
                    continue
                if event.get('quota_only'):
                    if since <= moment(event['at']) < until:
                        quotas.extend({**q, 'evidence_source': 'native_quota'} for q in event['quotas'])
                    continue
                if not event['session']:
                    gaps.add('missing_session_identity_record_excluded')
                    continue
                if not since <= moment(event['at']) < until:
                    continue
                old_format = formats.setdefault(event['session'], source['format'])
                if old_format != source['format']:
                    gaps.add('overlapping_formats_same_session_excluded')
                    continue
                key = (event['session'], event['id'])
                previous = events.get(key)
                if previous:
                    if previous['model'] != event['model']:
                        previous['model'] = 'unknown'
                        gaps.add('conflicting_request_model')
                    # Claude content blocks repeat a response with growing counts.
                    previous['usage'] = {k: max(previous['usage'].get(k, 0), event['usage'].get(k, 0))
                                         for k in previous['usage'].keys() | event['usage'].keys()}
                    previous['quota_evidence'] = merge_evidence(previous.get('quota_evidence', []),
                                                                 event.get('quota_evidence', []))
                else:
                    events[key] = event
                quotas.extend({**q, 'evidence_source': 'native_quota'} for q in event['quotas'])
        except (OSError, ValueError, TypeError, KeyError, ImportError, sqlite3.Error) as error:
            gaps.add('source_unreadable_or_invalid:' + str(source.get('format', 'unknown')))
            kind = type(error).__name__
            if isinstance(error, sqlite3.Error):
                kind = 'sqlite_missing_table' if 'no such table' in str(error) else getattr(error, 'sqlite_errorname', kind)
            key = source.get('format', 'unknown') + ':' + kind
            failures[key] = failures.get(key, 0) + 1
    attribution_observations = list(quotas)
    quotas = normalize_quota_endpoints(quotas, moment)
    # Index snapshots once: one binary search per model/group, not one full
    # historical quota scan for every request. A newer sample supersedes an old one.
    from bisect import bisect_right
    quota_index = {}
    for quota in quotas:
        try:
            if quota.get('stale') or quota.get('window_duration_mins') != 10080:
                continue
            at, reset = moment(quota['at']), moment(quota['resets_at'])
            key = (quota.get('quota_group', ''), tuple(sorted(quota.get('models') or [])))
            quota_index.setdefault(key, []).append((at, reset, quota))
        except (ValueError, TypeError, KeyError, OverflowError):
            gaps.add('invalid_quota_observation')
    indexed = []
    for (group, models), values in quota_index.items():
        values.sort(key=lambda q: (q[0], q[1]))
        indexed.append((models, [q[0] for q in values], values))
    buckets = {}
    for event in events.values():
        candidates = []
        at = moment(event['at'])
        for models, times, values in indexed:
            if models and event['model'] not in models:
                continue
            i = bisect_right(times, at) - 1
            if i >= 0 and at < values[i][1]:
                candidates.append(values[i][2])
        groups = {q.get('quota_group', '') for q in candidates}
        if len(groups) == 1:
            quota = max(candidates, key=lambda q: moment(q['at']))
            end = iso(quota['resets_at'])
            cycle = ('observed_reset', quota.get('quota_group', ''), end)
            gaps.add('cycle_start_unknown_not_a_complete_cycle_total')
        else:
            local = moment(event['at']).astimezone(ZoneInfo(timezone_name))
            year, week, _ = local.isocalendar()
            cycle = ('calendar_week', '', f'{year}-W{week:02}')
            gaps.add('weekly_reset_unavailable_or_ambiguous')
        key = (*cycle, event['model'])
        bucket = buckets.setdefault(key, {'period_kind': cycle[0], 'quota_group': cycle[1],
                                         'period': cycle[2], 'model': event['model'], 'usage': {}, 'requests': 0})
        bucket['requests'] += 1
        add(bucket['usage'], event['usage'])
        if event['model'] == 'unknown':
            gaps.add('unknown_model')
    totals = {}
    for event in events.values():
        add(totals, event['usage'])
    # Resident/AgentData totals are comparison evidence, never additional requests.
    comparisons = []
    from bisect import bisect_left
    sessions = {}
    for e in events.values():
        sessions.setdefault(e['execution_session'], []).append((moment(e['at']), e['usage'].get('total_tokens', 0)))
    session_index = {}
    for session, values in sessions.items():
        values.sort()
        prefix = [0]
        for _, tokens in values:
            prefix.append(prefix[-1] + tokens)
        session_index[session] = ([v[0] for v in values], prefix)
    for record in account.get('resident_records') or []:
        try:
            rstart, rend = moment(record['started_at']), moment(record['finished_at'])
        except (ValueError, TypeError, KeyError):
            gaps.add('resident_record_missing_execution_interval')
            continue
        if not since <= rstart < rend <= until:
            gaps.add('resident_record_outside_or_crossing_query_not_compared')
            continue
        times, prefix = session_index.get(record.get('session_id'), ([], [0]))
        measured = prefix[bisect_left(times, rend)] - prefix[bisect_left(times, rstart)]
        terminal = events.get((record.get('session_id'), record.get('execution_id')))
        if terminal and terminal.get('execution_result') and moment(terminal['at']) == rend:
            measured += terminal['usage'].get('total_tokens', 0)
        usage = record.get('usage') or {}
        expected = normalize_token_usage(usage.get('accounted', usage)).get('total_tokens', 0)
        comparisons.append({'execution_id': record.get('execution_id', ''), 'recorded_total': expected,
                            'request_total': measured, 'difference': expected - measured,
                            'evidence_source_present': record.get('session_id') in evidence_sessions})
        if record.get('session_id') not in evidence_sessions:
            gaps.add('portal_execution_not_linked_to_native_history')
        if measured != expected:
            gaps.add('resident_total_mismatch_not_added')
    if not events:
        gaps.add('no_attributable_requests_not_proof_of_zero_usage')
    if account['account_id'] == 'unresolved-account':
        gaps.add('historical_account_route_missing_or_ambiguous')
    if account.get('external_usage_declared'):
        gaps.add('account_also_used_outside_recorded_sources')
    gaps.add('coverage_limited_to_supplied_records')
    bounds = cycle_bounds(list(events.values()), quotas, moment)
    for bound in bounds:
        covered = since <= moment(bound['start_between'][0]) and moment(bound['end_between'][1]) <= until
        bound['query_covers_cycle'] = covered
        if not covered:
            bound['within_10_percent'] = None
    from scripts.provider_usage_capacity import full_cycle_capacity
    attributor = Attributor({**account, 'quota_observations': attribution_observations})
    def attributed():
        for event in events.values():
            attribution = attributor.attribute(event)
            if attribution_sink is not None:
                attribution_sink(event, attribution)
            yield event, attribution
    attribution_summary = summarize(attributed())
    cycles = full_cycle_capacity(events.values(), quotas, moment, since, until, provider_events, plan_periods)
    measured_cycles = [c for c in cycles if c['status'] == 'measured']
    return {'provider': account['provider'], 'account_id': account['account_id'], 'source_files': scanned,
            'usage_source': 'native_session_history' if native_primary else 'existing_api_records',
            'excluded_execution_requests': len(execution_requests) if native_primary else 0,
            'source_failures': failures,
            'quota_attribution': attribution_summary,
            'totals': totals, 'requests': len(events), 'buckets': sorted(buckets.values(), key=lambda b: (b['period'], b['model'])),
            'cycle_bounds': bounds, 'reset_evidence': reset_evidence(list(events.values()), quotas, moment),
            'resident_comparisons': comparisons,
            'weekly_cycles': cycles,
            'weekly_capacity_status': 'measured_full_cycles' if measured_cycles else 'insufficient_weekly_quota_evidence',
            'unplaced_execution_tokens': sum(max(0, c['difference']) for c in comparisons),
            'gaps': sorted(gaps)}


def build_report(manifest, since, until, timezone_name='Asia/Hong_Kong', cache=None):
    start, end = moment(since), moment(until)
    if start >= end:
        raise ValueError('since must precede until')
    ZoneInfo(timezone_name)
    accounts = manifest.get('accounts') or []
    identities = [(a['provider'], a['account_id']) for a in accounts]
    if len(set(identities)) != len(identities):
        raise ValueError('duplicate accounts: combine their sources instead')
    from scripts.provider_usage_weights import weight_report
    reports = [report_account(a, start, end, timezone_name, cache,
                              provider_events(manifest.get('provider_events'), a['provider'],
                                              manifest.get('ignored_provider_events') or ()),
                              declared_plans(manifest.get('plan_periods'), a['account_id']))
               for a in accounts]
    return {'schema': 'tfi.provider-weekly-usage.v1', 'since': iso(start), 'until': iso(end),
            'generated_at': iso(datetime.now(timezone.utc)), 'provider_usage_refresh_requested': False,
            'portal_provenance': manifest.get('portal_provenance', {'status': 'not_supplied'}),
            'provider_events_imported': bool(manifest.get('provider_events')),
            'timezone': timezone_name,
            # Also annotates measured cycles.
            'quota_weights': weight_report(reports, manifest.get('external_usage_accounts') or ()),
            'accounts': reports}


def declared_plans(periods, account_id):
    """Declared plan periods of one account: {account_id: [{plan, from, until}]}, ISO times."""
    return [{'plan': str(p['plan']), 'from': moment(p['from']), 'until': moment(p['until'])}
            for p in (periods or {}).get(account_id) or []]


def collect_portal(call, since, until, max_pages, timezone_name='Asia/Hong_Kong', *, accounts=None):
    """Existing authorized ISA only. Never force native account observation."""
    if accounts is None:
        accounts = call('ListProviderAccounts', refresh=False).get('accounts') or []
    result = []
    # Codex's existing sanitized export is the only native-history ISA today.
    import tempfile
    from management_common.portal_paths import path as portal_path
    from management_common.file_permissions import ensure_private_directory
    temp_root = portal_path('runtime') / 'tmp'
    ensure_private_directory(temp_root)
    with tempfile.TemporaryDirectory(prefix='provider-usage-', dir=temp_root) as directory:
        for index, account in enumerate(accounts):
            entry = {'provider': account['provider'], 'account_id': account['account_id'], 'sources': []}
            if account['provider'] == 'codex' and account.get('kind') == 'subscription_login':
                files, cursor, complete = {}, '', False
                try:
                    for _ in range(max_pages):
                        page = call('GetProviderUsageEvents', account_id=account['account_id'], since=since, until=until, cursor=cursor, limit=1000)
                        for row in page.get('records') or []:
                            # Hash source names rather than interpreting remote paths.
                            source = str(row['source'])
                            files.setdefault(source, []).append(row['record'])
                        cursor = page.get('next_cursor') or ''
                        if not cursor:
                            complete = True
                            break
                    for n, records in enumerate(files.values()):
                        path = Path(directory) / f'{index}-{n}.jsonl'
                        path.write_text(''.join(json.dumps(r) + '\n' for r in records))
                        entry['sources'].append({'format': 'codex', 'path': str(path)})
                    item = build_report({'accounts': [entry]}, since, until, timezone_name)['accounts'][0]
                    if not complete:
                        item['gaps'].append('history_pagination_truncated')
                except Exception:
                    item = build_report({'accounts': [entry]}, since, until, timezone_name)['accounts'][0]
                    item['gaps'].append('native_history_read_failed')
            else:
                item = build_report({'accounts': [entry]}, since, until, timezone_name)['accounts'][0]
                item['gaps'].append('native_history_ISA_not_available_for_this_account')
            # Current quota is not historical evidence for earlier requests.
            item['current_quota_windows'] = [{k: w.get(k) for k in ('id', 'quota_group', 'resets_at', 'observed_at', 'stale')}
                                             for w in account.get('rate_limits') or []]
            result.append(item)
    return {'schema': 'tfi.provider-weekly-usage.v1', 'since': since, 'until': until, 'accounts': result}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--manifest', help='Deployment area reference or canonical configured host path')
    source.add_argument('--resident-id')
    parser.add_argument('--since', required=True)
    parser.add_argument('--until', required=True)
    parser.add_argument('--timezone', default='Asia/Hong_Kong')
    parser.add_argument('--max-pages', type=int, default=100)
    parser.add_argument('--local-records', action='store_true', help='Local operator/service mode: native files and existing PostgreSQL provenance, no remote usage reads')
    parser.add_argument('--native-only', action='store_true', help='Read native history without querying Portal execution/session provenance')
    parser.add_argument('--isa-export', action='store_true', help='Explicit bounded ISA export instead of local incremental records')
    parser.add_argument('--rebuild', action='store_true', help='Explicitly rebuild the instance usage index from raw records')
    parser.add_argument('--cache-root', default='features:provider_usage/cache')
    parser.add_argument('--workers', type=int, default=8, help='Native file parsing processes, 1-16; 1 uses serial parsing')
    parser.add_argument('--refresh-cache-root', default='features:provider_usage/refresh-cache')
    parser.add_argument('--skip-refresh-evidence', action='store_true', help='Skip retained Home/Claude quota evidence ingestion')
    parser.add_argument('--subscription-only', action='store_true', help='Only registered subscription accounts in local mode')
    parser.add_argument('--skip-reset-reminders', action='store_true', help='Do not send the default Portal administrator reset-card expiry reminders')
    parser.add_argument('--account', action='append', default=[], help='Only this account ID in local mode; repeatable')
    parser.add_argument('--provider-events', help='Saved provider-wide events export (global resets, limit policy changes)')
    parser.add_argument('--ignore-provider-event', action='append', default=[], metavar='ID',
                        help='Event ID from --provider-events that does not apply, e.g. a limit change whose '
                             'announcement excludes this tool; repeatable')
    parser.add_argument('--external-usage', action='append', default=[], metavar='ACCOUNT',
                        help='Account also used where its records do not reach this report (another computer); '
                             'its cycles are marked; weeks with much unrecorded use fall out of the quota '
                             'pricing fit as outliers; repeatable')
    parser.add_argument('--plan-periods', help='Declared plans: {account_id: [{plan, from, until}]}, used where samples carry none')
    parser.add_argument('--instance', help='Instance name in the host registry; otherwise PORTAL_INSTANCE_ROOT')
    parser.add_argument('--write-report', action='store_true')
    parser.add_argument('--output-root', default='features:provider_usage', help='Deployment area reference for report artifacts')
    parser.add_argument('--knowledge-resource', help='Update this existing Knowledge resource; requires --local-records')
    args = parser.parse_args(argv)
    if args.isa_export and (not args.resident_id or args.local_records):
        parser.error('--isa-export requires --resident-id and cannot be combined with --local-records')
    if args.resident_id and not args.isa_export:
        args.local_records = True
    if args.native_only and not args.local_records:
        parser.error('--native-only requires local records with --resident-id')
    if args.instance:
        import os
        from management_common import host_instances
        os.environ['PORTAL_INSTANCE_ROOT'] = str(host_instances.find(args.instance).root)
    start, end = moment(args.since), moment(args.until)
    if not start < end or args.max_pages < 1 or not 1 <= args.workers <= 16:
        parser.error('invalid window or page budget')
    if args.local_records and not args.resident_id:
        parser.error('--local-records requires --resident-id')
    if args.knowledge_resource and not args.local_records:
        parser.error('--knowledge-resource requires --local-records')
    def open_cache():
        from scripts.provider_usage_incremental import IncrementalCache
        from scripts.provider_usage_parallel import ParallelIncrementalCache
        try:
            if args.workers == 1:
                return IncrementalCache(args.cache_root, rebuild=args.rebuild)
            return ParallelIncrementalCache(args.cache_root, rebuild=args.rebuild, workers=args.workers)
        except ValueError as error:
            parser.error(str(error))
    provider_events_payload = json.loads(record_path(args.provider_events).read_text()) if args.provider_events else None
    plan_periods_payload = json.loads(record_path(args.plan_periods).read_text()) if args.plan_periods else None

    def with_imports(manifest):
        if args.ignore_provider_event:
            manifest['ignored_provider_events'] = list(args.ignore_provider_event)
        if args.external_usage:
            manifest['external_usage_accounts'] = list(args.external_usage)
            for account in manifest.get('accounts') or []:
                if account.get('account_id') in args.external_usage:
                    account['external_usage_declared'] = True
        if provider_events_payload:
            manifest['provider_events'] = provider_events_payload
        if plan_periods_payload:
            manifest['plan_periods'] = plan_periods_payload
        return manifest
    if args.manifest:
        from contextlib import closing
        with closing(open_cache()) as cache:
            manifest = with_imports(json.loads(record_path(args.manifest).read_text()))
            result = build_report(manifest, args.since, args.until, args.timezone, cache)
            result['incremental'] = dict(cache.stats)
    else:
        if not args.local_records and end - start > timedelta(days=60):
            parser.error('Portal export window must not exceed 60 days')
        from scripts.resident_hatg_instruction import execute
        def call(name, **arguments):
            return execute(args.resident_id, {'schema': 'tfi.hatg-instruction.v2', 'instruction': name,
                           'arguments': arguments, 'idempotency_key': 'usage-report:' + uuid.uuid4().hex},
                           request_timeout=30, max_attempts=1)['result']
        accounts = call('ListProviderAccounts', refresh=False).get('accounts') or []
        reminders = {'status': 'disabled'}
        if not args.skip_reset_reminders:
            from scripts.provider_usage_reset_reminders import notify_expiring_resets
            try:
                reminders = notify_expiring_resets(call, accounts)
            except Exception as error:
                reminders = {'status': 'failed', 'error_code': type(error).__name__}
                print('Reset-card reminders failed: ' + type(error).__name__, file=sys.stderr)
        if args.local_records:
            from contextlib import closing
            from scripts.provider_usage_local import local_manifest
            if args.subscription_only:
                accounts = [a for a in accounts if ((a.get('registration') or {}).get('kind') or a.get('kind')) == 'subscription_login']
            if args.account:
                accounts = [a for a in accounts if a.get('account_id') in set(args.account)]
            with closing(open_cache()) as cache:
                manifest = with_imports(local_manifest(accounts, start, end, cache=cache, portal_evidence=not args.native_only,
                                                       home_evidence=args.skip_refresh_evidence))
                if args.subscription_only or args.account:
                    allowed = {(a['provider'], a['account_id']) for a in accounts}
                    manifest['accounts'] = [a for a in manifest['accounts'] if (a['provider'], a['account_id']) in allowed]
                refresh = {'status': 'disabled'}
                if not args.skip_refresh_evidence:
                    from scripts.provider_usage_refresh_journal import enrich_manifest
                    refresh = enrich_manifest(manifest, accounts, start, end, args.refresh_cache_root)
                result = build_report(manifest, args.since, args.until, args.timezone, cache)
                result['incremental'] = dict(cache.stats)
                result['refresh_evidence_ingestion'] = refresh
        else:
            result = collect_portal(call, args.since, args.until, args.max_pages, args.timezone, accounts=accounts)
        result['reset_card_reminders'] = reminders
    if args.write_report or args.knowledge_resource:
        from scripts.provider_usage_report import write_report, publish_report
        artifacts = write_report(result, args.output_root)
        publication = publish_report(call, args.knowledge_resource, result, artifacts) if args.knowledge_resource else None
        print(json.dumps({'artifacts': artifacts, 'publication': publication,
                          'reset_card_reminders': result.get('reset_card_reminders', {'status': 'offline'}),
                          'provider_usage_refresh_requested': False,
                          'portal_provenance': result.get('portal_provenance'),
                          'incremental': result.get('incremental'),
                          'accounts': [{'provider': a['provider'], 'account_id': a['account_id'],
                                        'requests': a['requests'], 'tokens': a['totals'].get('total_tokens', 0),
                                        'measured_weekly_cycles': sum(c['status'] == 'measured'
                                                                      for c in a.get('weekly_cycles', [])),
                                        'gaps': a['gaps']}
                                       for a in result['accounts']]}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
