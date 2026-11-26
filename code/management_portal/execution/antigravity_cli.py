"""Portal session setup for Antigravity CLI 1.5.18 (agy acp).

CLI contract: https://antigravity.google/docs/ide/extensions/acp
Provider-specific extensions must be advertised by the selected executable.
"""
from .antigravity_workspace import additional_directories
from .backend_contracts import full_system_context


def open_session(client, invocation, capabilities, servers, session_id):
    """Build the agy acp request from its initialize response."""
    params = {"cwd": invocation.request.cwd, "mcpServers": servers}
    context = full_system_context(invocation)
    extensions = (capabilities.get("_meta") or {}).get("antigravity") or {}
    if context:
        if (extensions.get("systemPrompt") or {}).get("append") is not True:
            raise RuntimeError("Antigravity CLI did not advertise Portal system instructions")
        params["_meta"] = {"antigravity": {"systemPrompt": {"append": context}}}
    roots = additional_directories(invocation.request)
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
