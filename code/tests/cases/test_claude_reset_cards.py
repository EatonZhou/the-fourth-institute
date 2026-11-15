from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock
import json

import pytest

from management_portal.execution import claude_reset_cards as module

NOW = datetime(2026, 11, 15, tzinfo=timezone.utc)


def grant(**changes):
    return {"id": "g1", "label": "Weekly reset", "resets_left": 2,
            "starts_at": "2026-11-01T00:00:00Z", "ends_at": "2026-11-20T00:00:00Z",
            "type": "weekly", "paused": False, "usable_now": False,
            "use_requires_limit": True, **changes}


@pytest.mark.parametrize("payload", [{}, {"five_hour": {}}, {"cedar_ember": None},
    {"cedar_ember": ""}, {"cedar_ember": {}}, {"cedar_ember": {"eligible": True}},
    {"cedar_ember": {"eligible": False, "grants": []}}])
def test_successful_blank_is_observed_zero(payload):
    result = module.parse_cards(payload, now=NOW)
    assert result["status"] == "observed" and result["available_count"] == 0
    assert result["cards"] == [] and not result["stale"]


def test_grants_count_available_resets_and_preserve_expiry():
    rows = [grant(), grant(id="used", resets_left=0), grant(id="expired", ends_at="2026-11-14T00:00:00Z"),
            grant(id="future", starts_at="2026-11-16T00:00:00Z"), grant(id="paused", paused=True)]
    result = module.parse_cards({"cedar_ember": {"eligible": True, "grants": rows}}, now=NOW)
    assert result["available_count"] == 2  # usable_now=false only requires being at the limit
    assert [r["status"] for r in result["cards"]] == ["available", "used", "expired", "pending", "paused"]
    assert result["cards"][0]["expiresAt"] == "2026-11-20T00:00:00+00:00"


@pytest.mark.parametrize("payload", [None, [], {"error": "private"}, {"type": "error"},
    {"cedar_ember": []}, {"cedar_ember": {"eligible": True, "grants": [grant(resets_left=True)]}},
    {"cedar_ember": {"eligible": True, "grants": [grant(ends_at="2026-11-20")]}},
    {"cedar_ember": {"eligible": True, "grants": [grant(), grant()]}}])
def test_failed_or_malformed_data_is_not_zero(payload):
    with pytest.raises(ValueError):
        module.parse_cards(payload, now=NOW)


def snapshot(at="2026-11-15T00:00:00Z", **changes):
    return {"quota_status": "available", "rate_limits": [{"id": "seven_day",
        "source": "claude_cli_usage", "observed_at": at}], **changes}


def test_one_read_per_native_revision_no_second_timer_and_failures_keep_previous(monkeypatch):
    reader = Mock(return_value=module.parse_cards({"cedar_ember": {"eligible": True, "grants": [grant()]}}, now=NOW))
    monkeypatch.setattr(module, "read_cards", reader)
    cache = module.ClaudeResetCards()
    assert cache.sample("/one", snapshot(quota_refreshing=True))["available_count"] is None
    assert reader.call_count == 0
    assert cache.sample("/one", snapshot())["available_count"] == 2
    cache.sample("/one", snapshot())
    assert reader.call_count == 1
    reader.side_effect = OSError("private bearer response")
    failed = cache.sample("/one", snapshot("2026-11-15T00:10:00Z"))
    assert failed["available_count"] == 2 and failed["stale"]
    assert "private" not in json.dumps(failed)
    assert cache.sample("/two", snapshot())["available_count"] is None
    reader.side_effect = None
    reader.return_value = module.parse_cards({}, now=NOW)
    assert cache.sample("/one", snapshot("2026-11-15T00:20:00Z"))["available_count"] == 0
    assert not cache.sample("/one", snapshot("2026-11-15T00:20:00Z"))["stale"]
    no_cli = snapshot()
    no_cli["rate_limits"][0]["source"] = "claude_sdk_events"
    calls = reader.call_count
    cache.sample("/one", no_cli)
    assert reader.call_count == calls


