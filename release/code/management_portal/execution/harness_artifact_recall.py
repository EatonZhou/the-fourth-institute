from __future__ import annotations

import json
from typing import Any

from .harness import HarnessTool, attach_harness_tools
from .harness_artifacts import HarnessArtifactStore


READ_HARNESS_ARTIFACT_TOOL_NAME = "read_harness_artifact"
SEARCH_HARNESS_ARTIFACT_TOOL_NAME = "search_harness_artifact"


def artifact_scope_for_invocation(invocation: Any) -> str:
    request = getattr(invocation, "request", None)
    session = getattr(invocation, "session", None)
    binding_id = str(getattr(session, "binding_id", "") or "").strip()
    provider = str(getattr(request, "provider", "") or "").strip()
    if not binding_id or not provider:
        return ""
    return f"v2:{provider}:{binding_id}"


def artifact_recall_tools(
    artifact_store: HarnessArtifactStore,
    *,
    scope: str,
    include_search: bool,
) -> tuple[HarnessTool, ...]:
    def read_artifact(arguments: dict[str, Any]) -> str:
        result = artifact_store.read(
            str(arguments.get("handle") or ""),
            scope=scope,
            offset=arguments.get("offset", 0),
            limit=arguments.get("limit", 16_000),
        )
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    tools = [HarnessTool(
        name=READ_HARNESS_ARTIFACT_TOOL_NAME,
        description=(
            "Read an exact character slice of a tool output previously "
            "offloaded by Portal AgentHarness. Use a handle and offset shown "
            "in conversation history or returned by search_harness_artifact."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "handle": {"type": "string"},
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 0,
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 32_000,
                    "default": 16_000,
                },
            },
            "required": ["handle"],
            "additionalProperties": False,
        },
        handler=read_artifact,
        read_only=True,
    )]
    if not include_search:
        return tuple(tools)

    def search_artifact(arguments: dict[str, Any]) -> str:
        result = artifact_store.search(
            str(arguments.get("handle") or ""),
            scope=scope,
            query=arguments.get("query"),
            max_hits=arguments.get("max_hits", 5),
            context_chars=arguments.get("context_chars", 600),
        )
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    tools.append(HarnessTool(
        name=SEARCH_HARNESS_ARTIFACT_TOOL_NAME,
        description=(
            "Search an offloaded tool output locally before reading it. "
            "Returns small exact snippets plus character offsets that can be "
            "passed to read_harness_artifact; never reads unrelated handles."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "handle": {"type": "string"},
                "query": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 500,
                },
                "max_hits": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "default": 5,
                },
                "context_chars": {
                    "type": "integer",
                    "minimum": 64,
                    "maximum": 4_000,
                    "default": 600,
                },
            },
            "required": ["handle", "query"],
            "additionalProperties": False,
        },
        handler=search_artifact,
        read_only=True,
    ))
    return tuple(tools)


def attach_artifact_recall_tools(
    invocation: Any,
    artifact_store: HarnessArtifactStore,
    *,
    include_search: bool,
) -> tuple[Any, int, str]:
    scope = artifact_scope_for_invocation(invocation)
    if not scope:
        return invocation, 0, "stable_artifact_scope_unavailable"
    return attach_harness_tools(
        invocation,
        artifact_recall_tools(
            artifact_store,
            scope=scope,
            include_search=include_search,
        ),
    )
