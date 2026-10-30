"""Antigravity's native ACP agent, with a durable Google session for each binding."""
from __future__ import annotations

from contextlib import ExitStack
import threading

from management_portal import antigravity_accounts as accounts
from management_portal.antigravity_client import AcpClient, ToolServer
from management_portal.antigravity_runtime import command
from management_portal.resident_shell_environment import resident_process_environment
from .backend_contracts import BackendResult, full_system_context
from .context_seed import realize_portable_seed
from .native_harness_tools import prepare_tools
from .native_options import NativeOptions
from .resident_capability_assembly import require_offered_tools
from .native_socket_backend import _report_tool_events, _report_context_usage


class AntigravityOptions(NativeOptions):
    permission_labels = {"default": "Portal tools only", "full-access": "Full Access"}
    default_permission_preset = "full-access"

    def native_options(self, options, settings):
        return {**options, "native_tools_enabled": settings.get("permission_preset", self.default_permission_preset) == "full-access"}


class AntigravityBackend:
    provider_id = "antigravity"
    _active = {}
    _lock = threading.Lock()

    def __init__(self, client_factory=AcpClient):
        self.client_factory = client_factory

    def prepare_context_seed(self, invocation):
        return realize_portable_seed(invocation)

    def execute(self, invocation, context):
        request, session = invocation.request, invocation.session
        options = request.runtime_options
        route = str(options.get("provider_profile") or session.backend_route_id or "")
        if session.backend_route_id and route != session.backend_route_id:
            raise ValueError("Provider route changes require a session migration")
        if request.operation == "release":
            # Each invocation closes its process. Google retains durable history.
            return BackendResult({"message": "", "provider": self.provider_id, "operation": "release"},
                                 session.backend_session_id, route)
        if request.operation not in {"create", "turn"}:
            raise ValueError(f"Antigravity does not support {request.operation}")
        prior = invocation.recovery_checkpoint.get("provider_recovery_state") or {}
        if prior.get("turn_id") == request.execution_id:
            if prior.get("phase") == "completed":
                return BackendResult(prior["output"], prior["session_id"], route, prior.get("usage", {}))
            if prior.get("phase") == "prompt_sent":
                raise RuntimeError("Antigravity input delivery is uncertain after disconnection; refusing to replay it")
        row = accounts.account_for_route(route)
        route = row["route_id"]
        env, _ = resident_process_environment(options)
        env.update(accounts.environment(row["account_id"]))
        native = request.permission_profile == "owner" and options.get("native_tools_enabled", True)
        catalog, dispatch = prepare_tools(invocation, context)
        require_offered_tools(invocation, {tool["name"] for tool in catalog})
        sid = session.backend_session_id
        active_turn = False
        segment = 0
        after_tool = False
        text_parts, tools = [], {}
        turn_id = request.execution_id

        def emit(method, **params):
            event = {"method": method, "params": {"threadId": sid, "turnId": turn_id, **params}}
            _report_tool_events(invocation, [event])
            context.observe_provider_event(event, provider=self.provider_id, backend_session_id=sid, backend_route_id=route)

        def update(params):
            nonlocal segment, after_tool
            if not active_turn or params.get("sessionId") != sid:
                return  # session/load may replay historical updates.
            item = params.get("update") or {}
            kind = item.get("sessionUpdate")
            if kind in {"agent_message_chunk", "agent_thought_chunk"}:
                content = item.get("content") or {}
                if content.get("type") != "text":
                    return
                delta = content.get("text", "")
                if kind == "agent_message_chunk":
                    if after_tool:
                        if text_parts:
                            emit("item/completed", item={"id": f"{turn_id}:answer:{segment}", "type": "agentMessage",
                                                        "text": "".join(text_parts), "phase": "commentary"})
                            text_parts.clear()
                            segment += 1
                        after_tool = False
                    text_parts.append(delta)
                    emit("item/agentMessage/delta", itemId=f"{turn_id}:answer:{segment}", delta=delta)
                else:
                    emit("item/reasoning/textDelta", itemId=f"{turn_id}:thought", delta=delta)
            elif kind in {"tool_call", "tool_call_update"}:
                after_tool = True
                tool_id = item.get("toolCallId")
                if not tool_id:
                    return
                before = tools.get(tool_id)
                merged = {**(before or {}), **item}
                tools[tool_id] = merged
                native_item = {"id": tool_id, "type": "dynamicToolCall", "tool": merged.get("title") or merged.get("kind") or "Antigravity tool",
                               "arguments": merged.get("rawInput") or {}, "status": "inProgress"}
                if before is None:
                    emit("item/started", item=native_item)
                if merged.get("status") in {"completed", "failed"} and (before or {}).get("status") not in {"completed", "failed"}:
                    emit("item/completed", item={**native_item, "status": merged["status"],
                                                "output": merged.get("rawOutput", merged.get("content", []))})
            elif kind == "usage_update":
                usage = {"input_tokens": int(item.get("used", 0))}
                context.observe_context_usage(usage, int(item.get("size", 0)))
                _report_context_usage(invocation, {"context": {"inputTokens": usage["input_tokens"]},
                                                    "modelContextWindow": int(item.get("size", 0))})

        def client_request(method, params):
            if method == "session/request_permission":
                if (not sid or params.get("sessionId") == sid) and native and not context.is_cancelled():
                    option = next((p for p in params.get("options", []) if p.get("kind") == "allow_once"), None)
                    if option:
                        return {"outcome": {"outcome": "selected", "optionId": option["optionId"]}}
                return {"outcome": {"outcome": "cancelled"}}
            return None

        with ExitStack() as stack:
            server = stack.enter_context(ToolServer(catalog, dispatch)) if catalog else None
            client = stack.enter_context(self.client_factory(command(), env=env, cwd=request.cwd,
                                        on_update=update, on_request=client_request))
            handshake = client.initialize()
            capabilities = handshake.get("agentCapabilities") or {}
            if not capabilities.get("loadSession") or (server and not capabilities.get("mcpCapabilities", {}).get("http")):
                raise RuntimeError("Antigravity ACP runtime lacks required session/MCP capabilities")
            client.call("authenticate", {"methodId": "oauth-personal"})
            meta = {"systemPrompt": {"append": full_system_context(invocation)},
                    "disabledTools": ["start_subagent", "ask_question"]}
            if not native:
                meta["enabledTools"] = []
            params = {"cwd": request.cwd, "mcpServers": [server.descriptor()] if server else [],
                      "_meta": {"antigravity": meta}}
            if sid:
                params["sessionId"] = sid
            created = client.call("session/load" if sid else "session/new", params)
            sid = sid or created.get("sessionId", "")
            if not sid:
                raise RuntimeError("Antigravity returned no session ID")
            context.set_interrupt_target(sid, turn_id, route)
            discovered = accounts.save_catalog(row["account_id"], created)
            model = str(options.get("model") or discovered["default_model"])
            if model not in discovered["models"]:
                raise ValueError("The selected model is no longer offered by this Antigravity account")
            client.call("session/set_model", {"sessionId": sid, "modelId": model})
            # Always retain native permission requests. Portal selects allow_once only
            # for an owner with Full Access, never by prompt or persisted auto-approval.
            client.call("session/set_mode", {"sessionId": sid, "modeId": "default"})
            if request.operation == "create":
                return BackendResult({"message": "", "provider": self.provider_id, "operation": "create"}, sid, route)
            if context.is_cancelled():
                raise InterruptedError("Antigravity execution cancelled before prompt")
            context.set_provider_recovery_state({"phase": "prompt_sent", "turn_id": turn_id, "session_id": sid})
            context.bind_input(provider=self.provider_id, backend_session_id=sid, turn_id=turn_id, backend_route_id=route)
            with self._lock:
                self._active[(route, sid)] = client
            active_turn = True
            emit("turn/started", turn={"id": turn_id})
            try:
                result = client.call("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": invocation.user_prompt}]},
                                     timeout=None, cancelled=context.is_cancelled, session_id=sid)
                if result.get("stopReason") == "cancelled" or context.is_cancelled():
                    raise InterruptedError("Antigravity turn cancelled")
                output = {"message": "".join(text_parts), "provider": self.provider_id,
                          "operation": "turn", "stop_reason": result.get("stopReason", "")}
                raw = result.get("usage") or {}
                usage = {target: int(raw[source]) for source, target in
                         (("inputTokens", "input_tokens"), ("outputTokens", "output_tokens"),
                          ("cachedReadTokens", "cached_input_tokens"), ("thoughtTokens", "reasoning_output_tokens"),
                          ("totalTokens", "total_tokens")) if isinstance(raw.get(source), (int, float))}
                emit("item/completed", item={"id": f"{turn_id}:answer:{segment}", "type": "agentMessage", "text": output["message"], "phase": "final_answer"})
                emit("turn/completed", turn={"id": turn_id, "status": "completed"})
                context.set_provider_recovery_state({"phase": "completed", "turn_id": turn_id, "session_id": sid,
                                                     "output": output, "usage": usage})
                return BackendResult(output, sid, route, usage, frozenset(tool["name"] for tool in catalog))
            finally:
                active_turn = False
                with self._lock:
                    self._active.pop((route, sid), None)

    def interrupt(self, *, backend_session_id, turn_id, backend_route_id, runtime_options):
        with self._lock:
            client = self._active.get((backend_route_id, backend_session_id))
        if client:
            client.notify("session/cancel", {"sessionId": backend_session_id})
            return True
        # Another worker owns the connection; its cancellation poll delivers cancel.
        return False
