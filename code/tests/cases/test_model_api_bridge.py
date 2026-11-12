"""Native harness protocol conversion keeps tools, history and credentials scoped.

:module: execution.providers
:importance: P1
"""
from __future__ import annotations

import json
import http.client
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from management_portal import model_api_stream as stream, model_api_wire as wire
from management_portal.model_api_bridge import Bridge


def test_rejection_contract_terms_never_echo_provider_body_values():
    from management_portal.model_api_bridge import rejection_terms
    terms = rejection_terms(b'reasoning_content required; max_completion_tokens unsupported; thought_signature missing. private-user secret-token')
    assert terms == ['reasoning_content', 'required', 'max_completion_tokens', 'unsupported', 'thought_signature']


def test_converted_responses_identify_final_answers_and_tool_commentary():
    for with_tool in (False, True):
        writer = stream.Writer(wire.RESPONSES, 'test-model')
        list(writer.take('start', {}))
        list(writer.take('block', {'index': 0, 'block': {'type': 'text', 'text': 'answer'}}))
        if with_tool:
            list(writer.take('block', {'index': 1, 'block': {
                'type': 'tool_use', 'id': 'call-test', 'name': 'read', 'arguments': '{}'}}))
        events = list(writer.take('close', {'index': 0}))
        done = next(event['item'] for event in events if event['type'] == 'response.output_item.done')
        assert done['phase'] == ('commentary' if with_tool else 'final_answer')
        assert writer._response('completed')['output'][0]['id'] == done['id']


def test_chat_reasoning_survives_responses_history_and_tool_continuation():
    chunks = [
        {'id': 'chat-test', 'model': 'deepseek', 'choices': [{'delta': {'reasoning_content': 'inspect then read'}, 'finish_reason': None}]},
        {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'call-read', 'function': {'name': 'read', 'arguments': '{}'}}]}, 'finish_reason': 'tool_calls'}]},
    ]
    writer = stream.Writer(wire.RESPONSES, 'deepseek')
    for kind, value in stream.decode(chunks, wire.CHAT):
        list(writer.take(kind, value))
    history = writer.result['output'] + [{'type': 'function_call_output', 'call_id': 'call-read', 'output': 'file contents'}]
    converted = wire.encode(wire.request({'model': 'deepseek', 'input': history}, wire.RESPONSES), wire.CHAT)
    assistant = converted['messages'][0]
    assert assistant['role'] == 'assistant'
    assert assistant['reasoning_content'] == 'inspect then read'
    assert assistant['tool_calls'][0]['id'] == 'call-read'
    assert converted['messages'][1]['tool_call_id'] == 'call-read'


def test_responses_developer_instructions_keep_system_priority_in_chat():
    request = {'model': 'chat-model', 'input': [
        {'role': 'developer', 'content': 'Follow the tool policy'},
        {'role': 'user', 'content': 'Read a file'},
    ]}
    payload = wire.encode(wire.request(request, wire.RESPONSES), wire.CHAT)
    assert [message['role'] for message in payload['messages']] == ['system', 'user']
    assert payload['messages'][0]['content'][0]['text'] == 'Follow the tool policy'


@pytest.mark.parametrize('omit_index', [False, True])
def test_chat_tool_signatures_survive_persisted_responses_and_parallel_calls(omit_index):
    metadata = {'google': {'thought_signature': 'genuine-upstream-state'}}
    chunks = [
        {'choices': [{'delta': {'tool_calls': [
            {'index': 0, 'id': 'first', 'function': {'name': 'read', 'arguments': '{'}},
            {'index': 1, 'id': 'second', 'function': {'name': 'list', 'arguments': '{}'}}]}}]},
        {'choices': [{'delta': {'tool_calls': [
            {'index': 0, 'function': {'arguments': '}'}, 'extra_content': metadata}]}, 'finish_reason': 'tool_calls'}]},
    ]
    if omit_index:
        for chunk in chunks:
            for tool in chunk['choices'][0]['delta']['tool_calls']:
                index = tool.pop('index')
                tool['id'] = ('first', 'second')[index]
        chunks[-1]['choices'][0]['finish_reason'] = 'stop'
    writer = stream.Writer(wire.RESPONSES, 'gemini')
    for kind, value in stream.decode(chunks, wire.CHAT):
        list(writer.take(kind, value))
    # Serialize as a harness does, then decode without any live bridge/cache.
    output = json.loads(json.dumps(writer.result['output']))
    output += [{'type': 'function_call_output', 'call_id': cid, 'output': 'ok'} for cid in ('first', 'second')]
    converted = wire.encode(wire.request({'model': 'gemini', 'input': output}, wire.RESPONSES), wire.CHAT)
    calls = converted['messages'][0]['tool_calls']
    assert [call['id'] for call in calls] == ['first', 'second']
    assert calls[0]['extra_content'] == metadata
    assert 'extra_content' not in calls[1]
    assert [message['tool_call_id'] for message in converted['messages'][1:]] == ['first', 'second']


