"""Chatroom identity, membership, viewer state and pending deliveries."""
import os
from contextlib import nullcontext
from functools import lru_cache
from management_common.domain_database import DomainDatabase
from management_portal import conversation_receipt_store
from psycopg.types.json import Jsonb
from management_common.database_time import instant, iso_time

FIELDS=('title','created_by','created_at','updated_at','last_ordinal','last_message')
DDL="""
CREATE TABLE IF NOT EXISTS rooms (chatroom_id TEXT PRIMARY KEY,title TEXT,created_by TEXT,created_at TIMESTAMPTZ,updated_at TIMESTAMPTZ,last_ordinal BIGINT,last_message JSONB);
CREATE TABLE IF NOT EXISTS members (chatroom_id TEXT NOT NULL REFERENCES rooms(chatroom_id) ON DELETE CASCADE,actor TEXT NOT NULL,PRIMARY KEY(chatroom_id,actor));
CREATE INDEX IF NOT EXISTS room_member_idx ON members(actor,chatroom_id);
CREATE INDEX IF NOT EXISTS room_member_identity_idx ON members(lower(actor),chatroom_id);
CREATE TABLE IF NOT EXISTS called_residents (chatroom_id TEXT NOT NULL REFERENCES rooms(chatroom_id) ON DELETE CASCADE,resident_id TEXT NOT NULL,PRIMARY KEY(chatroom_id,resident_id));
CREATE TABLE IF NOT EXISTS deliveries (chatroom_id TEXT NOT NULL REFERENCES rooms(chatroom_id) ON DELETE CASCADE,id TEXT NOT NULL,delivery_json JSONB NOT NULL,PRIMARY KEY(chatroom_id,id));
CREATE TABLE IF NOT EXISTS message_reactions (chatroom_id TEXT NOT NULL REFERENCES rooms(chatroom_id) ON DELETE CASCADE,message_id TEXT NOT NULL,emoji TEXT NOT NULL,actor TEXT NOT NULL,PRIMARY KEY(chatroom_id,message_id,emoji,actor));
CREATE TABLE IF NOT EXISTS message_changes (revision BIGSERIAL PRIMARY KEY,chatroom_id TEXT NOT NULL REFERENCES rooms(chatroom_id) ON DELETE CASCADE,message_id TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS room_message_changes_idx ON message_changes(chatroom_id,revision);
"""


@lru_cache(maxsize=8)
def _database(dsn,namespace):
    return DomainDatabase('chatrooms','rooms',DDL)


def database():
    return _database(os.getenv('PORTAL_DATABASE_URL'),os.getenv('PORTAL_DATABASE_NAMESPACE'))


def reactions(chatroom_id, message_ids):
    if not message_ids:
        return {}
    result = {}
    with database().read() as c:
        rows = c.execute("SELECT message_id,emoji,actor FROM message_reactions WHERE chatroom_id=%s AND message_id=ANY(%s) ORDER BY emoji,actor", (chatroom_id, list(message_ids))).fetchall()
    for row in rows:
        result.setdefault(row['message_id'], {}).setdefault(row['emoji'], []).append(row['actor'])
    return result


def set_reaction(chatroom_id, message_id, emoji, actor, active):
    with database().transaction('room:' + chatroom_id) as c:
        if active:
            row = c.execute("INSERT INTO message_reactions VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING actor", (chatroom_id, message_id, emoji, actor)).fetchone()
        else:
            row = c.execute("DELETE FROM message_reactions WHERE chatroom_id=%s AND message_id=%s AND emoji=%s AND actor=%s RETURNING actor", (chatroom_id, message_id, emoji, actor)).fetchone()
        return row is not None


def clear_reactions(chatroom_id, message_id):
    with database().transaction('room:' + chatroom_id) as c:
        c.execute("DELETE FROM message_reactions WHERE chatroom_id=%s AND message_id=%s", (chatroom_id, message_id))


