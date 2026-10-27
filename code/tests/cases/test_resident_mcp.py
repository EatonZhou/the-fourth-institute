"""Real isolated JSON-RPC transports; only the ResourceService boundary is replaced."""
import copy
import importlib
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


ACTOR = "resident_ec82f04e3c1c"
REF = "file_123456789abc"
SECRET = "file_123456789abd"
TOOL = {"name": "echo", "description": "Echo a value", "inputSchema": {
    "type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"],
    "additionalProperties": False}}


def test_execution_owner_is_available_for_assembly():
    assert importlib.util.find_spec("management_portal.execution.resident_mcp") is not None


SERVER = r'''
import json, os, sys, time, subprocess
log, mode = sys.argv[1:]
with open(log + ".pid", "w") as stream: stream.write(str(os.getpid()))
if mode == "descendant":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(log + ".child", "w") as stream: stream.write(str(child.pid))
for line in sys.stdin:
    msg = json.loads(line)
    with open(log, "a") as stream:
        stream.write(json.dumps(msg) + "\n")
    method = msg.get("method")
    if "id" not in msg:
        continue
    if method == "initialize":
        result = {"protocolVersion": "old" if mode == "version" else "2025-06-18",
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "fixture", "version": "1"},
                  "instructions": "NEVER inject this instruction"}
    elif method == "tools/list":
        if mode == "hang":
            time.sleep(10)
        tool = {"name": "echo", "description": "Echo a value", "inputSchema": {
            "type": "object", "properties": {"value": {"type": "string"}},
            "required": ["value"], "additionalProperties": False}}
        if mode == "schema": tool["inputSchema"] = {"type": "object", "$ref": "https://invalid.test/schema"}
        result = {"tools": [tool, dict(tool, name="unselected")]}
        if mode == "duplicate": result["tools"].append(tool)
        if mode == "pages":
            result["nextCursor"] = "repeat"
            if msg.get("params", {}).get("cursor"): result["tools"] = []
        if mode == "empty": result["tools"] = []
        if mode == "env":
            result["tools"][0]["description"] = json.dumps({k: os.environ.get(k) for k in ["MCP_ALLOWED", "MCP_DENIED", "MCP_SECRET"]})
        if mode == "change" and os.path.exists(log + ".changed"):
            result["tools"][0]["description"] = "modified"
        if mode == "contract":
            with open(log + ".tool.json") as stream:
                result["tools"][0] = json.load(stream)
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": msg["params"]["arguments"]["value"]}],
                  "isError": mode == "toolerror"}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
'''


@pytest.fixture
def mcp():
    module = importlib.util.find_spec("management_portal.execution.resident_mcp")
    assert module is not None, "Resident MCP execution owner must exist"
    return importlib.import_module(module.name)


@pytest.fixture
def connection(tmp_path):
    script = tmp_path / "server.py"
    script.write_text(SERVER)
    log = tmp_path / "requests.jsonl"
    doc = {"schema": "tfi.mcp-connection.v1", "protocol_version": "2025-06-18",
           "timeout_seconds": 3, "transport": {"type": "stdio", "command": sys.executable,
           "args": [str(script), str(log), "normal"], "env_allowlist": [], "env": {}}}
    return doc, log


@pytest.fixture
def resources(monkeypatch, mcp):
    documents, reads = {}, []
    class Service:
        def read_content(self, ref, *, actor, max_bytes):
            reads.append((ref, actor))
            value = documents[ref]
            if isinstance(value, Exception):
                raise value
            return (value if isinstance(value, bytes) else json.dumps(value).encode(),
                    "application/json", {})
    monkeypatch.setattr(mcp, "get_resource_service", Service)
    return documents, reads


def select(ref=REF, **kwargs):
    return {"ref": ref, "enabled": True, "required": True, "tools": ["echo"], **kwargs}


def build(mcp, items, **kwargs):
    return mcp.materialize_mcp_tools(items, caller=ACTOR, execution_id="execution_test", **kwargs)


