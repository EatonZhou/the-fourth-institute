"""Pinned metadata migration preserves business text and recovery identities."""
import json
from contextlib import contextmanager
import pytest

from scripts.migrate_tfi_persisted_state import rewrite_json, rewrite_environment, migrate_column, rewrite_admission


def test_only_structured_schema_tokens_change_and_other_bytes_survive():
    old = 'archive.operation-identity.v1'
    raw = '{ "schema" : "archive.operation-identity.v1", "id":"keep", "prompt": "\\\"schema\\\": \\\"archive.operation-identity.v1\\\"", "nested":[{"schema":"archive.operation-identity.v1"}]}\n'
    expected = raw.replace('"schema" : "archive.operation-identity.v1"', '"schema" : "tfi.operation-identity.v1"').replace('{"schema":"archive.operation-identity.v1"}', '{"schema":"tfi.operation-identity.v1"}')
    assert rewrite_json(raw, {old: 'tfi.operation-identity.v1'}) == expected
    assert json.loads(rewrite_json(raw, {old: 'tfi.operation-identity.v1'}))['id'] == 'keep'


def test_unplanned_schemas_are_untouched():
    raw = '{"schema":"archive.unknown.v9"}'
    assert rewrite_json(raw, {'archive.operation-identity.v1':'tfi.operation-identity.v1'}) == raw


def test_environment_removes_equal_old_keys_and_keeps_canonical_identity():
    value = {'env': {'ARCHIVE_RESIDENT_ID':'resident_a', 'PORTAL_RESIDENT_ID':'resident_a'}, 'prompt':'ARCHIVE_RESIDENT_ID'}
    assert rewrite_environment(value, {'ARCHIVE_RESIDENT_ID':'PORTAL_RESIDENT_ID'}) == {
        'env': {'PORTAL_RESIDENT_ID':'resident_a'}, 'prompt':'ARCHIVE_RESIDENT_ID'}


def test_environment_collision_never_changes_the_executor():
    with pytest.raises(ValueError, match='Conflicting'):
        rewrite_environment({'ARCHIVE_RESIDENT_ID':'resident_a','PORTAL_RESIDENT_ID':'resident_b'}, {'ARCHIVE_RESIDENT_ID':'PORTAL_RESIDENT_ID'})


def test_malformed_json_is_never_repaired_silently():
    with pytest.raises(ValueError):
        rewrite_json('{broken', {'archive.x.v1':'tfi.x.v1'})


def test_admission_requires_exact_original_preimage():
    from management_portal.execution.submission_identity import submission_digest
    raw = '{ "schema":"archive.execution-request.v8", "execution_id":"preserved", "prompt":"archive.execution-request.v8"}'
    mapping = {'archive.execution-request.v8':'tfi.execution-request.v8'}
    with pytest.raises(ValueError, match='preimage'):
        rewrite_admission(raw, '0'*64, mapping)
    after, digest = rewrite_admission(raw, submission_digest(json.loads(raw)), mapping)
    assert digest == submission_digest(json.loads(after))
    assert json.loads(after)['execution_id'] == 'preserved'
    assert json.loads(after)['prompt'] == json.loads(raw)['prompt']


def test_postgres_batches_back_up_before_commit_and_are_idempotent(tmp_path):
    import gzip
    mapping = {'archive.operation-identity.v1':'tfi.operation-identity.v1'}
    with migration_records() as (c, schema):
        raw = '{ "schema":"archive.operation-identity.v1", "prompt":"archive.operation-identity.v1"}'
        for key in ['a','b']:
            insert_record(c, schema, key, raw)
        result = migrate_column(c,schema,'records','body',mapping,tmp_path,batch_size=1)
        assert result['updated']==2 and result['remaining']==0
        backups=list(tmp_path.rglob('batch-*.jsonl.gz'))
        assert len(backups)==2
        assert all(json.loads(gzip.open(p,'rt').read())['before']==raw for p in backups)
        assert all(row[2] == 'keep' for row in read_records(c, schema))
        assert migrate_column(c,schema,'records','body',mapping,tmp_path)['updated']==0


