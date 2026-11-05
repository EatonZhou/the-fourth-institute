"""Personal preferences, bookmarks and UI associations; no access grants."""
from __future__ import annotations

import json
import os
from functools import lru_cache

from management_common.domain_database import DomainDatabase


DDL = """
CREATE TABLE IF NOT EXISTS user_preferences(actor TEXT PRIMARY KEY, preferences_json JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS favorites (
    actor TEXT NOT NULL, surface TEXT NOT NULL, target_id TEXT NOT NULL,
    position BIGINT NOT NULL, PRIMARY KEY(actor,surface,target_id)
);
CREATE TABLE IF NOT EXISTS associations (
    actor TEXT NOT NULL, namespace TEXT NOT NULL, association_key TEXT NOT NULL,
    value_json JSONB NOT NULL, updated_at TIMESTAMPTZ,
    PRIMARY KEY(actor,namespace,association_key)
);
CREATE INDEX IF NOT EXISTS associations_namespace ON associations(namespace,actor);
"""


@lru_cache(maxsize=8)
def _database(dsn, namespace):
    return DomainDatabase('portal_ui', 'presentation', DDL)


def database():
    return _database(os.getenv('PORTAL_DATABASE_URL'), os.getenv('PORTAL_DATABASE_NAMESPACE'))


def preferences(actor):
    with database().read() as connection:
        row = connection.execute('SELECT preferences_json FROM user_preferences WHERE actor=%s', (actor,)).fetchone()
        return row[0] if row else {}


def update_preferences(actor, update):
    with database().transaction('preferences:' + actor) as connection:
        row = connection.execute('SELECT preferences_json FROM user_preferences WHERE actor=%s', (actor,)).fetchone()
        value = update(row[0] if row else {})
        connection.execute('INSERT INTO user_preferences VALUES (%s,%s::jsonb) ON CONFLICT(actor) DO UPDATE SET preferences_json=excluded.preferences_json', (actor, json.dumps(value)))
        return value


def delete_preferences(actor):
    with database().transaction('preferences:' + actor) as connection:
        return bool(connection.execute('DELETE FROM user_preferences WHERE actor=%s', (actor,)).rowcount)


def favorite_ids(actor, surface):
    with database().read() as connection:
        return [row[0] for row in connection.execute('SELECT target_id FROM favorites WHERE actor=%s AND surface=%s ORDER BY position,target_id', (actor, surface))]


def set_favorite(actor, surface, target, favorite):
    with database().transaction('favorites:' + actor + ':' + surface) as connection:
        if favorite:
            connection.execute('''INSERT INTO favorites(actor,surface,target_id,position)
                SELECT %s,%s,%s,COALESCE(MAX(position),-1)+1 FROM favorites WHERE actor=%s AND surface=%s
                ON CONFLICT(actor,surface,target_id) DO NOTHING''', (actor, surface, target, actor, surface))
        else:
            connection.execute('DELETE FROM favorites WHERE actor=%s AND surface=%s AND target_id=%s', (actor, surface, target))
        return [row[0] for row in connection.execute('SELECT target_id FROM favorites WHERE actor=%s AND surface=%s ORDER BY position,target_id', (actor, surface))]


def forget_favorite_target(surface, target):
    """Remove a deleted object from everyone's favorites."""
    with database().transaction('favorites-target:' + surface + ':' + target) as connection:
        return connection.execute('DELETE FROM favorites WHERE surface=%s AND target_id=%s', (surface, target)).rowcount


def delete_actor_favorites(actor):
    """A deleted User's favorites on every surface."""
    with database().transaction('favorites:' + actor) as connection:
        return bool(connection.execute('DELETE FROM favorites WHERE actor=%s', (actor,)).rowcount)
