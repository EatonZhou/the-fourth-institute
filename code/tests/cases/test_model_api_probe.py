"""ProbeModelApi asks an endpoint which harness protocols it serves.

:module: execution.providers
:importance: P1
"""
from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from management_portal.execution import isa
from management_portal.hatg.contracts import InstructionError

KEY = "sk-probe-test"


class Gateway(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, answers):
        super().__init__(("127.0.0.1", 0), Handler)
        self.answers = answers
        self.requests: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    server: Gateway

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        request = {"path": self.path, "model": body.get("model"), "authorization": self.headers.get("Authorization"),
                   "x_api_key": self.headers.get("x-api-key"), "version": self.headers.get("anthropic-version"),
                   "team": self.headers.get("X-Team")}
        self.server.requests.append(request)
        status, content_type, payload = self.server.answers(request)
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def gateway():
    servers = []

    def start(answers):
        server = Gateway(answers)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.shutdown()


def _call(handler, **arguments):
    return handler(SimpleNamespace(caller="alice", argument=lambda name, default=None: arguments.get(name, default),
                                   expected_version_for=lambda object_id: None))


def test_a_probe_finds_the_protocols_a_gateway_serves_and_its_anthropic_auth(gateway, tmp_path):
    """检测已登记的接口：三种协议各发一次请求，按登记的密钥和请求头认证，找到 Anthropic 可用的认证方式

    Chat Completions 与 Responses 以 Bearer 密钥发往接口基础地址；Anthropic Messages 发往去掉 /v1 的地址下的 /v1/messages，先试 Bearer、被拒绝后改用 x-api-key。结果不含密钥，也不修改登记。
    """
    def answers(request):
        if request["path"] == "/v1/chat/completions":
            return 200, "application/json", {"choices": [{"message": {"role": "assistant", "content": "OK"}}]}
        if request["path"] == "/v1/responses":
            return 200, "application/json", {"object": "response", "output": []}
        if request["x_api_key"] == KEY:
            return 200, "application/json", {"type": "message", "role": "assistant", "content": []}
        return 401, "application/json", {"error": {"message": "invalid x-api-key"}}

    server = gateway(answers)
    key_file = tmp_path / "gateway.key"
    key_file.write_text(KEY + "\n")
    created = _call(isa.create_model_api, api_id="gateway", base_url=f"http://127.0.0.1:{server.server_port}/v1",
                    default_model="gpt-test", secret_file=str(key_file), headers={"X-Team": "lab"})
    result = _call(isa.probe_model_api, api_id="gateway")
    assert result["model"] == "gpt-test"
    assert {name: outcome["status"] for name, outcome in result["results"].items()} == {
        "openai_chat": "supported", "openai_responses": "supported", "anthropic_messages": "supported"}
    assert result["results"]["anthropic_messages"]["auth"] == "x-api-key"
    assert [(r["path"], r["authorization"], r["x_api_key"]) for r in server.requests] == [
        ("/v1/chat/completions", f"Bearer {KEY}", None), ("/v1/responses", f"Bearer {KEY}", None),
        ("/v1/messages", f"Bearer {KEY}", None), ("/v1/messages", None, KEY)]
    assert {r["team"] for r in server.requests} == {"lab"} and {r["model"] for r in server.requests} == {"gpt-test"}
    assert {r["version"] for r in server.requests if r["path"] == "/v1/messages"} == {"2023-06-01"}
    assert KEY not in json.dumps(result)
    assert _call(isa.list_model_apis)["apis"][0]["version"] == created["api"]["version"]


