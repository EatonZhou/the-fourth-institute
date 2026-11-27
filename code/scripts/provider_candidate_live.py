"""One candidate process, one isolated session and one short upstream response."""
import asyncio
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from urllib.request import Request, urlopen

from provider_candidate_probe import check, http_server


def codex(root, prompt, model):
    from management_portal.execution.provider_updates import npm_binary
    binary = root if root.is_file() else (root / "portal-codex" if sys.platform == "linux" else npm_binary(root, "codex"))
    output = Path.cwd() / "answer"
    command = [str(binary), "exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
               "-c", 'model_reasoning_effort="low"', "--output-last-message", str(output)]
    subprocess.run(command + (["-m", model] if model else []) + [prompt], check=True,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    return output.read_text()


async def claude(root, prompt, model):
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage
    options = ClaudeAgentOptions(cwd=os.getcwd(), tools=[], setting_sources=[], max_turns=1,
                                 model=model or None, extra_args={"no-session-persistence": None})
    async with ClaudeSDKClient(options) as client:
        await client.query(prompt)
        async for msg in client.receive_response():
            if isinstance(msg, ResultMessage):
                check(not msg.is_error, "Claude upstream failure")
                return msg.result


def antigravity(root, prompt, model):
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
            client.initialize()
            sid = client.call("session/new", params)["sessionId"]
            result = client.call("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": prompt}]})
            check(result["stopReason"] == "end_turn", "Antigravity terminal result")
            client.call("session/close", {"sessionId": sid})
        with AccountAcpClient(connect_local(socket_path)) as peer:
            caps = peer.initialize()["agentCapabilities"]
            resume = (root / MARKER).is_file() and "resume" in caps.get("sessionCapabilities", {})
            session = peer.call("session/resume" if resume else "session/load", {**params, "sessionId": sid})
            check(any(item.get("category") == "model" for item in session.get("configOptions", [])),
                  "Antigravity restored session configuration")
            peer.call("session/close", {"sessionId": sid})

    async def run():
        host = AccountServer(command, env, Path.cwd())
        async with await start_local_server(host.handle, socket_path, limit=FRAME_LIMIT):
            try:
                await asyncio.to_thread(exercise)
            finally:
                await host.close()

    asyncio.run(run())
    return "".join(text)


def kimi(root, prompt, model):
    from management_portal.kimi_code_client import runtime_client
    from management_portal.execution.provider_updates import npm_binary
    with http_server(npm_binary(root, "kimi"), ["web", "--no-open", "--dangerous-bypass-auth", "--host",
                                               "127.0.0.1"], "/api/v1/sessions") as origin:
        client = runtime_client(root, origin + "/api/v1", home=os.environ["KIMI_CODE_HOME"])
        sid = client.create_session(cwd=os.getcwd(), title="Provider acceptance", tools=[], model=model)["id"]
        result = client.run_turn(session_id=sid, prompt=prompt, permission_mode="manual",
                                 deny_all_tools=True, model=model)
        check(result.session_id == sid, "Kimi session identity")
        return result.response


def opencode(root, prompt, model):
    from management_portal.execution.provider_updates import npm_binary
    with http_server(npm_binary(root, "opencode"), ["serve", "--hostname", "127.0.0.1"], "/global/health") as origin:
        def post(path, body):
            request = Request(origin + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            with urlopen(request) as response:
                return json.load(response)
        sid = post("/session", {"title": "Provider acceptance"})["id"]
        result = post(f"/session/{sid}/message", {"model": {"providerID": os.environ["TFI_PROBE_SERVICE"],
                                                           "modelID": model},
                                                  "parts": [{"type": "text", "text": prompt}]})
        check(not result["info"].get("error"), "OpenCode upstream failure")
        return "".join(p["text"] for p in result["parts"] if p.get("type") == "text")


def dsh(root, prompt, model):
    from management_portal import dsh_client as module
    module.RUNTIME = root
    module.STATE, module.SOCKETS = Path.cwd() / "dsh", Path.cwd() / "s"
    route = os.environ["TFI_PROBE_ROUTE"]
    row = json.loads((Path.home() / "route.json").read_text())
    module.dsh_provider_catalog.profile = lambda _: row
    module.resident_process_environment = lambda _: (dict(os.environ), {})
    client = module.DshClient()
    key = secrets.token_hex(8)
    sid = module.session_id_for(key)
    params = dict(session_id=sid, session_key=key, turn_id=key, prompt=prompt, model=model,
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
    marker = "OK_" + secrets.token_hex(4)
    args = Path(directory), f"Reply with exactly {marker}. Do not use tools.", os.getenv("TFI_PROBE_MODEL", "")
    result = (asyncio.run(claude(*args)) if provider == "claude" else
              {"antigravity": antigravity, "codex": codex, "kimi": kimi, "opencode": opencode, "dsh": dsh}[provider](*args))
    check(isinstance(result, str) and result.strip() == marker, "Missing expected terminal response")
