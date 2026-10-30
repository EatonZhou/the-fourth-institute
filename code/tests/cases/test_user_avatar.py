import base64
import io
import stat
from types import SimpleNamespace
from urllib.parse import urlparse
from unittest.mock import Mock

import pytest
from PIL import Image

from management_portal import user_avatar as avatars


def image_payload(fmt='PNG', size=(64, 48)):
    stream = io.BytesIO()
    Image.new('RGB', size, (40, 80, 120)).save(stream, fmt)
    return {'kind': 'upload', 'data_base64': base64.b64encode(stream.getvalue()).decode()}


@pytest.mark.parametrize('fmt', ['PNG', 'JPEG', 'WEBP'])
def test_user_avatar_validates_and_normalizes_images_in_protected_store(tmp_path, fmt):
    """验证 Menu / Package 的授权来源、身份隔离、交互与生命周期：test_user_avatar_validates_and_normalizes_images_in_protected_store。

    :importance: P1
    :design: decision_b820faf42218, decision_5bc3fdfc7eec
    """
    store = avatars.UserAvatarStore(tmp_path / 'avatars')
    result = store.update('alice', image_payload(fmt, (512, 256)))
    assert result['url'].startswith('/api/account/avatar/image?user=alice&v=')
    content = store.image('alice', result['version'])
    with Image.open(io.BytesIO(content)) as image:
        assert image.format == 'PNG' and image.size == (256, 128) and not image.info
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    for path in store.root.iterdir():
        if path.suffix in {'.json', '.png'}:
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert store.get('bob') == avatars.sprite(0)
    with pytest.raises(FileNotFoundError):
        store.image('bob', result['version'])
    store.update('alice', {'kind': 'sprite', 'sprite_index': 41})
    with pytest.raises(FileNotFoundError):
        store.image('alice', result['version'])


@pytest.mark.parametrize('payload', [
    {'kind': 'sprite', 'sprite_index': True}, {'kind': 'sprite', 'sprite_index': 42},
    {'kind': 'sprite', 'sprite_index': 1, 'username': 'bob'},
    {'kind': 'sprite', 'sprite_index': 1, 'path': '/tmp/other'},
    {'kind': 'upload', 'data_base64': 'not-base64'},
    {'kind': 'upload', 'data_base64': base64.b64encode(b'<svg xmlns="http://www.w3.org/2000/svg"/>').decode()},
    image_payload('GIF'), image_payload(size=(15, 16)), image_payload(size=(2049, 16)),
])
def test_user_avatar_rejects_spoofed_targets_and_invalid_images_without_writes(tmp_path, payload):
    """验证 Menu / Package 的授权来源、身份隔离、交互与生命周期：test_user_avatar_rejects_spoofed_targets_and_invalid_images_without_writes。

    :importance: P1
    :design: decision_b820faf42218, decision_5bc3fdfc7eec
    """
    store = avatars.UserAvatarStore(tmp_path / 'avatars')
    with pytest.raises(ValueError):
        store.update('alice', payload)
    assert not store.root.exists()


def test_user_avatar_rejects_oversize_truncated_images_and_non_user_identity(tmp_path):
    """验证 Menu / Package 的授权来源、身份隔离、交互与生命周期：test_user_avatar_rejects_oversize_truncated_images_and_non_user_identity。

    :importance: P1
    :design: decision_b820faf42218, decision_5bc3fdfc7eec
    """
    store = avatars.UserAvatarStore(tmp_path / 'avatars')
    with pytest.raises(ValueError):
        store.update('alice', {'kind': 'upload', 'data_base64': 'A' * (avatars.MAX_JSON_BYTES + 1)})
    payload = image_payload()
    payload['data_base64'] = base64.b64encode(base64.b64decode(payload['data_base64'])[:32]).decode()
    with pytest.raises(ValueError):
        store.update('alice', payload)
    with pytest.raises(PermissionError):
        store.update('resident_123456789abc', {'kind': 'sprite', 'sprite_index': 1})
    assert not store.root.exists()


def test_user_avatar_delete_removes_only_the_selected_users_records_and_images(tmp_path):
    """删除用户头像只移除该用户的记录和图片，再次删除返回 False"""
    store = avatars.UserAvatarStore(tmp_path / 'avatars')
    store.update('alice', image_payload())
    bob = store.update('bob', image_payload('JPEG'))

    assert store.delete('alice') is True
    assert store.get('alice') == avatars.sprite(0)
    assert not list(store.root.glob(avatars._user('alice') + '-*.png'))
    assert store.image('bob', bob['version'])
    assert store.get('bob') == bob
    assert store.delete('alice') is False


