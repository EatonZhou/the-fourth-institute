"""Recognize each successful publication once per subscribed Event Source."""
from management_common import portal_release_events
from . import lineage_primitives, lineage_program_runtime

EVENT = 'portal.release_published'
LOCATOR = 'portal_release_publications'
POLL_SECONDS = 15


def value(sequence):
    return f'publication:{sequence}'


def prefetch(lineage_id, plan):
    sources = {key: source for key, source in plan.get('event_sources', {}).items()
               if source.get('event_type') == EVENT}
    if not sources:
        return {}
    runtime = lineage_program_runtime.read_runtime(lineage_id).get('event_sources', {})
    latest = portal_release_events.latest_sequence()
    result = {}
    for key, source in sources.items():
        state = runtime.get(key, {})
        previous = state.get('state_value', '')
        if state.get('state_locator') != LOCATOR or not previous.startswith('publication:'):
            result[key] = {'baseline': latest, 'records': [], 'previous': ''}
        else:
            sequence = int(previous.split(':')[1])
            result[key] = {'baseline': None, 'records': portal_release_events.after(
                sequence, latest_only=source.get('misfire_policy') == 'coalesce_latest'),
                           'previous': state.get('state_signature', '')}
    return result


def pending(source_id, source, batch, fingerprint):
    previous = batch['previous']
    result = []
    for record in batch['records']:
        signature = value(record['sequence'])
        result.append({'event_source_id': source_id,
            'event': lineage_primitives.make_event(EVENT, payload=record,
                correlation=signature, occurred_at=record['completed_at']),
            'cursor': signature, 'source_contract': fingerprint(source),
            'state_locator': LOCATOR, 'previous_state_signature': previous,
            'state_signature': signature, 'state_value': signature})
        previous = signature
    return result
