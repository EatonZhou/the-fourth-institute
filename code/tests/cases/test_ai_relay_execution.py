"""Relay TCP -> Execution RPC -> Worker -> real local HTTP upstream.

:importance: P1
"""
from tests.cases.execution_test_support import run_worker_once

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from management_portal import ai_socket_relay as relay
from management_portal.execution.runtime import ExecutionRuntime
from management_portal.execution.worker import ExecutionWorker
from tests.cases.model_api_test_support import register_apis, register_relay_resources
from tests.cases.test_execution_layer import _stack, _close, SchedulerClient


def test_relay_executes_each_request_with_its_own_model_and_messages(tmp_path, monkeypatch):
    """AI 中继同一连接上的每个请求经执行层按各自的模型、消息和参数发往上游

    :importance: P2"""
    received = []

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(payload)
            body = json.dumps({"choices": [{"message": {"role": "assistant", "content": payload["model"]}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    key = tmp_path / "test.key"
    key.write_text("synthetic-local-test-key")
    register_apis({
        "id": "relay_test_primary", "kind": "openai_compatible_chat", "enabled": True,
        "provider": "relay_test", "base_url": f"http://127.0.0.1:{upstream.server_port}/v1",
        "api_key_file": str(key), "default_model": "first", "timeout_s": 5, "max_retries": 0,
    })
    register_relay_resources(*({"id": name, "provider": "relay_test", "model": name} for name in ("first", "second")))
    scheduler, server, service_thread, client, service = _stack(tmp_path)
    monkeypatch.setenv("PORTAL_EXECUTION_SERVICE_SOCKET", str(tmp_path / "execution-service.sock"))
    runtime = ExecutionRuntime(state_root=tmp_path / "execution-state")
    worker = ExecutionWorker(build_id="test-build", worker_id="relay-worker", runtime=runtime,
        scheduler=SchedulerClient(worker_id="relay-worker", build_id="test-build",
            capabilities=runtime.capabilities(), pid=1234, service_client=client))
    worker.register()
    stop = threading.Event()

    def work():
        while not stop.is_set():
            run_worker_once(worker)

    worker_thread = threading.Thread(target=work, daemon=True)
    worker_thread.start()
    tcp = relay.RelayTCPServer(("127.0.0.1", 0), relay.RelayHandler,
        allowed_networks=relay._parse_allowed_networks("127.0.0.1"),
        max_bytes=relay.DEFAULT_MAX_BYTES, semaphore=threading.BoundedSemaphore(1))
    tcp_thread = threading.Thread(target=tcp.serve_forever, daemon=True)
    tcp_thread.start()
    try:
        with socket.create_connection(tcp.server_address, timeout=15) as connection:
            for name in ("first", "second"):
                relay._write_frame(connection, {"version": 1, "resource": name, "payload": {
                    "messages": [{"role": "user", "content": name}], "temperature": 0.1 if name == "first" else 0.8,
                }})
                response = relay._read_frame(connection, relay.DEFAULT_MAX_BYTES)
                assert response["status"] == 200, response
                assert response["body_json"]["choices"][0]["message"]["content"] == name
        assert [p["messages"] for p in received] == [
            [{"role": "user", "content": name}] for name in ("first", "second")]
        assert [p["temperature"] for p in received] == [0.1, 0.8]
    finally:
        stop.set()
        worker_thread.join(3)
        tcp.shutdown()
        tcp.server_close()
        _close(server, service_thread)
        upstream.shutdown()
        upstream.server_close()


def test_relay_read_deadline_releases_idle_connection(monkeypatch):
    """AI 中继在读取超时后关闭空闲连接并释放并发名额"""
    monkeypatch.setattr(relay, "DEFAULT_READ_TIMEOUT_SECONDS", 0.05)
    semaphore = threading.BoundedSemaphore(1)
    server = relay.RelayTCPServer(("127.0.0.1", 0), relay.RelayHandler,
        allowed_networks=relay._parse_allowed_networks("127.0.0.1"), max_bytes=1024, semaphore=semaphore)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.create_connection(server.server_address, timeout=2) as connection:
            assert connection.recv(1) == b""
        assert semaphore.acquire(timeout=1)
        semaphore.release()
    finally:
        server.shutdown()
        server.server_close()
