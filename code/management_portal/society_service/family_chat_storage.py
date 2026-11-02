"""Personal Family conversation and routing preferences.

``family_chat_preferences`` holds one viewer's Mute and Solo controls;
``family_chat_inputs`` freezes the author's policy for each admitted request.
"""
from management_common.actor_identity import canonical_actor_id

COLUMNS = {
    'family_chat_preferences': ('facade_id', 'actor', 'muted_member_ids', 'excluded_member_ids', 'solo_member_id'),
    'family_chat_inputs': ('facade_id', 'message_id', 'excluded_member_ids', 'solo_member_id'),
}


def create_schema(c):
    c.execute("""CREATE TABLE family_chat_preferences (
        facade_id TEXT NOT NULL REFERENCES residents(resident_id) ON DELETE CASCADE,
        actor TEXT NOT NULL, muted_member_ids TEXT[] NOT NULL DEFAULT '{}',
        excluded_member_ids TEXT[] NOT NULL DEFAULT '{}',
        solo_member_id TEXT NOT NULL DEFAULT '', PRIMARY KEY(facade_id,actor))""")
    c.execute("""CREATE TABLE family_chat_inputs (
        facade_id TEXT NOT NULL REFERENCES residents(resident_id) ON DELETE CASCADE,
        message_id TEXT NOT NULL, excluded_member_ids TEXT[] NOT NULL DEFAULT '{}',
        solo_member_id TEXT NOT NULL DEFAULT '', PRIMARY KEY(facade_id,message_id))""")


def read_policy(c, facade_id, actor):
    row = c.execute('SELECT muted_member_ids,excluded_member_ids,solo_member_id FROM family_chat_preferences WHERE facade_id=%s AND actor=%s',
                    (facade_id, canonical_actor_id(actor))).fetchone()
    return {'muted_member_ids': sorted(set(row['muted_member_ids']) | set(row['excluded_member_ids'])),
            'solo_member_id': row['solo_member_id']} if row else {'muted_member_ids': [], 'solo_member_id': ''}


def capture(c, facade_id, author_id, message_id):
    """The author's policy frozen for one request: written the first time, read after that."""
    value = read_policy(c, facade_id, author_id)
    c.execute('INSERT INTO family_chat_inputs(facade_id,message_id,excluded_member_ids,solo_member_id) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING',
              (facade_id, message_id, value['muted_member_ids'], value['solo_member_id']))
    row = c.execute('SELECT excluded_member_ids,solo_member_id FROM family_chat_inputs WHERE facade_id=%s AND message_id=%s',
                    (facade_id, message_id)).fetchone()
    # The private legacy column retains immutable admitted routing snapshots.
    return {'muted_member_ids': list(row['excluded_member_ids']), 'solo_member_id': row['solo_member_id']}
