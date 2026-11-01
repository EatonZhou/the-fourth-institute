"""Upload consumption is an accepted write, never validation or a read."""
from datetime import datetime, timezone

import pytest

from management_common.directory_retention import RetentionStore
from management_common.upload_retention import created
from management_portal.resources import isa
from tests.cases.test_resident_conversation_deleted_contract import active_repository
from tests.cases.resident_execution_test_support import resident_record, input_delivery


@pytest.fixture
def upload(tmp_path, monkeypatch):
    monkeypatch.setattr(isa, 'upload_root', lambda: tmp_path)
    identifier = 'upload_' + 'a' * 24
    directory = tmp_path / identifier
    directory.mkdir()
    created(directory, upload_id=identifier, created_at=datetime.now(timezone.utc))
    return identifier, directory


def artifact(path):
    from management_common.directory_paths import cleanup_location
    store = RetentionStore()
    with store.database.read() as connection:
        return dict(connection.execute(f'SELECT * FROM {store.table} WHERE location=%s',
                                       (cleanup_location(path),)).fetchone())


def test_delete_user_releases_owned_upload_in_identity_transaction(upload):
    from management_portal.portal_access_control import PortalAccessStore
    from management_common.upload_retention import consumed
    users = PortalAccessStore()
    users.upsert_user('upload-owner', actor='sample_admin')
    _identifier, directory = upload
    consumed(directory, owner_id='upload-owner')
    users.delete_user('upload-owner', actor='sample_admin')
    queue = RetentionStore().queue
    assert queue.status(owner_id='upload-owner')
    queue.run_pending()
    assert not directory.exists()


def test_consumption_cannot_silently_accept_an_unmigrated_upload(tmp_path):
    from management_common.upload_retention import consumed
    directory = tmp_path / 'unmigrated'
    directory.mkdir()
    with pytest.raises(ValueError, match='migration'):
        consumed(directory, owner_id='resident_111111111111')


def message(upload_id, message_id='message_111111111111'):
    return {'message_id': message_id, 'role': 'user', 'author_id': 'alice',
            'created_at': '2026-11-02T00:00:00Z',
            'content_parts': [{'type': 'text', 'text': 'look'},
                              {'type': 'image', 'upload_id': upload_id}]}


@pytest.mark.parametrize('method', ['accept_user_message', 'commit_user_message'])
def test_accepted_upload_transaction_first_owner_and_delete(active_repository, upload, method):
    repo = active_repository
    identifier, directory = upload
    first, second = 'resident_111111111111', 'resident_222222222222'
    accept = getattr(repo, method)
    with pytest.raises(KeyError):
        accept('resident_333333333333', message(identifier))
    assert artifact(directory)['category'] == 'unused_upload'
    accept(first, message(identifier))
    assert artifact(directory)['owner_id'] == first
    assert artifact(directory)['eligible_at'] is None
    accept(second, message(identifier))
    assert artifact(directory)['owner_id'] == first
    with pytest.raises(ValueError):
        accept(first, message('upload_' + 'b' * 24))
    store = RetentionStore()
    with pytest.raises(RuntimeError), store.database.transaction() as connection:
        store.release_owner(first, connection=connection)
        raise RuntimeError('abort object deletion')
    assert artifact(directory)['owner_id'] == first
    store.release_owner(first)
    store.queue.run_pending()
    assert not directory.exists()


def test_avatar_consumes_only_after_valid_save(upload):
    from management_portal.avatar_database import DatabaseAvatarStore
    from management_portal.avatar_store import ScopeAvatarSubjects, change_avatar
    from PIL import Image
    import json
    identifier, directory = upload
    image = directory / 'avatar.png'
    image.write_bytes(b'invalid image')
    (directory / 'meta.json').write_text(json.dumps({'upload_id': identifier, 'purpose': 'avatar',
        'ready': True, 'created_by': 'alice', 'file_name': image.name, 'path': str(image)}))
    store = DatabaseAvatarStore(ScopeAvatarSubjects(), 'scope')
    subject = 'lineage:lineage_111111111111'
    with pytest.raises(ValueError):
        change_avatar(store, subject, {'upload_id': identifier}, 'alice')
    assert artifact(directory)['category'] == 'unused_upload'
    Image.new('RGB', (32, 32), 'red').save(image)
    change_avatar(store, subject, {'upload_id': identifier}, 'alice')
    assert artifact(directory)['owner_id'] == 'lineage_111111111111'


@pytest.mark.usefixtures('registered_execution_defaults')
@pytest.mark.parametrize('route', ['bootstrap', 'queue', 'facade'])
def test_resident_intake_validates_before_consuming(input_delivery, upload, monkeypatch, route):
    import json
    from management_portal.society_service import resident_intake_domain as intake
    from management_portal.society_service import resident_backend, family_facade, family_recovery
    from management_portal.society_service import resident_conversation_stream
    from tests.cases.resident_execution_test_support import VALID_PNG_BYTES
    identifier, directory = upload
    image = directory / 'image.png'
    image.write_bytes(VALID_PNG_BYTES)
    meta = {'upload_id': identifier, 'purpose': 'conversation_attachment', 'ready': False,
            'created_by': 'CASE-A', 'file_name': image.name, 'media_type': 'image/png'}
    (directory / 'meta.json').write_text(json.dumps(meta))
    rid = input_delivery['resident_id']
    if route == 'bootstrap':
        monkeypatch.setattr(resident_backend, 'record_session_id', lambda record: '')
    elif route == 'queue':
        intake.deliver_resident_message_content(rid, 'CASE-A', 'first input', message_id='msg_3333333333333333')
    elif route == 'facade':
        monkeypatch.setattr(family_facade, 'is_facade', lambda record: True)
        monkeypatch.setattr(family_recovery, 'register', lambda *a, **k: None)
        monkeypatch.setattr(family_recovery, 'handle', lambda *a, **k: None)
        monkeypatch.setattr(family_facade, 'show_request', lambda *a, **k: None)
    media = [{'media_type': 'image', 'upload_id': identifier}]
    def send(message_id):
        return intake.deliver_resident_message_content(rid, 'CASE-A', 'look', message_id=message_id,
                                                       input_media=media)
    with pytest.raises(ValueError, match='上传完成'):
        send('msg_4444444444444444')
    assert artifact(directory)['category'] == 'unused_upload'
    meta['ready'] = True
    (directory / 'meta.json').write_text(json.dumps(meta))
    send('msg_5555555555555555')
    assert artifact(directory)['owner_id'] == rid
    assert resident_conversation_stream.get_repository().messages.stored_message(rid, 'msg_5555555555555555')
