"""Raise the viewer's alert on new Resident results, with the event that brings them."""
from __future__ import annotations

from management_common.principal import actor_principal, message_recipients
from management_portal import conversation_receipt_store

from . import resident_conversation_records


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
        conversation_receipt_store.alert(connection, [
            recipient['id'] for recipient in message_recipients(message)
            if recipient['kind'] in {'user', 'resident'}
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
    for target, source_id in sources:
        request = repository.stored_message(target, source_id) or {}
        # A relay copy belongs to the facade's conversation with the sender.
        if family_facade.parse_relay_mark(str(request.get('business_ref') or '')):
            continue
        author = str(request.get('author_id') or '')
        sender = (request.get('origin') or {}).get('sender') or {}
        if not author or (sender.get('kind') or actor_principal(author).kind) not in {'user', 'resident'}:
            continue
        # The reply is the event; an outcome without one is the Input's own.
        # Its time is the outcome's, as the conversation summary reports it.
        ref = reply_ids[-1] if reply_ids else f"resident-input-terminal:{source_id}:{value['state']}"
        conversation_receipt_store.alert(connection, [author], 'resident:' + target, ref, at=value.get('updated_at'))
