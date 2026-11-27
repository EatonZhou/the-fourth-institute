"""Portal session setup for Antigravity CLI 1.5.18 (agy acp).

CLI contract: https://antigravity.google/docs/cli/acp/
Provider-specific extensions must be advertised by the selected executable.
"""
import hashlib
from pathlib import Path

from management_common.file_permissions import atomic_write_private_bytes

from .antigravity_workspace import additional_directories
from .backend_contracts import full_system_context


PORTAL_AGENT = "portal-tools-only"


def policy_marker(account_root, session_id):
    return account_root / "portal-only-sessions" / hashlib.sha256(session_id.encode()).hexdigest()


def session_marker(account_root, session_id):
    return account_root / "cli-1518-sessions" / hashlib.sha256(session_id.encode()).hexdigest()


def needs_new_session(gemini_home, account_root, session_id, portal_only):
    if not session_id or Path(session_id).name != session_id or session_id in {".", ".."}:
        raise ValueError("Invalid Antigravity session ID")
    database = Path(gemini_home) / "antigravity-acp" / "conversations" / f"{session_id}.db"
    return (not session_marker(account_root, session_id).is_file() or not database.is_file()
            or policy_marker(account_root, session_id).is_file() != portal_only)


def write_portal_agent(cwd):
    agents = Path(cwd) / ".agents" / "agents"
    path = agents / f"{PORTAL_AGENT}.md"
    content = (
        "---\nname: portal-tools-only\ndescription: Use Portal tools for this conversation\n"
        "mainAgent: true\nsubagent: false\ntools: []\n---\n"
        "Use the tools supplied by Portal for this session.\n"
    ).encode()
    if not path.is_file() or path.read_bytes() != content:
        atomic_write_private_bytes(path, content)


def is_offered_portal_call(call, offered):
    # 1.5.18 reports either the qualified component name or its MCP dispatcher.
    if call.get("kind") == "other" and call.get("name") in {f"mcp_portal_{name}" for name in offered}:
        return True
    args = call.get("args")
    return (call.get("name") == "call_mcp_tool" and isinstance(args, dict)
            and args.get("ServerName") == "portal" and args.get("ToolName") in offered)


def open_session(client, invocation, capabilities, servers, session_id, *, cwd, portal_only):
    """Build the agy acp request from its initialize response."""
    params = {"cwd": cwd, "mcpServers": servers}
    context = full_system_context(invocation)
    extensions = (capabilities.get("_meta") or {}).get("antigravity") or {}
    if context:
        if (extensions.get("systemPrompt") or {}).get("append") is not True:
            raise RuntimeError("Antigravity CLI did not advertise Portal system instructions")
        params["_meta"] = {"antigravity": {"systemPrompt": {"append": context}}}
    roots = [] if portal_only else additional_directories(invocation.request)
    if roots:
        if "additionalDirectories" not in (capabilities.get("sessionCapabilities") or {}):
            raise RuntimeError("Antigravity CLI did not advertise additional workspace directories")
        params["additionalDirectories"] = roots
    if session_id:
        params["sessionId"] = session_id
    # agy acp uses the selected account's cached login.
    return client.call("session/load" if session_id else "session/new", params)


def configure_session(client, session_id, session, model):
    config = {item.get("category"): item for item in session.get("configOptions", [])}
    for category, value in (("model", model), ("mode", "default")):
        option = config.get(category)
        if not option or not option.get("id"):
            raise RuntimeError(f"Antigravity CLI did not advertise the {category} option")
        if option.get("currentValue") != value:
            client.call("session/set_config_option", {"sessionId": session_id, "configId": option["id"], "value": value})
