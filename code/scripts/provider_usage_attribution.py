"""Offline entitlement evidence, independent of token pricing or quota fitting.

Account state is supporting evidence, never a per-request debit receipt. Missing
fields stay missing. No network, credentials, balance-delta allocation or polling.
"""
from bisect import bisect_right
from collections import Counter
from datetime import datetime, timezone
import json
import math


VERSION = 'entitlement-attribution-v2'


def timestamp(value):
    if isinstance(value, bool):
        raise ValueError('Boolean timestamp')
    if isinstance(value, (int, float)) and math.isfinite(value):
        return value
    value = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('Timezone required')
    return value.timestamp()


def fields(value, names):
    if not isinstance(value, dict):
        return {}
    return {k: value[k] for k in names if k in value and
            (value[k] is None or isinstance(value[k], (str, bool, int)) or
             isinstance(value[k], float) and math.isfinite(value[k]))}


def codex_evidence(limits, at):
    if not isinstance(limits, dict) or not limits:
        return []
    windows = []
    main = limits.get('limit_id') or 'unknown'
    extra = limits.get('by_limit_id')
    groups = {main: limits, **(extra if isinstance(extra, dict) else {})}
    for group, limit in groups.items():
        if not isinstance(limit, dict):
            continue
        models = [str(limit['limit_name']).lower()] if group != 'codex' and limit.get('limit_name') else []
        for kind in ('primary', 'secondary'):
            w = limit.get(kind)
            if isinstance(w, dict):
                windows.append({**fields(w, ('used_percent', 'resets_at')),
                                'window_duration_mins': w.get('window_minutes'),
                                'id': str(group) + '/' + kind, 'quota_group': group, 'models': models})
    return [{'scope': 'account_state', 'source': 'codex.token_count.rate_limits',
             'observed_at': at, 'windows': windows,
             'flags': fields(limits, ('plan_type', 'rate_limit_reached_type', 'spend_control_reached')),
             'credits': fields(limits.get('credits'), ('has_credits', 'unlimited', 'balance'))}]


def claude_evidence(info, at):
    if not isinstance(info, dict) or not info:
        return []
    windows = []
    unified = info.get('unifiedWindows')
    infos = [info, *[{**w, 'rateLimitType': kind}
                     for kind, w in (unified if isinstance(unified, dict) else {}).items() if isinstance(w, dict)]]
    for w in infos:
        kind = w.get('rateLimitType')
        if kind:
            models = ['*sonnet*'] if kind == 'seven_day_sonnet' else ['*opus*'] if kind == 'seven_day_opus' else []
            windows.append({'id': kind, 'quota_group': kind, 'models': models,
                            **fields(w, ('status', 'utilization')),
                            'resets_at': w.get('resetsAt')})
    return [{'scope': 'account_state', 'source': 'claude.rate_limit_info',
             'observed_at': at, 'windows': windows,
             'flags': fields(info, ('isUsingOverage', 'overageStatus', 'overageDisabledReason'))}]


def request_metadata(provider, value, at):
    names = {'claude': ('fallback_credit', 'service_tier', 'speed'), 'kimi': ('usageScope',)}
    kept = fields(value, names.get(provider, ()))
    return ([{'scope': 'request_metadata', 'source': provider + '.native_usage',
              'observed_at': at, 'fields': kept}] if kept else [])


def antigravity_evidence(chat, at, request_id):
    """Google ACP 1.4.5 ChatModelMetadata: credit_cost=13, consumed_credits=18.

    Credits: credit_type=1 (GOOGLE_ONE_AI=1), credit_amount=2 (int64).
    credit_cost alone is a model cost, not proof a credits wallet was charged.
    """
    from management_portal.execution.antigravity_backend import _pb_fields
    result = []
    if 13 in chat:
        result.append({'scope': 'request_metadata', 'source': 'antigravity.ChatModelMetadata.credit_cost',
                       'observed_at': at, 'fields': {'credit_cost': chat[13]}})
    if 18 in chat:
        credit = {n: v for n, w, v in _pb_fields(chat[18])}
        amount = credit.get(2, 0)
        # protobuf int64 uses unsigned varints for negative signed values.
        if isinstance(amount, int) and amount >= 1 << 63:
            amount -= 1 << 64
        result.append({'scope': 'request_debit', 'source': 'antigravity.ChatModelMetadata.consumed_credits',
                       'request_id': str(request_id), 'observed_at': at,
                       'debit': {'credit_type': credit.get(1, 0), 'amount': amount}})
    return result


