"""Adapt provider-neutral Harness tools to the common MCP result envelope."""
from __future__ import annotations

from typing import Any

from management_portal.native_agent_transport import ToolCallHandedOver

from .harness import AgentLoopToolBatch, AgentLoopToolResult, tool_call_scope
from .provider_catalog import AGENT_PROVIDERS


def prepare_tools(invocation: Any, context: Any):
    handlers = {tool.name: tool for tool in invocation.harness_tools}
    if len(handlers) != len(invocation.harness_tools):
        raise ValueError("Duplicate Harness tool name")
    # Each turn rebuilds the SDK schema; hidden tools from an older session must
    # not silently retain an enabled handler or another execution's credentials.
    catalog = [{"name": tool.name, "description": tool.description,
                "inputSchema": dict(tool.input_schema)} for tool in handlers.values() if tool.expose_schema]

    provider = AGENT_PROVIDERS.get(str(getattr(getattr(invocation, "request", None), "provider", "") or ""))
    keeps_turn = bool(provider and provider.handover_keeps_turn)

    def dispatch(call: dict[str, Any]) -> dict[str, Any]:
        if context.is_cancelled():
            handover = getattr(context, "is_handover", None)
            if keeps_turn and callable(handover) and handover():
                # The turn runs on for the next Worker, which runs this call.
                raise ToolCallHandedOver("Worker handover: the next Worker runs this tool call")
            raise InterruptedError("Agent tool execution cancelled")
        name, arguments = call.get("tool"), call.get("arguments")
        if name not in handlers or not isinstance(arguments, dict):
            raise ValueError("Unknown Agent Harness tool or invalid arguments")
        try:
            with tool_call_scope(call.get("call_id")):
                text = str(handlers[name].handler(arguments) or "")
            result = {"success": True, "contentItems": [{"type": "inputText", "text": text}]}
        except InterruptedError:
            raise
        except Exception as exc:
            result = {"success": False, "contentItems": [{"type": "inputText",
                      "text": f"Harness tool failed: {type(exc).__name__}: {str(exc)[:1000]}"}]}
        if invocation.tool_batch_processor is not None:
            batch = invocation.tool_batch_processor.process(AgentLoopToolBatch(items=(AgentLoopToolResult(
                call_id=str(call.get("call_id") or ""), tool_name=name, arguments=arguments,
                result=result, source="harness",
            ),)))
            if not isinstance(batch, AgentLoopToolBatch) or len(batch.items) != 1:
                raise TypeError("Harness must preserve one tool result")
            result = batch.items[0].result
        return {"isError": not bool(result.get("success")), "content": [
            {"type": "text", "text": str(item.get("text") or "")}
            for item in result.get("contentItems", []) if item.get("type") == "inputText"
        ]}

    return catalog, dispatch