def messages(log):
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_selected_stdio_tool_real_discovery_then_call(mcp, resources, connection):
    docs, reads = resources
    docs[REF], log = connection
    tools, observations = build(mcp, [select()])
    assert len(tools) == 1
    assert tools[0].connection_ref == REF and tools[0].local_name == "echo"
    assert tools[0].capability_ref == f"mcp:{REF}#echo"
    assert not any(msg.get("method") == "tools/call" for msg in messages(log))
    assert "NEVER inject" not in repr(observations)
    assert observations[0]["status"] == "discovered"
    assert observations[0]["delivery"] == "not_sent"
    assert observations[0]["local_name"] == "echo"
    assert tools[0].read_only is False  # Never trust server annotation as local authority.
    result = json.loads(tools[0].handler({"value": "hello"}))
    assert result["content"][0]["text"] == "hello"
    assert reads == [(REF, ACTOR), (REF, ACTOR)]
    assert messages(log)[-1]["params"]["_meta"]["tfi/resident_id"] == ACTOR
    assert messages(log)[-1]["params"]["_meta"]["tfi/execution_id"] == "execution_test"
    assert [m["method"] for m in messages(log)] == [
        "initialize", "notifications/initialized", "tools/list", "initialize",
        "notifications/initialized", "tools/list", "tools/call"]


def test_aliases_isolate_connections_and_are_stable(mcp, resources, connection):
    docs, _ = resources
    docs[REF], _ = connection
    docs[SECRET] = docs[REF]
    first, _ = build(mcp, [select(), select(SECRET)])
    second, _ = build(mcp, [select(SECRET), select()])
    assert len({tool.name for tool in first}) == 2
    assert {t.name for t in first} == {t.name for t in second}
    assert all(len(t.name) <= 64 for t in first)


@pytest.mark.parametrize("replacement,reason", [
    (PermissionError("secret credential should not leak"), "access_denied"),
    ("changed", "connection_changed")])
def test_call_reauthorizes_and_refuses_changed_connection(mcp, resources, connection, replacement, reason):
    docs, _ = resources
    docs[REF], log = connection
    tools, _ = build(mcp, [select()])
    if replacement == "changed":
        replacement = copy.deepcopy(docs[REF])
        replacement["timeout_seconds"] = 4
    docs[REF] = replacement
    with pytest.raises(ValueError, match=reason) as error:
        tools[0].handler({"value": "no"})
    assert "secret credential" not in str(error.value)
    assert not any(m.get("method") == "tools/call" for m in messages(log))


def test_argument_validation_prevents_identity_override(mcp, resources, connection):
    docs, _ = resources
    docs[REF], log = connection
    tools, _ = build(mcp, [select()])
    with pytest.raises(ValueError, match="invalid_arguments"):
        tools[0].handler({"value": "ok", "caller": "resident_other"})
    assert not any(m.get("method") == "tools/call" for m in messages(log))


def test_disabled_empty_and_missing_selection(mcp, resources, connection):
    docs, reads = resources
    docs[REF], log = connection
    tools, obs = build(mcp, [select(enabled=False, required=False)])
    assert tools == () and obs[0]["status"] == "disabled" and reads == []
    tools, obs = build(mcp, [select(tools=[])])
    assert tools == () and reads == []
    tools, obs = build(mcp, [select(tools=["absent"], required=False)])
    assert tools == () and obs[0]["status"] == "missing"
    with pytest.raises(ValueError, match="required.*tool_missing"):
        build(mcp, [select(tools=["absent"])])


@pytest.mark.parametrize("mode,reason", [("version", "unsupported_protocol"),
    ("schema", "unsupported_schema_ref"), ("duplicate", "duplicate_tool"),
    ("pages", "invalid_cursor")])
def test_discovery_refuses_unsupported_or_ambiguous_server(mcp, resources, connection, mode, reason):
    docs, _ = resources
    docs[REF], _ = connection
    docs[REF]["transport"]["args"][-1] = mode
    tools, obs = build(mcp, [select(required=False)])
    assert tools == () and obs[0]["status"] == "refused"
    assert obs[0]["reason"] == reason


