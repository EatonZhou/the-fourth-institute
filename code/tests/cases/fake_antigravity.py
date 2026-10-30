"""A stdio ACP peer exercising real Portal framing, process lifetime and MCP calls."""
import json
import os
from pathlib import Path
import sys
import threading
import time
from urllib.request import Request, urlopen

root = Path(os.environ["GEMINI_HOME"])
lock = threading.Lock()
session = {}
stop = threading.Event()
permission = threading.Event()
permission_result = {}


def send(value):
    with lock:
        print(json.dumps({"jsonrpc": "2.0", **value}), flush=True)


def update(sid, **value):
    send({"method": "session/update", "params": {"sessionId": sid, "update": value}})


def prompt(identifier, params):
    sid = params["sessionId"]
    text = params["prompt"][0]["text"]
    if text == "/usage":
        if os.environ.get("FAKE_USAGE_FAIL"):
            send({"id": identifier, "error": {"code": -32000, "message": "access_token=secret"}})
            return
        report_file = root / "quota_report.txt"
        report = report_file.read_text() if report_file.exists() else (
            "## Usage\n### Token Usage\n- Total: 23456\n### Quota Limits\n"
            "- **Gemini Models**\n  - **Five Hour Limit:** 73% remaining (resets Nov 01, 2026, 08:30 UTC)\n"
            "  - **Weekly Limit:** 91% remaining (resets Nov 06, 2026, 08:30 UTC)\n")
        update("unrelated-session", sessionUpdate="agent_message_chunk", content={"type": "text", "text": "wrong"})
        for part in (report[:25], report[25:]):
            update(sid, sessionUpdate="agent_message_chunk", content={"type": "text", "text": part})
        send({"id": identifier, "result": {"stopReason": "end_turn"}})
        return
    if text == "disconnect":
        os._exit(7)
    if text == "wait":
        stop.wait(10)
        send({"id": identifier, "result": {"stopReason": "cancelled"}})
        return
    if text == "permission":
        send({"id": "permission-1", "method": "session/request_permission", "params": {
            "sessionId": sid, "options": [{"kind": "allow_once", "optionId": "yes"}]}})
        permission.wait(5)
        text = permission_result.get("outcome", {}).get("outcome", "missing")
    if text == "multi-step":
        update(sid, sessionUpdate="agent_message_chunk", content={"type": "text", "text": "Checking a file."})
        text = "Final answer."
    if text == "file-edit":
        update(sid, sessionUpdate="tool_call", toolCallId="edit-1", title="replace_file_content", kind="edit",
               status="in_progress", rawInput={"TargetFile": "file.txt", "TargetContent": "old", "ReplacementContent": "new"})
        update(sid, sessionUpdate="tool_call_update", toolCallId="edit-1", status="completed", rawOutput="written")
    for server in session.get("mcpServers", []):
        headers = {item["name"]: item["value"] for item in server["headers"]}
        headers["Content-Type"] = "application/json"
        def mcp(method, args):
            data = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": args}).encode()
            with urlopen(Request(server["url"], data=data, headers=headers), timeout=5) as response:
                return json.load(response)["result"]
        mcp("initialize", {})
        tools = mcp("tools/list", {})["tools"]
        if tools:
            result = mcp("tools/call", {"name": tools[0]["name"], "arguments": {"id": "resource-one"}})
            text += ":" + result["content"][0]["text"]
    update(sid, sessionUpdate="tool_call", toolCallId="native-1", title="view_file", kind="read",
           status="in_progress", rawInput={"path": "file.txt"})
    update(sid, sessionUpdate="tool_call_update", toolCallId="native-1", status="completed", rawOutput="content")
    update(sid, sessionUpdate="usage_update", used=80, size=1000)
    update(sid, sessionUpdate="agent_message_chunk", content={"type": "text", "text": text})
    send({"id": identifier, "result": {"stopReason": "end_turn", "usage": {"inputTokens": 80, "outputTokens": 6, "totalTokens": 86}}})


for line in sys.stdin:
    message = json.loads(line)
    method, params = message.get("method"), message.get("params", {})
    if method is None:
        permission_result = message.get("result", {})
        permission.set()
        continue
    with (root / "requests.jsonl").open("a") as log:
        log.write(json.dumps({"method": method, "params": params}) + "\n")
    if method == "initialize":
        result = {"protocolVersion": 1, "agentInfo": {"version": "1.4.5"},
                  "agentCapabilities": {"loadSession": True, "mcpCapabilities": {"http": True}}}
    elif method == "authenticate":
        if os.environ.get("FAKE_LOGIN"):
            print("https://accounts.google.com/o/oauth2/auth?state=s&redirect_uri=http%3A%2F%2Flocalhost%3A12345%2F", file=sys.stderr, flush=True)
            time.sleep(10)
        result = {}
    elif method in {"session/new", "session/load"}:
        session = params
        sid = params.get("sessionId", "google-session-1")
        if not os.environ.get("FAKE_NO_USAGE"):
            update(sid, sessionUpdate="available_commands_update", availableCommands=[{"name": "usage"}])
        if method == "session/load":
            update(sid, sessionUpdate="agent_message_chunk", content={"type": "text", "text": "old history"})
        result = {"sessionId": sid, "models": {"currentModelId": "gemini-test", "availableModels": [{"modelId": "gemini-test"}]}}
    elif method == "session/prompt":
        threading.Thread(target=prompt, args=(message["id"], params), daemon=True).start()
        continue
    elif method == "session/cancel":
        stop.set()
        continue
    else:
        result = {}
    send({"id": message["id"], "result": result})
