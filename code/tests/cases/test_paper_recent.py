import json
from types import SimpleNamespace
import urllib.error
import urllib.request
from unittest import mock

import pytest

from management_portal import http_auth, http_paper, paper_recent, presentation_store
from tests.cases.test_resource_portal_routes import _server

ONE, TWO, THREE = [f"paper_{index:012x}" for index in range(1, 4)]


@pytest.fixture
def owner(monkeypatch):
    get = mock.Mock(return_value={"resource_id": ONE})
    monkeypatch.setattr(paper_recent, "get_resource_service", lambda: SimpleNamespace(get=get))
    monkeypatch.setattr(paper_recent, "is_deleted", lambda identifier: False)
    calls = []
    def batch(ids, *, actor):
        calls.append((list(ids), actor))
        return [{"id": identifier, "title": identifier} for identifier in ids]
    monkeypatch.setattr(paper_recent, "resource_index_papers_by_ids", batch)
    return get, calls


def test_reopen_deduplicates_orders_and_isolates_actor(owner):
    get, calls = owner
    with mock.patch("management_portal.paper_store._all_papers", side_effect=AssertionError("scan")), \
         mock.patch("management_portal.paper_search.resource_index_papers", side_effect=AssertionError("refresh")):
        first = paper_recent.record_open({"paper_id": ONE, "expected_principal": "alice"}, actor="alice")
        paper_recent.record_open({"paper_id": TWO, "expected_principal": "alice"}, actor="alice")
        again = paper_recent.record_open({"paper_id": ONE, "expected_principal": "alice"}, actor="alice")
        paper_recent.record_open({"paper_id": THREE, "expected_principal": "bob"}, actor="bob")
        assert again["last_read_at"] >= first["last_read_at"]
        page = paper_recent.list_recent({"limit": 1}, actor="alice")
        assert [p["id"] for p in page["papers"]] == [ONE]
        next_page = paper_recent.list_recent({"limit": 1, "cursor": page["next_cursor"]}, actor="alice")
        assert [p["id"] for p in next_page["papers"]] == [TWO]
        assert next_page["next_cursor"] == ""
        assert [p["id"] for p in paper_recent.list_recent({}, actor="bob")["papers"]] == [THREE]
        assert all(len(ids) <= 2 for ids, actor in calls[:2])
        get.assert_any_call(ONE, actor="alice")


def test_unreadable_deleted_favorites_and_tied_order(owner, monkeypatch):
    for identifier in (ONE, TWO, THREE):
        paper_recent.record_open({"paper_id": identifier, "expected_principal": "alice"}, actor="alice")
    with presentation_store.database().transaction() as connection:
        connection.execute("UPDATE associations SET updated_at='2026-01-01T00:00:00Z' WHERE actor=%s AND namespace=%s",
                           ("alice", paper_recent.NAMESPACE))
    assert [p["id"] for p in paper_recent.list_recent({}, actor="alice")["papers"]] == [THREE, TWO, ONE]
    presentation_store.set_favorite("alice", "paper", ONE, True)
    rows = paper_recent.list_recent({}, actor="alice")["papers"]
    assert rows[-1]["favorite"] is True
    presentation_store.set_favorite("alice", "paper", ONE, False)
    assert len(paper_recent.list_recent({}, actor="alice")["papers"]) == 3
    monkeypatch.setattr(paper_recent, "is_deleted", lambda identifier: identifier == THREE)
    monkeypatch.setattr(paper_recent, "resource_index_papers_by_ids",
                        lambda ids, actor: [{"id": i} for i in ids if i != TWO])
    result = paper_recent.list_recent({"limit": 1}, actor="alice")
    assert [p["id"] for p in result["papers"]] == [ONE]
    assert result["next_cursor"] == ""
    with pytest.raises(KeyError):
        paper_recent.record_open({"paper_id": THREE, "expected_principal": "alice"}, actor="alice")
    owner[0].side_effect = PermissionError("unavailable")
    with pytest.raises(PermissionError):
        paper_recent.record_open({"paper_id": TWO, "expected_principal": "alice"}, actor="alice")


def test_bad_parameters_do_not_write(owner):
    for payload in ({"paper_id": ONE, "actor": "bob"}, {}, {"paper_id": "bad"}):
        with pytest.raises(ValueError):
            paper_recent.record_open(payload, actor="alice")
    for params in ({"actor": "bob"}, {"limit": 0}, {"cursor": "bad"}):
        with pytest.raises(ValueError):
            paper_recent.list_recent(params, actor="alice")
    with pytest.raises(PermissionError):
        paper_recent.record_open({"paper_id": ONE, "expected_principal": "alice"}, actor="")
    assert paper_recent.list_recent({}, actor="alice")["papers"] == []
    owner[0].assert_not_called()