def test_timeout_and_cancel_release_stdio_process(mcp, resources, connection):
    docs, _ = resources
    docs[REF], log = connection
    docs[REF]["transport"]["args"][-1] = "hang"
    docs[REF]["timeout_seconds"] = 0.3
    start = time.monotonic()
    _, obs = build(mcp, [select(required=False)])
    assert obs[0]["reason"] == "timeout"
    assert time.monotonic() - start < 2
    docs[REF]["timeout_seconds"] = 4
    cancelled = threading.Event()
    timer = threading.Timer(0.15, cancelled.set)
    timer.start()
    try:
        with pytest.raises(InterruptedError):
            build(mcp, [select()], is_cancelled=cancelled.is_set)
    finally:
        timer.cancel()
    # A blocked single-threaded server cannot read the cancellation notification;
    # process reaping is the observable cancellation guarantee for this fixture.
    pid = int(log.with_name(log.name + ".pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.fixture
def http_server():
    record = {"messages": [], "headers": [], "mode": "json", "deletes": 0,
              "waiting": threading.Event(), "release": threading.Event()}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_DELETE(self):
            record["deletes"] += 1
            self.send_response(204)
            self.end_headers()
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            record["messages"].append(request)
            record["headers"].append(dict(self.headers))
            if record["mode"] == "redirect":
                self.send_response(307)
                self.send_header("Location", "http://127.0.0.1:1/leaked")
                self.end_headers()
                return
            if "id" not in request:
                self.send_response(202)
                self.end_headers()
                return
            if record["mode"] == "hang" and request["method"] == "tools/list":
                record["waiting"].set()
                record["release"].wait(3)
            if request["method"] == "initialize":
                result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                          "serverInfo": {"name": "http-fixture", "version": "1"}}
            elif request["method"] == "tools/list":
                result = {"tools": [TOOL]}
            else:
                result = {"content": [{"type": "text", "text": "called"}]}
            response = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result})
            self.send_response(200)
            self.send_header("Mcp-Session-Id", "session-fixture")
            self.send_header("Content-Type", "text/event-stream" if record["mode"] == "sse" else "application/json")
            self.end_headers()
            if record["mode"] == "sse":
                response = ': keepalive\n\nevent: message\ndata: ' + response + '\n\n'
            try:
                self.wfile.write(response.encode())
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield f"http://127.0.0.1:{server.server_port}/mcp", record
    record["release"].set()
    server.shutdown()
    server.server_close()
    worker.join()


@pytest.mark.parametrize("mode", ["json", "sse"])
def test_http_negotiation_credentials_are_scoped_and_not_observed(mcp, resources, http_server, mode):
    docs, reads = resources
    url, record = http_server
    record["mode"] = mode
    docs[REF] = {"schema": "tfi.mcp-connection.v1", "protocol_version": "2025-06-18",
                 "timeout_seconds": 3, "transport": {"type": "streamable_http", "url": url,
                 "headers": {"Authorization": {"ref": SECRET}}}}
    docs[SECRET] = b"Bearer isolated-secret"
    tools, obs = build(mcp, [select()])
    assert "isolated-secret" not in repr(obs) and url not in repr(obs)
    assert json.loads(tools[0].handler({"value": "x"}))["content"][0]["text"] == "called"
    assert all(actor == ACTOR for _, actor in reads)
    assert reads.count((SECRET, ACTOR)) == 2
    assert all(h["Authorization"] == "Bearer isolated-secret" for h in record["headers"])
    for req, headers in zip(record["messages"], record["headers"]):
        if req["method"] != "initialize":
            assert headers["MCP-Protocol-Version"] == "2025-06-18"
            assert headers["Mcp-Session-Id"] == "session-fixture"
    assert record["deletes"] == 2