@contextmanager
def migration_records(body_type='text'):
    import os
    import uuid
    import psycopg
    from psycopg import sql
    schema = 'migration_test_' + uuid.uuid4().hex
    with psycopg.connect(os.environ['PORTAL_DATABASE_URL'], autocommit=True) as c:
        c.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            c.execute(sql.SQL('CREATE TABLE {}.records (id text primary key, body {}, unrelated text)').format(
                sql.Identifier(schema), sql.SQL(body_type)))
            yield c, schema
        finally:
            c.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def insert_record(c, schema, key, raw):
    from psycopg import sql
    c.execute(sql.SQL('INSERT INTO {}.records VALUES (%s,%s,%s)').format(sql.Identifier(schema)), (key,raw,'keep'))


def read_records(c, schema):
    from psycopg import sql
    return c.execute(sql.SQL('SELECT id,body,unrelated FROM {}.records ORDER BY id').format(sql.Identifier(schema))).fetchall()


def test_json_tokens_distinguish_business_key_and_keep_other_bytes():
    old = 'archive.operation-identity.v1'
    raw = '{"schema":"archive.operation-identity.v1", "business\\\"schema":"archive.operation-identity.v1", "prompt":"\\\"schema\\\":\\\"archive.operation-identity.v1\\\""}\n'
    expected = raw.replace('"schema":"archive.operation-identity.v1"', '"schema":"tfi.operation-identity.v1"', 1)
    assert rewrite_json(raw, {old:'tfi.operation-identity.v1'}) == expected


def test_json_unicode_key_and_value_only_replace_value_bytes():
    raw = '{ "sche\\u006da" : "archi\\u0076e.operation-identity.v1", "actor":"same", "prompt":"archi\\u0076e.operation-identity.v1"}\n'
    expected = raw.replace('"archi\\u0076e.operation-identity.v1"', '"tfi.operation-identity.v1"', 1)
    assert rewrite_json(raw, {'archive.operation-identity.v1':'tfi.operation-identity.v1'}) == expected


def test_mixed_batch_checks_every_row_and_keeps_non_schema_value(tmp_path):
    old = 'archive.operation-identity.v1'
    a = json.dumps({'schema':old})
    b = json.dumps({'schema':old, 'business"schema':old, 'prompt':old})
    with migration_records() as (c,schema):
        insert_record(c,schema,'a',a)
        insert_record(c,schema,'b',b)
        result=migrate_column(c,schema,'records','body',{old:'tfi.operation-identity.v1'},tmp_path,batch_size=2)
        assert result['updated']==2 and result['remaining']==0
        assert json.loads(read_records(c,schema)[1][1]) == {'schema':'tfi.operation-identity.v1', 'business"schema':old, 'prompt':old}


@pytest.mark.parametrize('body_type',['text','json','jsonb'])
def test_structured_selection_handles_unicode_and_only_string_schema_values(tmp_path,body_type):
    old='archive.operation-identity.v1'
    raws = [
        '{"sche\\u006da":"archi\\u0076e.operation-identity.v1"}',
        json.dumps({'nested':[{'schema':old}]}),
        json.dumps({'business"schema':old}),
        json.dumps({'schema':[old]}),
        json.dumps({'schema':{'schema':old}}),
    ]
    with migration_records(body_type) as (c,schema):
        for i,raw in enumerate(raws):insert_record(c,schema,str(i),raw)
        result=migrate_column(c,schema,'records','body',{old:'tfi.operation-identity.v1'},tmp_path)
        assert result['updated']==3 and result['remaining']==0
        for raw,(_,stored,_) in zip(raws,read_records(c,schema)):
            actual=json.loads(stored) if isinstance(stored,str) else stored
            from scripts.migrate_tfi_persisted_state import rewrite_schemas
            assert actual==rewrite_schemas(json.loads(raw),{old:'tfi.operation-identity.v1'})


def test_invalid_database_json_rejects_instead_of_silently_skipping(tmp_path):
    import psycopg
    old='archive.operation-identity.v1'
    with migration_records() as (c,schema):
        insert_record(c,schema,'a','not-json')
        insert_record(c,schema,'b',json.dumps({'schema':old}))
        with pytest.raises(psycopg.errors.InvalidTextRepresentation):
            migrate_column(c,schema,'records','body',{old:'tfi.operation-identity.v1'},tmp_path)
        assert read_records(c,schema)[1][1]==json.dumps({'schema':old})


def test_backup_write_failure_prevents_database_commit(tmp_path,monkeypatch):
    import gzip
    old='archive.operation-identity.v1'
    raw=json.dumps({'schema':old})
    def fail(*args,**kwargs):raise OSError('injected backup failure')
    with migration_records() as (c,schema):
        insert_record(c,schema,'a',raw)
        with monkeypatch.context() as patch:
            patch.setattr(gzip.GzipFile,'write',fail)
            with pytest.raises(OSError,match='backup failure'):
                migrate_column(c,schema,'records','body',{old:'tfi.operation-identity.v1'},tmp_path)
        assert read_records(c,schema)==[('a',raw,'keep')]