def test_indexless_tool_deltas_require_an_unambiguous_call():
    first = {'choices': [{'delta': {'tool_calls': [
        {'id': 'first', 'function': {'name': 'read', 'arguments': '{'}}]}}]}
    continuation = {'choices': [{'delta': {'tool_calls': [{'function': {'arguments': '}'}}]}, 'finish_reason': 'stop'}]}
    writer = stream.Writer(wire.RESPONSES, 'gemini')
    for event in stream.decode([first, continuation], wire.CHAT):
        list(writer.take(*event))
    assert writer.result['output'][0]['arguments'] == '{}'
    ambiguous = json.loads(json.dumps(first))
    ambiguous['choices'][0]['delta']['tool_calls'].append({'id': 'second', 'function': {'name': 'list', 'arguments': '{}'}})
    with pytest.raises(wire.WireError, match='unambiguous'):
        list(stream.decode([ambiguous, continuation], wire.CHAT))


def _reply(protocol, tool, name="read_file", arguments=None, reasoning=False):
    arguments = arguments or {"path": "hello.txt"}
    encoded = json.dumps(arguments)
    if protocol == wire.RESPONSES:
        output = ([{"type": "function_call", "id": "fc-1", "call_id": "call-1", "name": name, "arguments": encoded}]
                  if tool else [{"type": "message", "id": "msg-1", "role": "assistant", "content": [{"type": "output_text", "text": "读取成功"}]}])
        if reasoning:
            output.insert(0, {"type": "reasoning", "id": "rs-1", "summary": [{"type": "summary_text", "text": "Read the local file."}],
                              "encrypted_content": "opaque-upstream-reasoning"})
        return {"id": "resp-1", "object": "response", "status": "completed", "model": "test-model", "output": output,
                "usage": {"input_tokens": 21, "output_tokens": 7, "input_tokens_details": {"cached_tokens": 3}}}
    if protocol == wire.MESSAGES:
        return {"id": "msg-1", "type": "message", "role": "assistant", "model": "test-model",
                "content": ([{"type": "tool_use", "id": "call-1", "name": name, "input": arguments}]
                            if tool else [{"type": "text", "text": "读取成功"}]), "stop_reason": "tool_use" if tool else "end_turn",
                "usage": {"input_tokens": 18, "output_tokens": 7, "cache_read_input_tokens": 3}}
    return {"id": "chat-1", "object": "chat.completion", "model": "test-model", "choices": [{"index": 0,
            "message": {"role": "assistant", "content": None if tool else "读取成功", **({"tool_calls": [{"id": "call-1", "type": "function",
              "function": {"name": name, "arguments": encoded}}]} if tool else {})},
            "finish_reason": "tool_calls" if tool else "stop"}],
            "usage": {"prompt_tokens": 21, "completion_tokens": 7, "prompt_tokens_details": {"cached_tokens": 3}}}