def test_http_redirect_refused_without_following(mcp, resources, http_server):
    docs, _ = resources
    url, record = http_server
    record["mode"] = "redirect"
    docs[REF] = {"schema": "tfi.mcp-connection.v1", "protocol_version": "2025-06-18",
                 "transport": {"type": "streamable_http", "url": url}}
    _, obs = build(mcp, [select(required=False)])
    assert obs[0]["reason"] == "http_redirect_refused"
    assert len(record["messages"]) == 1


def test_credentials_and_extra_connection_fields_are_rejected(mcp, resources, connection):
    docs, _ = resources
    docs[REF], _ = connection
    docs[REF]["transport"]["env"] = {"API_TOKEN": "plaintext-secret"}
    _, obs = build(mcp, [select(required=False)])
    assert obs[0]["status"] == "refused" and "plaintext-secret" not in repr(obs)
    docs[REF]["transport"]["env"] = {}
    docs[REF]["prompts"] = ["auto-inject"]
    _, obs = build(mcp, [select(required=False)])
    assert obs[0]["reason"] == "invalid_connection"


def test_required_connection_with_no_tools_fails(mcp, resources, connection):
    docs, _ = resources
    docs[REF], _ = connection
    docs[REF]["transport"]["args"][-1] = "empty"
    with pytest.raises(ValueError, match="required.*tool_missing"):
        build(mcp, [select()])


@pytest.mark.parametrize("extra", [{}, {"tools": []}])
def test_omitted_tools_select_nothing_without_discovery(mcp, resources, connection, extra):
    docs, reads = resources
    docs[REF], log = connection
    tools, obs = build(mcp, [{"ref": REF, "required": True, **extra}])
    assert tools == () and reads == [] and not log.exists()
    assert obs[0]["status"] == "unselected" and obs[0]["observation"] == "unknown"


def test_stdio_environment_does_not_inherit_unselected_values(mcp, resources, connection, monkeypatch):
    docs, _ = resources
    docs[REF], _ = connection
    docs[REF]["transport"]["args"][-1] = "env"
    docs[REF]["transport"]["env_allowlist"] = ["MCP_ALLOWED"]
    monkeypatch.setenv("MCP_ALLOWED", "selected")
    monkeypatch.setenv("MCP_DENIED", "must-not-cross")
    tools, _ = build(mcp, [select()])
    observed = json.loads(tools[0].description)
    assert observed == {"MCP_ALLOWED": "selected", "MCP_DENIED": None, "MCP_SECRET": None}


def test_tool_definition_change_prevents_call(mcp, resources, connection):
    docs, _ = resources
    docs[REF], log = connection
    docs[REF]["transport"]["args"][-1] = "change"
    tools, _ = build(mcp, [select()])
    log.with_name(log.name + ".changed").touch()
    with pytest.raises(ValueError, match="tool_changed"):
        tools[0].handler({"value": "no"})
    assert not any(m.get("method") == "tools/call" for m in messages(log))


def test_server_echoed_credential_cannot_enter_tool_schema(mcp, resources, connection):
    docs, _ = resources
    docs[REF], _ = connection
    docs[REF]["transport"]["args"][-1] = "env"
    docs[REF]["transport"]["env"] = {"MCP_SECRET": {"ref": SECRET}}
    docs[SECRET] = b"do-not-expose-credential"
    tools, obs = build(mcp, [select(required=False)])
    assert tools == () and obs[0]["reason"] == "credential_exposure"
    assert "do-not-expose-credential" not in repr(obs)


def test_server_tool_error_is_preserved_without_retry(mcp, resources, connection):
    docs, _ = resources
    docs[REF], log = connection
    docs[REF]["transport"]["args"][-1] = "toolerror"
    tools, _ = build(mcp, [select()])
    assert json.loads(tools[0].handler({"value": "business failure"}))["isError"] is True
    assert sum(m.get("method") == "tools/call" for m in messages(log)) == 1