def test_expected_principal_is_only_a_precondition_not_authority(owner):
    with mock.patch.object(presentation_store, "database", side_effect=AssertionError("must not write")):
        with pytest.raises(paper_recent.PrincipalChanged):
            paper_recent.record_open({"paper_id": ONE, "expected_principal": "alice"}, actor="bob")
        with pytest.raises(ValueError):
            paper_recent.record_open({"paper_id": ONE}, actor="bob")
    owner[0].assert_not_called()
    # A forged match can only act as the already authenticated Bob, never Alice.
    paper_recent.record_open({"paper_id": ONE, "expected_principal": "bob"}, actor="bob")
    owner[0].assert_called_once_with(ONE, actor="bob")
    assert paper_recent.list_recent({}, actor="alice")["papers"] == []
    assert len(paper_recent.list_recent({}, actor="bob")["papers"]) == 1
    owner[0].side_effect = PermissionError("denied")
    with pytest.raises(PermissionError):
        paper_recent.record_open({"paper_id": TWO, "expected_principal": "bob"}, actor="bob")
    assert len(paper_recent.list_recent({}, actor="bob")["papers"]) == 1


def test_http_authentication_and_explicit_open_only(owner, monkeypatch):
    auth = {"username": "sample_admin"}
    monkeypatch.setattr(http_auth, "_get_agent_api_auth", lambda request: auth)
    monkeypatch.setattr(http_paper, "authorize_api", lambda request, a, **kwargs: a["username"])
    server, thread, origin = _server()
    def request(path, payload=None):
        return urllib.request.urlopen(urllib.request.Request(origin + "/paper/api/" + path,
            data=None if payload is None else json.dumps(payload).encode(), headers={"Content-Type": "application/json"}))
    try:
        with request("recent-reading") as response:
            assert json.load(response)["papers"] == []
        owner[0].assert_not_called()
        with request("recent-reading/open", {"paper_id": ONE, "expected_principal": "sample_admin"}) as response:
            assert json.load(response)["paper_id"] == ONE
        owner[0].assert_called_once_with(ONE, actor="sample_admin")
        auth["username"] = "bob"
        with pytest.raises(urllib.error.HTTPError) as changed:
            request("recent-reading/open", {"paper_id": TWO, "expected_principal": "sample_admin"})
        assert changed.value.code == 409
        assert json.load(changed.value)["code"] == "session_changed"
        assert paper_recent.list_recent({}, actor="bob")["papers"] == []
        assert len(paper_recent.list_recent({}, actor="sample_admin")["papers"]) == 1
        # Credential/session replacement under the same principal is allowed.
        auth.clear(); auth.update(username="sample_admin", session="new-session")
        with request("recent-reading/open", {"paper_id": ONE, "expected_principal": "sample_admin"}) as response:
            assert response.status == 200
        for path, payload in (("recent-reading?actor=bob", None), ("recent-reading/open", {"paper_id": ONE, "actor": "bob"})):
            with pytest.raises(urllib.error.HTTPError) as invalid:
                request(path, payload)
            assert invalid.value.code == 400
        auth.clear()
        with pytest.raises(urllib.error.HTTPError) as missing:
            request("recent-reading")
        assert missing.value.code == 401
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_real_signed_cookie_switch_rejects_old_page_without_writes(owner, monkeypatch, tmp_path):
    from management_portal import web_accounts
    from management_portal.portal_session_auth import PortalSessionAuthority
    key = tmp_path / "fixture-session-key"
    key.write_bytes(b"fixture-only-session-signing-key-32-bytes")
    access = SimpleNamespace(is_active=lambda actor: True, is_configured=lambda actor: True,
                             session_version=lambda actor: 0)
    authority = PortalSessionAuthority(key, access_store=access)
    monkeypatch.setattr(web_accounts, "_session_authority", lambda: authority)
    monkeypatch.setattr(web_accounts, "_portal_user_allowed", lambda actor: actor in {"alice", "bob"})
    alice, bob, alice_new = [authority.issue(actor, max_age=600) for actor in ("alice", "bob", "alice")]
    server, thread, origin = _server()
    def post(token, expected):
        return urllib.request.urlopen(urllib.request.Request(origin + "/paper/api/recent-reading/open",
            data=json.dumps({"paper_id": ONE, "expected_principal": expected}).encode(),
            headers={"Content-Type": "application/json", "Cookie": authority.cookie_name + "=" + token}))
    try:
        with post(alice, "alice") as response:
            assert response.status == 200
        with presentation_store.database().read() as connection:
            before = connection.execute("SELECT actor,association_key,updated_at FROM associations WHERE namespace=%s ORDER BY actor,association_key", (paper_recent.NAMESPACE,)).fetchall()
        with pytest.raises(urllib.error.HTTPError) as mismatch:
            post(bob, "alice")
        assert mismatch.value.code == 409
        with presentation_store.database().read() as connection:
            assert connection.execute("SELECT actor,association_key,updated_at FROM associations WHERE namespace=%s ORDER BY actor,association_key", (paper_recent.NAMESPACE,)).fetchall() == before
        owner[0].assert_called_once_with(ONE, actor="alice")
        with post(alice_new, "alice") as response:
            assert response.status == 200
        with post(bob, "bob") as response:
            assert response.status == 200
        owner[0].assert_called_with(ONE, actor="bob")
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)
