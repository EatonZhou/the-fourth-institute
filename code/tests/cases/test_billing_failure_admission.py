"""Billing failures must not become inference failures or implicit zero balances."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from management_portal.execution import account_exchange_rates, provider_balance_adapters
from management_portal.execution.account_monitor import AccountMonitor
from management_portal.execution.account_resources import with_account_resources
from tests.cases.test_account_close_threshold import scheduler, submit, claim, state


@pytest.mark.parametrize("http_status,body,reason", [
    (404, "not found", "unsupported_endpoint"),
    (401, "private upstream diagnostic", "unauthorized"),
    (503, "private upstream diagnostic", "unavailable"),
    (200, "<html>private upstream diagnostic</html>", "invalid_response"),
])
def test_failed_billing_http_keeps_healthy_api_dispatchable(scheduler, monkeypatch,
                                                         http_status, body, reason):
    """真实本地HTTP→余额观察→账号投影→PG调度，查询失败不阻塞默认阈值的聊天。"""
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            payload = body.encode()
            self.send_response(http_status)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    entry = {"id": "billing_cny", "provider": "deepseek", "enabled": True,
             "base_url": f"http://127.0.0.1:{server.server_port}/v1"}
    registry = provider_balance_adapters.shared_ai_provider_registry
    monkeypatch.setattr(provider_balance_adapters, "entries", lambda: [entry])
    monkeypatch.setattr(registry, "secret_path", lambda item: "fixture")
    monkeypatch.setattr(registry, "read_secret", lambda path: "fixture-key")
    monkeypatch.setattr(account_exchange_rates, "collect", lambda *args: {})
    try:
        monitor = AccountMonitor(scheduler.accounts, scheduler)
        account = next(a for a in scheduler.accounts.accounts() if a.account_id == "cny")
        monitor.refresh_account(account)
        sample = monitor._samples[account.account_id]
        projected = with_account_resources([sample], {})[0]
        assert requests == ["/v1/user/balance"]
        assert projected["status"] == "ok"
        assert projected["balance"]["reason"] == reason
        assert projected["balance"]["amount"] is None
        assert "private upstream diagnostic" not in json.dumps(projected)
        assert "fixture-key" not in json.dumps(projected)
        assert not state(scheduler, "cny")["automatic_paused"]
        request = submit(scheduler, "cny")
        assert claim(scheduler)["execution"]["execution_id"] == request.execution_id
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