def test_sync_handler_can_run_inside_provider_event_loop(mcp, resources, connection):
    import asyncio
    docs, _ = resources
    docs[REF], _ = connection
    async def run():
        tools, _ = build(mcp, [select()])
        return json.loads(tools[0].handler({"value": "loop"}))
    assert asyncio.run(run())["content"][0]["text"] == "loop"


def test_two_residents_never_share_http_credentials(mcp, monkeypatch, http_server):
    url, record = http_server
    reads = []
    class Service:
        def read_content(self, ref, *, actor, max_bytes):
            reads.append((ref, actor))
            if ref == SECRET:
                return ("Bearer token-for-" + actor).encode(), "text/plain", {}
            doc = {"schema": "tfi.mcp-connection.v1", "protocol_version": "2025-06-18",
                   "transport": {"type": "streamable_http", "url": url,
                   "headers": {"Authorization": {"ref": SECRET}}}}
            return json.dumps(doc).encode(), "application/json", {}
    monkeypatch.setattr(mcp, "get_resource_service", Service)
    one, _ = build(mcp, [select()])
    two, _ = mcp.materialize_mcp_tools([select()], caller="resident_other", execution_id="execution_two")
    assert one[0].name == two[0].name  # Stable alias, different captured authority.
    one[0].handler({"value": "one"})
    two[0].handler({"value": "two"})
    tokens = [header["Authorization"] for message, header in zip(record["messages"], record["headers"])
              if message["method"] == "tools/call"]
    assert tokens == ["Bearer token-for-" + ACTOR, "Bearer token-for-resident_other"]


def test_stdio_descendant_cannot_outlive_discovery(mcp, resources, connection):
    import signal
    from pathlib import Path
    docs, _ = resources
    docs[REF], log = connection
    docs[REF]["transport"]["args"][-1] = "descendant"
    build(mcp, [select()])
    pid = int(log.with_name(log.name + ".child").read_text())
    status = Path(f"/proc/{pid}/stat")
    try:
        for _ in range(25):
            if not status.exists() or status.read_text().split()[2] == "Z":
                break
            time.sleep(0.02)
        assert not status.exists() or status.read_text().split()[2] == "Z", "MCP child still running after discovery"
    finally:
        if status.exists():
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_http_cancel_sends_notification_and_closes_session(mcp, resources, http_server):
    docs, _ = resources
    url, record = http_server
    record["mode"] = "hang"
    docs[REF] = {"schema": "tfi.mcp-connection.v1", "protocol_version": "2025-06-18",
                 "timeout_seconds": 3, "transport": {"type": "streamable_http", "url": url}}
    with pytest.raises(InterruptedError):
        build(mcp, [select()], is_cancelled=record["waiting"].is_set)
    assert any(m.get("method") == "notifications/cancelled" for m in record["messages"])
    assert record["deletes"] == 1


def test_unknown_json_schema_dialect_refused(mcp, resources, connection):
    # Definitions may not silently claim support for a dialect the validator lacks.
    with pytest.raises(ValueError, match="unsupported_schema_dialect"):
        mcp._schema({"type": "object", "$schema": "https://invalid.test/new-dialect"})


def frozen_digest(value):
    import hashlib
    canonical = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def frozen_dependency(document, tool=TOOL):
    return {REF: {"connection_digest": frozen_digest(document), "tools": {tool["name"]: frozen_digest(tool)}}}


def restore(mcp, items, expected):
    return build(mcp, items, expected_dependencies=expected)


@pytest.mark.parametrize("required", [True, False])
def test_recovery_connection_change_fails_before_connect(mcp, resources, connection, required):
    docs, reads = resources
    docs[REF], log_a = connection
    build(mcp, [select(required=required)])
    expected = frozen_dependency(docs[REF])
    before = list(messages(log_a))
    changed = copy.deepcopy(docs[REF])
    log_b = log_a.with_name("endpoint-b.jsonl")
    changed["transport"]["args"][1] = str(log_b)  # Same server and tools, different connection.
    docs[REF] = changed
    with pytest.raises(ValueError, match="recovery_connection_changed"):
        restore(mcp, [select(required=required)], expected)
    assert not log_b.exists() and messages(log_a) == before
    assert reads[-1] == (REF, ACTOR)