def _events(protocol, tool, name="read_file", arguments=None, reasoning=False):
    reply = _reply(protocol, tool, name, arguments, reasoning)
    arguments = json.dumps(arguments or {"path": "hello.txt"})
    if protocol == wire.RESPONSES:
        yield {"type": "response.created", "response": {"id": "resp-1", "model": "test-model"}}
        for index, item in enumerate(reply["output"]):
            if item["type"] == "function_call":
                yield {"type": "response.output_item.added", "output_index": index, "item": {**item, "arguments": ""}}
                for args in [arguments[:8], arguments[8:]]:
                    yield {"type": "response.function_call_arguments.delta", "output_index": index, "delta": args}
            elif item["type"] == "message":
                for text in ["读取", "成功"]:
                    yield {"type": "response.output_text.delta", "output_index": index, "content_index": 0, "delta": text}
            yield {"type": "response.output_item.done", "output_index": index, "item": item}
        yield {"type": "response.completed", "response": reply}
    elif protocol == wire.MESSAGES:
        yield {"type": "message_start", "message": {**reply, "content": [], "stop_reason": None}}
        part = reply["content"][0]
        yield {"type": "content_block_start", "index": 0, "content_block": {**part, **({"input": {}} if tool else {"text": ""})}}
        for delta in ([{"type": "input_json_delta", "partial_json": arguments[:8]}, {"type": "input_json_delta", "partial_json": arguments[8:]}]
                      if tool else [{"type": "text_delta", "text": "读取"}, {"type": "text_delta", "text": "成功"}]):
            yield {"type": "content_block_delta", "index": 0, "delta": delta}
        yield {"type": "content_block_stop", "index": 0}
        yield {"type": "message_delta", "delta": {"stop_reason": reply["stop_reason"]}, "usage": {"output_tokens": 7}}
        yield {"type": "message_stop"}
    else:
        for delta in ([{"tool_calls": [{"index": 0, "id": "call-1", "type": "function", "function": {"name": name, "arguments": arguments[:8]}}]},
                       {"tool_calls": [{"index": 0, "function": {"arguments": arguments[8:]}}]}]
                      if tool else [{"content": "读取"}, {"content": "成功"}]):
            yield {"id": "chat-1", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
        yield {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if tool else "stop"}]}
        yield {"choices": [], "usage": reply["usage"]}


