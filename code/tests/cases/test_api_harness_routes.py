"""API accounts use real Kimi/DSH runtimes against disposable loopback models.

No deployed account, key or model endpoint is used.
"""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json
import io
import queue
import threading
import uuid

import pytest

from tests.cases.test_api_key_harnesses import gateway, _call, _register
from management_portal.execution import isa
from management_portal.execution.provider_catalog import AGENT_PROVIDERS

pytestmark = pytest.mark.empty_execution_registrations


@pytest.mark.parametrize("provider,protocol", [("kimi", "openai_chat"), ("dsh", "anthropic_messages")])
def test_registered_api_catalog_and_protocol_guard(gateway, provider, protocol):
    row = _register("gateway-" + provider, provider)
    assert row["route_id"] == row["account_id"]
    catalog = AGENT_PROVIDERS[provider].catalog(row["route_id"])
    assert catalog["models"] == ["gpt-test", "gpt-mini"]
    assert catalog["default_model"] == "gpt-test"
    assert AGENT_PROVIDERS[provider].api_protocol == protocol
    if provider == "dsh":
        from management_portal.hatg.contracts import InstructionError
        with pytest.raises(InstructionError):
            _call(isa.update_model_api, api_id="gateway", changes={"protocols": {}})


def test_kimi_controls_and_new_session_use_the_bound_account(gateway, monkeypatch, tmp_path):
    from management_portal import kimi_account_runtime as runtime
    from management_portal.execution.backends import KimiBackend
    from management_portal.execution.session_manager import RuntimeSession
    from tests.cases.test_dsh_backend import invocation
    monkeypatch.setattr(runtime, "root", lambda: tmp_path / "accounts")
    calls = []
    client = SimpleNamespace(home=tmp_path, create_session=lambda **kw: {"id": "new-session"},
        steer=lambda *a, **kw: calls.append("steer") or {"steered": True},
        interrupt=lambda *a, **kw: calls.append("stop") or {"interrupted": True})
    monkeypatch.setattr(runtime, "client_for_route", lambda route, **kw: calls.append(route) or client)
    turn = invocation("kimi", {"model": "gpt-test"}, "gateway-kimi")
    # Production session records are dataclasses; use the request's immutable options here.
    turn = replace(turn, request=replace(turn.request, operation="create"),
                   session=RuntimeSession("persistent", "resident:test", "", True, backend_route_id="gateway-kimi"))
    result = KimiBackend().execute(turn, SimpleNamespace(set_interrupt_target=lambda _: None))
    assert result.backend_route_id == "gateway-kimi"
    assert runtime.session_route(result.backend_session_id) == "gateway-kimi"
    assert KimiBackend().deliver_turn_input(session_key="k", session_id=result.backend_session_id,
        turn_id="t", items=[{"type": "text", "text": "continue"}], control_id="c")[0]
    assert KimiBackend().interrupt(backend_session_id=result.backend_session_id, turn_id="t",
        backend_route_id="gateway-kimi", runtime_options={})
    assert calls == ["gateway-kimi", "gateway-kimi", "steer", "gateway-kimi", "stop"]
    with pytest.raises(ValueError, match="migration"):
        KimiBackend().execute(replace(turn, request=replace(turn.request,
            runtime_options={"provider_profile": "other"})), None)


def _bounded(call):
    result = queue.Queue()
    def run():
        try:
            result.put(call())
        except BaseException as error:
            result.put(error)
    threading.Thread(target=run, daemon=True).start()
    value = result.get(timeout=90)
    if isinstance(value, BaseException):
        raise value
    return value