@pytest.mark.parametrize("changed_field", ["outputSchema", "annotations"])
def test_recovery_full_tool_definition_change_fails(mcp, resources, connection, changed_field):
    docs, _ = resources
    docs[REF], log = connection
    docs[REF]["transport"]["args"][-1] = "contract"
    tool = {**TOOL, "outputSchema": {"type": "object", "properties": {"value": {"type": "string"}}},
            "annotations": {"readOnlyHint": True, "destructiveHint": False}}
    definition = log.with_name(log.name + ".tool.json")
    definition.write_text(json.dumps(tool))
    build(mcp, [select(required=False)])
    expected = frozen_dependency(docs[REF], tool)
    if changed_field == "outputSchema":
        tool["outputSchema"]["properties"]["value"]["type"] = "integer"
    else:
        tool["annotations"]["destructiveHint"] = True
    definition.write_text(json.dumps(tool))
    with pytest.raises(ValueError, match="recovery_tool_changed"):
        restore(mcp, [select(required=False)], expected)
    assert not any(msg["method"] == "tools/call" for msg in messages(log))


def test_recovery_observation_digests_allow_canonical_equivalent_restore(mcp, resources, connection):
    docs, reads = resources
    docs[REF], log = connection
    docs[REF].pop("timeout_seconds")  # Hashes the complete document with default applied.
    docs[REF]["transport"]["args"][-1] = "contract"
    tool = {**TOOL, "outputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True},
            "_meta": {"revision": "fixture"}}
    definition = log.with_name(log.name + ".tool.json")
    definition.write_text(json.dumps(tool))
    initial, observations = build(mcp, [select(required=False)])
    observation = observations[0]
    assert observation["connection_digest"] == frozen_digest({**docs[REF], "timeout_seconds": 30})
    assert observation["tool_digest"] == frozen_digest(tool)
    assert str(log) not in json.dumps(observations)
    expected = {REF: {"connection_digest": observation["connection_digest"],
                      "tools": {observation["local_name"]: observation["tool_digest"]}}}
    saved_expected = copy.deepcopy(expected)
    # Equivalent JSON ordering/spacing and an explicit default do not change the contract.
    docs[REF] = {"timeout_seconds": 30, **dict(reversed(list(docs[REF].items())))}
    definition.write_text(json.dumps(dict(reversed(list(tool.items()))), indent=2))
    restored, recovered_observations = restore(mcp, [select(required=False)], expected)
    assert observations == recovered_observations
    assert initial[0].name == restored[0].name and expected == saved_expected
    assert not any(msg["method"] == "tools/call" for msg in messages(log))
    assert json.loads(restored[0].handler({"value": "resumed"}))["content"][0]["text"] == "resumed"
    assert all(actor == ACTOR for _, actor in reads)
    # Recovery still creates a handler that revalidates before each call.
    tool["annotations"]["readOnlyHint"] = False
    definition.write_text(json.dumps(tool))
    with pytest.raises(ValueError, match="tool_changed"):
        restored[0].handler({"value": "must not run"})
    assert sum(msg["method"] == "tools/call" for msg in messages(log)) == 1


@pytest.mark.parametrize("expected,reason", [
    ({}, "recovery_dependency_missing"),
    ({REF: {"connection_digest": "0" * 64, "tools": {}}}, "recovery_dependency_missing"),
    ({REF: {"connection_digest": "invalid", "tools": {"echo": "0" * 64}}}, "invalid_recovery_dependencies"),
    ({REF: {"connection_digest": "0" * 64, "tools": {"echo": True}}}, "invalid_recovery_dependencies"),
    ({REF: []}, "invalid_recovery_dependencies"),
    ([], "invalid_recovery_dependencies"),
])
def test_recovery_missing_or_invalid_snapshot_never_discovers(mcp, resources, connection, expected, reason):
    docs, reads = resources
    docs[REF], log = connection
    with pytest.raises(ValueError, match=reason):
        restore(mcp, [select(required=False)], expected)
    assert not log.exists() and reads == []