@pytest.fixture
def endpoint(tmp_path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.server.requests.append((self.path, payload, dict(self.headers)))
            status = self.server.status
            self.send_response(status)
            self.send_header("Content-Type", "application/json" if self.server.json else "text/event-stream")
            self.end_headers()
            if status != 200:
                self.wfile.write(b'{"error":"sk-upstream-secret must not be echoed"}')
                return
            if server.pause:
                self.wfile.write(b'data: {"type":"response.created","response":{"id":"r"}}\n\n')
                self.wfile.flush()
                self.connection.settimeout(8)
                if self.connection.recv(1) == b"":
                    server.disconnected.set()
                return
            protocol = self.server.protocol
            has_result = (any(i.get("type") == "function_call_output" for i in payload.get("input", [])) if protocol == wire.RESPONSES else
                          any(m.get("role") == "tool" or any(isinstance(b, dict) and b.get("type") == "tool_result" for b in m.get("content") or [])
                              for m in payload.get("messages", [])))
            if self.server.json:
                self.wfile.write(json.dumps(_reply(protocol, not has_result, server.tool_name, server.tool_arguments, server.reasoning)).encode())
            else:
                for event in _events(protocol, not has_result, server.tool_name, server.tool_arguments, server.reasoning):
                    self.wfile.write(("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode())
                    self.wfile.flush()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.requests, server.protocol, server.json, server.status = [], wire.RESPONSES, False, 200
    server.tool_name, server.tool_arguments = "read_file", None
    server.reasoning = False
    server.pause, server.disconnected = False, threading.Event()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    secret = tmp_path / "upstream.key"
    secret.write_text("sk-upstream-secret")
    entry = {"id": "upstream", "kind": wire.RESPONSES, "base_url": f"http://127.0.0.1:{server.server_port}/v1",
             "default_model": "test-model", "api_key_file": str(secret), "default_headers": {"X-Team": "lab"}}
    try:
        yield server, entry
    finally:
        server.shutdown()
        server.server_close()


def _post(bridge, payload, token=None, extra_headers=None):
    url = bridge.base_url + ({wire.MESSAGES: '/v1/messages', wire.RESPONSES: '/responses', wire.CHAT: '/chat/completions'}[bridge.target])
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={
        "Authorization": "Bearer " + (bridge.token if token is None else token),
        "Content-Type": "application/json", **(extra_headers or {})})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=10) as response:
        return (list(stream.sse(response)) if payload.get("stream") else json.load(response))


@pytest.mark.parametrize("protocol", [wire.CHAT, wire.MESSAGES])
def test_anonymous_upstream_keeps_configured_protocol_without_empty_auth(endpoint, protocol):
    from management_portal.model_api_bridge import connection, open_upstream

    server, entry = endpoint
    entry.pop("api_key_file")
    entry["kind"] = "openai_compatible_chat" if protocol == wire.CHAT else protocol
    if protocol == wire.MESSAGES:
        entry["protocols"] = {protocol: {"auth": "x-api-key"}}
    server.protocol, server.json = protocol, True
    assert connection(entry, protocol)[2] == ""
    with open_upstream(entry, protocol, {"model": "test-model", "messages": [], "stream": False}) as response:
        json.load(response)
    headers = server.requests[0][2]
    assert "Authorization" not in headers and "X-Api-Key" not in headers
    assert headers["X-Team"] == "lab"


@pytest.mark.parametrize("target,upstream", [(wire.MESSAGES, wire.RESPONSES), (wire.MESSAGES, wire.CHAT), (wire.RESPONSES, wire.MESSAGES)])
@pytest.mark.parametrize("as_json", [False, True])
def test_harness_tool_round_trip_uses_only_selected_protocol_and_credentials(endpoint, target, upstream, as_json):
    server, entry = endpoint
    server.protocol, server.json = upstream, as_json
    entry["kind"] = "openai_compatible_chat" if upstream == wire.CHAT else upstream
    if upstream == wire.MESSAGES:
        entry["protocols"] = {wire.MESSAGES: {"auth": "x-api-key"}}
    tools = [{"name": "read_file", "description": "read a local file", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}]
    if target == wire.MESSAGES:
        payload = {"model": "test-model", "max_tokens": 8000, "stream": True, "system": "Be helpful",
                   "messages": [{"role": "user", "content": "Read hello.txt"}], "tools": tools, "output_config": {"effort": "high"}}
    else:
        payload = {"model": "test-model", "stream": True, "instructions": "Be helpful", "input": [{"role": "user", "content": "Read hello.txt"}],
                   "tools": [{"type": "function", "name": t["name"], "description": t["description"], "parameters": t["input_schema"]} for t in tools], "reasoning": {"effort": "high"}}
    with Bridge(lambda: entry, target) as bridge:
        events = _post(bridge, payload, extra_headers={"X-Team": "malicious-client", "Cookie": "private"})
        if target == wire.MESSAGES:
            call = next(e["content_block"] for e in events if e["type"] == "content_block_start")
            assert call["id"] == "call-1" and call["name"] == "read_file"
            arguments = "".join(e["delta"]["partial_json"] for e in events if e["type"] == "content_block_delta")
            assert json.loads(arguments) == {"path": "hello.txt"}
            assert events[-2]["delta"]["stop_reason"] == "tool_use"
            payload["messages"] += [{"role": "assistant", "content": [{**call, "input": json.loads(arguments)}]},
                                    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call["id"], "content": "FILE_CONTENT"}]}]
        else:
            call = events[-1]["response"]["output"][0]
            assert (call["call_id"], call["name"], json.loads(call["arguments"])) == ("call-1", "read_file", {"path": "hello.txt"})
            assert events[-1]["type"] == "response.completed"
            payload["input"] += [call, {"type": "function_call_output", "call_id": call["call_id"], "output": "FILE_CONTENT"}]
        payload["stream"] = False
        final = _post(bridge, payload)
        content = final["content"] if target == wire.MESSAGES else final["output"][0]["content"]
        assert content[0]["text"] == "读取成功"
        assert final["usage"]["output_tokens"] == 7
        assert final["usage"]["input_tokens"] == (18 if target == wire.MESSAGES else 21)
    assert len(server.requests) == 2
    for path, body, headers in server.requests:
        assert path == {wire.RESPONSES: "/v1/responses", wire.CHAT: "/v1/chat/completions", wire.MESSAGES: "/v1/messages"}[upstream]
        assert body["model"] == "test-model" and body["stream"] is True
        assert headers["X-Team"] == "lab" and "Cookie" not in headers
        assert headers.get("X-Api-Key", headers.get("Authorization")) in ("sk-upstream-secret", "Bearer sk-upstream-secret")
        assert bridge.token not in str(headers)
    assert "FILE_CONTENT" in json.dumps(server.requests[1][1])


