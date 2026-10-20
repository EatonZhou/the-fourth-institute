"""Each viewer's state of each Mailbox conversation, owned by Portal UI.

One row per viewer and conversation key. ``alert_position`` is where the
latest event worth the viewer's attention stands: a result for them, a message
to them, a Lineage notification or a Notice Board mention. ``read_position`` is
how far they have read; the conversation is unread while the alert stands past
it. Positions and every change of a row take their values from one sequence,
so ``revision`` orders all the states a browser may hold of a row: it keeps the
highest one it has seen, from whichever source.

``alert_ref`` names the latest alert's event, so the same event delivered
again does not alert again. ``alerted_at`` is when the latest outcome of the
viewer's own request there happened: the Mailbox orders and pages a Resident
conversation by it together with the viewer's messages. ``dismissed_through``
hides the conversation until its activity passes that time ("清除全部");
``pin_position`` orders pins.

The functions take the caller's connection, so an alert commits with the
Conversation event that causes it; table names carry their schema for
connections bound to another one.
"""
from __future__ import annotations

import os
from datetime import datetime
from functools import lru_cache
from typing import Any, Iterable

from management_common.actor_identity import canonical_actor_id
from management_common.database_time import instant, iso_time
from management_common.domain_database import DomainDatabase


MAX_PINNED_ITEMS = 50
# Named columns: a column added online must not change the result type of a
# statement other processes have already prepared.
_FIELDS = "conversation_key, alert_position, read_position, dismissed_through, pin_position, revision"
# Every process runs the DDL on its first use. CREATE INDEX IF NOT EXISTS and
# ADD COLUMN IF NOT EXISTS lock the table even when nothing is missing, and
# wait behind any open transaction that wrote to it, with readers queued
# behind them: the index is looked up first, and columns go into CREATE TABLE.
DDL = """
CREATE SEQUENCE IF NOT EXISTS conversation_state_revisions;
-- A Chatroom member's row counts message positions: the newest message, how
-- far they read, their own mute and the mentions of them not yet read.
CREATE TABLE IF NOT EXISTS conversation_states (
    actor TEXT NOT NULL,
    conversation_key TEXT NOT NULL,
    alert_position BIGINT NOT NULL DEFAULT 0,
    alert_ref TEXT NOT NULL DEFAULT '',
    alerted_at TIMESTAMPTZ,
    read_position BIGINT NOT NULL DEFAULT 0,
    dismissed_through TIMESTAMPTZ,
    pin_position BIGINT,
    revision BIGINT NOT NULL,
    muted BOOLEAN NOT NULL DEFAULT FALSE,
    mentions JSONB NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (actor, conversation_key)
);
DO $index$
BEGIN
    IF to_regclass(current_schema() || '.conversation_states_unread_idx') IS NULL THEN
        CREATE INDEX conversation_states_unread_idx
            ON conversation_states(actor) WHERE alert_position > read_position;
    END IF;
END
$index$;
"""


@lru_cache(maxsize=8)
def _database(dsn, namespace):
    return DomainDatabase('portal_ui', 'conversation_states', DDL)


def database():
    return _database(os.getenv('PORTAL_DATABASE_URL'), os.getenv('PORTAL_DATABASE_NAMESPACE'))


def actor_key(actor: object) -> str:
    """Viewers compare case-insensitively, as everywhere in the Portal."""
    return canonical_actor_id(actor).casefold()


def _names() -> tuple[str, str]:
    store = database()
    store.ensure()
    schema = store.database.schema
    return f'{schema}.conversation_states', f'{schema}.conversation_state_revisions'


def _state(row: Any) -> dict[str, Any]:
    if row is None:
        return {'unread': False, 'pinned': False, 'read_revision': 0, 'dismissed_through': ''}
    return {
        'unread': int(row['alert_position']) > int(row['read_position']),
        'pinned': row['pin_position'] is not None,
        'read_revision': int(row['revision']),
        'dismissed_through': iso_time(row['dismissed_through']) or '',
        'pin_position': row['pin_position'],
    }


def empty_state() -> dict[str, Any]:
    return _state(None)


