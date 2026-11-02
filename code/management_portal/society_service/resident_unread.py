"""Raise the viewer's alert on new Resident results, with the event that brings them."""
from __future__ import annotations

from management_common.principal import actor_principal, message_recipients
from management_portal import conversation_receipt_store

from . import family_chat_storage, family_storage, resident_conversation_records


def _direct_members(connection, facade_id):
    role = family_storage.resident_roles(connection, [facade_id]).get(facade_id, {})
    return (set(family_storage.members_of(connection, role['family_id']))
            if role.get('role') == 'facade' else set())


def _message_member(connection, facade_id, message):
    """Resolve outward provenance to this facade's direct business member.

    Projected messages carry a via chain; stored outward messages carry the
    real initiating Resident. Walk that Resident's enclosing Families, never
    compare only the leaf ID with an outer facade's personal preference.
    Unknown or ambiguous provenance leaves notifications enabled.
    """
    from . import family_facade

    origin = message.get('origin') or {}
    initiator = (origin.get('initiated_by') or {}).get('id')
    provenance = message.get('family_reply') or message.get('relay') or message.get('family_history') or {}
    candidates = {item.get('resident_id') for item in provenance.get('via', [])}
    candidates.update((provenance.get('resident_id'), initiator))
    candidates.discard(None)
    candidates.discard('')
    if not candidates:
        return ''
    if initiator:
        member = initiator
        for family in family_storage.families_around(connection, initiator, max_hops=family_facade.MAX_RELAY_HOPS):
            if family['facade_id'] == facade_id:
                candidates.add(member)
                break
            member = family['facade_id']
    matches = candidates & _direct_members(connection, facade_id)
    return next(iter(matches)) if len(matches) == 1 else ''


def _muted(connection, facade_id, actor, member_id):
    """Suppress only a user's new alert, using the event's own transaction."""
    if not member_id or actor_principal(actor).kind != 'user':
        return False
    policy = family_chat_storage.read_policy(connection, facade_id, actor)
    return (member_id in policy['muted_member_ids']
            and member_id in _direct_members(connection, facade_id))


def project_event(repository, connection, event):
    from . import family_facade

    kind, resident_id = event['event_type'], event['resident_id']
    payload = event['payload']
    key = 'resident:' + resident_id
    if kind == resident_conversation_records.CONTEXT_CLEARED:
        conversation_receipt_store.catch_up(connection, key)
        return
    value = payload.get('input_state') or {}
    if kind in {resident_conversation_records.MESSAGE_APPENDED, resident_conversation_records.MESSAGE_UPDATED}:
        header = payload.get('message') or {}
        message_id = str(header.get('message_id') or '')
        if not message_id:
            return
        message = repository.stored_message(resident_id, message_id) or {}
        recipients = message_recipients(message)
        member_id = _message_member(connection, resident_id, message) if recipients else ''
        conversation_receipt_store.alert(connection, [
            recipient['id'] for recipient in recipients
            if recipient['kind'] in {'user', 'resident'}
            and not _muted(connection, resident_id, recipient['id'], member_id)
        ], key, message_id)
        request_id = str(message.get('reply_to_message_id') or '')
        if not request_id:
            return
        row = connection.execute(
            'SELECT * FROM conversation_input_states WHERE resident_id=%s AND message_id=%s',
            (resident_id, request_id),
        ).fetchone()
        value = resident_conversation_records.input_state_value(row) if row else {}
    elif kind != resident_conversation_records.INPUT_STATE_CHANGED:
        return
    if value.get('state') not in resident_conversation_records.TERMINAL_INPUT_STATES:
        return
    request_id = value['message_id']
    replies = repository.agent_data.chat_final_replies_many({resident_id: [request_id]})
    reply_ids = resident_conversation_records.final_reply(value['state'], replies.get(resident_id, {}).get(request_id))
    if value['state'] == 'completed':
        headers = repository.agent_data.chat_message_headers([resident_id], message_ids=reply_ids) if reply_ids else []
        reply_ids = [item['message_id'] for item in headers
                     if item['role'] == 'agent' and item['has_content']
                     and item['reply_to_message_id'] == request_id]
        if not reply_ids:
            return
    sources = [(resident_id, request_id), *family_facade.relay_sources_of(resident_id, request_id)]
    member_id = ''
    for target, source_id in sources:
        # Each hop's child is the direct member at that facade, including
        # an inner facade. Advance even for relay copies we do not alert on.
        direct_member, member_id = member_id, target
        request = repository.stored_message(target, source_id) or {}
        # A relay copy belongs to the facade's conversation with the sender.
        if family_facade.parse_relay_mark(str(request.get('business_ref') or '')):
            continue
        author = str(request.get('author_id') or '')
        sender = (request.get('origin') or {}).get('sender') or {}
        if not author or (sender.get('kind') or actor_principal(author).kind) not in {'user', 'resident'}:
            continue
        if _muted(connection, target, author, direct_member):
            continue
        # The reply is the event; an outcome without one is the Input's own.
        # Its time is the outcome's, as the conversation summary reports it.
        ref = reply_ids[-1] if reply_ids else f"resident-input-terminal:{source_id}:{value['state']}"
        conversation_receipt_store.alert(connection, [author], 'resident:' + target, ref, at=value.get('updated_at'))
