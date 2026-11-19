"""Notice search results and legacy resource links open the existing Home detail."""
from __future__ import annotations

import http.client
import threading
from http.server import ThreadingHTTPServer
from unittest import mock

import pytest

from management_portal import http_auth, portal_features, serve_management_portal, web_resources
from management_portal.resource_index_records import resource_record
from management_portal.resources import portal


NOTICE = "notice_0123456789ab"
DESTINATION = f"/?notice_post={NOTICE}"


@pytest.fixture(autouse=True)
def fresh_routes():
    portal.portal_routes.cache_clear()
    portal_features.contributions.cache_clear()
    yield
    portal.portal_routes.cache_clear()
    portal_features.contributions.cache_clear()


def test_notice_index_and_browser_contract_use_home_detail():
    assert portal.portal_url_for_address(NOTICE) == DESTINATION
    record = resource_record(resource_id=NOTICE, resource_type="notice",
                             title="Notice", adapter="fixture", url=f"/r/{NOTICE}")
    assert record["url"] == DESTINATION
    route = next(item for item in portal.portal_route_contract()["routes"]
                 if item["resource_type"] == "notice")
    assert (route["path"], route["parameter"]) == ("/", "notice_post")
    assert "notice_post" in portal.portal_route_contract()["identity_parameters"]
    assert portal.portal_url_for_address(NOTICE, "article") == ""


def test_package_without_notice_board_has_no_notice_destination(monkeypatch):
    monkeypatch.setattr(portal_features, "FEATURES", tuple(
        feature for feature in portal_features.FEATURES if feature.key != "notice_board"))
    assert portal.portal_url_for_address(NOTICE) == ""
    assert not any(item["resource_type"] == "notice"
                   for item in portal.portal_route_contract()["routes"])


@pytest.fixture
def http_server():
    class QuietHandler(serve_management_portal.Handler):
        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize("path", [f"/r/{NOTICE}", f"/resource?address={NOTICE}"])
def test_legacy_notice_link_redirects_without_index_rebuild(http_server, path):
    service = mock.Mock()
    service.reference.side_effect = KeyError(NOTICE)
    with (mock.patch.object(http_auth, "_check_session", return_value=True),
          mock.patch.object(http_auth, "_get_session_username", return_value="CASE-NOTICE"),
          mock.patch.object(http_auth, "_get_page_api_actor", return_value="CASE-NOTICE"),
          mock.patch.object(web_resources, "get_resource_service", return_value=service)):
        client = http.client.HTTPConnection(*http_server, timeout=5)
        try:
            client.request("GET", path)
            response = client.getresponse()
            body = response.read()
            assert response.status == 302, body
            assert response.getheader("Location") == DESTINATION
        finally:
            client.close()
    service.reference.assert_called_once_with(NOTICE, actor="CASE-NOTICE")


def test_notice_link_keeps_session_gate(http_server):
    with (mock.patch.object(http_auth, "_get_page_api_actor", return_value=None),
          mock.patch.object(http_auth, "_check_session", return_value=False)):
        client = http.client.HTTPConnection(*http_server, timeout=5)
        try:
            client.request("GET", f"/r/{NOTICE}")
            response = client.getresponse()
            response.read()
            assert response.status == 302
            assert response.getheader("Location").startswith("/login")
        finally:
            client.close()


def test_notice_content_keeps_owner_read_authorization():
    from management_portal.notice_board_backend import notice_board
    from management_portal.resources.providers.notice import install_resolver

    service = mock.Mock()
    install_resolver(service)
    kind, resolver = service.register_resolver.call_args.args
    assert kind == "notice"
    with mock.patch.object(notice_board, "get_post", side_effect=PermissionError("denied")) as read:
        with pytest.raises(PermissionError, match="denied"):
            resolver(NOTICE, actor="CASE-DENIED")
        read.assert_called_once_with("CASE-DENIED", NOTICE)
