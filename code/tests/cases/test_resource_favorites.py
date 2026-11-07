"""Resource bookmarks share storage and always recheck access at read/open."""
from unittest.mock import Mock

import pytest

from management_portal import presentation_store, resource_favorites as favorites

A = 'decision_0123456789ab'
B = 'workspace_1123456789ab/file/notes.md'
P = 'paper_2123456789ab'


def test_persistence_isolation_dedup_and_existing_paper_bookmarks():
    reference = Mock(side_effect=lambda address, actor: {'title': 'Readable', 'url': '/r/' + address})
    for target in (A, B, A, P):
        favorites.set_favorite('alice', target, True, reference)
    favorites.set_favorite('bob', B, True, reference)
    assert favorites.addresses('alice') == [A, B, P]
    assert favorites.addresses('bob') == [B]
    assert presentation_store.favorite_ids('alice', 'paper') == [P]
    assert len(favorites.list_favorites('alice', reference)['items']) == 3
    favorites.set_favorite('alice', P, False, reference)
    assert presentation_store.favorite_ids('alice', 'paper') == []
    presentation_store.set_favorite('alice', 'workbench', 'workbench_3123456789ab', True)
    assert 'workbench_3123456789ab' in favorites.addresses('alice')
    favorites.set_favorite('alice', 'workbench_3123456789ab', False, reference)
    assert presentation_store.favorite_ids('alice', 'workbench') == []


def test_revalidation_does_not_leak_old_metadata_and_removal_needs_no_access():
    reference = Mock(return_value={'title': 'Private title'})
    favorites.set_favorite('alice', A, True, reference)
    reference.side_effect = PermissionError('denied')
    assert favorites.list_favorites('alice', reference)['items'] == [{'address': A, 'status': 'denied'}]
    with pytest.raises(PermissionError):
        favorites.set_favorite('alice', B, True, reference)
    reference.side_effect = AssertionError('removal must not read a resource')
    favorites.set_favorite('alice', A, False, reference)
    assert favorites.addresses('alice') == []
    with pytest.raises(ValueError):
        favorites.set_favorite('alice', A, 'true', reference)
    with pytest.raises(PermissionError):
        favorites.set_favorite('', A, True, reference)


def test_http_actor_is_from_authentication(monkeypatch):
    import json
    import urllib.request
    from management_portal import http_auth, web_resources
    from tests.cases.test_resource_portal_routes import _server
    monkeypatch.setattr(http_auth, '_get_session_username', lambda request: 'alice')
    service = Mock()
    service.reference.side_effect = lambda address, actor: {'address': address, 'title': actor, 'url': '/r/' + address}
    monkeypatch.setattr(web_resources, 'get_resource_service', lambda: service)
    server, thread, origin = _server()
    try:
        request = urllib.request.Request(origin + '/api/resources/favorites',
            data=json.dumps({'address': A, 'favorite': True, 'actor': 'bob'}).encode(), headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request) as response:
            assert json.load(response)['favorite'] is True
        with urllib.request.urlopen(origin + '/api/resources/favorites') as response:
            assert json.load(response)['items'][0]['title'] == 'alice'
        assert favorites.addresses('bob') == []
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)