def test_user_avatar_image_read_needs_no_viewer_permission(tmp_path, monkeypatch):
    """读取用户头像图片无需查看者身份或权限，响应可长期缓存"""
    from management_portal import serve_management_portal as server
    from management_portal import web_accounts
    store = avatars.UserAvatarStore(tmp_path / 'avatars')
    monkeypatch.setattr(avatars, 'user_avatar_store', lambda: store)
    principal = {'admin': False}
    monkeypatch.setattr(web_accounts, '_PORTAL_ACCESS', SimpleNamespace(is_active=lambda user: True, has_portal_authority=lambda user: principal['admin']))
    monkeypatch.setattr(server._ACCOUNT_HTTP, 'respond', lambda handler, status, payload: (status, payload))
    monkeypatch.setattr(server._ACCOUNT_HTTP, 'serve_bytes', lambda handler, status, data, mime, **kwargs: (status, data, mime, kwargs))
    address = urlparse(store.update('alice', image_payload())['url'])
    result = server._ACCOUNT_HTTP.avatar_get(Mock(), address, 'alice')
    assert result[0] == 200 and result[2] == 'image/png'
    # The address carries the content hash, so a browser keeps the image.
    assert result[3]['cache_control'] == 'private, max-age=31536000, immutable'
    assert result[3]['extra_headers']['X-Content-Type-Options'] == 'nosniff'
    assert server._ACCOUNT_HTTP.avatar_get(Mock(), address, 'bob')[0] == 200
    # Reading an avatar never asks who is looking.
    assert server._ACCOUNT_HTTP.avatar_get(Mock(), address, '')[0] == 200
    monkeypatch.setattr(web_accounts, '_PORTAL_ACCESS', SimpleNamespace(is_active=lambda user: False))
    assert server._ACCOUNT_HTTP.avatar_get(Mock(), address, 'bob')[0] == 200
    monkeypatch.setattr(web_accounts, '_PORTAL_ACCESS', SimpleNamespace(is_active=lambda user: True))
    principal['admin'] = True
    assert server._ACCOUNT_HTTP.avatar_get(Mock(), address, 'bob')[0] == 200


def test_message_avatar_metadata_exposes_only_avatar(tmp_path, monkeypatch):
    """按 principal 查询用户头像只返回头像，查自己的头像设置需登录，参数非法返回 400"""
    from management_portal import serve_management_portal as server
    from management_portal import web_accounts
    store = avatars.UserAvatarStore(tmp_path / 'avatars')
    monkeypatch.setattr(avatars, 'user_avatar_store', lambda: store)
    monkeypatch.setattr(web_accounts, '_PORTAL_ACCESS', SimpleNamespace(is_active=lambda user: True))
    monkeypatch.setattr(server._ACCOUNT_HTTP, 'respond', lambda handler, status, payload: (status, payload))
    store.update('alice', {'kind': 'sprite', 'sprite_index': 3})
    address = urlparse('/api/account/avatar?principal=alice')
    status, payload = server._ACCOUNT_HTTP.avatar_get(Mock(), address, 'bob')
    assert status == 200 and payload == {'avatar': avatars.sprite(3)}
    assert server._ACCOUNT_HTTP.avatar_get(Mock(), address, '') == (200, {'avatar': avatars.sprite(3)})
    monkeypatch.setattr(web_accounts, '_PORTAL_ACCESS', SimpleNamespace(is_active=lambda user: False))
    assert server._ACCOUNT_HTTP.avatar_get(Mock(), address, 'bob')[0] == 200
    # Only one's own avatar settings need to know who is asking.
    assert server._ACCOUNT_HTTP.avatar_get(Mock(), urlparse('/api/account/avatar'), '')[0] == 403
    for query in ('principal=alice&principal=bob', 'principal=alice&other=1', 'principal=', 'principal=portal'):
        assert server._ACCOUNT_HTTP.avatar_get(Mock(), urlparse('/api/account/avatar?' + query), 'bob')[0] == 400


def test_resident_message_avatar_is_shared_without_node_read_permission(monkeypatch):
    """无需节点读取权限即可获取 Resident 头像，没有头像时返回空头像"""
    from management_portal import serve_management_portal as server, resources, resident_avatar
    from management_portal import web_accounts
    monkeypatch.setattr(web_accounts, '_PORTAL_ACCESS', SimpleNamespace(is_active=lambda user: True))
    monkeypatch.setattr(server._ACCOUNT_HTTP, 'respond', lambda handler, status, payload: (status, payload))
    service = Mock()
    service.get.side_effect = PermissionError('denied')
    monkeypatch.setattr(resources, 'get_resource_service', lambda: service)
    monkeypatch.setattr(resident_avatar, 'get_existing_avatar', lambda identity: {'sprite_index': 4})
    address = urlparse('/api/account/avatar?principal=resident_111111111111')
    assert server._ACCOUNT_HTTP.avatar_get(Mock(), address, 'bob') == (200, {'avatar': {'sprite_index': 4}})
    service.get.assert_not_called()
    assert server._ACCOUNT_HTTP.avatar_get(Mock(), address, '') == (200, {'avatar': {'sprite_index': 4}})
    monkeypatch.setattr(resident_avatar, 'get_existing_avatar', lambda identity: None)
    assert server._ACCOUNT_HTTP.avatar_get(Mock(), address, 'bob') == (200, {'avatar': None})
