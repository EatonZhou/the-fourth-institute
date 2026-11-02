"""Antigravity's native ACP agent, with a durable Google session for each binding."""
from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
import sqlite3
import threading

from management_common.provider_turn_artifacts import ToolEditArtifacts
from management_portal import antigravity_accounts as accounts
from management_portal.antigravity_client import AcpClient, ToolServer
from management_portal.antigravity_runtime import command
from management_portal.antigravity_input_media import prompt_content
from management_portal.resident_shell_environment import resident_process_environment
from .backend_contracts import BackendResult, full_system_context
from .context_seed import realize_portable_seed
from .native_harness_tools import prepare_tools
from .native_options import NativeOptions
from .provider_turn_usage import TOKEN_USAGE_KEYS, _CAMEL_KEYS, usage_payload
from .resident_capability_assembly import require_offered_tools
from .native_socket_backend import _report_tool_events, _observe_tool_edit_artifacts
from .native_tool_events import tool_started, tool_completed


def _pb_fields(buf):
    pos, end = 0, len(buf)
    while pos < end:
        tag = shift = 0
        while pos < end:
            b = buf[pos]
            pos += 1
            tag |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
        field, wtype = tag >> 3, tag & 7
        if wtype == 0:
            val = shift = 0
            while pos < end:
                b = buf[pos]
                pos += 1
                val |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
            yield field, wtype, val
        elif wtype == 1:
            pos += 8
        elif wtype == 2:
            ln = shift = 0
            while pos < end:
                b = buf[pos]
                pos += 1
                ln |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
            yield field, wtype, buf[pos:pos + ln]
            pos += ln
        elif wtype == 5:
            pos += 4
        else:
            return


def _step_usage(blob):
    """Extract canonical single-request token usage from CortexStepGeneratorMetadata."""
    vals = {}
    for f1, w1, v1 in _pb_fields(blob):
        if f1 == 1 and w1 == 2:
            for f2, w2, v2 in _pb_fields(v1):
                if f2 == 4 and w2 == 2:
                    for f3, w3, v3 in _pb_fields(v2):
                        if w3 == 0:
                            vals[f3] = int(v3)
    uncached = vals.get(2, 0) + vals.get(4, 0)
    cached = vals.get(5, 0)
    thinking = vals.get(9, 0)
    output = vals.get(3, 0) or (vals.get(10, 0) + thinking)
    input_tokens = uncached + cached
    usage = {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "output_tokens": output,
        "reasoning_output_tokens": thinking,
        "total_tokens": input_tokens + output,
    }
    return {k: v for k, v in usage.items() if v > 0}


def _read_steps(env, sid, after_idx=-1):
    """Read per-step usage rows from Antigravity's conversation SQLite database."""
    home = env.get("GEMINI_HOME") if isinstance(env, dict) else ""
    db_path = Path(home) / "antigravity-acp" / "conversations" / f"{sid}.db" if home and sid else None
    if not db_path or not db_path.is_file():
        return after_idx, []
    try:
        with sqlite3.connect(db_path, timeout=1.0) as conn:
            rows = conn.execute(
                "SELECT idx, data FROM gen_metadata WHERE idx > ? ORDER BY idx",
                (after_idx,),
            ).fetchall()
    except sqlite3.Error:
        return after_idx, []
    if not rows:
        return after_idx, []
    steps = [u for _, data in rows if isinstance(data, (bytes, memoryview)) and (u := _step_usage(bytes(data)))]
    return int(rows[-1][0]), steps


def _raw_turn_usage(raw):
    """Normalize ACP session/prompt usage into Portal's canonical token usage."""
    if not isinstance(raw, dict):
        return {}
    inp = int(raw.get("inputTokens") or 0)
    cached = int(raw.get("cachedReadTokens") or 0)
    out = int(raw.get("outputTokens") or 0)
    thought = int(raw.get("thoughtTokens") or 0)
    total = int(raw.get("totalTokens") or 0)
    if thought > 0 and (not total or inp + out + thought <= total):
        out += thought
    total = total or (inp + out)
    usage = {
        "input_tokens": inp,
        "cached_input_tokens": cached,
        "output_tokens": out,
        "reasoning_output_tokens": thought,
        "total_tokens": total,
    }
    return {k: v for k, v in usage.items() if v > 0}