def test_a_probe_reports_what_an_unsaved_endpoint_does_not_serve(gateway):
    """检测尚未保存的接口：网页式 200、404、400 和无法连接分别报告为不支持、不支持、拒绝请求和无法连接

    返回内容中出现的密钥替换为 ***；缺少模型或地址无效时以参数错误拒绝，未登记的接口返回 not_found。
    """
    def answers(request):
        if request["path"] == "/v1/chat/completions":
            return 400, "application/json", {"error": {"message": f"model gpt-x not found for key {KEY}"}}
        if request["path"] == "/v1/responses":
            return 200, "text/html", b"<!doctype html><title>Gateway</title>"
        return 404, "application/json", {"error": "no route"}

    server = gateway(answers)
    base = f"http://127.0.0.1:{server.server_port}/v1"
    results = _call(isa.probe_model_api, base_url=base, default_model="gpt-x", secret=KEY)["results"]
    assert results["openai_chat"] == {"status": "rejected", "http_status": 400, "message": "model gpt-x not found for key ***"}
    assert (results["openai_responses"]["status"], results["openai_responses"]["http_status"]) == ("unsupported", 200)
    assert results["anthropic_messages"] == {"status": "unsupported", "http_status": 404, "message": "no route"}
    with socket.socket() as closed:
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
    elsewhere = _call(isa.probe_model_api, base_url=base, default_model="gpt-x", secret=KEY,
                      anthropic_base_url=f"http://127.0.0.1:{port}")["results"]
    assert elsewhere["anthropic_messages"]["status"] == "unreachable"
    for arguments in ({"base_url": base}, {"base_url": "ftp://gateway.invalid", "default_model": "m"},
                      {"base_url": base, "default_model": "m", "anthropic_base_url": "gateway.invalid"}):
        with pytest.raises(InstructionError) as error:
            _call(isa.probe_model_api, **arguments)
        assert error.value.code == "instruction.invalid_arguments"
    with pytest.raises(InstructionError) as error:
        _call(isa.probe_model_api, api_id="missing")
    assert error.value.code == "instruction.not_found"


@pytest.mark.parametrize("provider,base_path,protocol_path,override_path,expected_path", [
    pytest.param("deepseek", "", None, None, "/anthropic/v1/messages", id="legacy-deepseek"),
    pytest.param("deepseek", "/v1", None, None, "/anthropic/v1/messages", id="legacy-v1"),
    pytest.param("deepseek", "/anthropic", None, None, "/anthropic/v1/messages", id="legacy-anthropic"),
    pytest.param("deepseek", "/v1", "/declared/", None, "/declared/v1/messages", id="declared"),
    pytest.param("deepseek", "/v1", "", None, "/v1/messages", id="declared-default"),
    pytest.param("deepseek", "/v1", False, None, "/v1/messages", id="explicit-no-messages"),
    pytest.param("gateway", "/v1", None, None, "/v1/messages", id="ordinary-undeclared"),
    pytest.param("deepseek", "/v1", None, "/override/", "/override/v1/messages", id="override-legacy"),
    pytest.param("deepseek", "/v1", "/declared", "/override/", "/override/v1/messages", id="override-declared"),
])
def test_messages_probe_uses_the_harness_endpoint_or_explicit_override(
        gateway, provider, base_path, protocol_path, override_path, expected_path):
    """注册接口沿用 Harness 地址；未声明协议仍可探测，显式探测地址优先且不修改登记。"""
    def answers(request):
        if request["path"] == expected_path:
            return 200, "application/json", {"type": "message", "role": "assistant", "content": []}
        return 404, "application/json", {"error": "no route"}

    server = gateway(answers)
    root = f"http://127.0.0.1:{server.server_port}"
    registration = {"provider": provider, "base_url": root + base_path,
                    "default_model": "deepseek-v4-flash", "secret": KEY}
    if protocol_path is False:
        registration["protocols"] = {}
    elif protocol_path is not None:
        registration["protocols"] = {"anthropic_messages": (
            {"base_url": root + protocol_path} if protocol_path else {})}
    _call(isa.create_model_api, api_id="endpoint-probe", **registration)
    before = _call(isa.list_model_apis)
    overrides = {"anthropic_base_url": root + override_path} if override_path else {}
    result = _call(isa.probe_model_api, api_id="endpoint-probe", **overrides)

    assert result["results"]["anthropic_messages"] == {
        "status": "supported", "http_status": 200, "auth": "bearer"}
    assert [request["path"] for request in server.requests] == [
        base_path + "/chat/completions",
        base_path + "/responses", expected_path]
    assert {request["model"] for request in server.requests} == {"deepseek-v4-flash"}
    assert _call(isa.list_model_apis) == before
