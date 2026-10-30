"""Admission gates on disposable PG16 only; never use the deployed database."""
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo, conninfo_to_dict
import pytest

from scripts import tfi_application_admission as admission


@contextmanager
def make_database(server, tmp_path):
    name = 'admission_' + uuid.uuid4().hex
    with psycopg.connect(server, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    dsn = make_conninfo(server, dbname=name)
    target = {'database_name': name, 'install_root': str(tmp_path / name)}
    api = SimpleNamespace(clean_environment=lambda _: {'PORTAL_DATABASE_URL': dsn})
    try:
        with psycopg.connect(dsn) as held:
            held.execute('CREATE TABLE saved_work (id int PRIMARY KEY, payload text)')
            held.execute("INSERT INTO saved_work VALUES (1,'durable waiting input')")
            held.commit()
            held.execute('LOCK TABLE saved_work IN SHARE MODE')
            yield SimpleNamespace(server=server, dsn=dsn, records=[{'target': target}],
                                  api=api, held=held, target=target)
    finally:
        with psycopg.connect(server, autocommit=True) as admin:
            admin.execute(sql.SQL('ALTER DATABASE {} ALLOW_CONNECTIONS true').format(sql.Identifier(name)))
            admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))


@pytest.fixture
def database(tmp_path):
    with make_database(os.environ['PORTAL_POSTGRES_TEST_DSN'], tmp_path) as value:
        yield value


def allowed(db):
    return db.held.execute('SELECT datallowconn FROM pg_database WHERE datname=current_database()').fetchone()[0]


def prepare(db):
    return admission.prepare(db.api, db.records, [db.held])