def test_adapter_rejects_disabled_api_wrong_auth_models_and_stateful_input_before_upstream(endpoint):
    server, entry = endpoint
    payload = {"model": "test-model", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 20}
    with Bridge(lambda: entry, wire.MESSAGES) as bridge:
        for changed, token, status in [({}, "wrong", 401), ({"model": "other"}, None, 400), ({"messages": [{"role": "user", "content": [{"type": "document"}]}]}, None, 400)]:
            with pytest.raises(urllib.error.HTTPError) as error:
                _post(bridge, {**payload, **changed}, token=token)
            assert error.value.code == status
        entry["enabled"] = False
        with pytest.raises(urllib.error.HTTPError):
            _post(bridge, payload)
    assert server.requests == []
    with pytest.raises(wire.WireError, match="complete harness history"):
        wire.request({"model": "m", "previous_response_id": "resp-private"}, wire.RESPONSES)


@pytest.mark.parametrize("status", [401, 429, 500])
def test_upstream_failure_is_not_retried_or_echoed(endpoint, status):
    server, entry = endpoint
    server.status = status
    with Bridge(lambda: entry, wire.MESSAGES) as bridge:
        with pytest.raises(urllib.error.HTTPError) as error:
            _post(bridge, {"model": "test-model", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 20})
        assert error.value.code == status and b"sk-upstream-secret" not in error.value.read()
    assert len(server.requests) == 1


def test_stream_eof_and_malformed_tool_arguments_never_report_success():
    writer = stream.Writer(wire.MESSAGES, "m")
    with pytest.raises(wire.WireError, match="completion event"):
        for event in stream.decode([{"type": "response.created", "response": {"id": "r"}}], wire.RESPONSES):
            list(writer.take(*event))
    assert writer.result is None
    list(writer.take("block", {"index": 0, "block": {"type": "tool_use", "id": "call", "name": "read", "arguments": "broken"}}))
    with pytest.raises(wire.WireError, match="invalid tool arguments"):
        list(writer.take("close", {"index": 0}))


def test_closing_the_harness_stream_closes_its_upstream_without_retry(endpoint):
    server, entry = endpoint
    server.pause = True
    with Bridge(lambda: entry, wire.MESSAGES) as bridge:
        connection = http.client.HTTPConnection("127.0.0.1", bridge.server.server_port, timeout=5)
        connection.request("POST", "/v1/messages", body=json.dumps({"model": "test-model", "max_tokens": 20, "stream": True,
            "messages": [{"role": "user", "content": "hello"}]}), headers={"Authorization": "Bearer " + bridge.token})
        response = connection.getresponse()
        assert response.status == 200 and b"message_start" in response.readline()
        response.close()
        connection.close()
        assert server.disconnected.wait(3), "Cancellation must release the upstream socket"
    assert len(server.requests) == 1