@pytest.fixture
def model_server():
    from scripts.windows.native_cli_smoke import ModelFixture
    fixture = ModelFixture()
    fixture.model = "custom-model"
    fixture.headers = []
    fixture.next_tool = None
    fixture.bodies = []
    handler = fixture.server.RequestHandlerClass
    original = handler.do_POST
    def post(self):
        fixture.headers.append(dict(self.headers))
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        body = json.loads(raw)
        fixture.bodies.append(body)
        self.rfile = io.BytesIO(raw)
        tool = fixture.next_tool
        if tool and not self.path.endswith("count_tokens"):
            fixture.next_tool = None
            fixture.calls.append({"model": body.get("model"), "path": self.path})
            names = [item.get("name", item.get("function", {}).get("name")) for item in body.get("tools", [])]
            assert tool["name"] in names, names
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            if "/messages" in self.path:
                events = [
                    ("message_start", {"message": {"id": "msg_tool", "type": "message", "role": "assistant",
                        "model": fixture.model, "content": [], "usage": {"input_tokens": 10, "output_tokens": 0}}}),
                    ("content_block_start", {"index": 0, "content_block": {"type": "tool_use", "id": "call_write", "name": tool["name"], "input": {}}}),
                    ("content_block_delta", {"index": 0, "delta": {"type": "input_json_delta", "partial_json": json.dumps(tool["arguments"])}}),
                    ("content_block_stop", {"index": 0}),
                    ("message_delta", {"delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 10}}),
                    ("message_stop", {})]
                for kind, data in events:
                    self.wfile.write(f'event: {kind}\ndata: {json.dumps({"type": kind, **data})}\n\n'.encode())
            else:
                for delta, finish in [({"role": "assistant", "tool_calls": [{"index": 0, "id": "call_write", "type": "function",
                    "function": {"name": tool["name"], "arguments": json.dumps(tool["arguments"])}}]}, None), ({}, "tool_calls")]:
                    data = {"id": "fixture", "object": "chat.completion.chunk", "model": fixture.model,
                            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                    self.wfile.write(f'data: {json.dumps(data)}\n\n'.encode())
                self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
            self.close_connection = True
            return
        original(self)
    handler.do_POST = post
    try:
        yield fixture
    finally:
        fixture.close()


def _api(fixture, name, auth="bearer"):
    _call(isa.create_model_api, api_id=name, provider="fixture", base_url=fixture.url + "/v1",
        default_model=fixture.model, secret="fixture-" + name, headers={"X-Account": name},
        protocols={"anthropic_messages": {"base_url": fixture.url, "auth": auth}},
        models=[{"id": fixture.model, "context_window": 32768}])


def test_native_kimi_accounts_isolate_config_sessions_and_credentials(model_server, installed_provider_paths, tmp_path, monkeypatch):
    from management_portal import kimi_account_runtime as runtime
    binary = installed_provider_paths["runtime_root"] / "kimi-code-current/bin/kimi"
    if not binary.is_file():
        pytest.skip("installed Kimi runtime unavailable")
    monkeypatch.setenv("PORTAL_KIMI_CODE_BINARY", str(binary))
    monkeypatch.setattr(runtime, "root", lambda: tmp_path / "accounts")
    accounts = []
    try:
        for name in ("first", "second"):
            _api(model_server, name)
            row = _register(name + "-kimi", "kimi", name)
            accounts.append(row)
            client = runtime.client_for_route(row["route_id"])
            target = tmp_path / (name + ".txt")
            model_server.next_tool = {"name": "Write", "arguments": {"path": str(target), "content": name}}
            session = client.create_session(cwd=str(tmp_path), title=name, tools=["Write"], permission_mode="yolo", model=model_server.model)
            result = _bounded(lambda: client.run_turn(session_id=session["id"], prompt="Say hello.", tools=[], model=model_server.model))
            assert model_server.reply in result.response
            assert target.exists(), [m for m in model_server.bodies[-1].get("messages", []) if m.get("role") == "tool"]
            assert target.read_text() == name
            assert runtime.client_for_route(row["route_id"]).base_url == client.base_url
            headers = {k.lower(): v for k, v in model_server.headers[-1].items()}
            assert headers["authorization"] == "Bearer fixture-" + name
            assert headers["x-account"] == name
            assert client.home == runtime.home(row)
            if name == "first":
                from management_portal import model_api_store
                model_api_store.update_api(name, {}, "test", secret="rotated-fixture-key")
                renewed = runtime.client_for_route(row["route_id"])
                # Backend settles the profile before subscribing, loading a cold session.
                renewed.update_session_profile(session["id"], tools=["Write"], model=model_server.model)
                result = _bounded(lambda: renewed.run_turn(session_id=session["id"], prompt="Continue.", tools=[], model=model_server.model))
                assert model_server.reply in result.response
                assert {k.lower(): v for k, v in model_server.headers[-1].items()}["authorization"] == "Bearer rotated-fixture-key"
                assert any("Say hello." in json.dumps(body.get("messages")) for body in model_server.bodies[-1:])
        assert all(call["model"] == model_server.model for call in model_server.calls)
        assert (runtime.home(accounts[0]) / "config.toml").read_bytes() != (runtime.home(accounts[1]) / "config.toml").read_bytes()
    finally:
        for account in accounts:
            runtime.remove_account(account)


@pytest.mark.parametrize("auth", ["bearer", "x-api-key"])
def test_native_dsh_custom_messages_api(model_server, installed_provider_paths, tmp_path, monkeypatch, auth):
    from management_portal import dsh_client
    runtime = installed_provider_paths["runtime_root"] / "dsh-runtime-current"
    if not (runtime / "node_modules/@deepseek-ai/dsh/lib/bin.js").is_file():
        pytest.skip("installed DSH runtime unavailable")
    monkeypatch.setattr(dsh_client, "RUNTIME", runtime)
    monkeypatch.setattr(dsh_client, "STATE", tmp_path / "state")
    monkeypatch.setattr(dsh_client, "SOCKETS", tmp_path / "sockets")
    _api(model_server, "gateway", auth)
    row = _register("gateway-dsh", "dsh")
    client = dsh_client.DshClient()
    turn_id = uuid.uuid4().hex
    session_id = dsh_client.session_id_for(turn_id)
    # JSON object order can change when execution options pass through JSONB.
    # The launch environment canonicalizes Resident before Lineage; the wire
    # identity must denote that same Resident regardless of its original order.
    identity = {"PORTAL_LINEAGE_ID": "lineage_123456789abc", "PORTAL_RESIDENT_ID": "resident_123456789abc"}
    try:
        target = tmp_path / "written.txt"
        model_server.next_tool = {"name": "write", "arguments": {"file_path": str(target), "content": "native-tool-ok"}}
        result = _bounded(lambda: client.run_turn(turn_id=turn_id, provider_profile=row["route_id"],
            cwd=str(tmp_path), model=model_server.model, prompt="Say hello.", is_owner=True,
            permission_profile="owner", allow_subagents=False, resident_environment=identity))
        assert model_server.reply in result.response
        assert target.read_text() == "native-tool-ok"
        headers = {k.lower(): v for k, v in model_server.headers[-1].items()}
        assert headers["authorization" if auth == "bearer" else "x-api-key"] == ("Bearer " if auth == "bearer" else "") + "fixture-gateway"
        assert headers["x-account"] == "gateway"
        assert all(call["model"] == model_server.model for call in model_server.calls)
        import yaml
        [patch] = yaml.safe_load((tmp_path / "state" / session_id / "profiles/portal-resident/cordis.patch.yml").read_text())
        assert not any(row["id"] == "dsh-web-search-deepseek" for row in patch["insert"])
        # A new Worker can attach to the existing process with either key order.
        resumed = dsh_client.DshClient()
        common = dict(session_id=session_id, provider_profile=row["route_id"], cwd=str(tmp_path),
            model=model_server.model, allow_subagents=False)
        continued = _bounded(lambda: resumed.run_turn(**common, turn_id=uuid.uuid4().hex,
            prompt="Continue.", is_owner=True, permission_profile="owner",
            resident_environment=dict(reversed(list(identity.items())))))
        assert model_server.reply in continued.response
        _bounded(lambda: resumed.compact_session(**common, resident_environment=identity))

        # Normalization must never permit a different Resident/Lineage, a
        # missing identity, or extra environment fields on a cached process.
        from management_portal.native_agent_transport import NativeAgentError
        before = len(model_server.calls)
        for field, other in (("PORTAL_RESIDENT_ID", "resident_abcdef123456"),
                             ("PORTAL_LINEAGE_ID", "lineage_abcdef123456")):
            wrong = {**identity, field: other}
            with pytest.raises(NativeAgentError, match="Resident identity mismatch"):
                resumed.run_turn(**common, turn_id=uuid.uuid4().hex, prompt="Do not run.",
                    is_owner=True, permission_profile="owner", resident_environment=wrong)
            with pytest.raises(NativeAgentError, match="Resident identity mismatch"):
                resumed.compact_session(**common, resident_environment=wrong)
        with pytest.raises(NativeAgentError, match="Resident identity mismatch"):
            resumed.compact_session(**common)
        with pytest.raises(ValueError, match="Unexpected Resident environment field"):
            resumed.run_turn(**common, turn_id=uuid.uuid4().hex, prompt="Do not run.",
                is_owner=True, permission_profile="owner", resident_environment={**identity, "EXTRA": "value"})
        assert len(model_server.calls) == before
    finally:
        client.close_session("", session_id)


def test_kimi_key_rotation_does_not_interrupt_busy_session_or_block_stop(gateway, tmp_path, monkeypatch):
    from management_portal import kimi_account_runtime as runtime
    from management_common.file_permissions import ensure_private_directory
    row = _register("gateway-kimi", "kimi")
    monkeypatch.setattr(runtime, "root", lambda: tmp_path / "accounts")
    target = runtime.home(row)
    ensure_private_directory(target)
    (target / "server.json").write_text(json.dumps({"pid": 1234, "port": 1234, "version": [0, 0]}))
    client = SimpleNamespace(_request=lambda *a, **kw: {"sessions": [{"id": "busy"}]}, get_status=lambda _: {"busy": True})
    # The account's own server is running (ownership is checked by PID, start time and home).
    monkeypatch.setattr(runtime, "_owned_process", lambda _target, _state: SimpleNamespace(pid=1234))
    monkeypatch.setattr(runtime, "_client", lambda *args: client)
    def forbidden(*args, **kwargs):
        pytest.fail("configuration rotation killed or started a process during a turn")
    monkeypatch.setattr(runtime, "terminate_process_tree", forbidden)
    monkeypatch.setattr(runtime, "spawn_process", forbidden)
    with pytest.raises(RuntimeError, match="running"):
        runtime.client_for_route(row["route_id"])
    assert runtime.client_for_route(row["route_id"], for_control=True) is client