def test_close_keeps_existing_barrier_and_work_restore_is_idempotent(database):
    db = database
    transaction = db.held.execute('SELECT txid_current()').fetchone()[0]
    entries = prepare(db)
    assert json.loads(json.dumps(entries)) == entries
    assert entries[0]['database']['allow_connections'] is True
    assert db.dsn not in json.dumps(entries)
    assert allowed(db)
    admission.close(db.api, db.records, entries)
    admission.close(db.api, db.records, entries)
    assert not allowed(db)
    assert db.held.execute('SELECT * FROM saved_work').fetchall() == [(1, 'durable waiting input')]
    # The test connection is superuser: even it cannot make a NEW connection.
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(db.dsn)
    admission.restore(db.api, db.records, entries)
    admission.restore(db.api, db.records, entries)
    assert allowed(db)
    assert prepare(db) == entries
    assert db.held.execute('SELECT txid_current()').fetchone()[0] == transaction
    with psycopg.connect(db.dsn) as writer:
        writer.execute("SET lock_timeout='50ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            writer.execute("INSERT INTO saved_work VALUES (2,'must not write')")
    # No maintenance session survives any API call.
    from scripts.tfi_application_freeze import assert_no_clients
    assert_no_clients(db.held)


def test_original_false_is_refused_without_opening_gate(database):
    db = database
    entries = prepare(db)
    admission.close(db.api, db.records, entries)
    with pytest.raises(admission.AdmissionError, match='original_admission_closed'):
        prepare(db)
    assert not allowed(db)
    admission.restore(db.api, db.records, entries)


@pytest.mark.parametrize('change,code', [
    ('target', 'target_binding_changed'), ('system', 'cluster_identity_changed'),
    ('oid', 'database_identity_changed'), ('owner', 'database_identity_changed'),
    ('maintenance_oid', 'maintenance_identity_changed'), ('original_false', 'invalid_database_receipt'),
    ('extra', 'invalid_entry'), ('length', 'entry_count_mismatch'),
])
def test_changed_or_malformed_receipt_cannot_close(database, change, code):
    db = database
    entries = prepare(db)
    if change == 'target': entries[0]['target_sha256'] = '0' * 64
    elif change == 'system': entries[0]['system_identifier'] = '1'
    elif change in ('oid', 'owner'): entries[0]['database']['oid' if change == 'oid' else 'owner_oid'] += 1
    elif change == 'maintenance_oid': entries[0]['maintenance']['oid'] += 1
    elif change == 'original_false': entries[0]['database']['allow_connections'] = False
    elif change == 'extra': entries[0]['dsn'] = 'private-value'
    elif change == 'length': entries.append(deepcopy(entries[0]))
    with pytest.raises(admission.AdmissionError, match=code):
        admission.close(db.api, db.records, entries)
    assert allowed(db)


def test_same_cluster_targets_refused_before_action(database, tmp_path):
    db = database
    with make_database(db.server, tmp_path) as other:
        with pytest.raises(admission.AdmissionError, match='same_cluster_targets_unsupported'):
            admission.prepare(db.api, db.records + other.records, [db.held, other.held])
        assert allowed(db) and allowed(other)


def test_wrong_barrier_or_dsn_refused(database):
    db = database
    with psycopg.connect(db.server) as other:
        with pytest.raises(admission.AdmissionError, match='barrier_database_mismatch'):
            admission.prepare(db.api, db.records, [other])
    wrong = SimpleNamespace(clean_environment=lambda _: {'PORTAL_DATABASE_URL': db.server})
    with pytest.raises(admission.AdmissionError, match='target_dsn_mismatch'):
        admission.prepare(wrong, db.records, [db.held])
    assert allowed(db)


def test_catalog_permission_refused_without_driver_details(database):
    db = database
    role = 'admission_role_' + uuid.uuid4().hex
    with psycopg.connect(db.server, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE ROLE {} LOGIN').format(sql.Identifier(role)))
        try:
            entries = prepare(db)
            limited = SimpleNamespace(clean_environment=lambda _: {
                'PORTAL_DATABASE_URL': make_conninfo(db.dsn, user=role, password='secret-canary')})
            with pytest.raises(admission.AdmissionError) as error:
                admission.close(limited, db.records, entries)
            assert str(error.value) == 'admission_operation_failed'
            assert 'secret-canary' not in repr(error.value)
            assert allowed(db)
        finally:
            admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


def test_catalog_identity_is_rechecked_on_restore(database):
    db = database
    entries = prepare(db)
    admission.close(db.api, db.records, entries)
    role = 'new_owner_' + uuid.uuid4().hex
    with psycopg.connect(db.server, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE ROLE {}').format(sql.Identifier(role)))
        original = admin.execute('SELECT current_user').fetchone()[0]
        try:
            admin.execute(sql.SQL('ALTER DATABASE {} OWNER TO {}').format(
                sql.Identifier(db.target['database_name']), sql.Identifier(role)))
            with pytest.raises(admission.AdmissionError, match='database_identity_changed'):
                admission.restore(db.api, db.records, entries)
            assert not allowed(db)
        finally:
            admin.execute(sql.SQL('ALTER DATABASE {} OWNER TO {}').format(
                sql.Identifier(db.target['database_name']), sql.Identifier(original)))
            admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
    admission.restore(db.api, db.records, entries)


@pytest.mark.parametrize('commit_happened', [False, True])
def test_all_live_entries_checked_before_action_and_partial_commit_recovers(database, tmp_path, monkeypatch, commit_happened):
    from tests.cases.postgres_test_database import start_disposable_cluster, stop_cluster
    second_server = start_disposable_cluster()
    root = Path(conninfo_to_dict(second_server)['host']).parent
    try:
        with make_database(second_server, tmp_path) as other:
            db = database
            records = db.records + other.records
            dsns = {db.target['database_name']: db.dsn, other.target['database_name']: other.dsn}
            api = SimpleNamespace(clean_environment=lambda t: {'PORTAL_DATABASE_URL': dsns[t['database_name']]})
            entries = admission.prepare(api, records, [db.held, other.held])
            wrong = deepcopy(entries)
            wrong[1]['database']['owner_oid'] += 1
            with pytest.raises(admission.AdmissionError, match='database_identity_changed'):
                admission.close(api, records, wrong)
            assert allowed(db) and allowed(other)
            real_connect = admission._connect

            class LostAcknowledgement:
                def __init__(self, conn): self.conn = conn
                def __getattr__(self, key): return getattr(self.conn, key)
                def commit(self):
                    if commit_happened:
                        self.conn.commit()
                    raise OSError('private driver canary during commit')

            def connect(api, target, name, stack):
                conn = real_connect(api, target, name, stack)
                return LostAcknowledgement(conn) if target == other.target else conn

            with monkeypatch.context() as patch:
                patch.setattr(admission, '_connect', connect)
                with pytest.raises(admission.AdmissionError, match='admission_operation_failed'):
                    admission.close(api, records, entries)
            # The first commit is durable, the second has an uncertain outcome.
            # Recovery uses the sealed original states, not a progress counter.
            assert not allowed(db) and allowed(other) is (not commit_happened)
            admission.restore(api, records, entries)
            assert allowed(db) and allowed(other)
    finally:
        stop_cluster(root)


def test_postgres_target_uses_template1_and_missing_maintenance_refuses(database):
    db = database
    target = dict(db.target, database_name='postgres')
    records = [{'target': target}]
    api = SimpleNamespace(clean_environment=lambda _: {'PORTAL_DATABASE_URL': db.server})
    with psycopg.connect(db.server) as held:
        original = held.execute("SELECT datallowconn FROM pg_database WHERE datname='template1'").fetchone()[0]
        assert original is True
        entries = admission.prepare(api, records, [held])
        assert entries[0]['maintenance']['name'] == 'template1'
        try:
            admission.close(api, records, entries)
            with pytest.raises(psycopg.OperationalError):
                psycopg.connect(db.server)
        finally:
            admission.restore(api, records, entries)
        with psycopg.connect(db.dsn, autocommit=True) as control:
            control.execute('ALTER DATABASE template1 ALLOW_CONNECTIONS false')
            try:
                with pytest.raises(admission.AdmissionError, match='maintenance_database_unavailable'):
                    admission.prepare(api, records, [held])
            finally:
                control.execute('ALTER DATABASE template1 ALLOW_CONNECTIONS true')


@pytest.fixture
def short_root():
    # Native Unix socket addresses have a 107-byte limit, unlike pytest's
    # deployment-configured temporary directory path on this host.
    with tempfile.TemporaryDirectory(prefix='admission-', dir='/tmp') as root:
        yield Path(root)


def test_cli_runtime_child_restores_using_stdin_only(database, short_root):
    from scripts import tfi_release_install as installer
    db = database
    parts = conninfo_to_dict(db.dsn)
    root = short_root
    (root / 'runtime').mkdir(parents=True)
    (root / 'runtime/s').symlink_to(parts['host'], target_is_directory=True)
    target = dict(db.target, install_root=str(root), database_user=parts['user'],
                  service_prefix='test-admission', run_id='test',
                  ports={'postgres': int(parts['port']), 'web': 12345, 'nats': 12346},
                  data_roots={'staging_root': str(root / 'staging')})
    records = [{'target': target}]
    # Prove the runtime's real environment path separately from the sanitized
    # public gate error, so fixture setup failures retain their test traceback.
    with psycopg.connect(installer.clean_environment(target)['PORTAL_DATABASE_URL']):
        pass
    entries = admission.prepare(installer, records, [db.held])
    admission.close(installer, records, entries)
    command = [sys.executable, '-m', 'scripts.tfi_application_admission']
    result = subprocess.run(command, input=json.dumps({'records': records, 'entries': entries}),
                            text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'status': 'restored', 'instances': 1}
    assert result.stderr == ''
    assert allowed(db)
    failed = subprocess.run(command, input='{"dsn":"secret-canary"}', text=True,
                            capture_output=True, timeout=20)
    assert failed.returncode == 1 and not failed.stdout
    assert json.loads(failed.stderr) == {'status': 'refused', 'code': 'invalid_request'}
    assert 'secret-canary' not in failed.stderr


def test_prepare_never_changes_supplied_connection_on_driver_error(database, monkeypatch):
    db = database
    def fail(*args): raise OSError('postgresql://password@private-host/db')
    monkeypatch.setattr(admission, '_catalog_lock', fail)
    with pytest.raises(admission.AdmissionError) as error:
        prepare(db)
    assert str(error.value) == 'admission_operation_failed'
    assert allowed(db)
    assert db.held.execute('SELECT count(*) FROM saved_work').fetchone() == (1,)