def read_actor(connection, actor) -> dict[str, dict[str, Any]]:
    """Every row of the viewer by conversation key; pins first in their order."""
    states, _sequence = _names()
    rows = connection.execute(
        f'SELECT {_FIELDS} FROM {states} WHERE actor=%s ORDER BY pin_position NULLS LAST, conversation_key',
        (actor_key(actor),),
    ).fetchall()
    return {row['conversation_key']: _state(row) for row in rows}


def read_keys(connection, actor, keys: Iterable[str]) -> dict[str, dict[str, Any]]:
    states, _sequence = _names()
    rows = connection.execute(
        f'SELECT {_FIELDS} FROM {states} WHERE actor=%s AND conversation_key=ANY(%s)',
        (actor_key(actor), list(dict.fromkeys(keys))),
    ).fetchall()
    return {row['conversation_key']: _state(row) for row in rows}


def alert(connection, actors: Iterable[object], key: str, ref: str, *, at: object = None) -> list[str]:
    """Raise each viewer's alert to a new event, once per event; returns the viewers it reached.

    ``at`` is when an outcome of the viewer's request happened; other alerts leave it out.
    """
    states, sequence = _names()
    viewers = sorted({actor_key(actor) for actor in actors} - {''})
    if not viewers or not key or not ref:
        return []
    rows = connection.execute(
        f'''INSERT INTO {states} AS current
            (actor, conversation_key, alert_position, alert_ref, alerted_at, revision)
            SELECT viewer, %s, position, %s, %s::timestamptz, position FROM (
                SELECT viewer, nextval('{sequence}') AS position FROM unnest(%s::text[]) viewer
            ) allotted
            ON CONFLICT (actor, conversation_key) DO UPDATE SET
                alert_position=EXCLUDED.alert_position, alert_ref=EXCLUDED.alert_ref,
                alerted_at=GREATEST(current.alerted_at, EXCLUDED.alerted_at),
                dismissed_through=NULL, revision=EXCLUDED.revision
            WHERE current.alert_ref <> EXCLUDED.alert_ref
            RETURNING actor''',
        (key, ref, instant(at) if at else None, viewers),
    ).fetchall()
    return [row['actor'] for row in rows]


def outcome_times(connection, actor, prefix: str) -> dict[str, str]:
    """When the latest outcome of the viewer's request happened, by conversation key."""
    states, _sequence = _names()
    rows = connection.execute(
        f'''SELECT conversation_key, alerted_at FROM {states}
            WHERE actor=%s AND alerted_at IS NOT NULL AND starts_with(conversation_key, %s)''',
        (actor_key(actor), prefix),
    ).fetchall()
    return {row['conversation_key']: iso_time(row['alerted_at']) for row in rows}


def dismissed_times(connection, actor, prefix: str) -> dict[str, str]:
    """Through when "清除全部" hides each of the viewer's unpinned conversations, by key."""
    states, _sequence = _names()
    rows = connection.execute(
        f'''SELECT conversation_key, dismissed_through FROM {states}
            WHERE actor=%s AND dismissed_through IS NOT NULL AND pin_position IS NULL
              AND starts_with(conversation_key, %s)''',
        (actor_key(actor), prefix),
    ).fetchall()
    return {row['conversation_key']: iso_time(row['dismissed_through']) for row in rows}


def catch_up(connection, key: str) -> list[str]:
    """Every viewer has read the conversation, as after its context was cleared."""
    states, sequence = _names()
    rows = connection.execute(
        f'''UPDATE {states} SET read_position=alert_position, revision=nextval('{sequence}')
            WHERE conversation_key=%s AND alert_position > read_position RETURNING actor''',
        (key,),
    ).fetchall()
    return [row['actor'] for row in rows]


class VersionConflict(Exception):
    def __init__(self, current: dict[str, Any]):
        super().__init__('conversation state changed')
        self.current = current


def _locked(connection, states: str, viewer: str, key: str) -> Any:
    return connection.execute(
        f'SELECT {_FIELDS} FROM {states} WHERE actor=%s AND conversation_key=%s FOR UPDATE', (viewer, key),
    ).fetchone()