def _tool_arguments(item):
    """Translate ACP's documented file/command arguments to Portal's vocabulary."""
    name = item.get("title") or item.get("kind") or "Antigravity tool"
    raw = item.get("rawInput") or {}
    args = dict(raw) if isinstance(raw, dict) else {}
    path = next((args[k] for k in ("file_path", "path", "TargetFile", "target_file", "AbsolutePath", "absolute_path", "FilePath")
                 if isinstance(args.get(k), str)), "")
    if path:
        args["file_path"] = path
    if item.get("kind") == "execute":
        args["command"] = next((args[k] for k in ("command", "cmd", "CommandLine", "command_line")
                                if isinstance(args.get(k), str)), name)
        return "run_command", args
    # ACP includes the actual before/after text when available. Do not read a
    # file after the tool ran to invent its previous contents.
    diffs = [v for v in item.get("content", []) if v.get("type") == "diff"]
    if len(diffs) == 1 and isinstance(diffs[0].get("newText"), str):
        diff = diffs[0]
        args = {"file_path": diff["path"]}
        if isinstance(diff.get("oldText"), str):
            return "edit", {**args, "old_string": diff["oldText"], "new_string": diff["newText"]}
        return "write", {**args, "content": diff["newText"]}
    if name in {"create_file", "edit_file", "write_file", "write_to_file"}:
        content = next((args[k] for k in ("code_content", "CodeContent", "new_text", "content", "text")
                        if isinstance(args.get(k), str)), None)
        if path and content is not None:
            return "write", {"file_path": path, "content": content}
    if name == "replace_file_content" and path:
        old = args.get("TargetContent", args.get("target_content"))
        new = args.get("ReplacementContent", args.get("replacement_content"))
        if isinstance(old, str) and isinstance(new, str):
            return "edit", {"file_path": path, "old_string": old, "new_string": new,
                            "replace_all": args.get("AllowMultiple", args.get("allow_multiple")) is True}
    return name, args


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
                return BackendResult(prior["output"], prior["session_id"], route, prior.get("usage", {}),
                                     frozenset(prior.get("available_harness_tool_names", [])))
            if prior.get("phase") == "prompt_sent":
                raise RuntimeError("Antigravity input delivery is uncertain after disconnection; refusing to replay it")
        prompt = prompt_content(invocation.user_prompt, request.input_media) if request.operation == "turn" else []
        row = accounts.account_for_route(route)
        route = row["route_id"]
        env, _ = resident_process_environment(options)
        env.update(accounts.environment(row["account_id"]))
        native = request.permission_profile == "owner" and options.get("native_tools_enabled", True)
        catalog, dispatch = prepare_tools(invocation, context)
        offered_tools = {tool["name"] for tool in catalog}
        require_offered_tools(invocation, {tool["name"] for tool in catalog})
        sid = session.backend_session_id
        active_turn = False
        accepted = False
        segment = 0
        after_tool = False
        text_parts, tools = [], {}
        completed_tools = set()
        turn_id = request.execution_id
        tool_edits = ToolEditArtifacts(request.cwd)
        context_usage, context_window = {}, 0
        start_idx = -1

        def emit(method, **params):
            event = {"method": method, "params": {"threadId": sid, "turnId": turn_id, **params}}
            tool_edits.observe(event)
            _report_tool_events(invocation, [event])
            context.observe_provider_event(event, provider=self.provider_id, backend_session_id=sid, backend_route_id=route)

        def accept():
            nonlocal accepted
            if not accepted:
                context.bind_input(provider=self.provider_id, backend_session_id=sid, turn_id=turn_id, backend_route_id=route)
                emit("turn/started", turn={"id": turn_id})
                accepted = True

        def complete_tool(tool_id, item):
            name, arguments = _tool_arguments(item)
            event = tool_completed(tool_id, name, arguments, item.get("rawOutput", item.get("content", [])),
                                   failed=item["status"] == "failed", cwd=request.cwd)
            emit(event["method"], **event["params"])
            completed_tools.add(tool_id)

        def update(params):
            nonlocal segment, after_tool, context_usage, context_window
            item = params.get("update") or {}
            kind = item.get("sessionUpdate")
            if kind == "usage_update" and (not sid or params.get("sessionId") == sid):
                context_window = int(item.get("size", 0)) or context_window
            if not active_turn or params.get("sessionId") != sid:
                return  # session/load may replay historical updates.
            if kind in {"agent_message_chunk", "agent_thought_chunk", "tool_call", "tool_call_update"}:
                accept()
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
                tool_id = item.get("toolCallId")
                if not tool_id or tool_id in completed_tools:
                    return
                after_tool = True
                before = tools.get(tool_id)
                previous = dict(before or {})
                if previous.get("status") == "failed" and item.get("status") in {"pending", "in_progress", "completed"}:
                    # A sibling's error is not the resumed tool's result.
                    previous.pop("rawOutput", None)
                    previous.pop("content", None)
                merged = {**previous, **item}
                tools[tool_id] = merged
                name, arguments = _tool_arguments(merged)
                if before is None:
                    event = tool_started(tool_id, name, arguments, cwd=request.cwd)
                    emit(event["method"], **event["params"])
                # ACP can flush every open parallel call as failed when just
                # one tool errors, then re-announce a sibling and complete it.
                # Failures remain provisional until prompt settlement; success
                # is final and later progress must never reopen its Segment.
                if merged.get("status") == "completed":
                    complete_tool(tool_id, merged)
            elif kind == "usage_update":
                _, steps = _read_steps(env, sid, start_idx)
                used = int(item.get("used", 0))
                step_usage = steps[-1] if steps else ({"input_tokens": used} if used > 0 else {})
                if step_usage:
                    context_usage = step_usage
                    context.observe_context_usage(step_usage, context_window)
                    emit("thread/tokenUsage/updated", tokenUsage={
                        "context": {_CAMEL_KEYS[k]: v for k, v in step_usage.items() if k in _CAMEL_KEYS and v > 0},
                        "modelContextWindow": context_window,
                    })

        def client_request(method, params):
            if method == "session/request_permission":
                if active_turn and params.get("sessionId") == sid:
                    accept()
                # ACP asks permission for MCP calls too. The private Portal
                # endpoint still authorizes each dispatch as this Resident;
                # native filesystem/command access is a separate capability.
                call = params.get("toolCall") or {}
                mcp = (call.get("_meta") or {}).get("mcp") or {}
                portal_tool = mcp.get("server") == "portal" and mcp.get("tool") in offered_tools
                if (not sid or params.get("sessionId") == sid) and (native or portal_tool) and not context.is_cancelled():
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
            if request.input_media and not capabilities.get("promptCapabilities", {}).get("image"):
                raise ValueError("Antigravity ACP runtime does not support image input")
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
            discovered = accounts.save_catalog(row["account_id"], created, capabilities)
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
            with self._lock:
                self._active[(route, sid, turn_id)] = client
            start_idx, _ = _read_steps(env, sid)
            active_turn = True
            try:
                result = client.call("session/prompt", {"sessionId": sid, "prompt": prompt},
                                     timeout=None, cancelled=context.is_cancelled, session_id=sid)
                if result.get("stopReason") == "cancelled" or context.is_cancelled():
                    raise InterruptedError("Antigravity turn cancelled")
                accept()
                for tool_id, item in tools.items():
                    if tool_id not in completed_tools and item.get("status") == "failed":
                        complete_tool(tool_id, item)
                output = {"message": "".join(text_parts), "provider": self.provider_id,
                          "operation": "turn", "turn_id": turn_id, "stop_reason": result.get("stopReason", "")}
                turn_usage = _raw_turn_usage(result.get("usage"))
                _, steps = _read_steps(env, sid, start_idx)
                if steps:
                    summed = {
                        k: sum(s.get(k, 0) for s in steps)
                        for k in TOKEN_USAGE_KEYS
                        if sum(s.get(k, 0) for s in steps) > 0
                    }
                    if summed.get("total_tokens", 0) >= turn_usage.get("total_tokens", 0):
                        turn_usage = summed
                    context_usage = steps[-1]
                    if context_window:
                        context.observe_context_usage(context_usage, context_window)
                usage = usage_payload(turn_usage, context_usage or turn_usage, context_window)
                emit("item/completed", item={"id": f"{turn_id}:answer:{segment}", "type": "agentMessage", "text": output["message"], "phase": "final_answer"})
                _observe_tool_edit_artifacts(context, tool_edits, provider=self.provider_id, session_id=sid,
                                             turn_id=turn_id, status="completed")
                emit("turn/completed", turn={"id": turn_id, "status": "completed"})
                context.set_provider_recovery_state({"phase": "completed", "turn_id": turn_id, "session_id": sid,
                                                     "output": output, "usage": usage,
                                                     "available_harness_tool_names": [tool["name"] for tool in catalog]})
                return BackendResult(output, sid, route, usage, frozenset(tool["name"] for tool in catalog))
            finally:
                active_turn = False
                with self._lock:
                    self._active.pop((route, sid, turn_id), None)

    def interrupt(self, *, backend_session_id, turn_id, backend_route_id, runtime_options):
        with self._lock:
            client = self._active.get((backend_route_id, backend_session_id, turn_id))
        if client:
            client.notify("session/cancel", {"sessionId": backend_session_id})
            return True
        # Another worker owns the connection; its cancellation poll delivers cancel.
        return False
