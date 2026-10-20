"""Turn-scoped SDK MCP bridge. Handlers remain in the Execution worker."""
from __future__ import annotations

import re
from typing import Any, Awaitable, Callable

from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server


class ClaudeToolBridge:
    def __init__(self, catalog: list[dict[str, Any]]) -> None:
        self.dispatch: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None
        self.names: list[str] = []
        tools = []
        for entry in catalog:
            name = str(entry.get("name") or "")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or name in self.names:
                raise ValueError("Invalid or duplicate Claude Harness tool name")
            schema = entry.get("inputSchema")
            if not isinstance(schema, dict) or schema.get("type") != "object":
                raise ValueError("Claude Harness tool requires an object schema")
            self.names.append(name)

            async def call(arguments: dict[str, Any], tool_name: str = name) -> dict[str, Any]:
                if self.dispatch is None:
                    raise RuntimeError("Claude Harness tool has no active turn owner")
                return await self.dispatch(tool_name, arguments)

            tools.append(SdkMcpTool(name, str(entry.get("description") or ""), schema, call))
        # Claude Code puts MCP tools behind its tool search unless a server is
        # always loaded; then the model had to search before every Portal tool
        # (an offloaded result names read_harness_artifact, which it could not
        # call yet).  Portal's few tools are always in the prompt.
        self.servers = (
            {"portal": {**create_sdk_mcp_server(name="portal", tools=tools), "alwaysLoad": True}}
            if tools else {}
        )
        self.allowed_tools = ["mcp__portal__" + name for name in self.names]
