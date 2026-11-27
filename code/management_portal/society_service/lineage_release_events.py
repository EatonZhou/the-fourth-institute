"""Recognize ordered release and provider acceptance facts once per subscribed Event Source."""
from management_common import portal_release_events, provider_validation_timeouts
from . import lineage_primitives, lineage_program_runtime

EVENT = 'portal.release_published'
LOCATOR = 'portal_release_publications'
POLL_SECONDS = 15


def value(sequence, prefix="publication"):
    return f'{prefix}:{sequence}'


def prefetch(lineage_id, plan, *, event=EVENT):
    timeout = event == provider_validation_timeouts.EVENT
    facts = provider_validation_timeouts if timeout else portal_release_events
    locator = event if timeout else LOCATOR
    prefix = 'timeout' if timeout else 'publication'
    sources = {key: source for key, source in plan.get('event_sources', {}).items()
               if source.get('event_type') == event}
    if not sources:
        return {}
    runtime = lineage_program_runtime.read_runtime(lineage_id).get('event_sources', {})
    if timeout:
        # Establish a new subscriber's cursor before publishing overdue attempts,
        # including those recovered after a SocietyService restart.
        baseline, next_due = facts.collect_due()
    result = {}
    for key, source in sources.items():
        state = runtime.get(key, {})
        previous = state.get('state_value', '')
        if state.get('state_locator') != locator or not previous.startswith(prefix + ':'):
            if timeout:
                result[key] = {'baseline': baseline, 'records': facts.after(baseline),
                               'previous': value(baseline, prefix)}
            else:
                result[key] = {'baseline': facts.latest_sequence(), 'records': [], 'previous': ''}
        else:
            sequence = int(previous.split(':')[1])
            if timeout:
                records = facts.after(sequence)
            else:
                records = facts.after(sequence, latest_only=source.get('misfire_policy') == 'coalesce_latest')
            result[key] = {'baseline': None, 'records': records, 'previous': state.get('state_signature', '')}
        if timeout:
            result[key]['next_due'] = next_due
    return result


def pending(source_id, source, batch, fingerprint):
    timeout = source['event_type'] == provider_validation_timeouts.EVENT
    previous = batch['previous']
    result = []
    for record in batch['records']:
        signature = value(record['sequence'], 'timeout' if timeout else 'publication')
        result.append({'event_source_id': source_id,
            'event': lineage_primitives.make_event(source['event_type'], payload=record,
                correlation=signature, occurred_at=record['expired_at' if timeout else 'completed_at']),
            'cursor': signature, 'source_contract': fingerprint(source),
            'state_locator': source['event_type'] if timeout else LOCATOR, 'previous_state_signature': previous,
            'state_signature': signature, 'state_value': signature})
        previous = signature
    return result
