"""Read-only, bounded navigation over canonical conversation inputs.

No second history store. Business metadata is not an authorization grant: the
subject owner resolves each row with the actual caller before it is returned.
"""
from management_portal.business_context import message_metadata
from management_portal import isa_client
import time


def default_view(target, repository):
    """An authored isolated workflow declares attempt navigation by its policy.

    No hardcoded Paper Resident IDs, role-title inference, or Web callback.
    A mixed continuous/isolated target keeps the ordinary conversation default.
    """
    routes = []
    for lineage_id in repository.state_store.lineage_ids_for_resident(target.resident_id):
        graph = repository.state_store.lineage_definition(lineage_id)
        wires = graph.get('program', {}).get('wires', [])
        effects = {wire['source'] for wire in wires if wire.get('kind') == 'target' and wire.get('target') == target.resident_id}
        routes.extend(wire for wire in wires if wire.get('kind') == 'route' and wire.get('target') in effects)
    return 'attempts' if routes and all(wire.get('config', {}).get('context_policy') == 'fresh' for wire in routes) else 'messages'


def _visible(actor, resident_id, context, *, timeout=10.0):
    if timeout <= 0:
        raise TimeoutError('Business history authorization deadline exceeded')
    result = isa_client.call(actor, 'ResolveMessageContextReferences', {
        'target_resident_id': resident_id,
        'reference': {'type': 'business.history', 'version': 1,
                      'business_context': context}}, timeout=timeout)
    snapshot = result.get('snapshot')
    if not isinstance(snapshot, dict) or snapshot.get('type') != 'business-history':
        raise ValueError('Business history owner returned an invalid projection')
    if snapshot.get('visible') is False:
        return None
    if not isinstance(snapshot.get('label'), str):
        raise ValueError('Business history owner returned an invalid label')
    return snapshot


def page(resident_id, actor, *, cursor='', limit=50, input_message_id=''):
    from . import resident_protocol as protocol, resident_conversation_stream
    # Authorize the Resident before reading its storage. Subject authorization
    # below remains independent of that grant. Never hold intake locks over RPC.
    target = protocol._target(resident_id, actor)
    repository = resident_conversation_stream.get_repository()
    deadline = time.monotonic() + 10.0
    limit = max(1, min(int(limit), 200))
    if input_message_id:
        stored = repository.messages.stored_message(resident_id, input_message_id) or {}
        context = stored.get('metadata', {}).get('business_context')
        if not context or not _visible(actor, resident_id, context):
            raise PermissionError('Business attempt is unavailable')
        snapshot = protocol.get_resident_conversation_snapshot(
            resident_id, actor, input_message_id=input_message_id, history_limit=1000)
        return {'attempts': [], 'conversation': {key: snapshot.get(key, []) for key in ('messages', 'input_states')}}
    # A window, not an all-history count. Filter before grouping and limiting;
    # cursors continue scanning older canonical messages, including old untagged
    # records, without inventing their business association.
    boundary = ''
    if cursor:
        if not cursor.startswith('attempt-window:msg_') or len(cursor) > 160:
            raise ValueError('Invalid business history cursor')
        mid = cursor.removeprefix('attempt-window:')
        position = repository.message_positions([(resident_id, mid)]).get((resident_id, mid))
        if not position:
            raise ValueError('Business history cursor is no longer retained')
        boundary = str(position)
    raw = (protocol.get_conversation_messages(resident_id, actor, boundary, limit=min(1000, limit * 4))
           if cursor else protocol.get_resident_conversation_snapshot(
               resident_id, actor, history_limit=min(1000, limit * 4)))
    rows = []
    authorized = {}
    messages = list(reversed(raw.get('messages') or []))
    next_cursor = ''
    for index, message in enumerate(messages):
        # No ordinal, count or unfiltered business row is returned. The cursor
        # is a canonical conversation identity, resolved only inside this owner.
        next_cursor = ('attempt-window:' + message['message_id']
            if index + 1 < len(messages) or raw.get('next_page_token') else '')
        if message.get('role') != 'user':
            continue
        stored = repository.messages.stored_message(resident_id, message['message_id']) or {}
        metadata = stored.get('metadata')
        if not metadata:
            continue
        context = message_metadata(metadata)['business_context']
        cache_key = (context['application'], context['subject_ref'])
        if cache_key not in authorized:
            authorized[cache_key] = _visible(actor, resident_id, context, timeout=deadline-time.monotonic())
        subject = authorized[cache_key]
        if not subject:
            continue
        state = repository.input_states_by_id([(resident_id, message['message_id'])]).get((resident_id, message['message_id']), {})
        rows.append({'input_message_id': message['message_id'], 'business_context': context,
                     'label': subject['label'], 'created_at': message.get('created_at', ''),
                     'state': state.get('state', 'unknown'), 'error': state.get('error', ''),
                     'reply_message_ids': state.get('reply_message_ids', [])})
        if len(rows) == limit:
            break
    return {'attempts': rows, 'next_page_token': next_cursor, 'default_view': default_view(target, repository)}
