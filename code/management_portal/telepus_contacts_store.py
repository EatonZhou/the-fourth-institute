"""Telepus Telegram contacts: the Portal User each Telegram user ID acts as.

Portal owns this table and writes every binding (BindTelepusContactForUser,
UnbindTelepusContactForUser and the Telepus Watchboard's own-contact action).
The Telepus Telegram router reads it to authorize each Telegram message, so
the table lives in the router's schema (its ``TELEPUS_DATABASE_SCHEMA``,
``telepus``); Portal never touches the router's other tables.
"""
from __future__ import annotations

import os
from functools import lru_cache

from management_common.domain_database import DomainDatabase


SCHEMA = 'telepus'
DDL = """
CREATE TABLE IF NOT EXISTS telegram_contacts (
    telegram_user_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    portal_actor TEXT NOT NULL
);
"""
# Writers that check the one-binding-per-User rule hold this lock.
LOCK = 'contacts'


@lru_cache(maxsize=8)
def _database(dsn, namespace):
    return DomainDatabase(SCHEMA, 'telegram_contacts', DDL)


def database():
    return _database(os.getenv('PORTAL_DATABASE_URL'), os.getenv('PORTAL_DATABASE_NAMESPACE'))


def contacts(connection) -> dict[str, dict[str, str]]:
    rows = connection.execute('SELECT telegram_user_id, name, portal_actor FROM telegram_contacts ORDER BY telegram_user_id')
    return {row['telegram_user_id']: dict(row) for row in rows}


def save(connection, contact: dict[str, str]) -> None:
    connection.execute(
        'INSERT INTO telegram_contacts(telegram_user_id, name, portal_actor) VALUES (%s,%s,%s) '
        'ON CONFLICT(telegram_user_id) DO UPDATE SET name=excluded.name, portal_actor=excluded.portal_actor',
        (contact['telegram_user_id'], contact['name'], contact['portal_actor']),
    )


def delete(connection, telegram_user_id: str) -> None:
    connection.execute('DELETE FROM telegram_contacts WHERE telegram_user_id=%s', (telegram_user_id,))