def test_custom_namespace_tools_images_and_opaque_thinking_survive_round_trip():
    source = {"model": "m", "input": [{"role": "user", "content": [{"type": "input_image", "image_url": "data:image/png;base64,aW1hZ2U="}]}],
              "tools": [{"type": "namespace", "name": "functions", "tools": [{"type": "function", "name": "read", "parameters": {"type": "object"}}]},
                        {"type": "custom", "name": "apply_patch", "format": {"type": "text"}}]}
    canonical = wire.request(source, wire.RESPONSES)
    anthropic = wire.encode(canonical, wire.MESSAGES)
    assert anthropic["tools"][0]["name"] == "functions__read"
    assert anthropic["messages"][0]["content"][0]["source"]["data"] == "aW1hZ2U="
    reply = {"id": "msg", "content": [{"type": "thinking", "thinking": "hmm", "signature": "real-anthropic-signature"},
                                        {"type": "tool_use", "id": "call", "name": "apply_patch", "input": {"input": "*** PATCH ***"}}],
             "stop_reason": "tool_use", "usage": {"input_tokens": 5, "output_tokens": 2}}
    writer = stream.Writer(wire.RESPONSES, "m", canonical["tool_map"])
    for event in stream.decode(stream.json_events(reply, wire.MESSAGES), wire.MESSAGES):
        list(writer.take(*event))
    output = writer.result["output"]
    assert output[1]["type"] == "custom_tool_call" and output[1]["input"] == "*** PATCH ***"
    restored = wire.encode(wire.request({**source, "input": [*source["input"], *output,
        {"type": "custom_tool_call_output", "call_id": "call", "output": "OK"}]}, wire.RESPONSES), wire.MESSAGES)
    assert restored["messages"][1]["content"][0] == reply["content"][0]
    assert restored["messages"][1]["content"][1]["input"] == {"input": "*** PATCH ***"}


@pytest.mark.parametrize("protocol", [wire.RESPONSES, wire.CHAT])
def test_empty_thinking_does_not_block_resuming_history_but_foreign_state_is_rejected(protocol):
    payload = {"model": "m", "messages": [
        {"role": "assistant", "content": [{"type": "thinking", "thinking": "", "signature": ""},
                                          {"type": "text", "text": "Previous answer"}]},
        {"role": "user", "content": "Continue"}]}
    canonical = wire.request(payload, wire.MESSAGES)
    assert canonical["messages"][0]["content"] == [{"type": "text", "text": "Previous answer"}]
    converted = wire.encode(canonical, protocol)
    assert "Previous answer" in json.dumps(converted) and "Continue" in json.dumps(converted)
    payload["messages"][0]["content"][0]["signature"] = "native-anthropic-state"
    with pytest.raises(wire.WireError, match="different upstream"):
        wire.encode(wire.request(payload, wire.MESSAGES), protocol)


def _instruction(handler, **arguments):
    return handler(SimpleNamespace(caller="alice", argument=lambda name, default=None: arguments.get(name, default),
                                   expected_version_for=lambda _: None))