def record_message_change(chatroom_id, message_id):
    with database().transaction('room:' + chatroom_id) as c:
        c.execute("INSERT INTO message_changes(chatroom_id,message_id) VALUES (%s,%s)", (chatroom_id, message_id))


def message_changes(chatroom_id, after=None):
    with database().read() as c:
        if after is None:
            row = c.execute("SELECT COALESCE(MAX(revision),0) AS revision FROM message_changes WHERE chatroom_id=%s", (chatroom_id,)).fetchone()
            return [], str(row['revision'])
        rows = c.execute("SELECT message_id,revision FROM message_changes WHERE chatroom_id=%s AND revision>%s ORDER BY revision LIMIT 200", (chatroom_id, int(after))).fetchall()
    return list(dict.fromkeys(r['message_id'] for r in rows)), str(rows[-1]['revision'] if rows else after)


def _relations(room):
    return {
        'members': {actor: {} for actor in room.get('members', [])},
        'called_residents': {actor: {} for actor in room.get('called_residents', [])},
        'deliveries': {d['id']: {'delivery_json': {k: v for k, v in d.items() if k != 'id'}}
                       for d in room.get('deliveries', [])},
    }


def _viewer_states(room):
    """Each member's (read position, muted, unread mentions) as the room holds them."""
    reads, muted, mentions = (room.get(k, {}) for k in ('read_through', 'muted_by', 'user_mentions'))
    return {member.casefold(): (int(reads.get(member.casefold()) or 0), bool(muted.get(member.casefold())),
                                list(mentions.get(member.casefold()) or []))
            for member in room.get('members', [])}


def _inbox_key(chatroom_id):
    from .chatroom_backend import INBOX_KEY_PREFIX
    return INBOX_KEY_PREFIX + chatroom_id


def _hydrate(c, rows, *, viewer=None, delivery_ids=None):
    from .chatroom_backend import SCHEMA
    rooms = {}
    for row in rows:
        record = dict(row)
        for key in ('created_at', 'updated_at'):
            record[key] = iso_time(record[key])
        rooms[record['chatroom_id']] = {
            **record, 'schema': SCHEMA, 'members': [], 'called_residents': [],
            'deliveries': [], 'read_through': {}, 'muted_by': {}, 'user_mentions': {}, 'read_revisions': {},
        }
    if not rooms:
        return rooms
    ids = list(rooms)
    for table, key in [('members', 'actor'), ('called_residents', 'resident_id')]:
        for row in c.execute(f'SELECT * FROM {table} WHERE chatroom_id=ANY(%s) ORDER BY {key}', (ids,)):
            rooms[row['chatroom_id']][table].append(row[key])
    # Members' read state lives in their Mailbox rows.
    keys = {_inbox_key(chatroom_id): chatroom_id for chatroom_id in ids}
    for row in conversation_receipt_store.chatroom_viewers(c, list(keys), viewer):
        room = rooms[keys[row['conversation_key']]]
        room['read_revisions'][row['actor']] = int(row['revision'])
        room['read_through'][row['actor']] = int(row['read_position'])
        room['muted_by'][row['actor']] = bool(row['muted'])
        room['user_mentions'][row['actor']] = list(row['mentions'] or [])
    if delivery_ids is None or delivery_ids:
        delivery_where = "" if delivery_ids is None else " AND id=ANY(%s)"
        delivery_params = (ids,) if delivery_ids is None else (ids, list(delivery_ids))
        for row in c.execute("SELECT * FROM deliveries WHERE chatroom_id=ANY(%s)" + delivery_where
                             + " ORDER BY delivery_json->>'created_at',id", delivery_params):
            rooms[row['chatroom_id']]['deliveries'].append({'id': row['id'], **row['delivery_json']})
    return rooms