def merge_evidence(left, right):
    result, seen = [], set()
    for item in [*left, *right]:
        key = json.dumps(item, sort_keys=True)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _applicable(window, model, provider=None):
    from fnmatch import fnmatchcase
    models = window.get('models') or []
    if models and not any(fnmatchcase(str(model).lower(), m.lower()) for m in models):
        return False
    # A named Codex extra limit without a model mapping is not a universal pool.
    group = str(window.get('quota_group') or '')
    if group == 'unknown':
        return False
    if provider == 'antigravity' and not models:
        # Cached ACP output names model families rather than supplying model IDs.
        # Use only exact recognized family labels; unknown labels stay unmapped.
        family = {'Gemini Models': 'gemini', 'Claude Models': 'claude'}.get(group)
        if family is None or not str(model).lower().startswith(family):
            return False
    if group.startswith('codex_') and not models:
        return False
    # This weekly model-specific included pool has no verified model mapping here.
    if group == 'seven_day_overage_included' and not models:
        return False
    return True


def _remaining(window):
    for key, convert in [('remaining_percent', lambda x: x), ('used_percent', lambda x: 100-x),
                         ('utilization', lambda x: 100*(1-x))]:
        v = window.get(key)
        if not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v):
            value = convert(v)
            if 0 <= value <= 100:
                return value
    return None


