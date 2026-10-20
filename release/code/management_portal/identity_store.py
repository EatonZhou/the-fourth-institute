"""Portal account and authentication facts, each with a fixed SQL contract."""
from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache

from management_common.domain_database import DomainDatabase
from management_common.database_time import instant, iso_time, epoch_seconds
from management_common.relational_rows import read_rows, sync_rows

# table -> (identity columns, value columns and their SQL types)
TABLES = {
    'user_accounts': (('username',), {'display_name':'TEXT NOT NULL','email':'TEXT NOT NULL','status':'TEXT NOT NULL','session_version':'BIGINT NOT NULL','created_at':'TIMESTAMPTZ NOT NULL','updated_at':'TIMESTAMPTZ NOT NULL','updated_by':'TEXT NOT NULL','category':"TEXT NOT NULL DEFAULT 'member'"}),
    'passwords': (('username',), {'digest':'TEXT NOT NULL'}),
    'directory_users': (('id',), {'name':'TEXT NOT NULL'}),
    'user_invitations': (('invitation_id',), {'username':'TEXT NOT NULL','display_name':'TEXT NOT NULL','invited_by':'TEXT NOT NULL','created_at':'TIMESTAMPTZ NOT NULL','expires_at':'TIMESTAMPTZ NOT NULL','status':'TEXT NOT NULL','secret_sha256':'TEXT NOT NULL UNIQUE','accepted_at':'TIMESTAMPTZ','first_admin':'BOOLEAN'}),
    'user_access_tokens': (('credential_id',), {'username':'TEXT NOT NULL','name':'TEXT NOT NULL','secret_sha256':'TEXT NOT NULL UNIQUE','created_at':'TIMESTAMPTZ NOT NULL','expires_at':'TIMESTAMPTZ NOT NULL','status':'TEXT NOT NULL'}),
    'password_reset_tokens': (('digest',), {'username':'TEXT NOT NULL','created_at':'TIMESTAMPTZ NOT NULL','expires_at':'TIMESTAMPTZ NOT NULL'}),
    'login_attempts': (('attempt_key',), {'attempts':'BIGINT NOT NULL','expires_at':'TIMESTAMPTZ NOT NULL'}),
    'passkey_users': (('username',), {'user_handle':'TEXT NOT NULL UNIQUE'}),
    'passkey_credentials': (('credential_id',), {'username':'TEXT NOT NULL REFERENCES passkey_users(username) ON DELETE CASCADE','public_key':'TEXT NOT NULL','sign_count':'BIGINT NOT NULL','name':'TEXT NOT NULL','transports':'TEXT[] NOT NULL','device_type':'TEXT NOT NULL','backed_up':'BOOLEAN NOT NULL','created_at':'TIMESTAMPTZ NOT NULL','last_used_at':'TIMESTAMPTZ'}),
    'two_factor_users': (('username',), {'nonce':'TEXT NOT NULL','ciphertext':'TEXT NOT NULL','enabled_at':'TIMESTAMPTZ NOT NULL','last_counter':'BIGINT NOT NULL'}),
    'two_factor_recovery_codes': (('username','digest'), {}),
}
DDL = '\n'.join(f'CREATE TABLE IF NOT EXISTS {table} (' + ','.join([*(key+' TEXT NOT NULL' for key in keys), *(key+' '+kind for key,kind in columns.items()), 'PRIMARY KEY('+','.join(keys)+')']) + ');'
                for table,(keys,columns) in TABLES.items()) + """
CREATE INDEX IF NOT EXISTS invitation_username_idx ON user_invitations(username);
CREATE INDEX IF NOT EXISTS access_token_username_idx ON user_access_tokens(username);
CREATE INDEX IF NOT EXISTS reset_expiry_idx ON password_reset_tokens(expires_at);
CREATE INDEX IF NOT EXISTS login_attempt_expiry_idx ON login_attempts(expires_at);
CREATE INDEX IF NOT EXISTS passkey_username_idx ON passkey_credentials(username);
"""
KINDS = {
    'access': ('user_accounts',), 'credentials': ('passwords',), 'user_directory': ('directory_users',),
    'user_enrollment': ('user_invitations','user_access_tokens'), 'password_resets': ('password_reset_tokens',),
    'authentication_attempts': ('login_attempts',), 'passkeys': ('passkey_users','passkey_credentials'),
    'two_factor': ('two_factor_users','two_factor_recovery_codes'),
}


@lru_cache(maxsize=8)
def _database(dsn, namespace):
    return DomainDatabase('portal_identity', 'accounts', DDL)


def database():
    return _database(os.getenv('PORTAL_DATABASE_URL'), os.getenv('PORTAL_DATABASE_NAMESPACE'))


TIME_FIELDS = {
    'user_accounts': ('created_at', 'updated_at'),
    'user_invitations': ('created_at', 'expires_at', 'accepted_at'),
    'user_access_tokens': ('created_at', 'expires_at'),
    'password_reset_tokens': ('created_at', 'expires_at'),
    'login_attempts': ('expires_at',),
    'passkey_credentials': ('created_at', 'last_used_at'),
    'two_factor_users': ('enabled_at',),
}


