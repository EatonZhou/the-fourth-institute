"""One-time avatar repair preserves identity, explicit choices and bytes."""
import hashlib
import io
import json

import pytest
from PIL import Image

from management_portal.avatar_store import ScopeAvatarStore
from management_portal.resident_avatar import ResidentAvatarStore
from management_portal.user_avatar import UserAvatarStore
from management_portal import avatar_migration as migration
from management_common.file_lock import lock_stream

RID = 'resident_000000000001'


def setup_store(tmp_path, kind='resident'):
    old, new = tmp_path / 'old', tmp_path / 'new'
    old.mkdir()
    new.mkdir()
    return old, new, {'stores': [{'kind': kind, 'source_root': str(old),
        'target_root': str(new), 'source_schemas': [f'archive.{kind}-avatar.v1'],
        'restore_automatic': True}]}


def record(root, subject=RID, store_kind='resident', **avatar):
    store = {'resident': ResidentAvatarStore, 'user': UserAvatarStore,
             'scope': ScopeAvatarStore}[store_kind](root)
    value = {'schema': f'archive.{store_kind}-avatar.v1', store.identity_field: subject, **avatar}
    path = store._path(subject)
    path.write_text(json.dumps(value))
    return path


def run(config, tmp_path):
    plan = migration.build_plan(config)
    return plan, migration.apply_plan(plan, tmp_path / 'backup')


def png():
    data = io.BytesIO()
    Image.new('RGB', (32, 32), (20, 30, 40)).save(data, 'PNG')
    return data.getvalue()


@pytest.mark.parametrize('kind,subject', [('resident', RID), ('user', 'sample-user'),
    ('scope', 'lineage_000000000001'), ('scope', 'project:sample-project')])
def test_missing_record_in_existing_root_migrates_identity_and_is_idempotent(tmp_path, kind, subject):
    if subject.startswith('lineage_'):
        subject = 'lineage:' + subject
    old, new, config = setup_store(tmp_path, kind)
    store_cls = {'resident': ResidentAvatarStore, 'user': UserAvatarStore, 'scope': ScopeAvatarStore}[kind]
    source = store_cls(old)
    path = source._path(subject)
    payload = {'schema': f'archive.{kind}-avatar.v1', source.identity_field: subject,
               'kind': 'sprite', 'sprite_index': 20}
    path.write_text(json.dumps(payload))
    before = path.read_bytes()
    plan, result = run(config, tmp_path)
    assert result['entries'][0]['status'] == 'applied'
    target = store_cls(new)
    assert target.read(subject)['sprite_index'] == 20
    assert json.loads(target._path(subject).read_text())['schema'] == f'tfi.{kind}-avatar.v1'
    assert path.read_bytes() == before
    assert migration.apply_plan(plan, tmp_path / 'backup')['entries'][0]['status'] == 'already_applied'


def test_restores_automatic_redraw_but_keeps_explicit_new_choice(tmp_path):
    old, new, config = setup_store(tmp_path)
    record(old, sprite_index=20)
    new_store = ResidentAvatarStore(new)
    new_store.get(RID)
    _, result = run(config, tmp_path)
    assert result['entries'][0]['status'] == 'applied'
    assert new_store.get(RID)['sprite_index'] == 20
    new_store.update(RID, {'kind': 'sprite', 'sprite_index': 34})
    _, result = run(config, tmp_path)
    assert result['entries'][0]['status'] == 'preserved'
    assert new_store.get(RID)['sprite_index'] == 34


def test_upload_keeps_original_version_and_recovers_missing_png(tmp_path):
    old, new, config = setup_store(tmp_path)
    data = png()
    version = hashlib.sha256(data).hexdigest()
    record(old, **{'kind': 'upload', 'version': version})
    (old / f'{RID}-{version}.png').write_bytes(data)
    _, result = run(config, tmp_path)
    target = ResidentAvatarStore(new)
    assert target.image(RID, version) == data
    before = target._path(RID).read_bytes()
    target._image_path(RID, version).unlink()
    _, result = run(config, tmp_path)
    assert result['entries'][0]['status'] == 'applied'
    assert target._path(RID).read_bytes() == before
    assert target.image(RID, version) == data


def test_plan_detects_concurrent_user_change_and_never_clobbers(tmp_path):
    old, new, config = setup_store(tmp_path)
    record(old, sprite_index=20)
    plan = migration.build_plan(config)
    target = ResidentAvatarStore(new)
    target.update(RID, {'kind': 'sprite', 'sprite_index': 34})
    result = migration.apply_plan(plan, tmp_path / 'backup')
    assert result['entries'][0]['status'] == 'conflict'
    assert target.read(RID)['sprite_index'] == 34


def test_busy_subject_is_deferred_and_retried_without_changing_content(tmp_path):
    old, new, config = setup_store(tmp_path)
    record(old, sprite_index=20)
    plan = migration.build_plan(config)
    with (new / f'{RID}.lock').open('a+b') as stream, lock_stream(stream):
        result = migration.apply_plan(plan, tmp_path / 'backup')
    assert result['entries'][0]['status'] == 'busy'
    assert not (new / f'{RID}.json').exists()
    assert migration.apply_plan(plan, tmp_path / 'backup')['entries'][0]['status'] == 'applied'


