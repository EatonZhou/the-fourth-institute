"""Native operations on the account and session bound to a Resident."""
from __future__ import annotations

import json
from uuid import UUID


def execute(provider, command, argument, session_id, route, cwd, identity):
    if command in {"archive", "unarchive", "checkpoints", "diff", "unrevert"} and argument:
        raise ValueError(f"/{command} 不接受参数")
    if command in {"rename", "rewind", "revert"} and not argument:
        raise ValueError(f"/{command} 需要一个参数")
    if provider == "codex":
        from .account_adapters import CodexRoutes
        from management_portal.codex_app_server_client import client_for_socket
        method = {"rename": "thread/name/set", "archive": "thread/archive", "unarchive": "thread/unarchive"}[command]
        params = {"threadId": session_id}
        if command == "rename":
            params["name"] = argument
        client_for_socket(CodexRoutes().get(route).socket_path).request(method, params)
        return f"/{command} 已完成。"
    params = {"session_id": session_id, "cwd": cwd, "provider_profile": route, "resident_environment": identity}
    if provider == "claude":
        from management_portal.claude_code_client import shared_client
        if command == "rewind":
            params["message_id"] = str(UUID(argument))
        result = shared_client(route).session_command(command, params)
    elif provider == "opencode":
        from management_portal.opencode_client import shared_client
        result = shared_client().session_command(command, params, argument)
    elif provider == "dsh" and command == "goal":
        from management_portal.dsh_client import shared_client
        result = shared_client().session_goal(params, argument)
    else:
        raise ValueError("Unsupported native session command")
    return json.dumps(result, ensure_ascii=False, indent=2)