def set_read(connection, actor, key: str, *, unread: bool, expected_revision: object = None) -> dict[str, Any]:
    """Read through the latest alert, or move the read position back before it.

    ``expected_revision`` is the revision the caller saw: a newer alert or any
    other change since then refuses the change instead of reading it unseen.
    """
    states, sequence = _names()
    viewer = actor_key(actor)
    current = _locked(connection, states, viewer, key)
    if expected_revision is not None and str(expected_revision) != str(_state(current)['read_revision']):
        raise VersionConflict(_state(current))
    if bool(unread) == _state(current)['unread']:
        return _state(current)
    if unread:
        row = connection.execute(
            f'''WITH allotted AS (SELECT nextval('{sequence}') AS position)
                INSERT INTO {states} AS current (actor, conversation_key, alert_position, read_position, revision)
                SELECT %s, %s, position, 0, position FROM allotted
                ON CONFLICT (actor, conversation_key) DO UPDATE SET
                    alert_position=CASE WHEN current.alert_position > 0 THEN current.alert_position
                                        ELSE EXCLUDED.alert_position END,
                    read_position=LEAST(current.read_position, CASE WHEN current.alert_position > 0
                                        THEN current.alert_position ELSE EXCLUDED.alert_position END - 1),
                    revision=EXCLUDED.revision
                RETURNING {_FIELDS}''',
            (viewer, key),
        ).fetchone()
    else:
        row = connection.execute(
            f'''UPDATE {states} SET read_position=alert_position, revision=nextval('{sequence}')
                WHERE actor=%s AND conversation_key=%s RETURNING {_FIELDS}''',
            (viewer, key),
        ).fetchone()
    return _state(row)


