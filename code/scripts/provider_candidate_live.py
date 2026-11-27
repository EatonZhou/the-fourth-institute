"""Create or resume an isolated provider session for the one-turn upgrade check."""
import asyncio
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from urllib.request import Request, urlopen

from provider_candidate_probe import check, codex_server, http_server


def codex(root, prompt, model, sid):
    from management_portal.execution.provider_updates import npm_binary
    binary = root if root.is_file() else (root / "portal-codex" if sys.platform == "linux" else npm_binary(root, "codex"))
    if not sid:
        with codex_server(root) as (_, request):
            sid = request("thread/start", {"cwd": os.getcwd(), "sandbox": "read-only"})["thread"]["id"]
            request("thread/inject_items", {"threadId": sid, "items": [{"type": "message", "role": "user",
                    "content": [{"type": "input_text", "text": prompt}]}]})
            return sid
    output = Path.cwd() / "answer"
    command = [str(binary), "exec", "--sandbox", "read-only", "resume", "--skip-git-repo-check",
               "-c", 'model_reasoning_effort="low"', "--output-last-message", str(output)]
    subprocess.run(command + (["-m", model] if model else []) + [sid, prompt], check=True,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    return output.read_text()


async def claude(root, prompt, model, sid):
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage
    options = ClaudeAgentOptions(cwd=os.getcwd(), tools=[], setting_sources=[], max_turns=1,
                                 model=model or None, resume=sid)
    async with ClaudeSDKClient(options) as client:
        await client.query(prompt)
        async for msg in client.receive_response():
            if isinstance(msg, ResultMessage):
                check(not msg.is_error, "Claude upstream failure")
                check(msg.session_id == sid, "Claude resumed session identity")
                return msg.result


def antigravity(root, prompt, model, sid):
    from management_common.local_ipc import connect_local, start_local_server
    from management_portal import antigravity_runtime as runtime
    from management_portal.antigravity_account_server import AccountServer, FRAME_LIMIT
    from management_portal.antigravity_client import AccountAcpClient
    from management_portal.execution import antigravity_cli
    from management_portal.execution.provider_session_features import MARKER
    env = {**os.environ, **runtime.credential_environment(), "PORTAL_SHARED_ACP": "1"}
    env.pop("PORTAL_INSTANCE_ROOT", None)
    antigravity_cli.write_portal_agent(os.getcwd())
    command = runtime._cli_args(runtime.binary_path(root), env) + [
        "--include-default-customizations=false", "--agent", antigravity_cli.PORTAL_AGENT]
    text = []
    def update(params):
        item = params.get("update", {})
        if item.get("sessionUpdate") == "agent_message_chunk":
            text.append(item.get("content", {}).get("text", ""))
    socket_path = Path.cwd() / "account.sock"
    params = {"cwd": os.getcwd(), "mcpServers": []}

    def exercise():
        with AccountAcpClient(connect_local(socket_path), on_update=update) as client:
            caps = client.initialize()["agentCapabilities"]
            if not sid:
                created = client.call("session/new", params)["sessionId"]
                check(bool(created), "Antigravity seed session")
                client.call("session/close", {"sessionId": created})
                return created
            resume = (root / MARKER).is_file() and "resume" in caps.get("sessionCapabilities", {})
            method = "session/resume" if resume else "session/load"
            session = client.call(method, {**params, "sessionId": sid})
            check(session.get("sessionId", sid) == sid, "Antigravity handoff identity")
            text.clear()
            result = client.call("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": prompt}]})
            check(result["stopReason"] == "end_turn", "Antigravity terminal result")
            client.call("session/close", {"sessionId": sid})
        with AccountAcpClient(connect_local(socket_path)) as peer:
            peer.initialize()
            session = peer.call(method, {**params, "sessionId": sid})
            check(any(item.get("category") == "model" for item in session.get("configOptions", [])),
                  "Antigravity restored session configuration")
            peer.call("session/close", {"sessionId": sid})

    async def run():
        host = AccountServer(command, env, Path.cwd())
        async with await start_local_server(host.handle, socket_path, limit=FRAME_LIMIT):
            try:
                return await asyncio.to_thread(exercise)
            finally:
                await host.close()

    created = asyncio.run(run())
    return "".join(text) if sid else created