class Attributor:
    def __init__(self, account, *, max_state_age_seconds=300):
        if type(max_state_age_seconds) not in (int, float) or not math.isfinite(max_state_age_seconds) or not 0 <= max_state_age_seconds <= 900:
            raise ValueError('State evidence age must be between 0 and 900 seconds')
        self.account = account
        self.max_age = max_state_age_seconds
        grouped = {}
        # Only supplied, already-recorded account observations; never refresh.
        for q in account.get('quota_observations') or []:
            if not isinstance(q, dict) or q.get('account_id') != account['account_id']:
                continue
            if q.get('provider') and q['provider'] != account['provider']:
                continue
            try:
                at = timestamp(q.get('observed_at') or q['at'])
                datetime.fromtimestamp(at, timezone.utc)  # Reject out-of-range epochs.
            except (ValueError, TypeError, KeyError, OverflowError):
                continue
            key = (q.get('quota_group'), q.get('id'), tuple(q.get('models') or []), q.get('identity_epoch'))
            grouped.setdefault(key, []).append((at, q))
        self.index = []
        for rows in grouped.values():
            rows.sort(key=lambda x: x[0])
            self.index.append(([at for at, q in rows], rows))

    def attribute(self, event):
        at = timestamp(event['at'])
        evidence = list(event.get('quota_evidence') or [])
        for times, samples in self.index:
            i = bisect_right(times, at)-1
            if i < 0 or at-times[i] > self.max_age:
                continue
            latest = times[i]
            while i >= 0 and times[i] == latest:
                when, q = samples[i]
                i -= 1
                identity = q.get('identity_epoch')
                if identity is not None or event.get('identity_epoch') is not None:
                    if identity is None or identity != event.get('identity_epoch'):
                        continue
                evidence.append({'scope': 'account_state', 'source': q.get('evidence_source', 'supplied_quota_history'),
                                 'stale': bool(q.get('stale')),
                                 'observed_at': datetime.fromtimestamp(when, timezone.utc).isoformat(),
                                 'windows': [{**fields(q, ('id', 'quota_group', 'used_percent', 'remaining_percent',
                                                          'utilization', 'resets_at', 'status')),
                                              'models': list(q.get('models') or [])}]})
        evidence = merge_evidence([], evidence)
        result = {'version': VERSION, 'status': 'unknown', 'source': 'unknown',
                  'reason': 'no_request_debit_or_fresh_state', 'constraints': [],
                  'policy_ids': [], 'ordinary_capacity_eligible': False,
                  'evidence': evidence, 'limits': ['state_is_not_a_receipt',
                  'request_interval_and_external_concurrency_not_proven',
                  'unobserved_limits_and_other_funding_pools_may_apply',
                  'account_routing_depends_on_supplied_provenance']}
        debits, states, flags = [], [], []
        unavailable_at = None
        for e in evidence:
            origin = str(e.get('source', '')).split('.')[0]
            if origin in {'codex', 'claude', 'kimi', 'antigravity'} and origin != self.account['provider']:
                continue
            if e.get('scope') == 'request_debit' and e.get('request_id') == str(event['id']):
                d = e.get('debit') or {}
                amount = d.get('amount')
                if self.account['provider'] == 'antigravity' and e.get('source') == 'antigravity.ChatModelMetadata.consumed_credits' and \
                   isinstance(amount, int) and not isinstance(amount, bool) and amount > 0:
                    debits.append(d)
            if e.get('scope') != 'account_state':
                continue
            try:
                age = at-timestamp(e['observed_at'])
            except (ValueError, TypeError, KeyError, OverflowError):
                continue
            if not 0 <= age <= self.max_age:
                continue
            if e.get('stale'):
                unavailable_at = max(unavailable_at or float('-inf'), timestamp(e['observed_at']))
                continue
            applicable = []
            for w in e.get('windows') or []:
                if w.get('stale') or not _applicable(w, event.get('model', 'unknown'), self.account['provider']):
                    continue
                try:
                    if w.get('resets_at') is not None and timestamp(w['resets_at']) <= at:
                        continue
                except (ValueError, TypeError, OverflowError):
                    continue
                applicable.append({**fields(w, ('id', 'quota_group', 'resets_at', 'status')),
                                   'observed_at': e['observed_at'], 'remaining_percent': _remaining(w)})
            states.extend(applicable)
            # A later event in this same session supersedes earlier account flags.
            flags.append((timestamp(e['observed_at']), e.get('flags') or {}))
        # Superseded values from the same window must not outvote its latest
        # observation. Equal-time contradictions remain visible and unresolved.
        newest = {}
        for s in states:
            key = (s.get('quota_group'), s.get('id'))
            newest[key] = max(newest.get(key, float('-inf')), timestamp(s['observed_at']))
        states = [s for s in states if timestamp(s['observed_at']) == newest[(s.get('quota_group'), s.get('id'))]]
        result['constraints'] = merge_evidence([], states)
        conflict = any(len({s['remaining_percent'] for s in states if (s.get('quota_group'), s.get('id')) == key
                            and s['remaining_percent'] is not None}) > 1 for key in newest)
        if debits:
            result.update(status='confirmed', source='credits_involved', reason='request_linked_positive_consumed_credits',
                          debit_components=debits, credit_origin='unknown_purchased_or_promotional',
                          token_split=None)
            # A positive credit debit proves credit use, not exclusive credit funding.
            result['limits'].append('credits_use_confirmed_but_exclusivity_and_token_split_unknown')
        else:
            latest = max((t for t, f in flags), default=None)
            using = {f['isUsingOverage'] for t, f in flags if t == latest and type(f.get('isUsingOverage')) is bool}
            balances = [s['remaining_percent'] for s in states if s['remaining_percent'] is not None]
            latest_available = max((t for t, f in flags), default=float('-inf'))
            if unavailable_at is not None and unavailable_at >= latest_available:
                result['reason'] = 'latest_state_unavailable'
            elif len(using) > 1 or conflict:
                result['reason'] = 'conflicting_state'
            elif using == {True}:
                result.update(status='inferred', source='paid_extra', reason='fresh_isUsingOverage_state')
            elif balances and min(balances) > 0:
                result.update(status='inferred', source='subscription', reason='fresh_nonexhausted_applicable_windows')
            elif balances:
                result['reason'] = 'exhausted_or_transition_no_debit_proof'
        # Policies are explicit supplied applicability evidence, not a web guess or
        # inference from fitted capacity. They decorate funding; never allocate tokens.
        for policy in self.account.get('entitlement_policies') or []:
            if policy.get('account_id') != self.account['account_id'] or policy.get('applicability_confirmed') is not True:
                continue
            if not policy.get('evidence_source') or not policy.get('id') or not _applicable(policy, event.get('model')):
                continue
            try:
                applies = timestamp(policy['effective_from']) <= at < timestamp(policy['effective_until'])
            except (ValueError, TypeError, KeyError, OverflowError):
                continue
            if applies:
                result['policy_ids'].append(policy['id'])
        return result


def summarize(rows):
    counts, tokens, reasons = Counter(), Counter(), Counter()
    for event, attribution in rows:
        key = attribution['status'] + ':' + attribution['source']
        counts[key] += 1
        tokens[key] += event['usage'].get('total_tokens', 0)
        reasons[attribution['reason']] += 1
    return {'version': VERSION, 'requests': sum(counts.values()), 'requests_by_status_source': dict(counts),
            'tokens_by_status_source': dict(tokens), 'reasons': dict(reasons),
            'token_semantics': 'Whole request tokens grouped by evidence; NOT an allocation of tokens to funding sources.',
            'ordinary_capacity_filter_applied': False,
            'warning': 'Historical capacity fits remain estimates; attribution does not certify their funding source.'}