def read_through(connection, actor, keys: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Read every given conversation that is unread now."""
    states, sequence = _names()
    rows = connection.execute(
        f'''UPDATE {states} SET read_position=alert_position, revision=nextval('{sequence}')
            WHERE actor=%s AND conversation_key=ANY(%s) AND alert_position > read_position RETURNING {_FIELDS}''',
        (actor_key(actor), list(dict.fromkeys(keys))),
    ).fetchall()
    return {row['conversation_key']: _state(row) for row in rows}


def dismiss(connection, actor, activity: dict[str, object]) -> dict[str, dict[str, Any]]:
    """Read and hide unpinned conversations until their activity passes the given times."""
    states, sequence = _names()
    viewer = actor_key(actor)
    changed: dict[str, dict[str, Any]] = {}
    for key, updated_at in activity.items():
        at = instant(updated_at) if updated_at else datetime.now().astimezone()
        row = connection.execute(
            f'''INSERT INTO {states} AS current (actor, conversation_key, dismissed_through, revision)
                VALUES (%s, %s, %s, nextval('{sequence}'))
                ON CONFLICT (actor, conversation_key) DO UPDATE SET
                    read_position=current.alert_position,
                    dismissed_through=GREATEST(current.dismissed_through, EXCLUDED.dismissed_through),
                    revision=EXCLUDED.revision
                WHERE current.pin_position IS NULL
                RETURNING {_FIELDS}''',
            (viewer, key, at),
        ).fetchone()
        if row is not None:
            changed[key] = _state(row)
    return changed


def set_pinned(connection, actor, key: str, pinned: bool) -> dict[str, Any] | None:
    """Pin after the existing pins, or unpin; None when nothing changes."""
    states, sequence = _names()
    viewer = actor_key(actor)
    current = _locked(connection, states, viewer, key)
    if _state(current)['pinned'] == bool(pinned):
        return None
    if pinned:
        count = connection.execute(
            f'SELECT COUNT(*) AS count FROM {states} WHERE actor=%s AND pin_position IS NOT NULL', (viewer,),
        ).fetchone()['count']
        if count >= MAX_PINNED_ITEMS:
            raise ValueError("每位用户最多置顶 50 个对话")
        row = connection.execute(
            f'''WITH allotted AS (SELECT nextval('{sequence}') AS position)
                INSERT INTO {states} AS current (actor, conversation_key, pin_position, revision)
                SELECT %s, %s, position, position FROM allotted
                ON CONFLICT (actor, conversation_key) DO UPDATE SET
                    pin_position=EXCLUDED.pin_position, dismissed_through=NULL, revision=EXCLUDED.revision
                RETURNING {_FIELDS}''',
            (viewer, key),
        ).fetchone()
    else:
        row = connection.execute(
            f'''UPDATE {states} SET pin_position=NULL, revision=nextval('{sequence}')
                WHERE actor=%s AND conversation_key=%s RETURNING {_FIELDS}''',
            (viewer, key),
        ).fetchone()
    return _state(row)


def release_pins(connection, actor, available: set[str]) -> list[str]:
    """Unpin conversations whose targets are confirmed gone or out of reach."""
    states, sequence = _names()
    rows = connection.execute(
        f'''UPDATE {states} SET pin_position=NULL, revision=nextval('{sequence}')
            WHERE actor=%s AND pin_position IS NOT NULL AND NOT (conversation_key = ANY(%s))
            RETURNING conversation_key''',
        (actor_key(actor), sorted(available)),
    ).fetchall()
    return [row['conversation_key'] for row in rows]


def chatroom_viewers(connection, keys: Iterable[str], actor: object = None) -> list[Any]:
    """Chatroom members' rows: newest message and read positions, mute, unread mentions."""
    states, _sequence = _names()
    viewer = "" if actor is None else " AND actor=%s"
    return connection.execute(
        f'SELECT actor, conversation_key, alert_position, read_position, muted, mentions, revision '
        f'FROM {states} WHERE conversation_key=ANY(%s){viewer}',
        (list(keys), *([actor_key(actor)] if actor is not None else [])),
    ).fetchall()


def save_chatroom_viewers(connection, key: str, newest: int, viewers: dict[str, tuple[int, bool, list]],
                          leaving: Iterable[str] = ()) -> dict[str, int]:
    """Write the members whose state changed; the newest message is every member's alert.

    ``viewers`` maps a member to (read position, muted, unread mentions).
    Returns each written member's new revision.
    """
    from psycopg.types.json import Jsonb

    states, sequence = _names()
    revisions = {}
    for actor, (read, muted, mentions) in viewers.items():
        row = connection.execute(
            f'''INSERT INTO {states} AS current
                (actor, conversation_key, alert_position, read_position, muted, mentions, revision)
                VALUES (%s, %s, %s, %s, %s, %s, nextval('{sequence}'))
                ON CONFLICT (actor, conversation_key) DO UPDATE SET
                    alert_position=GREATEST(current.alert_position, EXCLUDED.alert_position),
                    read_position=EXCLUDED.read_position, muted=EXCLUDED.muted, mentions=EXCLUDED.mentions,
                    revision=EXCLUDED.revision
                WHERE (current.alert_position, current.read_position, current.muted, current.mentions)
                    IS DISTINCT FROM (GREATEST(current.alert_position, EXCLUDED.alert_position),
                                      EXCLUDED.read_position, EXCLUDED.muted, EXCLUDED.mentions)
                RETURNING revision''',
            (actor_key(actor), key, int(newest), int(read), bool(muted), Jsonb(list(mentions))),
        ).fetchone()
        if row is not None:
            revisions[actor] = int(row['revision'])
    leavers = sorted({actor_key(actor) for actor in leaving} - {''})
    if leavers:
        connection.execute(f'DELETE FROM {states} WHERE conversation_key=%s AND actor=ANY(%s)', (key, leavers))
    return revisions


def forget_conversation(connection, key: str) -> None:
    """Every viewer's row of a conversation that no longer exists."""
    states, _sequence = _names()
    connection.execute(f'DELETE FROM {states} WHERE conversation_key=%s', (key,))


def delete_actor(connection, actor) -> bool:
    states, _sequence = _names()
    return bool(connection.execute(f'DELETE FROM {states} WHERE actor=%s', (actor_key(actor),)).rowcount)
