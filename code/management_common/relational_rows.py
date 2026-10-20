"""Diff writes for a domain's explicit table and column contract.

Identifiers are supplied by domain code, never by incoming records. Unknown
fields are rejected instead of being saved in an open-ended JSON container.
"""
from psycopg.types.json import Jsonb
import re


def identifier(value):
    if not re.fullmatch(r'[a-z_][a-z0-9_]*', value):
        raise ValueError('Invalid domain SQL identifier')
    return '"'+value+'"'


def read_rows(connection, table, columns=None):
    """The table's rows; with ``columns``, only those, so a column added later changes no prepared statement."""
    selected = ','.join(map(identifier, columns)) if columns else '*'
    return [dict(row) for row in connection.execute(f'SELECT {selected} FROM {table}')]


def sync_rows(connection, table, keys, columns, rows, *, json_columns=()):
    fields = (*keys, *columns)
    current = {tuple(row[key] for key in keys): row for row in read_rows(connection, table, fields)}
    incoming = {}
    for row in rows:
        if set(row) - set(fields):
            raise ValueError(f'Unknown {table} fields: {sorted(set(row) - set(fields))}')
        row = {key: row.get(key) for key in fields}
        identity = tuple(row[key] for key in keys)
        if identity in incoming:
            raise ValueError(f'Duplicate {table} identity')
        incoming[identity] = row
    predicate = ' AND '.join(identifier(key)+'=%s' for key in keys)
    for identity in current.keys() - incoming.keys():
        connection.execute(f'DELETE FROM {table} WHERE {predicate}', identity)
    for identity, row in incoming.items():
        if current.get(identity) == row:
            continue
        update = 'DO UPDATE SET '+','.join(identifier(key)+'=excluded.'+identifier(key) for key in columns) if columns else 'DO NOTHING'
        connection.execute(f'INSERT INTO {table} ({",".join(map(identifier,fields))}) VALUES ({",".join(["%s"]*len(fields))}) ON CONFLICT ({",".join(map(identifier,keys))}) {update}',
                           tuple(Jsonb(row[key]) if key in json_columns else row[key] for key in fields))
