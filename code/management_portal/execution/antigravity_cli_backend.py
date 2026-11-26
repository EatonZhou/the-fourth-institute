"""Portal execution adapter for the official Antigravity CLI 1.5.18 ACP server."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import re
import tempfile
import threading

from management_common.file_permissions import atomic_write_private_bytes
from management_common.provider_turn_artifacts import ToolEditArtifacts
from management_portal import antigravity_accounts as accounts
from management_portal.antigravity_client import AcpClient, AcpTransportError, ToolServer
from management_portal.antigravity_runtime import VERSION, command, credential_environment
from management_portal.antigravity_input_media import prompt_content
from management_portal.resident_shell_environment import resident_process_environment
from . import antigravity_cli
from .backend_contracts import BackendResult
from .context_seed import realize_portable_seed
from .native_harness_tools import prepare_tools
from .antigravity_history import _read_steps, _latest_checkpoint
from .provider_turn_usage import TOKEN_USAGE_KEYS, _CAMEL_KEYS, usage_payload
from .resident_capability_assembly import require_offered_tools
from .native_socket_backend import _report_tool_events, _observe_tool_edit_artifacts, record_native_compaction
from .native_tool_events import tool_started, tool_completed
from .provider_failures import ProviderOutcomeUnknown


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


class _CliTurn:
    """Project notifications only for the prompt currently in flight."""

    provider_id = "antigravity"

    def __init__(self, invocation, context, env, route, session_id):
        self.invocation = invocation
        self.request = invocation.request
        self.context = context
        self.env = env
        self.route = route
        self.sid = session_id
        self.turn_id = self.request.execution_id
        self.compact = self.request.operation == "compact"
        self.active_turn = False
        self.accepted = False
        self.segment = 0
        self.after_tool = False
        self.text_parts = []
        self.tools = {}
        self.completed_tools = set()
        self.tool_edits = ToolEditArtifacts(self.request.cwd)
        self.context_usage = {}
        self.context_window = 0
        self.start_idx = -1
        self.command_list_received = threading.Event()
        self.compact_offered = False

    def emit(self, method, **params):
        event = {"method": method, "params": {"threadId": self.sid, "turnId": self.turn_id, **params}}
        self.tool_edits.observe(event)
        _report_tool_events(self.invocation, [event])
        self.context.observe_provider_event(event, provider=self.provider_id, backend_session_id=self.sid, backend_route_id=self.route)

    def accept(self):
        if not self.accepted:
            self.context.bind_input(provider=self.provider_id, backend_session_id=self.sid, turn_id=self.turn_id, backend_route_id=self.route)
            self.emit("turn/started", turn={"id": self.turn_id})
            self.accepted = True

    def complete_tool(self, tool_id, item):
        name, arguments = _tool_arguments(item)
        event = tool_completed(tool_id, name, arguments, item.get("rawOutput", item.get("content", [])),
                               failed=item["status"] == "failed", cwd=self.request.cwd)
        self.emit(event["method"], **event["params"])
        self.completed_tools.add(tool_id)

    def update(self, params):
        item = params.get("update") or {}
        kind = item.get("sessionUpdate")
        if self.compact and kind == "available_commands_update" and params.get("sessionId") == self.sid:
            self.compact_offered = any(cmd.get("name") == "compact" for cmd in item.get("availableCommands", []))
            self.command_list_received.set()
        if kind == "usage_update" and (not self.sid or params.get("sessionId") == self.sid):
            self.context_window = int(item.get("size", 0)) or self.context_window
        if not self.active_turn or params.get("sessionId") != self.sid:
            return  # session/load may replay historical updates.
        if kind in {"agent_message_chunk", "agent_thought_chunk", "tool_call", "tool_call_update"}:
            self.accept()
        if kind in {"agent_message_chunk", "agent_thought_chunk"}:
            content = item.get("content") or {}
            if content.get("type") != "text":
                return
            delta = content.get("text", "")
            if kind == "agent_message_chunk":
                if self.after_tool:
                    if self.text_parts:
                        self.emit("item/completed", item={"id": f"{self.turn_id}:answer:{self.segment}", "type": "agentMessage",
                                                    "text": "".join(self.text_parts), "phase": "commentary"})
                        self.text_parts.clear()
                        self.segment += 1
                    self.after_tool = False
                self.text_parts.append(delta)
                self.emit("item/agentMessage/delta", itemId=f"{self.turn_id}:answer:{self.segment}", delta=delta)
            else:
                self.emit("item/reasoning/textDelta", itemId=f"{self.turn_id}:thought", delta=delta)
        elif kind in {"tool_call", "tool_call_update"}:
            tool_id = item.get("toolCallId")
            if not tool_id or tool_id in self.completed_tools:
                return
            self.after_tool = True
            before = self.tools.get(tool_id)
            previous = dict(before or {})
            if previous.get("status") == "failed" and item.get("status") in {"pending", "in_progress", "completed"}:
                # A sibling's error is not the resumed tool's result.
                previous.pop("rawOutput", None)
                previous.pop("content", None)
            merged = {**previous, **item}
            self.tools[tool_id] = merged
            name, arguments = _tool_arguments(merged)
            if before is None:
                event = tool_started(tool_id, name, arguments, cwd=self.request.cwd)
                self.emit(event["method"], **event["params"])
            # ACP can flush every open parallel call as failed when just
            # one tool errors, then re-announce a sibling and complete it.
            # Failures remain provisional until prompt settlement; success
            # is final and later progress must never reopen its Segment.
            if merged.get("status") == "completed":
                self.complete_tool(tool_id, merged)
        elif kind == "usage_update":
            _, steps = _read_steps(self.env, self.sid, self.start_idx)
            used = int(item.get("used", 0))
            step_usage = steps[-1] if steps and not self.compact else ({"input_tokens": used} if used > 0 else {})
            if step_usage:
                self.context_usage = step_usage
                self.context.observe_context_usage(step_usage, self.context_window)
                self.emit("thread/tokenUsage/updated", tokenUsage={
                    "context": {_CAMEL_KEYS[k]: v for k, v in step_usage.items() if k in _CAMEL_KEYS and v > 0},
                    "modelContextWindow": self.context_window,
                })

    def finish(self, result, model, catalog):
        self.accept()
        for tool_id, item in self.tools.items():
            if tool_id not in self.completed_tools and item.get("status") == "failed":
                self.complete_tool(tool_id, item)
        output = {"message": "".join(self.text_parts), "provider": self.provider_id,
                  "operation": self.request.operation, "turn_id": self.turn_id, "model": model, "stop_reason": result.get("stopReason", "")}
        if self.compact:
            output.update(message="已保存上下文摘要，可以继续当前会话。", compacted=True)
        turn_usage = _raw_turn_usage(result.get("usage"))
        _, steps = _read_steps(self.env, self.sid, self.start_idx)
        if steps:
            summed = {
                k: sum(s.get(k, 0) for s in steps)
                for k in TOKEN_USAGE_KEYS
                if sum(s.get(k, 0) for s in steps) > 0
            }
            if summed.get("total_tokens", 0) >= turn_usage.get("total_tokens", 0):
                turn_usage = summed
            if not self.compact:
                self.context_usage = steps[-1]
                if self.context_window:
                    self.context.observe_context_usage(self.context_usage, self.context_window)
        usage = usage_payload(turn_usage, self.context_usage if self.compact else self.context_usage or turn_usage, self.context_window)
        self.emit("item/completed", item={"id": f"{self.turn_id}:answer:{self.segment}", "type": "agentMessage", "text": output["message"], "phase": "final_answer"})
        _observe_tool_edit_artifacts(self.context, self.tool_edits, provider=self.provider_id, session_id=self.sid,
                                     turn_id=self.turn_id, status="completed")
        self.emit("turn/completed", turn={"id": self.turn_id, "status": "completed"})
        self.context.set_provider_recovery_state({"phase": "completed", "turn_id": self.turn_id, "session_id": self.sid,
                                             "output": output, "usage": usage,
                                             "available_harness_tool_names": [tool["name"] for tool in catalog]})
        return BackendResult(output, self.sid, self.route, usage, frozenset(tool["name"] for tool in catalog))


class AntigravityCliBackend:
    provider_id = "antigravity"

    @staticmethod
    def native_tools_allowed(request):
        return request.permission_profile == "owner" and bool(request.runtime_options.get("native_tools_enabled", True))

    @classmethod
    def local_file_read_policy(cls, invocation):
        from .local_file_read import LocalFileReadAssessment as Assessment

        request = invocation.request
        if not cls.native_tools_allowed(request):
            return Assessment("blocked", "native_tools_disabled")
        # A fake ACP view_file event proves protocol handling, not a native
        # runtime's file-read capability. Nominal support stays undeclared.
        return Assessment("unknown", "native_file_capability_unverified")
    _active = {}
    _lock = threading.Lock()

    def __init__(self, client_factory=AcpClient):
        self.client_factory = client_factory

    def open_client(self, cwd, env, *, portal_only=False, **callbacks):
        args = command(env)
        if portal_only:
            args += ["--include-default-customizations=false", "--agent", antigravity_cli.PORTAL_AGENT]
        return self.client_factory(args, env={**env, **credential_environment()}, cwd=cwd, **callbacks)

    @contextmanager
    def session(self, invocation, env, account, catalog, dispatch, previous_id,
                portal_only, on_update, on_request):
        """Own the process, MCP endpoint and agent definition for one ACP connection."""
        request = invocation.request
        with ExitStack() as resources:
            cwd = request.cwd
            if portal_only:
                cwd = resources.enter_context(tempfile.TemporaryDirectory(prefix="portal-agy-agent-"))
                antigravity_cli.write_portal_agent(cwd)
            servers = []
            if catalog:
                server = resources.enter_context(ToolServer(catalog, dispatch))
                servers.append(server.descriptor())
            client = resources.enter_context(self.open_client(cwd, env, portal_only=portal_only,
                                                                on_update=on_update, on_request=on_request))
            welcome = client.initialize()
            if welcome.get("agentInfo", {}).get("version") != VERSION:
                raise RuntimeError(f"Antigravity CLI handshake must report {VERSION}")
            capabilities = welcome.get("agentCapabilities") or {}
            if request.input_media and not capabilities.get("promptCapabilities", {}).get("image"):
                raise ValueError("Antigravity ACP runtime does not support image input")
            if not capabilities.get("loadSession") or (servers and not capabilities.get("mcpCapabilities", {}).get("http")):
                raise RuntimeError("Antigravity CLI lacks required session/MCP capabilities")
            opened = antigravity_cli.open_session(client, invocation, capabilities, servers,
                                                   previous_id, cwd=cwd, portal_only=portal_only)
            session_id = opened.get("sessionId", previous_id)
            if not isinstance(session_id, str) or not session_id:
                raise RuntimeError("Antigravity returned no session ID")
            if previous_id and previous_id != session_id:
                raise RuntimeError("Antigravity resumed a different session")
            if not previous_id:
                account_root = accounts.account_runtime(self.provider_id, account["account_id"])
                if portal_only:
                    atomic_write_private_bytes(antigravity_cli.policy_marker(account_root, session_id), VERSION.encode())
                atomic_write_private_bytes(antigravity_cli.session_marker(account_root, session_id), VERSION.encode())
            models = accounts.save_catalog(account["account_id"], opened, capabilities)
            model = str(request.runtime_options.get("model") or models["default_model"])
            if model not in models["models"]:
                raise ValueError("The selected model is no longer offered by this Antigravity account")
            antigravity_cli.configure_session(client, session_id, opened, model)
            yield client, session_id, model

    def prepare_context_seed(self, invocation):
        return realize_portable_seed(invocation)

    def execute(self, invocation, context):
        request, session = invocation.request, invocation.session
        options = request.runtime_options
        route = str(options.get("provider_profile") or session.backend_route_id or "")
        if session.backend_route_id and route != session.backend_route_id:
            raise ValueError("Provider route changes require a session migration")
        if request.operation == "release":
            # Google retains durable history when the Worker releases its session.
            return BackendResult({"message": "", "provider": self.provider_id, "operation": "release"},
                                 session.backend_session_id, route)
        compact = request.operation == "compact"
        if request.operation not in {"create", "turn", "compact"}:
            raise ValueError(f"Antigravity does not support {request.operation}")
        prior = invocation.recovery_checkpoint.get("provider_recovery_state") or {}
        if prior.get("turn_id") == request.execution_id:
            if prior.get("phase") == "completed":
                return BackendResult(prior["output"], prior["session_id"], route, prior.get("usage", {}),
                                     frozenset(prior.get("available_harness_tool_names", [])))
            if prior.get("phase") == "prompt_sent":
                raise ProviderOutcomeUnknown("Antigravity input delivery is uncertain after disconnection; refusing to replay it")
        row = accounts.account_for_route(route)
        route = row["route_id"]
        env, _ = resident_process_environment(options)
        env.update(accounts.environment(row["account_id"]))
        catalog, dispatch = ([], None) if compact else prepare_tools(invocation, context)
        offered_tools = {tool["name"] for tool in catalog}
        if not compact:
            require_offered_tools(invocation, offered_tools)
        sid = session.backend_session_id
        account_root = accounts.account_runtime("antigravity", row["account_id"])
        created_portal_only = bool(sid and antigravity_cli.policy_marker(account_root, sid).is_file())
        requested_portal_only = not self.native_tools_allowed(request)
        portal_only = created_portal_only or requested_portal_only
        user_prompt = invocation.user_prompt
        reset = (getattr(request, "metadata", {}) or {}).get("antigravity_session_reset")
        if reset:
            if (request.operation != "turn" or not sid or not isinstance(reset, dict)
                    or reset.get("route") != route or reset.get("session_id") != sid):
                raise ValueError("Antigravity session binding changed before reset")
            sid = ""
            portal_only = requested_portal_only
        prompt = prompt_content(user_prompt, request.input_media) if request.operation == "turn" else []
        # session/load restores the original agent. --agent only selects it at creation.
        if sid and portal_only and not created_portal_only:
            raise PermissionError("原会话允许原生工具。请保留 Portal 上下文重建仅用 Portal 工具的会话；本轮输入未发送。")
        if portal_only and request.operation == "turn" and re.match(r"^/[a-zA-Z][\w-]*(?:\s|$)", invocation.user_prompt.lstrip()):
            raise PermissionError("仅用 Portal 工具的会话通过 Portal 命令入口处理斜杠命令。")
        if compact and not sid:
            raise ValueError("Manual compaction requires an existing Antigravity CLI session")
        turn = _CliTurn(invocation, context, env, route, sid)

        def permission(method, params):
            if method != "session/request_permission":
                return None
            denied = {"outcome": {"outcome": "cancelled"}}
            if compact or not turn.active_turn or params.get("sessionId") != turn.sid or context.is_cancelled():
                return denied
            turn.accept()
            if portal_only and not antigravity_cli.is_offered_portal_call(params.get("toolCall") or {}, offered_tools):
                return denied
            for option in params.get("options", []):
                if option.get("kind") == "allow_once":
                    return {"outcome": {"outcome": "selected", "optionId": option["optionId"]}}
            return denied

        with self.session(invocation, env, row, catalog, dispatch, sid, portal_only,
                          turn.update, permission) as (client, turn.sid, model):
            if request.operation == "create":
                context.set_interrupt_target(turn.sid, turn.turn_id, route)
                return BackendResult({"message": "", "provider": self.provider_id, "operation": "create"}, turn.sid, route)
            if context.is_cancelled():
                raise InterruptedError("Antigravity execution cancelled before prompt")
            if compact:
                if not turn.command_list_received.wait(8) or not turn.compact_offered:
                    raise ValueError("This Antigravity session did not advertise /compact")
                previous_checkpoint = _latest_checkpoint(env, turn.sid)
                prompt = [{"type": "text", "text": "/compact"}]
            turn.start_idx, _ = _read_steps(env, turn.sid)
            response = self.prompt(client, turn, prompt)
            if compact:
                if response.get("stopReason") != "end_turn":
                    raise RuntimeError("Antigravity compaction did not finish")
                checkpoint = _latest_checkpoint(env, turn.sid)
                if not checkpoint or (previous_checkpoint and previous_checkpoint.ref == checkpoint.ref):
                    raise RuntimeError("Antigravity did not commit a new context checkpoint")
                record_native_compaction(context, provider=self.provider_id, backend_session_id=turn.sid,
                                         backend_route_id=route, compaction=checkpoint)
            return turn.finish(response, model, catalog)

    def prompt(self, client, turn, content):
        key = turn.route, turn.sid, turn.turn_id
        turn.context.set_provider_recovery_state({"phase": "prompt_sent", "turn_id": turn.turn_id, "session_id": turn.sid})
        turn.context.set_interrupt_target(turn.sid, turn.turn_id, turn.route)
        with self._lock:
            self._active[key] = client
        turn.active_turn = True
        try:
            result = client.call("session/prompt", {"sessionId": turn.sid, "prompt": content},
                                 timeout=None, cancelled=turn.context.is_cancelled, session_id=turn.sid)
            if result.get("stopReason") == "cancelled" or turn.context.is_cancelled():
                raise InterruptedError("Antigravity turn cancelled")
            return result
        except AcpTransportError as exc:
            if turn.context.is_cancelled():
                raise InterruptedError("Antigravity turn cancelled") from exc
            raise ProviderOutcomeUnknown("Antigravity input delivery is uncertain after disconnection; refusing to replay it") from exc
        finally:
            turn.active_turn = False
            with self._lock:
                self._active.pop(key, None)

    def interrupt(self, *, backend_session_id, turn_id, backend_route_id, runtime_options):
        with self._lock:
            client = self._active.get((backend_route_id, backend_session_id, turn_id))
        if client:
            client.notify("session/cancel", {"sessionId": backend_session_id})
            return True
        # Another worker owns the connection; its cancellation poll delivers cancel.
        return False
