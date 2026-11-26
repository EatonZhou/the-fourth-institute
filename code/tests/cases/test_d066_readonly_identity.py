"""Cold preview reads use installed state without initiating migrations."""
from __future__ import annotations

import uuid

import psycopg
import pytest

from management_common.domain_database import DomainDatabase
from management_common.portal_database import portal_database
from management_common.postgres_database import PostgresConnection, PostgresDatabase
from management_portal import identity_store
from management_portal.portal_access_control import PortalAccessStore


def forbidden(*args, **kwargs):
    raise AssertionError('readonly path reached cache, pool, revision or ensure')


@pytest.fixture
def installed_database():
    db = portal_database(schema='readonly_' + uuid.uuid4().hex[:8])
    with db.transaction() as connection:
        connection.execute('CREATE TABLE probe (id integer PRIMARY KEY, value text NOT NULL)')
        connection.execute("INSERT INTO probe VALUES (1, 'committed')")
    return db


def test_cold_read_uses_installed_schema_without_pool_or_ddl(installed_database, monkeypatch):
    db = installed_database
    cold = PostgresDatabase(db.dsn, schema=db.schema)
    monkeypatch.setattr(cold, '_ensure_schema', forbidden)
    monkeypatch.setattr(cold, '_connection_pool', forbidden)
    monkeypatch.setattr(cold, '_configure', forbidden)
    with cold.readonly_snapshot() as connection:
        assert isinstance(connection, PostgresConnection)
        assert connection.execute('SELECT current_schema()').fetchone()[0] == db.schema
        assert connection.execute('SHOW transaction_read_only').fetchone()[0] == 'on'
        assert connection.execute('SHOW transaction_isolation').fetchone()[0] == 'repeatable read'
        assert connection.execute('SELECT value FROM probe').fetchone()['value'] == 'committed'
    assert connection.raw.closed


def test_readonly_transaction_rejects_writes_and_closes(installed_database):
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        with installed_database.readonly_snapshot() as connection:
            connection.execute("UPDATE probe SET value='forbidden'")
    assert connection.raw.closed
    with installed_database.read() as regular:
        assert regular.execute('SELECT value FROM probe').fetchone()[0] == 'committed'


def test_readonly_never_inherits_existing_write_transaction(installed_database):
    db = installed_database
    with db.transaction() as writer:
        writer.execute("UPDATE probe SET value='uncommitted'")
        writer_pid = writer.execute('SELECT pg_backend_pid()').fetchone()[0]
        with db.readonly_snapshot() as reader:
            assert reader is not writer
            assert reader.execute('SELECT pg_backend_pid()').fetchone()[0] != writer_pid
            assert reader.execute('SELECT value FROM probe').fetchone()[0] == 'committed'
        with db.snapshot() as ordinary:
            assert ordinary is writer  # Existing normal snapshot semantics stay intact.
            assert ordinary.execute('SELECT value FROM probe').fetchone()[0] == 'uncommitted'
        writer.execute("UPDATE probe SET value='finished'")
    with db.readonly_snapshot() as reader:
        assert reader.execute('SELECT value FROM probe').fetchone()[0] == 'finished'


def test_readonly_keeps_repeatable_snapshot_across_commits(installed_database):
    db = installed_database
    with db.readonly_snapshot() as reader:
        assert reader.execute('SELECT value FROM probe').fetchone()[0] == 'committed'
        with db.transaction() as writer:
            writer.execute("UPDATE probe SET value='new'")
        assert reader.execute('SELECT value FROM probe').fetchone()[0] == 'committed'
    with db.readonly_snapshot() as reader:
        assert reader.execute('SELECT value FROM probe').fetchone()[0] == 'new'


def test_missing_schema_fails_without_creating_it(monkeypatch):
    db = portal_database(schema='absent_' + uuid.uuid4().hex[:8])
    monkeypatch.setattr(db, '_ensure_schema', forbidden)
    monkeypatch.setattr(db, '_connection_pool', forbidden)
    with pytest.raises(RuntimeError, match='schema.*not installed'):
        with db.readonly_snapshot():
            pytest.fail('missing schema must not yield a connection')
    with psycopg.connect(db.dsn) as check:
        assert check.execute('SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname=%s', (db.schema,)).fetchone() is None