def kimi(root, prompt, model, sid):
    from management_portal.kimi_code_client import runtime_client
    from management_portal.execution.provider_updates import npm_binary
    with http_server(npm_binary(root, "kimi"), ["web", "--no-open", "--dangerous-bypass-auth", "--host",
                                               "127.0.0.1"], "/api/v1/sessions") as origin:
        client = runtime_client(root, origin + "/api/v1", home=os.environ["KIMI_CODE_HOME"])
        if not sid:
            return client.create_session(cwd=os.getcwd(), title=prompt, tools=[], model=model)["id"]
        status = client.get_status(sid)
        check(status["id"] == sid, "Kimi restored session identity")
        prompt = f"Reply with exactly {status['title']}. Do not use tools."
        result = client.run_turn(session_id=sid, prompt=prompt, permission_mode="manual",
                                 deny_all_tools=True, model=model)
        check(result.session_id == sid, "Kimi session identity")
        return result.response


def opencode(root, prompt, model, sid):
    from management_portal.execution.provider_updates import npm_binary
    with http_server(npm_binary(root, "opencode"), ["serve", "--hostname", "127.0.0.1"], "/global/health") as origin:
        def post(path, body):
            request = Request(origin + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            with urlopen(request) as response:
                return json.load(response)
        selection = {"providerID": os.environ["TFI_PROBE_SERVICE"], "modelID": model}
        if not sid:
            created = post("/session", {"title": "Provider acceptance"})["id"]
            post(f"/session/{created}/message", {"noReply": True, "model": selection, "parts": [{"type": "text", "text": prompt}]})
            return created
        with urlopen(origin + "/session/" + sid) as response:
            check(json.load(response)["id"] == sid, "OpenCode restored session")
        result = post(f"/session/{sid}/message", {"model": selection,
                                                  "parts": [{"type": "text", "text": prompt}]})
        check(not result["info"].get("error"), "OpenCode upstream failure")
        return "".join(p["text"] for p in result["parts"] if p.get("type") == "text")


def dsh(root, prompt, model, sid):
    from management_portal import dsh_client as module
    module.RUNTIME = root
    module.STATE, module.SOCKETS = Path.cwd() / "dsh", Path.cwd() / "s"
    route = os.environ["TFI_PROBE_ROUTE"]
    row = json.loads((Path.home() / "route.json").read_text())
    module.dsh_provider_catalog.profile = lambda _: row
    module.resident_process_environment = lambda _: (dict(os.environ), {})
    client = module.DshClient()
    check(sid == module.session_id_for("probe"), "DSH restored session")
    params = dict(session_id=sid, session_key="probe", turn_id=secrets.token_hex(8), prompt=prompt, model=model,
                  provider_profile=route, effort="", cwd=os.getcwd(), is_owner=True,
                  permission_profile="owner", resident_environment={}, allow_subagents=False,
                  tools=[], system_prompt_append="")
    transport = client._start(sid, params)
    try:
        return transport._request("turn/run", params)["response"]
    finally:
        client.close_session("", sid)


if __name__ == "__main__":
    provider, directory = sys.argv[1:]
    handlers = {"antigravity": antigravity, "codex": codex, "kimi": kimi, "opencode": opencode, "dsh": dsh}
    model = os.getenv("TFI_PROBE_MODEL", "")
    record = Path("sdk-session.json")
    if os.getenv("TFI_HANDOFF_SEED") == "1":
        marker = "probe-" + secrets.token_hex(8)
        sid = handlers[provider](Path(directory), marker, model, "")
        check(isinstance(sid, str) and sid, "Missing baseline session")
        record.write_text(json.dumps({"session": sid, "answer": marker}))
        sys.exit(0)
    saved = json.loads(record.read_text())
    record.unlink()
    sid, marker = saved["session"], saved["answer"]
    check(isinstance(sid, str) and sid and marker, "Missing baseline handoff")
    prompt = ("Return exactly the probe- code from our conversation. Output only that code. Do not use tools."
              if provider not in {"antigravity", "kimi"} else f"Reply with exactly {marker}. Do not use tools.")
    args = Path(directory), prompt, model, sid
    result = asyncio.run(claude(*args)) if provider == "claude" else handlers[provider](*args)
    check(isinstance(result, str) and result.strip() == marker, "Missing expected terminal response")