@pytest.mark.skipif(not os.environ.get("PORTAL_CLAUDE_NATIVE_TEST_BINARY"), reason="Native Claude Code binary was not selected")
@pytest.mark.parametrize("protocol", [wire.RESPONSES, wire.CHAT])
@pytest.mark.parametrize("as_json", [False, True])
def test_real_claude_code_executes_a_local_tool_through_an_openai_only_api(endpoint, tmp_path, protocol, as_json):
    from management_portal.execution import isa
    from tests.cases.claude_sdk_support import load_claude_module
    daemon = load_claude_module("claude_code_daemon")
    server, entry = endpoint
    server.protocol, server.tool_name = protocol, "Read"
    server.json, server.reasoning = as_json, protocol == wire.RESPONSES
    note = tmp_path / "hello.txt"
    note.write_text("NATIVE_HARNESS_FILE_MARKER")
    server.tool_arguments = {"file_path": str(note)}
    _instruction(isa.create_model_api, api_id="native_bridge", kind="openai_responses" if protocol == wire.RESPONSES else "openai_compatible_chat",
        base_url=entry["base_url"], default_model="test-model", secret_file=entry["api_key_file"])
    env, _ = daemon._provider_environment("native_bridge-claude")
    env.update({"HOME": str(tmp_path / "home"), "PATH": os.defpath, "CLAUDE_CONFIG_DIR": str(tmp_path / "config"),
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", **dict.fromkeys(daemon.GATEWAY_MODEL_VARIABLES, "test-model")})
    result = subprocess.run([os.environ["PORTAL_CLAUDE_NATIVE_TEST_BINARY"], "-p", "Read hello.txt and report its contents.",
        "--model", "test-model", "--output-format", "json", "--tools", "Read", "--allowedTools", "Read", "--max-turns", "3"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr[-2000:] + result.stdout[-2000:]
    assert "读取成功" in result.stdout
    assert any("NATIVE_HARNESS_FILE_MARKER" in json.dumps(body) for _, body, _ in server.requests), result.stdout[-2000:]
    if protocol == wire.RESPONSES:
        assert any(item.get("encrypted_content") == "opaque-upstream-reasoning"
                   for _, body, _ in server.requests for item in body.get("input", []))
    assert {path for path, _, _ in server.requests} == ({"/v1/responses"} if protocol == wire.RESPONSES else {"/v1/chat/completions"})
    from management_portal.model_api_bridge import close_bridges
    close_bridges()


@pytest.mark.skipif(not os.environ.get("PORTAL_CODEX_NATIVE_TEST_BINARIES"), reason="Native Codex binary was not selected")
def test_real_codex_app_server_executes_a_local_tool_through_an_anthropic_only_api(endpoint, tmp_path, monkeypatch):
    from management_portal.codex_app_server_client import CodexAppServerClient
    from management_portal.codex_app_server_host import run_host
    from management_portal.execution import account_hosting, account_login, isa, codex_api_accounts
    from management_portal.execution.account_registrations import registration
    from management_portal.execution.codex_backend import CodexBackend
    server, entry = endpoint
    server.protocol = wire.MESSAGES
    server.tool_name, server.tool_arguments = "exec_command", {"cmd": "cat hello.txt", "max_output_tokens": 100}
    (tmp_path / "hello.txt").write_text("NATIVE_HARNESS_FILE_MARKER")
    binary = os.environ["PORTAL_CODEX_NATIVE_TEST_BINARIES"].split(os.pathsep)[0]
    monkeypatch.setattr(account_login, "provider_binary", lambda provider: binary)
    _instruction(isa.create_model_api, api_id="native_bridge", kind="anthropic_messages", base_url=entry["base_url"].removesuffix("/v1"),
        default_model="test-model", secret_file=entry["api_key_file"], protocols={wire.MESSAGES: {"auth": "x-api-key"}})
    row = registration("native_bridge-codex")
    codex_api_accounts.prepare(row)
    home = account_hosting.account_home("codex", row["account_id"])
    stop = threading.Event()
    outcome = []
    # Native Unix listeners have a short path limit, independent of pytest's temp root.
    with tempfile.TemporaryDirectory(prefix="tfi-cx-") as short, Bridge(lambda: {**entry, "kind": wire.MESSAGES}, wire.RESPONSES) as bridge:
        path = Path(short) / "cx.sock"
        def host():
            try:
                outcome.append(run_host(path, binary, home, stop, config_overrides=[
                    "model_providers.portal_api.base_url=" + json.dumps(bridge.base_url)], extra_env={"TFI_MODEL_BRIDGE_TOKEN": bridge.token}))
            except Exception as error:
                outcome.append(error)
        thread = threading.Thread(target=host, daemon=True)
        thread.start()
        client = CodexAppServerClient(path, request_timeout_seconds=10)
        try:
            deadline = time.monotonic() + 35
            while not path.exists() and not outcome and time.monotonic() < deadline:
                stop.wait(.05)
            assert path.exists(), outcome
            config = CodexBackend._thread_config({})
            session = client.start_thread({"cwd": str(tmp_path), "model": "test-model", "config": config,
                                           "approvalPolicy": "never", "sandbox": "danger-full-access"})["thread"]["id"]
            turn_deadline = time.monotonic() + 45
            result = client.run_turn(session, "Read hello.txt and report its contents.", cwd=str(tmp_path), model="test-model", config=config,
                                     cancellation_requested=lambda: time.monotonic() >= turn_deadline)
            assert "读取成功" in str(result)
        finally:
            client.close()
            stop.set()
            thread.join(10)
        assert not thread.is_alive() and outcome == [0], outcome
    assert any("NATIVE_HARNESS_FILE_MARKER" in json.dumps(body) for _, body, _ in server.requests)
    assert {path for path, _, _ in server.requests} == {"/v1/messages"}
