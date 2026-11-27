"""Probe one explicit candidate, launched by provider_candidate.validate in a private HOME."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE))

from provider_probe_model import loopback_model, stalled_request
from management_common.process_runtime import spawn_process, terminate_process_tree


def check(condition, name):
    if not condition:
        raise ValueError(name)


@contextmanager
def process(command, **kwargs):
    child = spawn_process(command, stderr=subprocess.DEVNULL, **kwargs)
    try:
        yield child
    finally:
        terminate_process_tree(child, force=True)


RECEIPT = {"name": "receipt", "description": "Get one of the two fixture receipts",
           "inputSchema": {"type": "object", "properties": {"index": {"type": "integer", "enum": [0, 1]}, "path": {"type": "string"}},
                           "required": ["index", "path"]}}


@contextmanager
def codex_server(root):
    from management_portal.execution.provider_updates import npm_binary
    binary = root
    if root.is_dir():
        binary = root / "portal-codex" if sys.platform == "linux" else npm_binary(root, "codex")
    with process([str(binary), "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as child:
        count = 0
        def request(method, params, *, error=False):
            nonlocal count
            count += 1
            child.stdin.write(json.dumps({"method": method, "id": count, "params": params}) + "\n")
            child.stdin.flush()
            for line in child.stdout:
                reply = json.loads(line)
                if reply.get("id") == count:
                    check(("error" in reply) == error, method)
                    return reply.get("result")
            raise EOFError(method)
        request("initialize", {"clientInfo": {"name": "portal", "version": "1"},
                               "capabilities": {"experimentalApi": True}})
        child.stdin.write('{"method":"initialized"}\n')
        child.stdin.flush()
        yield child, request


def codex(root, model):
    Path(os.environ["CODEX_HOME"], "config.toml").write_text(
        'model="gpt-5.4"\nmodel_provider="probe"\n[model_providers.probe]\n'
        f'name="probe"\nbase_url="{model.url}/v1"\nwire_api="responses"\n')
    with codex_server(root) as (child, request):
        check(request("model/list", {})["data"], "model/list")
        options = {"cwd": os.getcwd(), "approvalPolicy": "never", "sandbox": "read-only"}
        if not model.previous_id:
            options["dynamicTools"] = [RECEIPT]
        if model.previous_id:
            options["threadId"] = model.previous_id
        thread = request("thread/resume" if model.previous_id else "thread/start", options)["thread"]["id"]
        check(bool(thread), "thread identity")
        if model.previous_id:
            with stalled_request(model):
                interrupted = request("turn/start", {"threadId": thread, "input": [{"type": "text", "text": "Wait"}]})
                check(model.request_started.wait(10), "Codex cancel targets active request")
                request("turn/interrupt", {"threadId": thread, "turnId": interrupted["turn"]["id"]})
        result = request("turn/start", {"threadId": thread, "input": [{"type": "text", "text": "Hello"}]})
        turn = result["turn"]["id"]
        messages, calls = [], []
        for line in child.stdout:
            event = json.loads(line)
            params = event.get("params", {})
            if event.get("method") == "item/tool/call":
                index = params["arguments"]["index"]
                check(params["tool"] == "receipt" and params["turnId"] == turn, "tool identity")
                calls.append(index)
                result = model.dispatch(params["arguments"])
                child.stdin.write(json.dumps({"id": event["id"], "result": {"success": not result["isError"],
                    "contentItems": [{"type": "inputText", "text": result["content"][0]["text"]}]}}) + "\n")
                child.stdin.flush()
            if params.get("turnId") == turn and event.get("method") == "item/completed":
                messages.append(params["item"].get("text"))
            if event.get("method") == "turn/completed" and params["turn"]["id"] == turn:
                check(params["turn"]["status"] == "completed", "turn failed")
                break
        check(sorted(calls) == [0, 1], "Codex tool callbacks")
        check(model.answer in messages, "Codex terminal message")
        request("thread/read", {"threadId": "invalid"}, error=True)
        check(request("thread/read", {"threadId": thread})["thread"]["id"] == thread, "thread/read")
        if model.previous_id and ((root if root.is_dir() else root.resolve().parent) / "PORTAL_SESSION_CONTROLS").is_file():
            request("thread/name/set", {"threadId": thread, "name": "Portal probe"})
            request("thread/archive", {"threadId": thread})
            check(request("thread/unarchive", {"threadId": thread})["thread"]["id"] == thread, "Codex unarchive")
        return thread


async def claude(root, model):
    sys.path.insert(0, str(root))
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
    from management_portal.claude_tool_bridge import ClaudeToolBridge
    bridge = ClaudeToolBridge([RECEIPT])
    calls = []
    async def dispatch(name, arguments):
        check(name == "receipt", "tool name")
        index = arguments["index"]
        calls.append(index)
        return model.dispatch(arguments)
    bridge.dispatch = dispatch
    os.environ.update(ANTHROPIC_BASE_URL=model.url, ANTHROPIC_API_KEY="fixture",
                      CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", CLAUDE_CODE_DISABLE_1M_CONTEXT="1")
    cli_env = {}
    if model.previous_id:
        # Exercise the receiving daemon's environment contract on the candidate.
        # The old daemon only seeds the session and may predate workspace fields.
        from management_portal import claude_code_daemon as daemon
        check(Path(daemon.__file__).resolve().is_relative_to(root.resolve()), "packaged Claude daemon")
        workspace = os.getcwd()
        resident_env = {
            "PORTAL_RESIDENT_ID": "resident_aabbccddeeff", "PORTAL_LINEAGE_ID": "lineage_aabbccddeeff",
            "PORTAL_RESIDENT_WORKSPACE": workspace, "PORTAL_LINEAGE_WORKSPACE": workspace,
            "PORTAL_PROJECT_WORKSPACE": workspace, "PORTAL_WORKSPACE_AABBCCDDEEFF": workspace,
            "PORTAL_CONTEXT_PROBE_LABEL": "candidate 交接", "PORTAL_TEAM_WORKSPACE": workspace,
        }
        signature = daemon.ClaudeCodeDaemon._signature({"resident_environment": resident_env}, "")
        check(json.dumps(resident_env, sort_keys=True) in signature, "Claude session environment")
        cli_env = daemon._cli_environment({"resident_environment": resident_env}, {},
                                           Path(os.environ["CLAUDE_CONFIG_DIR"]), 0)
        check(all(cli_env.get(k) == v for k, v in resident_env.items()), "Claude CLI workspace environment")
    options = ClaudeAgentOptions(cwd=os.getcwd(), resume=model.previous_id or None, setting_sources=[], permission_mode="default",
                                 mcp_servers=bridge.servers, allowed_tools=bridge.allowed_tools, env=cli_env)
    async with ClaudeSDKClient(options) as client:
        info = await client.get_server_info()
        check(isinstance(info, dict) and bool(info), "SDK initialization")
        for _ in range(30):
            status = await client.get_mcp_status()
            if any(server["name"] == "portal" and server["status"] == "connected"
                   for server in status["mcpServers"]):
                break
            await asyncio.sleep(.1)
        check(any(server["name"] == "portal" and server["status"] == "connected"
                  for server in status["mcpServers"]), "Portal MCP registration")
        await client.set_permission_mode("default")
        if model.previous_id:
            with stalled_request(model):
                await client.query("Wait")
                check(await asyncio.to_thread(model.request_started.wait, 10), "Claude cancel targets active request")
                await client.interrupt()
                async for _ in client.receive_response():
                    pass
        await client.query("Call the receipt tool.")
        from claude_agent_sdk import ResultMessage
        results = [msg async for msg in client.receive_response() if isinstance(msg, ResultMessage)]
        check(len(results) == 1 and results[0].result == model.answer, "Claude terminal result")
        check(sorted(calls) == [0, 1], "Claude tool round trip")
        return results[0].session_id


def antigravity(root):
    from management_portal import antigravity_runtime as runtime
    from management_portal.antigravity_client import AcpClient, AcpError
    env = {**os.environ, **runtime.credential_environment()}
    env.pop("PORTAL_INSTANCE_ROOT", None)
    with AcpClient(runtime._cli_args(runtime.binary_path(root), env), cwd=os.getcwd(), env=env) as client:
        hello = client.initialize()
        check(hello["agentInfo"]["version"] == json.loads((root / "distribution.json").read_text())["version"],
              "candidate identity")
        caps = hello["agentCapabilities"]
        check(caps["loadSession"] and caps["mcpCapabilities"]["http"], "session/load and HTTP MCP")
        try:
            client.call("portal/nonexistent", {})
        except AcpError as error:
            check(error.code == -32601, "JSON-RPC method-not-found")
        else:
            raise ValueError("unknown ACP method accepted")

    if os.environ.get("TFI_PROBE_AUTHENTICATED") == "1":
        from management_portal.antigravity_client import ToolServer
        from management_portal.execution import antigravity_cli
        from management_portal.execution.antigravity_history import _latest_checkpoint

        cwd = str(Path.cwd() / "project")
        if not Path(cwd).exists():
            antigravity_cli.write_portal_agent(cwd)
        record = Path("sdk-session.json")
        saved = json.loads(record.read_text()) if record.exists() else {}
        text, calls = [], []
        receipt = secrets.token_hex(12)
        def update(params):
            item = params.get("update", {})
            if item.get("sessionUpdate") == "agent_message_chunk":
                text.append(item.get("content", {}).get("text", ""))
        def dispatch(call):
            calls.append(call["tool"])
            return {"content": [{"type": "text", "text": receipt}]}
        def permission(method, params):
            if method == "session/request_permission" and antigravity_cli.is_offered_portal_call(
                    params.get("toolCall", {}), {"receipt"}):
                for option in params.get("options", []):
                    if option.get("kind") == "allow_once":
                        return {"outcome": {"outcome": "selected", "optionId": option["optionId"]}}
            return {"outcome": {"outcome": "cancelled"}}
        catalog = [{"name": "receipt", "description": "Get the test receipt",
                    "inputSchema": {"type": "object", "properties": {}}}]
        command = runtime._cli_args(runtime.binary_path(root), env) + [
            "--include-default-customizations=false", "--agent", antigravity_cli.PORTAL_AGENT]
        with ToolServer(catalog, dispatch) as tools, AcpClient(command, env=env, cwd=cwd,
                on_update=update, on_request=permission) as client:
            client.initialize()
            params = {"cwd": cwd, "mcpServers": [tools.descriptor()]}
            if saved:
                params["sessionId"] = saved["session"]
                loaded = client.call("session/load", params)
                sid = loaded.get("sessionId", saved["session"])
                check(sid == saved["session"], "ACP resume identity")
            else:
                sid = client.call("session/new", params)["sessionId"]
            def prompt(message):
                text.clear()
                result = client.call("session/prompt", {"sessionId": sid, "prompt": [
                    {"type": "text", "text": message}]}, timeout=90)
                check(result["stopReason"] == "end_turn", "ACP terminal reply")
                return "".join(text)
            message = "Call receipt once and return its exact value."
            if saved:
                message += " Also return the receipt from the previous turn."
            answer = prompt(message)
            check(receipt in answer and calls == ["receipt"], "ACP tool round trip")
            if saved:
                check(saved["answer"] in answer, "ACP resumed context")
                before = _latest_checkpoint(env, sid)
                prompt("/compact")
                after = _latest_checkpoint(env, sid)
                check(after and (not before or before.ref != after.ref), "ACP compact checkpoint")
            record.write_text(json.dumps({"session": sid, "answer": receipt}))


@contextmanager
def http_server(binary, args, ready_path):
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        port = port_socket.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    with process([str(binary), *args, "--port", str(port)], stdout=subprocess.DEVNULL) as child:
        deadline = time.monotonic() + 35
        while child.poll() is None and time.monotonic() < deadline:
            try:
                with urlopen(origin + ready_path, timeout=1) as response:
                    if response.status == 200:
                        break
            except (URLError, TimeoutError):
                pass
            time.sleep(.1)
        else:
            raise RuntimeError("candidate did not become ready")
        yield origin


def opencode(root, model):
    from management_portal.antigravity_client import ToolServer
    calls = []
    def dispatch(call):
        check(call["tool"] == "receipt", "tool name")
        index = call["arguments"]["index"]
        calls.append(index)
        return model.dispatch(call["arguments"])
    with ToolServer([RECEIPT], dispatch) as tool_server:
        result = opencode_session(root, model, tool_server)
        check(sorted(calls) == [0, 1], "OpenCode tool callbacks")
        return result


def opencode_session(root, model, tools):
    from management_portal.execution.provider_updates import npm_binary
    os.environ.update(OPENCODE_DISABLE_AUTOUPDATE="1", OPENCODE_DISABLE_MODELS_FETCH="1",
                      OPENCODE_DISABLE_SHARE="1", OPENCODE_DISABLE_PROJECT_CONFIG="1")
    os.environ["OPENCODE_CONFIG_CONTENT"] = json.dumps({"permission": {"portal_receipt": "allow"}, "mcp": {
        "portal": {"type": "remote", "url": tools.descriptor()["url"], "oauth": False,
                   "headers": {"Authorization": "Bearer " + tools.token}}},
        "provider": {"probe": {
        "npm": "@ai-sdk/openai-compatible", "name": "probe",
        "options": {"baseURL": model.url + "/v1", "apiKey": "fixture"},
        "models": {"probe": {"name": "probe"}}}}})
    with http_server(npm_binary(root, "opencode"), ["serve", "--hostname", "127.0.0.1"], "/global/health") as origin:
        def request(method, path, body=None):
            data = json.dumps(body).encode() if body is not None else None
            with urlopen(Request(origin + path, method=method, data=data,
                                 headers={"Content-Type": "application/json"}), timeout=10) as response:
                return json.load(response)
        session = model.previous_id or request("POST", "/session", {"title": "Portal SDK check"})["id"]
        check(request("GET", "/session/" + session)["id"] == session, "session identity")
        history = request("GET", "/session/" + session + "/message")
        check(isinstance(history, list) and bool(history) == bool(model.previous_id), "session history")
        check(isinstance(request("GET", "/session/status"), dict), "session/status")
        if model.previous_id:
            with stalled_request(model):
                data = {"model": {"providerID": "probe", "modelID": "probe"},
                        "parts": [{"type": "text", "text": "Wait"}]}
                url = Request(origin + f"/session/{session}/prompt_async", data=json.dumps(data).encode(),
                              headers={"Content-Type": "application/json"})
                with urlopen(url, timeout=10) as response:
                    check(response.status in {200, 204}, "OpenCode prompt accepted")
                check(model.request_started.wait(10), "OpenCode cancel targets active request")
                check(request("POST", f"/session/{session}/abort") is True, "OpenCode abort")
        response = request("POST", f"/session/{session}/message", {
            "model": {"providerID": "probe", "modelID": "probe"},
            "parts": [{"type": "text", "text": "Hello"}]})
        check(any(part.get("text") == model.answer for part in response["parts"]), "OpenCode response")
        if model.previous_id and (root / "PORTAL_SESSION_CONTROLS").is_file():
            child = request("POST", f"/session/{session}/fork", {})["id"]
            messages = request("GET", f"/session/{child}/message")
            # A fork into another Resident must survive import into its separate database.
            export_path = Path.cwd() / "fork-export.json"
            export_path.write_text(json.dumps({"info": request("GET", f"/session/{child}"), "messages": messages}))
            isolated_env = {**os.environ, "OPENCODE_DB": str(Path.cwd() / "fork-probe.db")}
            binary = str(npm_binary(root, "opencode"))
            subprocess.run([binary, "import", str(export_path)], check=True, capture_output=True, env=isolated_env, timeout=60)
            exported = subprocess.run([binary, "export", child], check=True, capture_output=True, env=isolated_env, timeout=60)
            restored = json.loads(exported.stdout)
            check(restored["info"]["id"] == child and len(restored["messages"]) == len(messages), "OpenCode fork import")
            mid = next(row["info"]["id"] for row in messages if row["info"]["role"] == "user")
            check(isinstance(request("GET", f"/session/{child}/diff"), list), "OpenCode diff")
            check(request("POST", f"/session/{child}/revert", {"messageID": mid})["revert"]["messageID"] == mid,
                  "OpenCode revert")
            check(not request("POST", f"/session/{child}/unrevert", {}).get("revert"), "OpenCode unrevert")
        check(request("POST", "/session/" + session + "/abort") is True, "abort")
        return session


def kimi(root, model):
    from management_portal.antigravity_client import ToolServer
    with ToolServer([RECEIPT], lambda call: model.dispatch(call["arguments"])) as server:
        descriptor = server.descriptor()
        headers = {item["name"]: item["value"] for item in descriptor["headers"]}
        config = {"mcpServers": {"portal": {"url": descriptor["url"], "headers": headers, "transport": "http"}}}
        Path(os.environ["KIMI_CODE_HOME"], "mcp.json").write_text(json.dumps(config))
        return kimi_session(root, model)


def kimi_session(root, model):
    from management_portal.execution.provider_updates import npm_binary
    from management_portal.kimi_account_runtime import _server_busy
    from management_portal.kimi_live_events import wait_for_idle
    from management_portal.kimi_code_client import runtime_client
    from management_portal.kimi_code_server import DEFAULT_CONFIG_TEMPLATE
    template = DEFAULT_CONFIG_TEMPLATE.read_text().replace("__PORTAL_KIMI_DEFAULT_MODEL__", '"kimi-k3"')
    template = template.replace("__PORTAL_KIMI_API_KEY__", '"probe"').replace(
        "__PORTAL_KIMI_BASE_URL__", json.dumps(model.url + "/v1"))
    Path(os.environ["KIMI_CODE_HOME"], "config.toml").write_text(template)
    with http_server(npm_binary(root, "kimi"), ["web", "--no-open", "--dangerous-bypass-auth",
                                                "--host", "127.0.0.1"], "/api/v1/sessions") as origin:
        client = runtime_client(root, origin + "/api/v1", home=os.environ["KIMI_CODE_HOME"])
        if model.previous_id:
            sid = client.create_session(cwd=os.getcwd(), title="Restricted SDK check", tools=[], model="kimi-k3")["id"]
            check(bool(sid) and bool(client.get_status(sid)), "session created")
            client.update_session_profile(sid, tools=[], model="kimi-k3")
            client.require_deny_all_tools(sid)
            check(bool(client.get_status(sid)), "session status")
            with stalled_request(model):
                submitted = client._request("POST", f"sessions/{sid}/prompts", {
                    "content": [{"type": "text", "text": "Wait"}], "model": "kimi-k3",
                    "permission_mode": "manual", "deny_all_tools": True})
                check(bool(submitted["prompt_id"]), "Kimi prompt accepted")
                check(model.request_started.wait(10), "Kimi cancel targets active request")
                # The first sample follows the WebSocket ACK. Abort at that point
                # so the probe requires a native wakeup even with no attached Worker.
                samples = []
                def busy():
                    value = _server_busy(client)
                    if not samples:
                        check(value, "Kimi native work is active")
                        client.abort_session(sid)
                    samples.append(value)
                    return value
                check(wait_for_idle(client.base_url, busy, time.monotonic() + 12), "Kimi native idle event")
            result = client.run_turn(session_id=sid, prompt="Hello", permission_mode="manual",
                                     deny_all_tools=True, model="kimi-k3")
            check(result.session_id == sid and result.response == model.answer, "Kimi turn result")
            check(bool(result.tool_policy_receipt), "Kimi deny-all receipt")
            check(model.paths[0].read_text() == model.receipts[0], "Kimi deny-all suppressed tools")
            # Kimi fixes deny-all for the lifetime of a restricted session.
        sid = model.previous_id or client.create_session(cwd=os.getcwd(), title="SDK tools", model="kimi-k3")["id"]
        names = [tool["name"] for tool in client.session_tools(sid) if tool["name"].endswith("receipt")]
        check(len(names) == 1, "Kimi MCP catalog")
        disabled = [tool["name"] for tool in client.session_tools(sid) if tool["name"] not in names]
        result = client.run_turn(session_id=sid, prompt="Fetch receipts", tools=names,
                                 disabled_tools=disabled, model="kimi-k3")
        check(result.session_id == sid and result.response == model.answer, "Kimi MCP turn")
        return sid


def dsh(root, model):
    from management_portal import dsh_client as module
    from management_portal.native_agent_transport import NativeAgentError
    module.RUNTIME = root
    module.STATE, module.SOCKETS = Path.cwd() / "dsh", Path.cwd() / "s"
    key = Path.cwd() / "probe.key"
    key.write_text("local-contract-fixture")
    row = {"id": "fixture", "kind": "anthropic_messages", "base_url": model.url,
           "api_key_file": str(key), "default_model": "deepseek-v4-flash"}
    module.dsh_provider_catalog.profile = lambda route: row
    module.resident_process_environment = lambda params: (dict(os.environ), {})
    client = module.DshClient()
    sid = module.session_id_for("probe")
    transport = client._start(sid, {"provider_profile": "fixture", "cwd": os.getcwd(), "allow_subagents": False})
    try:
        info = transport._request("health", response_timeout=3)
        check(info["protocol"] == "tfi.native-agent.v1" and info["capabilities"]["session_compact"], "bridge health")
        catalog = [RECEIPT]
        calls, events = [], []
        def dispatch(call):
            check(call["tool"] == "receipt", "tool name")
            index = call["arguments"]["index"]
            calls.append(index)
            return model.dispatch(call["arguments"])
        params = dict(session_id=sid, session_key="probe", turn_id=model.answer, prompt="Call receipt",
                      model="deepseek-v4-flash", provider_profile="fixture", effort="", cwd=os.getcwd(),
                      is_owner=True, permission_profile="owner", resident_environment={},
                      allow_subagents=False, tools=catalog, system_prompt_append="")
        if model.previous_id:
            with ThreadPoolExecutor(max_workers=1) as executor:
                with stalled_request(model):
                    cancel_id = params["turn_id"] + "-cancel"
                    pending = executor.submit(transport._request, "turn/run", {**params, "turn_id": cancel_id})
                    check(model.request_started.wait(10), "DSH cancel targets active request")
                    check(client.interrupt("", sid, turn_id=cancel_id)["interrupted"], "DSH interrupt")
                try:
                    pending.result(timeout=15)
                except NativeAgentError:
                    pass
                else:
                    raise ValueError("interrupted DSH turn completed normally")
        result = transport._request("turn/run", params, event_callback=events.append, tool_callback=dispatch)
        check(result["response"] == model.answer and sorted(calls) == [0, 1], "DSH tool round trip")
        check(sum(event["method"] == "turn/completed" for event in events) == 1, "DSH terminal event")
        if model.previous_id and (root / "PORTAL_SESSION_CONTROLS").is_file():
            def goal(argument):
                return transport._request("session/goal", {**params, "argument": argument})["goal"]
            check(goal("fixture objective")["objective"] == "fixture objective", "DSH create goal")
            check(goal("pause")["phase"] == "paused", "DSH pause goal")
            check(goal("clear") is None, "DSH clear goal")
            fork = client.fork_session(source_session_id=sid, provider_profile="fixture", cwd=os.getcwd(),
                                       fork_key=model.answer, at_seq=0)
            check(fork["session_id"] != sid, "DSH historical fork")
        check(client.interrupt("", sid, turn_id="absent") == {"interrupted": False}, "stale cancel")
        try:
            transport._request("portal/nonexistent", response_timeout=5)
        except NativeAgentError:
            pass
        else:
            raise ValueError("unknown bridge method accepted")
    finally:
        client.close_session("", sid)
    return sid


if __name__ == "__main__":
    provider, directory = sys.argv[1:]
    if provider == "antigravity":
        antigravity(Path(directory))
    else:
        with loopback_model() as model:
            record = Path("sdk-session.json")
            saved = json.loads(record.read_text()) if record.exists() else {}
            model.previous_id = saved.get("session", "")
            model.previous_answer = saved.get("answer", "")
            if provider == "claude":
                session = asyncio.run(claude(Path(directory), model))
            else:
                handlers = dict(codex=codex, kimi=kimi, opencode=opencode, dsh=dsh)
                session = handlers[provider](Path(directory), model)
            check(model.paths[0].read_text() == "received:" + model.receipts[0], "authorized workspace write")
            check(model.paths[1].read_text() == "unchanged", "denied workspace untouched")
            check(model.results_matched, "tool result correlation")
            check(session and (not saved or model.previous_id == session), "resume identity")
            if saved:
                check(model.received_previous_answer, "resumed model context")
            record.write_text(json.dumps({"session": session, "answer": model.answer}))