class ChatroomState:
    def transaction(self, chatroom_id):
        return database().transaction('room:' + chatroom_id)

    def get(self, chatroom_id, *, viewer=None, delivery_ids=None):
        with database().read() as c:
            return _hydrate(c, c.execute('SELECT * FROM rooms WHERE chatroom_id=%s', (chatroom_id,)),
                            viewer=viewer, delivery_ids=delivery_ids).get(chatroom_id)

    def list(self, *, actor=None):
        with database().read() as c:
            if actor is None:
                rows = c.execute('SELECT * FROM rooms ORDER BY chatroom_id')
            else:
                rows = c.execute(
                    'SELECT r.* FROM rooms r JOIN members m USING(chatroom_id) '
                    'WHERE lower(m.actor)=%s ORDER BY r.chatroom_id', (actor.casefold(),))
            return list(_hydrate(c, rows).values())

    def save(self, chatroom_id, room, previous, *, connection=None):
        """Write only changed rows of the locked room; no collection replacement."""
        with (nullcontext(connection) if connection is not None else self.transaction(chatroom_id)) as c:
            if room is None:
                c.execute('DELETE FROM rooms WHERE chatroom_id=%s', (chatroom_id,))
                conversation_receipt_store.forget_conversation(c, _inbox_key(chatroom_id))
                return
            allowed = {'schema', 'chatroom_id', *FIELDS, 'members', 'called_residents',
                       'deliveries', 'read_through', 'muted_by', 'user_mentions', 'read_revisions'}
            if set(room) - allowed or room['chatroom_id'] != chatroom_id:
                raise ValueError('Invalid chatroom storage fields or identity')
            if previous is None or any(previous.get(key) != room.get(key) for key in FIELDS):
                values = [Jsonb(room.get(key)) if key == 'last_message' else
                          instant(room.get(key)) if key in ('created_at', 'updated_at') else room.get(key)
                          for key in FIELDS]
                c.execute(
                    f"INSERT INTO rooms(chatroom_id,{','.join(FIELDS)}) VALUES ({','.join(['%s'] * (len(FIELDS)+1))}) "
                    "ON CONFLICT(chatroom_id) DO UPDATE SET " + ','.join(f'{key}=excluded.{key}' for key in FIELDS),
                    (chatroom_id, *values))
            # The newest message raises every member's alert; a member's own
            # message also moves their read position past it.
            viewers_before, viewers_after = _viewer_states(previous or {}), _viewer_states(room)
            newest_changed = int((previous or {}).get('last_ordinal') or 0) != int(room.get('last_ordinal') or 0)
            changed = {actor: state for actor, state in viewers_after.items()
                       if newest_changed or viewers_before.get(actor) != state}
            if changed or viewers_before.keys() - viewers_after.keys():
                room.setdefault('read_revisions', {}).update(conversation_receipt_store.save_chatroom_viewers(
                    c, _inbox_key(chatroom_id), int(room.get('last_ordinal') or 0), changed,
                    leaving=viewers_before.keys() - viewers_after.keys(),
                ))
            before, after = _relations(previous or {}), _relations(room)
            for table, key in [('members', 'actor'), ('called_residents', 'resident_id'), ('deliveries', 'id')]:
                for identity in before[table].keys() - after[table].keys():
                    c.execute(f'DELETE FROM {table} WHERE chatroom_id=%s AND {key}=%s', (chatroom_id, identity))
                for identity, values in after[table].items():
                    if before[table].get(identity) == values:
                        continue
                    fields = ['chatroom_id', key, *values]
                    params = (chatroom_id, identity, *(Jsonb(v) if k in ('mentions', 'delivery_json') and v is not None else v for k, v in values.items()))
                    update = 'DO UPDATE SET ' + ','.join(f'{k}=excluded.{k}' for k in values) if values else 'DO NOTHING'
                    c.execute(f"INSERT INTO {table}({','.join(fields)}) VALUES ({','.join(['%s'] * len(fields))}) "
                              f"ON CONFLICT(chatroom_id,{key}) {update}", params)