def test_http_is_get_uses_registered_native_credential_and_never_follows_redirects(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from io import BytesIO
    (tmp_path / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "fake-test-bearer"}}))
    calls = []
    @contextmanager
    def open_request(request, **options):
        calls.append((request, options))
        yield BytesIO(b'{"cedar_ember":null}')
    monkeypatch.setattr(module, "urlopen", open_request)
    assert module.read_cards(tmp_path)["available_count"] == 0
    request, options = calls[0]
    assert request.get_method() == "GET" and request.full_url == module.ENDPOINT
    assert request.get_header("Authorization") == "Bearer fake-test-bearer"
    assert options["follow_redirects"] is False


def test_claude_adapter_exports_cards_to_home_and_history(monkeypatch):
    from management_portal.execution.account_adapters import ClaudeAccounts
    from management_portal.execution.account_observation_store import evidence
    from management_portal import claude_code_client
    adapter = ClaudeAccounts.__new__(ClaudeAccounts)
    adapter.entries = {"one": {"config": {"account_dir": "/one"}}}
    adapter.reset_cards = module.ClaudeResetCards()
    monkeypatch.setattr(module, "read_cards", lambda _: module.parse_cards({}, now=NOW))
    client = Mock()
    client.account_usage.return_value = snapshot()
    monkeypatch.setattr(claude_code_client, "shared_client", lambda: client)
    account = SimpleNamespace(account_id="one", route_id="one", quota_policy="reserve")
    result = adapter.observe(account, passive=True)
    assert result["refresh_cards"]["available_count"] == 0
    assert evidence(result)["refresh_cards"]["available_count"] == 0


def test_multi_use_grant_consumption_classifies_once_and_excludes_expiry():
    from scripts.provider_usage_resets import classify_resets
    from scripts.provider_weekly_usage import moment
    from tests.cases.test_provider_usage_resets import row
    rows = [row(1, 20), row(2, 100)]
    for i, r in enumerate(rows):
        r["provider"] = "claude"
        r["sample"]["rate_limits"][0].update(id="seven_day", quota_group="claude")
        r["sample"]["refresh_cards"].update(available_count=2-i,
            cards=[{"id": "g1", "status": "available", "remaining_count": 2-i,
                    "expiresAt": "2026-11-20T00:00:00Z"}])
    assert classify_resets(rows, "claude", "a", moment)[0]["reset_cause"] == "manual_card"
    rows[0]["sample"]["refresh_cards"]["cards"][0]["expiresAt"] = "2026-11-01T01:30:00Z"
    assert classify_resets(rows, "claude", "a", moment)[0]["reset_cause"] == "unknown"


def test_failed_card_read_after_restart_preserves_logged_value():
    from management_portal.execution.account_observation_store import evidence, merge_windows
    previous = {"refresh_cards": module.parse_cards({"cedar_ember": {"eligible": True, "grants": [grant()]}}, now=NOW)}
    current = merge_windows({"refresh_cards": {"status": "unavailable", "available_count": None}}, previous, now=NOW.timestamp())
    assert current["refresh_cards"]["available_count"] == 2 and current["refresh_cards"]["stale"]
    assert evidence(current)["refresh_cards"]["cards"][0]["remaining_count"] == 2


def test_redirect_is_refused_in_cancellable_http_observation():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from urllib.error import HTTPError
    from urllib.request import Request
    from management_portal.execution.account_observation import ObservationLifetime, urlopen
    reached = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            reached.append(self.path)
            self.send_response(302 if self.path == "/start" else 200)
            self.send_header("Location", "/unexpected")
            self.end_headers()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with ObservationLifetime().scope():
            with pytest.raises(HTTPError) as caught:
                with urlopen(Request(f"http://127.0.0.1:{server.server_port}/start",
                        headers={"Authorization": "Bearer fake"}), timeout=5, follow_redirects=False):
                    pass
        assert caught.value.code == 302 and reached == ["/start"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