@pytest.mark.parametrize('fault', ['identity', 'filename', 'schema', 'symlink', 'image_hash'])
def test_invalid_inputs_are_reported_without_target_writes(tmp_path, fault):
    old, new, config = setup_store(tmp_path)
    path = record(old, sprite_index=20)
    value = json.loads(path.read_text())
    if fault == 'identity':
        value['resident_id'] = 'resident_000000000002'
    elif fault == 'filename':
        path.rename(old / 'wrong.json')
    elif fault == 'schema':
        value['schema'] = 'unexpected.v2'
    elif fault == 'symlink':
        external = tmp_path / 'external.json'
        path.rename(external)
        path.symlink_to(external)
    elif fault == 'image_hash':
        value.update(kind='upload', version='a' * 64)
        (old / f'{RID}-{"a" * 64}.png').write_bytes(png())
    if fault not in {'filename', 'symlink'}:
        path.write_text(json.dumps(value))
    plan, result = run(config, tmp_path)
    assert result['entries'][0]['status'] == 'invalid'
    assert not list(new.glob('*.json'))


def test_failure_after_image_write_is_resumable_and_other_subjects_continue(tmp_path, monkeypatch):
    old, new, config = setup_store(tmp_path)
    data = png()
    version = hashlib.sha256(data).hexdigest()
    record(old, **{'kind': 'upload', 'version': version})
    (old / f'{RID}-{version}.png').write_bytes(data)
    record(old, subject='resident_000000000002', sprite_index=9)
    plan = migration.build_plan(config)
    write = migration._atomic_write
    def fail_record(directory, name, data):
        if name == f'{RID}.json':
            raise OSError('simulated interruption')
        return write(directory, name, data)
    with monkeypatch.context() as patch:
        patch.setattr(migration, '_atomic_write', fail_record)
        first = migration.apply_plan(plan, tmp_path / 'backup')
    assert [e['status'] for e in first['entries']] == ['error', 'applied']
    final = migration.apply_plan(plan, tmp_path / 'backup')
    assert [e['status'] for e in final['entries']] == ['applied', 'already_applied']
    assert ResidentAvatarStore(new).image(RID, version) == data


def test_same_or_nested_roots_are_rejected(tmp_path):
    old, new, config = setup_store(tmp_path)
    config['stores'][0]['target_root'] = str(old / 'nested')
    with pytest.raises(ValueError):
        migration.build_plan(config)


def test_preserves_restored_upload_without_rewriting_record_or_image(tmp_path):
    old, new, config = setup_store(tmp_path)
    data = png()
    version = hashlib.sha256(data).hexdigest()
    record(old, kind='upload', version=version)
    (old / f'{RID}-{version}.png').write_bytes(data)
    target = ResidentAvatarStore(new)
    target._path(RID).write_text(json.dumps({'schema': target.schema, 'resident_id': RID,
                                           'kind': 'upload', 'version': version}))
    target._image_path(RID, version).write_bytes(data)
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in new.iterdir()}
    _, result = run(config, tmp_path)
    assert result['entries'][0]['status'] == 'preserved'
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in new.iterdir()} == before


def test_changed_source_and_target_symlink_do_not_get_applied(tmp_path):
    old, new, config = setup_store(tmp_path)
    source = record(old, sprite_index=20)
    plan = migration.build_plan(config)
    source.write_text(source.read_text().replace('20', '34'))
    assert migration.apply_plan(plan, tmp_path / 'backup')['entries'][0]['status'] == 'source_changed'
    plan = migration.build_plan(config)
    external = tmp_path / 'outside'
    external.write_text('keep')
    (new / f'{RID}.json').symlink_to(external)
    assert migration.apply_plan(plan, tmp_path / 'backup')['entries'][0]['status'] == 'error'
    assert external.read_text() == 'keep'


def test_bad_upload_does_not_replace_automatic_avatar(tmp_path):
    old, new, config = setup_store(tmp_path)
    record(old, kind='upload', version='a' * 64)
    target = ResidentAvatarStore(new)
    before = target.get(RID)
    _, result = run(config, tmp_path)
    assert result['entries'][0]['status'] == 'invalid'
    assert target.get(RID) == before


def test_http_avatar_handler_reads_repaired_bytes_in_isolation(tmp_path, monkeypatch):
    from management_portal.http_account import AccountHttp
    from urllib.parse import urlparse
    old, new, config = setup_store(tmp_path)
    data = png()
    version = hashlib.sha256(data).hexdigest()
    record(old, kind='upload', version=version)
    (old / f'{RID}-{version}.png').write_bytes(data)
    run(config, tmp_path)
    monkeypatch.setenv('PORTAL_RESIDENT_AVATAR_ROOT', str(new))
    http = object.__new__(AccountHttp)
    http.respond = lambda req, status, body: (status, body)
    http.serve_bytes = lambda req, status, body, content_type, **kw: (status, body, content_type)
    http.exception = lambda *args: pytest.fail(str(args))
    # Authentication belongs to the HTTP entry owner, not this storage test.
    status, body = http.avatar_get(None, urlparse('/api/account/avatar?principal=' + RID), 'fixture-viewer')
    assert status == 200
    assert body['avatar']['version'] == version
    assert http.avatar_get(None, urlparse(body['avatar']['url']), 'fixture-viewer') == (200, data, 'image/png')


def test_cli_accepts_relative_output_path_without_following_symlinks(tmp_path, monkeypatch, capsys):
    import sys
    old, new, config = setup_store(tmp_path)
    record(old, sprite_index=20)
    (tmp_path / 'config.json').write_text(json.dumps(config))
    folder = tmp_path / 'working'
    folder.mkdir()
    monkeypatch.chdir(folder)
    monkeypatch.setattr(sys, 'argv', ['avatar_migration', 'plan', '--config', '../config.json', '--output', '../plan.json'])
    assert migration.main() == 0
    assert json.loads((tmp_path / 'plan.json').read_text())['entries'][0]['action'] == 'copy_missing'
