"""Personal Family M/S policy, admission snapshots and projection filtering.

The caller owns preferences; the message author owns the immutable routing
snapshot. Neither changes Family membership, intake or another viewer's chat.
"""
from management_common.actor_identity import canonical_actor_id

COLUMNS = {
    'family_chat_preferences': ('facade_id', 'actor', 'muted_member_ids', 'solo_member_id'),
    'family_chat_inputs': ('facade_id', 'message_id', 'muted_member_ids', 'solo_member_id'),
}


def create_schema(c):
    c.execute("""CREATE TABLE family_chat_preferences (
        facade_id TEXT NOT NULL REFERENCES residents(resident_id) ON DELETE CASCADE,
        actor TEXT NOT NULL, muted_member_ids TEXT[] NOT NULL DEFAULT '{}',
        solo_member_id TEXT NOT NULL DEFAULT '', PRIMARY KEY(facade_id,actor))""")
    c.execute("""CREATE TABLE family_chat_inputs (
        facade_id TEXT NOT NULL REFERENCES residents(resident_id) ON DELETE CASCADE,
        message_id TEXT NOT NULL, muted_member_ids TEXT[] NOT NULL DEFAULT '{}',
        solo_member_id TEXT NOT NULL DEFAULT '', PRIMARY KEY(facade_id,message_id))""")


def _db():
    from .state_store import get_lineage_data_store
    return get_lineage_data_store().database


def _read(c, facade_id, actor):
    row = c.execute('SELECT muted_member_ids,solo_member_id FROM family_chat_preferences WHERE facade_id=%s AND actor=%s',
                    (facade_id, canonical_actor_id(actor))).fetchone()
    return dict(row) if row else {'muted_member_ids': [], 'solo_member_id': ''}


def read(facade, actor):
    with _db().read() as c:
        return _read(c, facade['resident_id'], actor)


def get(facade_id, actor):
    from . import family_facade, resident_record_domain
    target = resident_record_domain.resolve_resident_runtime_target(facade_id, actor, require_visible=True)
    if not family_facade.is_facade(target.record):
        raise ValueError('目标必须是 Family 门面')
    return read(target.record, actor)


def change(facade_id, actor, member_id, control, enabled):
    from . import changes, family_facade, resident_record_domain
    target = resident_record_domain.resolve_resident_runtime_target(facade_id, actor, require_visible=True)
    if not family_facade.is_facade(target.record):
        raise ValueError('目标必须是 Family 门面')
    if control not in {'mute', 'solo'} or type(enabled) is not bool:
        raise ValueError('control 必须为 mute 或 solo，enabled 必须为布尔值')
    if member_id not in family_facade.family_member_ids(target.record):
        # A removed Solo can always be cleared without silently selecting another member.
        if enabled or member_id != read(target.record, actor)['solo_member_id']:
            raise ValueError('只能选择本 Family 的业务成员')
    def write(c):
        identity = canonical_actor_id(actor)
        c.execute('INSERT INTO family_chat_preferences(facade_id,actor) VALUES (%s,%s) ON CONFLICT DO NOTHING', (facade_id, identity))
        c.execute('SELECT actor FROM family_chat_preferences WHERE facade_id=%s AND actor=%s FOR UPDATE', (facade_id, identity))
        policy = _read(c, facade_id, identity)
        muted = policy['muted_member_ids']
        solo = policy['solo_member_id']
        if control == 'mute':
            muted = sorted((set(muted) | {member_id}) if enabled else (set(muted) - {member_id}))
            if enabled and solo == member_id:
                solo = ''
        elif enabled:
            solo = member_id
        elif solo == member_id:
            solo = ''
        c.execute('UPDATE family_chat_preferences SET muted_member_ids=%s,solo_member_id=%s WHERE facade_id=%s AND actor=%s',
                  (muted, solo, facade_id, identity))
        return {'muted_member_ids': muted, 'solo_member_id': solo}
    result = _db().write(write)
    changes.resident_changed(facade_id)
    return result


def capture(facade, message):
    """Freeze once when admitted, before any event or Router model runs."""
    def write(c):
        value = _read(c, facade['resident_id'], message.get('author_id', ''))
        c.execute('INSERT INTO family_chat_inputs VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                  (facade['resident_id'], message['message_id'], value['muted_member_ids'], value['solo_member_id']))
        row = c.execute('SELECT muted_member_ids,solo_member_id FROM family_chat_inputs WHERE facade_id=%s AND message_id=%s',
                        (facade['resident_id'], message['message_id'])).fetchone()
        return dict(row)
    return _db().write(write)


def allowed(facade, policy):
    from . import family_facade
    members = family_facade.family_members(facade)
    solo = policy.get('solo_member_id')
    return [m for m in members if m == solo] if solo else [m for m in members if m not in policy.get('muted_member_ids', [])]


def direct_member(facade, message):
    from . import family_facade
    from .resident_questions import answered_set
    policy = capture(facade, message)
    quoted = family_facade.quoted_member(facade, message)
    # Answer cards keep their original execution's reply path.
    if quoted and answered_set(message):
        return quoted
    if policy['solo_member_id']:
        return policy['solo_member_id']  # Invalid/departed Solo fails; never silently route elsewhere.
    if quoted and quoted not in policy['muted_member_ids']:
        return quoted
    continued = family_facade.continued_member(facade, message)
    return continued if continued not in policy['muted_member_ids'] else ''


def validate_target(facade, message, member_id):
    from . import family_facade
    from .resident_questions import answered_set
    policy = capture(facade, message)
    if answered_set(message) and family_facade.quoted_member(facade, message) == member_id:
        return
    solo = policy['solo_member_id']
    if (solo and member_id != solo) or (not solo and member_id in policy['muted_member_ids']):
        raise ValueError('该成员不在发送者的 M/S 路由范围内')


def filter_messages(facade, actor, messages):
    policy = read(facade, actor)
    solo, muted = policy['solo_member_id'], set(policy['muted_member_ids'])
    if not solo and not muted:
        return messages
    from . import family_facade
    members = set(family_facade.family_member_ids(facade))
    def visible(message):
        relay = message.get('family_reply') or message.get('relay') or message.get('family_history') or {}
        chain = [item.get('resident_id') for item in relay.get('via', [])] + [relay.get('resident_id'), (message.get('origin', {}).get('initiated_by') or {}).get('id')]
        owner = next((rid for rid in chain if rid in members or rid in muted or rid == solo), '')
        return not owner or (owner == solo if solo else owner not in muted)
    return [m for m in messages if visible(m)]


def project(facade, actor, snapshot):
    policy = read(facade, actor)
    snapshot['family_facade']['chat_preferences'] = policy
    before = snapshot.get('messages', [])
    after = filter_messages(facade, actor, before)
    hidden = {m['message_id'] for m in before} - {m['message_id'] for m in after}
    snapshot['messages'] = after
    for key in ('input_states', 'queue'):
        if isinstance(snapshot.get(key), list):
            snapshot[key] = [item for item in snapshot[key] if item.get('message_id') not in hidden]
    queue = snapshot.get('queue')
    if isinstance(queue, dict):
        queue['items'] = [item for item in queue.get('items', []) if item.get('message_id') not in hidden]
    return snapshot