@pytest.fixture
def identity_domain(monkeypatch):
    domain = DomainDatabase('identity_' + uuid.uuid4().hex[:8], 'accounts', identity_store.DDL)
    monkeypatch.setattr(identity_store, 'database', lambda: domain)
    return domain


def account_document():
    return {'schema': 'tfi.portal-access.v1', 'updated_at': '', 'users': {
        'alice': {'username': 'alice', 'display_name': '  Alice  ', 'email': '',
                  'status': 'active', 'session_version': 0, 'created_at': '2026-11-26T00:00:00+00:00',
                  'updated_at': '2026-11-26T00:00:00+00:00', 'updated_by': 'alice', 'category': 'member'}}}


def test_identity_readonly_returns_raw_document_without_migration(identity_domain, monkeypatch):
    state = identity_store.IdentityState('access')
    state.replace(account_document())
    expected = state.read()
    identity_domain._ready = False
    monkeypatch.setattr(identity_domain, 'ensure', forbidden)
    monkeypatch.setattr(identity_domain.database, '_connection_pool', forbidden)
    assert state.readonly({'schema': 'fallback'}) == expected
    assert state.readonly()['users']['alice']['display_name'] == '  Alice  '


def test_empty_installed_identity_uses_owner_empty_document(identity_domain, monkeypatch):
    identity_domain.ensure()
    monkeypatch.setattr(identity_domain, 'ensure', forbidden)
    assert identity_store.IdentityState('access').readonly() == {
        'schema': 'tfi.portal-access.v1', 'users': {}, 'updated_at': ''}


@pytest.mark.parametrize('missing', ['schema', 'table', 'column'])
def test_identity_readonly_missing_structures_fail_without_defaults(identity_domain, monkeypatch, missing):
    if missing == 'table':
        identity_domain.database._ensure_schema()  # Only isolated fixture setup.
    elif missing == 'column':
        identity_domain.ensure()
        with identity_domain.database.transaction() as connection:
            connection.execute('ALTER TABLE user_accounts DROP COLUMN category')
    monkeypatch.setattr(identity_domain, 'ensure', forbidden)
    monkeypatch.setattr(identity_domain.database, '_connection_pool', forbidden)
    error = {'schema': RuntimeError, 'table': psycopg.errors.UndefinedTable,
             'column': psycopg.errors.UndefinedColumn}[missing]
    with pytest.raises(error):
        identity_store.IdentityState('access').readonly({'schema': 'fallback', 'users': {}})


def test_access_readonly_bypasses_stale_cache_and_revision(identity_domain, monkeypatch):
    state = identity_store.IdentityState('access')
    state.replace(account_document())
    access = PortalAccessStore()
    old = access.snapshot()
    assert old['users']['alice']['display_name'] == 'Alice'
    with identity_domain.transaction() as writer:
        writer.execute("UPDATE user_accounts SET display_name='  New Alice  ', status='disabled' WHERE username='alice'")
    monkeypatch.setattr(access, '_revision', forbidden)
    monkeypatch.setattr(access.state, 'read', forbidden)
    monkeypatch.setattr(identity_domain, 'ensure', forbidden)
    fresh = access.snapshot(read_only=True)
    assert fresh['users']['alice']['display_name'] == 'New Alice'
    assert fresh['users']['alice']['status'] == 'disabled'
    assert access._cached == old
    fresh['users'].clear()
    assert 'alice' in access.snapshot(read_only=True)['users']


def test_access_normal_snapshot_keeps_ordinary_revision_cache(identity_domain):
    state = identity_store.IdentityState('access')
    state.replace(account_document())
    access = PortalAccessStore()
    assert access.snapshot() == access.snapshot(read_only=True)
    with identity_domain.transaction() as writer:
        writer.execute("UPDATE user_accounts SET display_name='Updated' WHERE username='alice'")
    assert access.snapshot()['users']['alice']['display_name'] == 'Updated'