def test_new_backup_directories_sync_each_parent_before_next_child(tmp_path,monkeypatch):
    from pathlib import Path
    import os
    import scripts.migrate_tfi_persisted_state as migration
    events=[]
    mkdir=Path.mkdir
    sync=os.fsync
    def record_mkdir(path,*args,**kwargs):
        result=mkdir(path,*args,**kwargs);events.append(('mkdir',path));return result
    def record_sync(fd):
        path=Path(os.readlink('/proc/self/fd/'+str(fd)))
        events.append(('sync',path));return sync(fd)
    with monkeypatch.context() as patch:
        patch.setattr(Path,'mkdir',record_mkdir)
        patch.setattr(os,'fsync',record_sync)
        migration.durable_private_directory(tmp_path/'new'/'child')
    assert events.index(('mkdir',tmp_path/'new')) < events.index(('sync',tmp_path)) < events.index(('mkdir',tmp_path/'new'/'child'))
    child_created=events.index(('mkdir',tmp_path/'new'/'child'))
    assert child_created < events.index(('sync',tmp_path/'new'),child_created)


@pytest.mark.parametrize('invalid',['NaN','Infinity','-Infinity'])
def test_non_json_number_constants_are_explicitly_rejected(invalid):
    with pytest.raises(ValueError):
        rewrite_json('{"schema":"archive.x.v1","value":'+invalid+'}',{'archive.x.v1':'tfi.x.v1'})


def test_backup_file_sync_failure_prevents_database_commit(tmp_path,monkeypatch):
    import os
    old='archive.operation-identity.v1'
    raw=json.dumps({'schema':old})
    sync=os.fsync
    def fail_backup(fd):
        if os.readlink('/proc/self/fd/'+str(fd)).endswith('.jsonl.gz'):
            raise OSError('injected backup fsync failure')
        return sync(fd)
    with migration_records() as (c,schema):
        insert_record(c,schema,'a',raw)
        with monkeypatch.context() as patch:
            patch.setattr(os,'fsync',fail_backup)
            with pytest.raises(OSError,match='fsync failure'):
                migrate_column(c,schema,'records','body',{old:'tfi.operation-identity.v1'},tmp_path)
        assert read_records(c,schema)==[('a',raw,'keep')]


def test_backup_directory_failure_releases_migration_lock(tmp_path):
    import os
    import psycopg
    old='archive.operation-identity.v1'
    unavailable=tmp_path/'not-a-directory'
    unavailable.touch()
    with migration_records() as (c,schema):
        insert_record(c,schema,'a',json.dumps({'schema':old}))
        with pytest.raises(NotADirectoryError):
            migrate_column(c,schema,'records','body',{old:'tfi.operation-identity.v1'},unavailable)
        with psycopg.connect(os.environ['PORTAL_DATABASE_URL'],autocommit=True) as other:
            lock_name='tfi-schema-name-migration:'+schema+'.records.body'
            assert other.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0))',(lock_name,)).fetchone()[0]
            other.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))',(lock_name,))


def test_locked_rows_are_reported_and_can_be_retried_without_overwrite(tmp_path):
    import os
    import psycopg
    from psycopg import sql
    old='archive.operation-identity.v1'
    raw=json.dumps({'schema':old,'prompt':'keep'})
    with migration_records() as (c,schema):
        for key in ['a','b']:insert_record(c,schema,key,raw)
        with psycopg.connect(os.environ['PORTAL_DATABASE_URL']) as writer:
            writer.execute(sql.SQL('SELECT * FROM {}.records WHERE id=%s FOR UPDATE').format(sql.Identifier(schema)),('a',))
            result=migrate_column(c,schema,'records','body',{old:'tfi.operation-identity.v1'},tmp_path,batch_size=2)
            assert result['updated']==1 and result['remaining']==1
            assert read_records(c,schema)[0][1]==raw
            writer.rollback()
        result=migrate_column(c,schema,'records','body',{old:'tfi.operation-identity.v1'},tmp_path,batch_size=2)
        assert result['updated']==1 and result['remaining']==0
        assert all(json.loads(row[1])=={'schema':'tfi.operation-identity.v1','prompt':'keep'} for row in read_records(c,schema))