@pytest.mark.parametrize("extra", [{}, {"tools": []}, {"enabled": False, "tools": ["echo"]}])
def test_recovery_unselected_does_not_require_or_read_dependencies(mcp, resources, connection, extra):
    docs, reads = resources
    docs[REF], log = connection
    tools, _ = restore(mcp, [{"ref": REF, "required": False, **extra}], {})
    assert tools == () and reads == [] and not log.exists()


@pytest.mark.parametrize("reason", ["access_denied", "tool_missing"])
def test_recovery_optional_dependency_loss_is_fatal(mcp, resources, connection, reason):
    docs, _ = resources
    docs[REF], log = connection
    build(mcp, [select(required=False)])
    expected = frozen_dependency(docs[REF])
    before = list(messages(log))
    if reason == "access_denied":
        docs[REF] = PermissionError("must not expose permission details")
        error = "access_denied"
    else:
        # Same document, same endpoint, selected tool removed from remote directory.
        script = log.with_name("server.py")
        script.write_text(SERVER.replace('if mode == "empty":', 'if mode == "normal":'))
        error = "recovery_tool_changed"
    with pytest.raises(ValueError, match=error):
        restore(mcp, [select(required=False)], expected)
    if reason == "access_denied":
        assert messages(log) == before
    assert not any(msg["method"] == "tools/call" for msg in messages(log))


def test_recovery_credentials_rotate_without_changing_reference_digest(mcp, resources, http_server):
    docs, reads = resources
    url, record = http_server
    docs[REF] = {"schema": "tfi.mcp-connection.v1", "protocol_version": "2025-06-18",
                 "timeout_seconds": 3, "transport": {"type": "streamable_http", "url": url,
                 "headers": {"Authorization": {"ref": SECRET}}}}
    docs[SECRET] = b"Bearer first-credential"
    _, observations = build(mcp, [select()])
    obs = observations[0]
    assert obs["connection_digest"] == frozen_digest(docs[REF])
    expected = {REF: {"connection_digest": obs["connection_digest"], "tools": {"echo": obs["tool_digest"]}}}
    docs[SECRET] = b"Bearer rotated-credential"
    before = len(record["headers"])
    tools, restored_obs = restore(mcp, [select()], expected)
    assert observations == restored_obs
    assert all(headers["Authorization"] == "Bearer rotated-credential" for headers in record["headers"][before:])
    assert json.loads(tools[0].handler({"value": "continue"}))["content"][0]["text"] == "called"
    assert reads.count((SECRET, ACTOR)) == 3
    assert "credential" not in json.dumps(observations)


def test_recovery_http_connection_change_rejected_before_credentials_or_connect(mcp, resources, http_server):
    docs, reads = resources
    url, record = http_server
    docs[REF] = {"schema": "tfi.mcp-connection.v1", "protocol_version": "2025-06-18",
                 "timeout_seconds": 3, "transport": {"type": "streamable_http", "url": url,
                 "headers": {"Authorization": {"ref": SECRET}}}}
    docs[SECRET] = b"Bearer original"
    _, observations = build(mcp, [select(required=False)])
    obs = observations[0]
    expected = {REF: {"connection_digest": obs["connection_digest"], "tools": {"echo": obs["tool_digest"]}}}
    docs[REF]["transport"]["url"] += "/connection-b"
    docs[SECRET] = PermissionError("credentials must not be read on rejected connection")
    before_messages, before_reads = len(record["messages"]), len(reads)
    with pytest.raises(ValueError, match="recovery_connection_changed"):
        restore(mcp, [select(required=False)], expected)
    assert len(record["messages"]) == before_messages
    assert reads[before_reads:] == [(REF, ACTOR)]