def decode(kind, tables):
    tables = {table: [dict(row) for row in rows] for table, rows in tables.items()}
    for table, rows in tables.items():
        for row in rows:
            for field in TIME_FIELDS.get(table, ()):
                if table == 'user_accounts':
                    row[field] = iso_time(row[field])
                elif table == 'passkey_credentials' and field == 'last_used_at' and row[field] is None:
                    row[field] = 0
                else:
                    row[field] = epoch_seconds(row[field])
    def mapping(table, key, *, remove=False):
        result = {}
        for row in tables[table]:
            value = {k:v for k,v in row.items() if v is not None and (not remove or k != key)}
            result[row[key]] = value
        return result
    if kind == 'access':
        users = mapping('user_accounts','username')
        return {'schema':'tfi.portal-access.v1','users':users,'updated_at':max((v['updated_at'] for v in users.values()), default='')}
    if kind == 'credentials':
        return {r['username']:r['digest'] for r in tables['passwords']}
    if kind == 'user_directory':
        return {'humans':sorted(tables['directory_users'], key=lambda r:r['id']), 'agents':[]}
    if kind == 'user_enrollment':
        return {'invitations':mapping('user_invitations','invitation_id'), 'tokens':mapping('user_access_tokens','credential_id')}
    if kind == 'password_resets':
        return {'schema':'tfi.portal-password-resets.v1','tokens':mapping('password_reset_tokens','digest',remove=True)}
    if kind == 'authentication_attempts':
        return mapping('login_attempts','attempt_key',remove=True)
    if kind == 'passkeys':
        users = {r['username']:{'user_handle':r['user_handle'],'credentials':[]} for r in tables['passkey_users']}
        for row in sorted(tables['passkey_credentials'], key=lambda r:(r['created_at'],r['credential_id'])):
            users[row['username']]['credentials'].append({k:v for k,v in row.items() if k!='username'})
        return {'schema':'tfi.portal-passkeys.v1','users':users}
    if kind == 'two_factor':
        users = {r['username']:{'secret':{'nonce':r['nonce'],'ciphertext':r['ciphertext']},'enabled_at':r['enabled_at'],'last_counter':r['last_counter'],'recovery_code_digests':[]} for r in tables['two_factor_users']}
        for row in tables['two_factor_recovery_codes']:
            users[row['username']]['recovery_code_digests'].append(row['digest'])
        return {'schema':'tfi.portal-two-factor.v1','users':users}
    raise ValueError('Unknown identity collection')


def encode(kind, value):
    tables = _encode_content(kind, value)
    for table, rows in tables.items():
        tables[table] = [dict(row) for row in rows]
        for row in tables[table]:
            for field in TIME_FIELDS.get(table, ()):
                if field not in row:
                    continue
                value = row[field]
                if table == 'passkey_credentials' and field == 'last_used_at' and value == 0:
                    value = None
                row[field] = instant(value)
    return tables


def _encode_content(kind, value):
    if kind == 'access':
        return {'user_accounts':list(value['users'].values())}
    if kind == 'credentials':
        return {'passwords':[{'username':k,'digest':v} for k,v in value.items()]}
    if kind == 'user_directory':
        if value.get('agents'):
            raise ValueError('Agent identities belong to the Agent domain')
        return {'directory_users':value['humans']}
    if kind == 'user_enrollment':
        return {'user_invitations':list(value['invitations'].values()), 'user_access_tokens':list(value['tokens'].values())}
    if kind == 'password_resets':
        return {'password_reset_tokens':[{'digest':k,**v} for k,v in value['tokens'].items()]}
    if kind == 'authentication_attempts':
        return {'login_attempts':[{'attempt_key':k,**v} for k,v in value.items()]}
    if kind == 'passkeys':
        return {'passkey_users':[{'username':k,'user_handle':v['user_handle']} for k,v in value['users'].items()],
                'passkey_credentials':[{'username':k,**c} for k,v in value['users'].items() for c in v['credentials']]}
    if kind == 'two_factor':
        return {'two_factor_users':[{'username':k,**v['secret'],'enabled_at':v['enabled_at'],'last_counter':v['last_counter']} for k,v in value['users'].items()],
                'two_factor_recovery_codes':[{'username':k,'digest':d} for k,v in value['users'].items() for d in v['recovery_code_digests']]}
    raise ValueError('Unknown identity collection')


def write(connection, kind, value):
    for table, rows in encode(kind,value).items():
        keys, columns = TABLES[table]
        sync_rows(connection,table,keys,tuple(columns),rows)


class IdentityState:
    """Domain transaction facade for the existing account service methods."""
    def __init__(self, kind):
        if kind not in KINDS:
            raise ValueError('Unknown identity collection')
        self.kind = kind

    def transaction(self):
        # Enrollment composes account, credential and invitation writes in one
        # database transaction. One identity lock also fixes lock-order inversion.
        return database().transaction('identity')

    def read(self, default=None):
        with database().read() as connection:
            return decode(self.kind, {table:read_rows(connection,table,(*TABLES[table][0],*TABLES[table][1])) for table in KINDS[self.kind]})

    def replace(self, value):
        with self.transaction() as connection:
            write(connection,self.kind,value)

    def revision(self):
        # Only the small account directory uses this cache token; credentials
        # and secrets never leave their owning service.
        with database().read() as connection:
            digest = connection.execute("SELECT md5(COALESCE(string_agg(user_accounts::text,',' ORDER BY username),'')) FROM user_accounts").fetchone()[0]
            return int(digest[:13], 16)
