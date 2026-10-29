from __future__ import annotations
from management_common.portal_paths import path as portal_path

from management_portal.ai_provider_registry import AIProviderRegistry

import argparse
import asyncio
import dataclasses
import json
import os
import re
import signal
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from management_common.local_ipc import start_local_server
from typing import Any, Awaitable, Callable, Mapping

from management_portal import claude_provider_catalog
from management_portal import claude_input_media
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    RateLimitEvent,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ServerToolUseBlock,
    ServerToolResultBlock,
    ToolUseBlock,
    UserMessage,
    project_key_for_directory,
)

from management_common.product_environment import mirror_product_env
from management_portal.resident_shell_environment import portal_database_environment, resident_identity_environment, resident_identity_fingerprint
from management_portal.claude_tool_bridge import ClaudeToolBridge
from management_portal.claude_account_usage import ClaudeAccountUsage
from management_portal.claude_runtime import native_cli_path
from management_common.provider_subagent_runs import PROGRESS_METHOD, SubagentRuns


DEFAULT_SOCKET_PATH = (portal_path("staging_root") / "portal_locks" / "claude-code-daemon.sock")
APP_SERVER_PROTOCOL_VERSION = 1


def _app_server_version() -> str:
    version_path = Path(__file__).resolve().parents[1] / "VERSION"
    try:
        return version_path.read_text(encoding="utf-8").strip() or "development"
    except OSError:
        return "development"


APP_SERVER_VERSION = _app_server_version()
# A deployment may explicitly configure a fallback, but fresh installations
# have no account/route until onboarding. Requests normally select their route.
DEFAULT_PROVIDER_PROFILE = ""
OWNER_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep", "Bash", "NotebookEdit", "WebFetch", "WebSearch"]
NON_OWNER_TOOLS = ["Read", "Glob", "Grep", "WebFetch", "WebSearch"]
NON_OWNER_DISALLOWED_TOOLS = ["Write", "Edit", "Bash", "NotebookEdit"]
# The tools with which the model starts a subagent; a Resident that does not
# allow subagents ("允许开启 sub agent" off) does not get them.
SUBAGENT_TOOLS = ["Agent", "Task"]


def _tool_result_failed(block: ToolResultBlock | ServerToolResultBlock) -> bool:
    if isinstance(block, ToolResultBlock):
        return bool(block.is_error)
    content = block.content
    return bool(content.get("is_error") or content.get("error_code")
                or str(content.get("type") or "").endswith("_error"))


def _subagent_events(message: AssistantMessage | UserMessage, turn_id: str, index: int) -> list[dict[str, Any]]:
    """Route SDK child events while they arrive, outside the parent's output."""
    parent = str(message.parent_tool_use_id or "")
    if not parent:
        return []
    message_id = str(getattr(message, "uuid", None) or f"{turn_id}:{index}")
    events: list[dict[str, Any]] = []
    blocks = message.content if isinstance(message.content, list) else [TextBlock(text=message.content)]
    for block_index, block in enumerate(blocks):
        item_id = f"{message_id}:{block_index}"
        method = "item/completed"
        if isinstance(block, TextBlock):
            item = {"id": item_id, "type": "agentMessage" if isinstance(message, AssistantMessage) else "userMessage",
                    "phase": "commentary", "text": block.text}
        elif isinstance(block, ThinkingBlock):
            item = {"id": item_id, "type": "reasoning", "thinking": block.thinking, "signature": block.signature}
        elif isinstance(block, (ToolUseBlock, ServerToolUseBlock)):
            method = "item/started"
            item = {"id": block.id, "type": "toolCall", "name": block.name, "input": block.input}
        elif isinstance(block, (ToolResultBlock, ServerToolResultBlock)):
            item = {"id": block.tool_use_id, "type": "toolCall", "output": block.content,
                    "status": "failed" if _tool_result_failed(block) else "completed", "error": _tool_result_failed(block)}
        else:
            continue
        events.append({"method": "subagent/event", "params": {
            "parentToolUseId": parent, "traceKey": parent,
            "event": {"method": method, "params": {"turnId": turn_id, "item": item}},
        }})
    return events
# One stream-json message from the Claude Code CLI can carry a whole image
# (a Read of a PNG returns it base64-encoded) or a long tool result.  The SDK
# rejects any message above its 1 MiB default and fails the turn.
CLI_MESSAGE_BUFFER_BYTES = 64 * 1024 * 1024
# The CLI echoes each prompt when it starts on it.  A result that arrives
# before the echo belongs to work the CLI finished first: on resume a
# background task left running by the previous process is reported stopped
# with an empty result, and a finished task can start a turn of its own.
CLI_ARGS = {"replay-user-messages": None}


# A turn's prompt has a UUID derived from the turn, which the CLI keeps in
# the session transcript: a daemon that no longer has the turn (it
# restarted) finds there whether the CLI had taken the turn's input.
TURN_PROMPT_NAMESPACE = uuid.UUID("5d0d8c43-6f1e-4b8e-9a51-0b7f3a1c2d64")


def _turn_prompt_uuid(session_key: str, turn_id: str) -> str:
    return str(uuid.uuid5(TURN_PROMPT_NAMESPACE, f"{session_key}\n{turn_id}"))


def _transcript_has_message(config_dir: Path, session_id: str, message_uuid: str) -> bool:
    """Whether the session's transcript holds the user message with this UUID."""

    if not session_id or Path(f"{session_id}.jsonl").name != f"{session_id}.jsonl":
        return False
    for path in (config_dir / "projects").glob(f"*/{session_id}.jsonl"):
        try:
            with path.open("r", encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    if message_uuid not in line:
                        continue
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if record.get("uuid") == message_uuid and record.get("type") == "user":
                        return True
        except OSError:
            continue
    return False


async def _prompt_message(content: Any, session_id: str, message_uuid: str):
    """One prompt for the CLI, tagged so its echo marks where this turn begins."""
    yield {"type": "user", "uuid": message_uuid, "message": {"role": "user", "content": content},
           "parent_tool_use_id": None, "session_id": session_id or "default"}


# How long the CLI may take to start on steering it queued behind a finished
# answer before that answer stands as the turn's.
STEER_START_SECONDS = 360.0
# A turn waits on the subagents its model left running in the background:
# the CLI takes each run's notification in a turn of its own, which is still
# this one.  Once no run goes on, the CLI starts on the last notifications
# within moments; a run stopped without one leaves the turn after this long.
BACKGROUND_SETTLE_SECONDS = 45.0


async def _turn_messages(client: ClaudeSDKClient, state: dict[str, Any], first_within: float = 0.0,
                         within: Callable[[], float | None] | None = None):
    """The CLI's messages up to its next result.

    With ``first_within`` the first message must arrive in time; otherwise
    ``state["timed_out"]`` is set and nothing is yielded.  ``within()``, when
    given, says before each later message how long to wait for it (``None``:
    as long as it takes); when that passes ``state["timed_out"]`` is set too.
    """

    stream = client.receive_response()
    try:
        if first_within:
            try:
                message = await asyncio.wait_for(stream.__anext__(), first_within)
            except StopAsyncIteration:
                return
            except asyncio.TimeoutError:
                state["timed_out"] = True
                return
            yield message
            if isinstance(message, ResultMessage):
                return
        if within is None:
            async for message in stream:
                yield message
            return
        while True:
            limit = within()
            try:
                message = await (stream.__anext__() if limit is None else asyncio.wait_for(stream.__anext__(), limit))
            except StopAsyncIteration:
                return
            except asyncio.TimeoutError:
                state["timed_out"] = True
                return
            yield message
            if isinstance(message, ResultMessage):
                return
    finally:
        await stream.aclose()


def _summed(first: Any, second: Any) -> Any:
    """Two usage records of one Portal turn added up (numbers add, the rest is kept)."""

    if isinstance(first, dict) and isinstance(second, dict):
        merged = dict(first)
        for key, value in second.items():
            merged[key] = _summed(merged[key], value) if key in merged else value
        return merged
    if isinstance(first, (int, float)) and isinstance(second, (int, float)) \
            and not isinstance(first, bool) and not isinstance(second, bool):
        return first + second
    return second if second is not None else first


def _json_safe(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return _json_safe(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _provider_profile(profile_id: str) -> dict[str, Any]:
    return claude_provider_catalog.profile(profile_id)


def _provider_environment(profile_id: str) -> tuple[dict[str, str], dict[str, Any]]:
    profile = _provider_profile(profile_id)
    if claude_provider_catalog.is_native(profile):
        env = claude_provider_catalog.login_environment(profile)
        return env, profile
    key = AIProviderRegistry.read_secret(AIProviderRegistry.secret_path(profile))
    base_url, auth = AIProviderRegistry.harness_endpoint(profile, "anthropic_messages")
    env = {
        "ANTHROPIC_BASE_URL": base_url,
        # Bearer goes in Authorization; x-api-key in Anthropic's own header.
        "ANTHROPIC_AUTH_TOKEN": key if auth == "bearer" else "",
        "ANTHROPIC_API_KEY": key if auth == "x-api-key" else "",
        "CLAUDE_CODE_OAUTH_TOKEN": "",
        "ANTHROPIC_PROFILE": "",
        "CLAUDE_CODE_USE_BEDROCK": "",
        "CLAUDE_CODE_USE_VERTEX": "",
        "CLAUDE_CODE_USE_FOUNDRY": "",
    }
    if claude_provider_catalog.is_declared(profile):
        headers = {str(name): str(value).strip() for name, value in (profile.get("default_headers") or {}).items()
                   if str(value or "").strip()}
        env["ANTHROPIC_CUSTOM_HEADERS"] = "\n".join(f"{name}: {value}" for name, value in headers.items())
    return env, profile


# A declared gateway serves only its own models; background, title and
# subagent requests would otherwise ask it for Claude model names.
GATEWAY_MODEL_VARIABLES = ("ANTHROPIC_MODEL", "ANTHROPIC_SMALL_FAST_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
                           "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL",
                           "ANTHROPIC_DEFAULT_FABLE_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL")


def _validated_model(profile: dict[str, Any], requested: str) -> str:
    return claude_provider_catalog.validate_settings(profile, requested)[0]


def _validated_effort(profile: dict[str, Any], model: str, requested: str) -> str:
    return claude_provider_catalog.validate_settings(profile, model, requested)[1]


class ClaudeTurnInterrupted(RuntimeError):
    """An acknowledged native interrupt must not poison the retry cache."""


@dataclass
class SessionHandle:
    key: str
    signature: tuple[Any, ...]
    client: ClaudeSDKClient
    session_id: str = ""
    active_turn_id: str = ""
    interrupted_turn_id: str = ""
    invalidated: bool = False
    last_used: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    tool_bridge: ClaudeToolBridge | None = None
    # Context size (tokens) from which Claude Code compacts in this session;
    # 0: Claude Code's own default for the model.
    auto_compact_window: int = 0
    # Input given to the running turn (``turn/steer``): open until the turn's
    # result arrives; the UUIDs of messages the CLI has not started on yet;
    # the controls already given, so a retried control is not sent twice.
    steering_open: bool = False
    pending_steers: set[str] = field(default_factory=set)
    steer_keys: set[str] = field(default_factory=set)
    # The subagents the session's model started, for their cards in the
    # Chat; a run in the background may end in a later turn.
    subagent_runs: SubagentRuns = field(default_factory=SubagentRuns)
    # The CLI's task of each subagent run (Agent call id -> task id): the
    # Chat stops one run through it (``subagent/stop``).
    subagent_tasks: dict[str, str] = field(default_factory=dict)
    # The runs the Chat stopped: the CLI may say only that their task was
    # killed, with no notification for the model, so their cards end then.
    subagent_stops: set[str] = field(default_factory=set)
    # The CLI tasks whose latest end already gave a card: the CLI may tell it
    # twice (its task notification and the notification replayed to the model).
    subagent_notified: set[str] = field(default_factory=set)


async def _report_context_usage(handle: SessionHandle, turn_id: str, emit) -> None:
    """Read the CLI's current context, independently of cumulative turn usage."""
    read_usage = getattr(handle.client, "get_context_usage", None)
    if not callable(read_usage):
        return
    try:
        usage = await asyncio.wait_for(read_usage(), timeout=6.0)
        tokens = int(usage.get("totalTokens") or 0)
        window = int(usage.get("maxTokens") or 0)
    except Exception:
        return
    if tokens <= 0 or window <= 0 or handle.active_turn_id != turn_id:
        return
    await emit({"method": "thread/tokenUsage/updated", "params": {
        "threadId": handle.session_id, "turnId": turn_id,
        "tokenUsage": {"context": {"totalTokens": tokens}, "modelContextWindow": window},
    }})


def _message_text(content: object) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for block in content if isinstance(content, list) else []:
        text = block.text if isinstance(block, TextBlock) else block.get("text") if isinstance(block, dict) else None
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def _tag(text: str, name: str) -> str:
    start = text.find(f"<{name}>")
    end = text.find(f"</{name}>", start + 1)
    return text[start + len(name) + 2:end].strip() if start >= 0 and end > start else ""


def _async_launched(outcome: object) -> bool:
    return isinstance(outcome, dict) and (outcome.get("isAsync") or outcome.get("status") == "async_launched")


def _subagents_in_background(handle: "SessionHandle", message: UserMessage) -> list[str]:
    """The runs this message tells the model went on in the background.

    An Agent call that does not set ``run_in_background`` may still run in the
    background (the CLI's default); its result is the launch, not the run's end.
    """

    if not _async_launched(message.tool_use_result):
        return []
    launched = []
    for block in message.content if isinstance(message.content, list) else []:
        if isinstance(block, ToolResultBlock) and handle.subagent_runs.knows(block.tool_use_id):
            handle.subagent_runs.in_background(block.tool_use_id)
            launched.append(block.tool_use_id)
    return launched


def _agent_result_usage(outcome: Mapping[str, Any]) -> dict[str, Any]:
    """The token use an Agent call's result reports for its run."""

    usage = outcome.get("usage") if isinstance(outcome.get("usage"), Mapping) else {}
    return {
        "total_tokens": outcome.get("totalTokens"),
        "input_tokens": int(usage.get("input_tokens") or 0) + int(usage.get("cache_creation_input_tokens") or 0)
        + int(usage.get("cache_read_input_tokens") or 0),
        "cached_input_tokens": usage.get("cache_read_input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "tool_uses": outcome.get("totalToolUseCount"),
    }


_NOTICE_USAGE_NAMES = {"total_tokens": "total_tokens", "subagent_tokens": "total_tokens", "tool_uses": "tool_uses"}


def _notice_usage(notice: str) -> dict[str, Any]:
    """The ``<usage>`` of a task notification.

    The CLI writes it as child tags, ``<subagent_tokens>1234</subagent_tokens>``;
    older ones wrote ``total_tokens: 1234`` lines.
    """

    block = _tag(notice, "usage")
    usage: dict[str, Any] = {}
    for name, value in re.findall(r"<(\w+)>\s*(\d+)\s*</\1>", block):
        if name in _NOTICE_USAGE_NAMES:
            usage[_NOTICE_USAGE_NAMES[name]] = int(value)
    for line in block.splitlines():
        name, _, value = line.partition(":")
        if name.strip() in _NOTICE_USAGE_NAMES and value.strip().isdigit():
            usage.setdefault(_NOTICE_USAGE_NAMES[name.strip()], int(value.strip()))
    return usage


def _observe_subagent_task(handle: "SessionHandle", message: SystemMessage) -> str:
    """Follow the CLI's task of a subagent run; the run's Agent call id when its card changed."""

    data = message.data if isinstance(message.data, Mapping) else {}
    tool_use_id = str(data.get("tool_use_id") or "")
    if message.subtype not in {"task_started", "task_progress", "task_notification"} or not handle.subagent_runs.knows(tool_use_id):
        return ""
    if data.get("task_id"):
        handle.subagent_tasks[tool_use_id] = str(data["task_id"])
    if message.subtype == "task_started":
        # Again when the model resumes the run, which then ends again.
        handle.subagent_runs.mark_running(tool_use_id)
        handle.subagent_notified.discard(str(data.get("task_id") or ""))
    if isinstance(data.get("usage"), Mapping):
        handle.subagent_runs.report_usage(tool_use_id, data["usage"])
    return tool_use_id


_TASK_ENDS = {"completed", "failed", "stopped", "killed"}


def _ended_subagent_task(handle: "SessionHandle", message: SystemMessage) -> str:
    """The Agent call id of the run whose CLI task this message says ended, or ``""``.

    The run's work is over, so it no longer holds its turn open.
    """

    data = message.data if isinstance(message.data, Mapping) else {}
    patch = data.get("patch") if isinstance(data.get("patch"), Mapping) else {}
    if message.subtype not in {"task_notification", "task_updated"} or str(data.get("status") or patch.get("status") or "") not in _TASK_ENDS:
        return ""
    task_id = str(data.get("task_id") or "")
    tool_use_id = str(data.get("tool_use_id") or "") or next(
        (call for call, task in handle.subagent_tasks.items() if task_id and task == task_id), "")
    handle.subagent_runs.mark_running(tool_use_id, False)
    return tool_use_id


def _ended_subagent_card(handle: "SessionHandle", message: SystemMessage, turn_id: str) -> dict[str, Any] | None:
    """The card of a run the CLI says its task ended, when no result gives it.

    A run the Chat stopped ends here and is then forgotten, so a later
    notification of the same task does not end it twice.  A run in the
    background ends here with the CLI's task notification: the CLI replays
    the notification to the stream as a user message only when it reaches a
    turn already running, not when the CLI starts a turn of its own for it.
    Its result is then the run's last message.
    """

    data = message.data if isinstance(message.data, Mapping) else {}
    task_id = str(data.get("task_id") or "")
    tool_use_id = _ended_subagent_task(handle, message)
    if not tool_use_id:
        return None
    if tool_use_id in handle.subagent_stops:
        handle.subagent_stops.discard(tool_use_id)
        handle.subagent_tasks.pop(tool_use_id, None)
        card = handle.subagent_runs.finish(tool_use_id, status="stopped", end_id=task_id, turn_id=turn_id)
        handle.subagent_runs.forget(tool_use_id)
        return card
    if message.subtype != "task_notification" or not handle.subagent_runs.ran_in_background(tool_use_id):
        return None
    if _already_notified(handle, task_id):
        return None
    status = str(data.get("status") or "completed")
    return handle.subagent_runs.finish(
        tool_use_id, status="stopped" if status == "killed" else status, end_id=task_id, turn_id=turn_id)


def _already_notified(handle: "SessionHandle", task_id: str) -> bool:
    """Whether the CLI already told this end of the task (its other telling then gives no card)."""

    if task_id in handle.subagent_notified:
        handle.subagent_notified.discard(task_id)
        return True
    if task_id:
        handle.subagent_notified.add(task_id)
    return False


def _subagent_cards(handle: "SessionHandle", message: UserMessage, turn_id: str) -> list[dict[str, Any]]:
    """The cards of the subagent runs whose result this message gives the model.

    A run in the foreground ends with its Agent call's result; Claude Code
    tells a run in the background ended with a task notification, which the
    CLI replays as a user message when the model takes it.
    """

    runs = handle.subagent_runs
    end_id = str(getattr(message, "uuid", None) or "")
    cards = []
    for block in message.content if isinstance(message.content, list) else []:
        if not isinstance(block, ToolResultBlock) or not runs.knows(block.tool_use_id):
            continue
        outcome = message.tool_use_result if isinstance(message.tool_use_result, dict) else {}
        if _async_launched(outcome):
            continue
        runs.report_usage(block.tool_use_id, _agent_result_usage(outcome))
        handle.subagent_tasks.pop(block.tool_use_id, None)
        handle.subagent_stops.discard(block.tool_use_id)
        card = runs.finish(
            block.tool_use_id, status="failed" if block.is_error else str(outcome.get("status") or "completed"),
            result=_message_text(outcome.get("content")), end_id=end_id, turn_id=turn_id,
        )
        cards.append(card)
    text = _message_text(message.content)
    for notice in text.split("<task-notification>")[1:]:
        tool_use_id = _tag(notice, "tool-use-id")
        if runs.knows(tool_use_id) and not _already_notified(handle, _tag(notice, "task-id")):
            runs.report_usage(tool_use_id, _notice_usage(notice))
            handle.subagent_tasks.pop(tool_use_id, None)
            handle.subagent_stops.discard(tool_use_id)
            cards.append(runs.finish(
                tool_use_id, status=_tag(notice, "status") or "completed", result=_tag(notice, "result"),
                end_id=end_id or _tag(notice, "task-id"), turn_id=turn_id,
            ))
    return [card for card in cards if card is not None]


def _notified_runs(message: object) -> list[str]:
    """The Agent call ids of the runs whose task notification this user message replays to the model."""

    if not isinstance(message, UserMessage):
        return []
    notices = _message_text(message.content).split("<task-notification>")[1:]
    return [call for call in (_tag(notice, "tool-use-id") for notice in notices) if call]


def _allows_subagents(params: dict[str, Any]) -> bool:
    """Whether the turn's model may start subagents; a client that does not say allows them."""

    return params.get("allow_subagents") is not False


def _auto_compact_window(params: dict[str, Any]) -> int:
    try:
        window = int(params.get("auto_compact_window_tokens") or 0)
    except (TypeError, ValueError):
        return 0
    return window if window >= 10_000 else 0


def _wants_earlier_compaction(handle: SessionHandle, params: dict[str, Any]) -> bool:
    """Whether this turn asks for an earlier compaction than the live CLI has.

    The window is fixed when the CLI starts, so only a request for an earlier
    compaction restarts the CLI.  A turn that asks for none, or for a later
    one, keeps the live session: compacting early is the safe side, and a
    session must not restart every time the Harness plan changes.
    """

    requested = _auto_compact_window(params)
    return bool(requested and (not handle.auto_compact_window or requested < handle.auto_compact_window))


# How many events a turn keeps while no Worker is connected, and how long a
# Portal tool call of the turn waits for the next Worker.  A Worker handover
# leaves the turn running here; the next Worker attaches within seconds.
DETACHED_EVENT_LIMIT = 5000
DETACHED_TOOL_WAIT_SECONDS = 540.0


class WorkerConnectionLost(ConnectionError):
    """The Worker connection of a turn closed before a tool call was answered."""


@dataclass
class TurnConnection:
    """The current Worker connection for a turn that may outlive its socket.

    A Worker handover does not interrupt the turn: the retiring Worker closes
    its connection and the next Worker attaches to the same turn (``attach``).
    In between, the turn's events are kept and delivered to the next Worker
    first, and a Portal tool call waits for it, so the Chat and the tools see
    the turn as if no handover happened.
    """

    emit: Callable[[dict[str, Any]], Awaitable[None]]
    dispatch_tool: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None
    detached: bool = False
    held: list[dict[str, Any]] = field(default_factory=list)
    dropped: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _attached: asyncio.Event = field(default_factory=asyncio.Event)

    def __post_init__(self) -> None:
        self._attached.set()

    def _detach(self) -> None:
        self.detached = True
        self._attached.clear()

    def _hold(self, event: dict[str, Any]) -> None:
        self.held.append(event)
        if len(self.held) > DETACHED_EVENT_LIMIT:
            del self.held[0]
            self.dropped += 1

    async def send_event(self, event: dict[str, Any]) -> None:
        async with self._lock:
            if not self.detached:
                try:
                    await self.emit(event)
                    return
                except Exception:
                    # The Worker closed its connection (a handover, or it
                    # died); the next Worker attaches to this turn.
                    self._detach()
            self._hold(event)

    async def attach(
        self,
        emit: Callable[[dict[str, Any]], Awaitable[None]],
        dispatch_tool: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None,
    ) -> None:
        """Make a new Worker connection current, after the events it missed."""

        async with self._lock:
            try:
                while self.held:
                    await emit(self.held[0])
                    del self.held[0]
            except Exception:
                return
            self.emit = emit
            self.dispatch_tool = dispatch_tool
            self.detached = False
            self._attached.set()

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        deadline = time.monotonic() + DETACHED_TOOL_WAIT_SECONDS
        while True:
            dispatch = None if self.detached else self.dispatch_tool
            if dispatch is not None:
                try:
                    return await dispatch(name, arguments)
                except WorkerConnectionLost:
                    # The call never reached a Worker: send it to the next one.
                    if self.dispatch_tool is dispatch:
                        self._detach()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("No Worker connection for Claude Harness tool")
            try:
                await asyncio.wait_for(self._attached.wait(), remaining)
            except asyncio.TimeoutError:
                raise RuntimeError("No Worker connection for Claude Harness tool") from None


def _session_location(params: dict[str, Any]) -> tuple[str, Path, Path]:
    """The session key, working directory and Claude config directory a session starts with."""

    session_key = str(params.get("session_key") or "").strip()
    cwd = Path(str(params.get("cwd") or "")).expanduser().resolve(strict=False)
    config_dir = Path(str(params.get("config_dir") or "")).expanduser().resolve(strict=False)
    if not session_key or not cwd.is_absolute() or not config_dir.is_absolute():
        raise ValueError("session_key, absolute cwd, and absolute config_dir are required")
    return session_key, cwd, config_dir


def _permission_mode(params: dict[str, Any]) -> str:
    permission_mode = str(params.get("permission_mode") or "dontAsk").strip()
    if permission_mode not in {"dontAsk", "bypassPermissions"}:
        raise ValueError("Unsupported Claude permission mode")
    return permission_mode


def _cli_environment(
    params: dict[str, Any], provider_env: dict[str, str], config_dir: Path, auto_compact_window: int,
) -> dict[str, str]:
    """The environment the session's CLI starts with, over the daemon's own."""

    env = {
        **provider_env,
        "CLAUDE_CONFIG_DIR": str(config_dir),
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        # The CLI inherits the daemon's environment, which holds the Portal
        # database for model API registrations; only an authorized
        # Resident's shell gets it (below).
        "PORTAL_DATABASE_URL": "",
    }
    # Mask both inherited identity aliases, including for a human session.
    for key in ("PORTAL_RESIDENT_ID", "PORTAL_LINEAGE_ID"):
        mirror_product_env(env, key, "")
    env.update(resident_identity_environment(params.get("resident_environment")))
    if params.get("inherit_portal_database"):
        if not env["PORTAL_RESIDENT_ID"]:
            raise ValueError("Portal database environment requires Resident identity")
        env.update(portal_database_environment(True))
    if auto_compact_window:
        # Claude Code reads its auto-compaction window when the CLI starts.
        env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = str(auto_compact_window)
    return env


def _agent_options(
    params: dict[str, Any], *, model: str, effort: str, cwd: Path, session_id: str,
    permission_mode: str, env: dict[str, str], bridge: ClaudeToolBridge,
) -> ClaudeAgentOptions:
    is_owner = bool(params.get("is_owner"))
    # Always Claude Code's own system prompt: the SDK turns a missing one into
    # ``--system-prompt ""``, an empty system prompt.  The system prompt is
    # not part of the session history, so a resumed process has exactly what
    # this start gives it.
    system_prompt = {"type": "preset", "preset": "claude_code"}
    system_prompt_append = str(params.get("system_prompt_append") or "").strip()
    if system_prompt_append:
        system_prompt["append"] = system_prompt_append
    return ClaudeAgentOptions(
        model=model,
        cwd=cwd,
        resume=session_id or None,
        effort=effort or None,
        system_prompt=system_prompt,
        allowed_tools=[*(OWNER_TOOLS if is_owner else NON_OWNER_TOOLS), *bridge.allowed_tools],
        mcp_servers=bridge.servers,
        disallowed_tools=[
            *([] if is_owner else NON_OWNER_DISALLOWED_TOOLS),
            *([] if _allows_subagents(params) else SUBAGENT_TOOLS),
        ],
        permission_mode=permission_mode,
        add_dirs=[Path(str(item)).expanduser().resolve(strict=False) for item in params.get("add_dirs") or []],
        hooks=None,
        setting_sources=[],
        skills=[],
        include_partial_messages=False,
        env=env,
        stderr=lambda line: print(f"Claude Code stderr: {line}", flush=True),
        max_buffer_size=CLI_MESSAGE_BUFFER_BYTES,
        extra_args=dict(CLI_ARGS),
    )


async def _disconnect(handle: SessionHandle) -> None:
    try:
        await asyncio.wait_for(handle.client.disconnect(), timeout=30)
    except Exception:
        pass


@dataclass(frozen=True)
class TurnDelivery:
    """What a turn sends the CLI and the UUID it goes under.

    ``recovered`` says how a turn an earlier attempt started is delivered.
    """

    content: Any
    prompt_uuid: str
    recovered: dict[str, Any] | None = None


async def _turn_delivery(
    params: dict[str, Any], handle: SessionHandle, turn_id: str, prompt: str, blocks: Any, media: list[Any],
) -> TurnDelivery:
    prompt_uuid = _turn_prompt_uuid(str(params.get("session_key") or ""), turn_id)
    content = blocks if blocks is not None else prompt
    continue_prompt = str(params.get("continue_prompt") or "")
    if not continue_prompt:
        return TurnDelivery(content, prompt_uuid)
    # An earlier attempt started this turn, and this daemon no longer has
    # it.  Once the CLI had taken the turn's input, the turn is continued in
    # the session; the input is never sent twice.
    config_dir = Path(str(params.get("config_dir") or "")).expanduser().resolve(strict=False)
    if await asyncio.to_thread(_transcript_has_message, config_dir, handle.session_id, prompt_uuid):
        return TurnDelivery(continue_prompt, str(uuid.uuid4()), {"delivery": "continued", "prompt": continue_prompt})
    return TurnDelivery(content, prompt_uuid, {"delivery": "first_delivery", "prompt": prompt, "imageCount": len(media)})


def _tool_call_event(block: ToolUseBlock | ServerToolUseBlock) -> dict[str, Any]:
    if block.name == "Bash":
        item = {
            "type": "commandExecution",
            "command": str(block.input.get("command") or ""),
            "status": "running",
            "id": block.id,
        }
        return {"method": "item/started", "params": {"item": item}}
    return {"type": "response_item", "payload": {
        "type": "function_call", "name": block.name, "call_id": block.id, "arguments": block.input,
    }}


def _tool_output_event(block: ToolResultBlock | ServerToolResultBlock) -> dict[str, Any]:
    return {"type": "response_item", "payload": {
        "type": "function_call_output", "call_id": block.tool_use_id,
        "output": block.content, "is_error": _tool_result_failed(block),
    }}


class ClaudeTurn:
    """One Portal turn: its prompt to the session's CLI, and the CLI's messages up to the turn's answer.

    A Portal turn may span several CLI turns: when steering the CLI took only
    after an answer made it run another turn (see ``ClaudeCodeDaemon.steer``),
    and when subagents went on in the background after an answer, each CLI
    turn that takes one of their notifications.  Each message is handled by
    its kind (``_handler``), each block of an answer by its kind
    (``_block_handler``).
    """

    def __init__(
        self, daemon: "ClaudeCodeDaemon", handle: SessionHandle, params: dict[str, Any],
        emit: Callable[[dict[str, Any]], Awaitable[None]], turn_id: str,
    ) -> None:
        self.daemon = daemon
        self.handle = handle
        self.params = params
        self.emit = emit
        self.turn_id = turn_id
        self.prompt_uuid = ""
        self.prompt_seen = False
        # Answer text not yet given: commentary, unless the turn's answer
        # is empty and this text becomes it.
        self.pending_text_items: list[dict[str, Any]] = []
        self.assistant_message_index = 0
        self.subagent_message_indices: dict[str, int] = {}
        self.result: ResultMessage | None = None
        self.attached_event_session_id = handle.session_id
        # Earlier answers of this turn, which another CLI turn went on from.
        self.answered: list[ResultMessage] = []
        # The turn waits on subagents in the background; since the last
        # answer the CLI started a turn; this CLI turn ended on a result
        # that belongs to earlier work.
        self.waiting_background = False
        self.cli_turn = False
        self.ended_on_earlier_result = False
        # Runs in the background whose task ended but whose notification
        # the model has not taken: the CLI takes each in a turn of its own,
        # or replays it into a turn already running.  ``told``: runs whose
        # notification came before the daemon saw their task end.
        self.untold: list[str] = []
        self.told: set[str] = set()
        self._handlers = (
            (SystemMessage, self._on_system),
            (RateLimitEvent, self._on_rate_limit),
            (StreamEvent, self._on_stream_event),
            (AssistantMessage, self._on_assistant),
            (UserMessage, self._on_user),
            (ResultMessage, self._on_result),
        )
        self._block_handlers = (
            (TextBlock, self._on_text_block),
            (ThinkingBlock, self._on_thinking_block),
            ((ToolUseBlock, ServerToolUseBlock), self._on_tool_use_block),
            ((ToolResultBlock, ServerToolResultBlock), self._on_tool_result_block),
        )

    async def run(self, delivery: TurnDelivery) -> dict[str, Any]:
        """Run the turn on the session (the caller holds the session's lock and a slot)."""

        self._open()
        await self._emit_started(delivery.recovered)
        try:
            if self.handle.tool_bridge is not None:
                self.handle.tool_bridge.dispatch = self.params.get("_tool_dispatch")
            self.prompt_uuid = delivery.prompt_uuid
            await self.handle.client.query(_prompt_message(delivery.content, self.handle.session_id, delivery.prompt_uuid))
            await self._read_answer()
            return await self._complete()
        except Exception as exc:
            if self.handle.interrupted_turn_id == self.turn_id:
                await self.daemon._forget_turn_task((self.handle.key, self.turn_id))
                raise ClaudeTurnInterrupted("Claude Code turn interrupted") from exc
            if isinstance(exc, ClaudeSDKError):
                # The SDK stops reading the CLI after a transport error,
                # so the next turn reconnects and resumes the session.
                self.handle.invalidated = True
            raise
        finally:
            try:
                await self._flush_commentary()
            finally:
                self._close()

    def _open(self) -> None:
        handle = self.handle
        handle.active_turn_id = self.turn_id
        handle.interrupted_turn_id = ""
        handle.last_used = time.monotonic()
        handle.pending_steers.clear()
        handle.steer_keys.clear()
        handle.steering_open = True

    def _close(self) -> None:
        handle = self.handle
        if handle.tool_bridge is not None:
            handle.tool_bridge.dispatch = None
        handle.steering_open = False
        handle.active_turn_id = ""
        handle.last_used = time.monotonic()

    async def _emit_started(self, recovered: dict[str, Any] | None) -> None:
        await self.emit({
            "method": "turn/started",
            "params": {"threadId": self.handle.session_id, "turn": {"id": self.turn_id}},
        })
        if recovered is not None:
            await self.emit({
                "method": "turn/recovered",
                "params": {"threadId": self.handle.session_id, "turnId": self.turn_id, **recovered},
            })

    async def _read_answer(self) -> None:
        """Read the CLI's turns until one gives this turn's answer (``self.result``)."""

        while True:
            self.ended_on_earlier_result = False
            stream_state: dict[str, Any] = {}
            self.cli_turn = False
            async for message in _turn_messages(
                self.handle.client, stream_state, self._first_message_limit(), self._message_limit(),
            ):
                await self._take(message)
            if self.result is None and self.answered and stream_state.get("timed_out"):
                self._let_last_answer_stand()
            self.waiting_background = self._waits_on_background()
            if self.result is not None and (self.handle.pending_steers or self.waiting_background) \
                    and not self.result.is_error:
                await self._hold_answer()
                continue
            if self.result is not None or not self.ended_on_earlier_result:
                return
            # Text from that earlier work stays visible as commentary.
            await self._flush_commentary()

    def _first_message_limit(self) -> float:
        """How long the CLI may take to start on steering queued behind an earlier answer."""

        return STEER_START_SECONDS if self.answered and not self.waiting_background else 0.0

    def _message_limit(self) -> Callable[[], float | None] | None:
        return self._background_wait_limit if self.waiting_background else None

    def _background_wait_limit(self) -> float | None:
        if self.cli_turn or self.handle.subagent_runs.running_in_background():
            return None
        # A stopped turn reads no notification of the runs it stopped.
        return 0.5 if self.handle.interrupted_turn_id == self.turn_id else BACKGROUND_SETTLE_SECONDS

    def _let_last_answer_stand(self) -> None:
        """The CLI did not start on the steering, or on the end of a run stopped in the background."""

        if self.handle.interrupted_turn_id == self.turn_id:
            raise RuntimeError("Claude Code turn interrupted while its subagents ran in the background")
        self.handle.pending_steers.clear()
        self.untold.clear()
        self.handle.steering_open = False
        self.result = self.answered.pop()
        self.pending_text_items.clear()

    def _waits_on_background(self) -> bool:
        result = self.result
        return bool(
            result is not None and not result.is_error and self.handle.interrupted_turn_id != self.turn_id
            and (self.handle.subagent_runs.running_in_background() or self.untold)
        )

    async def _hold_answer(self) -> None:
        """Keep the answer as an earlier one: the CLI answers steering that arrived
        after the last step in another turn, which is still this one.  So is each
        turn in which it takes the notification of a subagent the model left
        running in the background."""

        self.answered.append(self.result)
        self.result = None
        # Its answer so far stays visible as commentary.
        earlier_answer = str(self.answered[-1].result or "").strip()
        if not self.pending_text_items and earlier_answer:
            self.pending_text_items.append({
                "id": f"{self.turn_id}:answer:{len(self.answered)}",
                "type": "agentMessage", "text": earlier_answer,
            })
        await self._flush_commentary()

    async def _complete(self) -> dict[str, Any]:
        result = self._final_result()
        response = str(result.result or "").strip() or "".join(
            item["text"] for item in self.pending_text_items
        ).strip()
        self.pending_text_items.clear()
        if response:
            await self._emit_text({
                "id": f"{self.turn_id}:final", "type": "agentMessage",
                "phase": "final_answer", "text": response,
            })
        usage, model_usage, duration_ms = self._totals(result)
        await self.emit({
            "method": "turn/completed",
            "params": {"threadId": self.handle.session_id, "turn": {"id": self.turn_id}, "usage": usage},
        })
        return {
            "session_id": self.handle.session_id,
            "turn_id": self.turn_id,
            "response": response,
            "usage": usage,
            "model_usage": model_usage,
            "duration_ms": duration_ms,
        }

    def _final_result(self) -> ResultMessage:
        result = self.result
        if result is None:
            raise RuntimeError("Claude Code turn ended without a ResultMessage")
        if result.is_error:
            detail = "; ".join(result.errors or []) or str(result.result or result.subtype)
            if "failed to authenticate" in detail.lower():
                self.handle.invalidated = True
            raise RuntimeError(f"Claude Code turn failed: {detail}")
        return result

    def _totals(self, result: ResultMessage) -> tuple[Any, Any, int]:
        """The usage, model usage and duration of all the turn's answers."""

        usage = _json_safe(result.usage or {})
        model_usage = _json_safe(result.model_usage or {})
        duration_ms = int(result.duration_ms or 0)
        for earlier in self.answered:
            usage = _summed(_json_safe(earlier.usage or {}), usage)
            model_usage = _summed(_json_safe(earlier.model_usage or {}), model_usage)
            duration_ms += int(earlier.duration_ms or 0)
        return usage, model_usage, duration_ms

    async def _take(self, message: Any) -> None:
        self._note_cli_turn(message)
        if self._is_reported_raw(message):
            await self.emit({
                "method": "claude/message",
                "params": {
                    "sessionId": self.handle.session_id,
                    "turnId": self.turn_id,
                    "messageType": message.__class__.__name__,
                    "message": _json_safe(message),
                },
            })
        handler = self._handler(message)
        if handler is not None:
            await handler(message)

    def _handler(self, message: Any) -> Callable[[Any], Awaitable[None]] | None:
        if isinstance(message, (AssistantMessage, UserMessage)) and message.parent_tool_use_id:
            return self._on_subagent_message
        return next((handler for kind, handler in self._handlers if isinstance(message, kind)), None)

    def _block_handler(self, block: Any) -> Callable[[Any, int, dict[str, Any]], Awaitable[None]] | None:
        return next((handler for kind, handler in self._block_handlers if isinstance(block, kind)), None)

    def _ours(self, result: ResultMessage) -> bool:
        """Whether a result ends this turn's work: one before the prompt's echo ends earlier work."""

        return self.prompt_seen or bool(result.is_error)

    def _is_reported_raw(self, message: Any) -> bool:
        return (
            (isinstance(message, ResultMessage) and self._ours(message))
            or (isinstance(message, SystemMessage) and message.subtype == "init")
        )

    def _note_cli_turn(self, message: Any) -> None:
        if not isinstance(message, (AssistantMessage, UserMessage)) or message.parent_tool_use_id:
            return
        if (self.waiting_background and not self.cli_turn and self.untold
                and not _notified_runs(message)
                and str(getattr(message, "uuid", None) or "") not in self.handle.pending_steers):
            # The CLI started a turn of its own for one notification.
            self.untold.pop(0)
        self.cli_turn = True

    async def _on_system(self, message: SystemMessage) -> None:
        handle = self.handle
        await self._attach_session(str(message.data.get("session_id") or message.data.get("sessionId") or ""))
        running = set(handle.subagent_runs.running_in_background())
        followed = _observe_subagent_task(handle, message)
        if followed:
            await self._emit_subagent_progress(followed)
        ended_card = _ended_subagent_card(handle, message, self.turn_id)
        if ended_card is not None:
            await self._emit_item(ended_card)
        for ended in running - set(handle.subagent_runs.running_in_background()):
            if ended in self.told:
                self.told.discard(ended)
            else:
                self.untold.append(ended)

    async def _on_rate_limit(self, message: RateLimitEvent) -> None:
        self.daemon.account_usage.observe(
            str(self.params.get("provider_profile") or self.daemon.default_profile),
            str(self.params.get("config_dir") or ""),
            message.rate_limit_info.raw,
        )

    async def _on_stream_event(self, message: StreamEvent) -> None:
        await self._attach_session(str(message.session_id or ""))

    async def _on_subagent_message(self, message: AssistantMessage | UserMessage) -> None:
        parent = str(message.parent_tool_use_id)
        child_index = self.subagent_message_indices.get(parent, 0) + 1
        self.subagent_message_indices[parent] = child_index
        model = str(getattr(message, "model", "") or "")
        for child_event in _subagent_events(message, self.turn_id, child_index):
            self.handle.subagent_runs.observe(parent, child_event["params"]["event"]["params"]["item"], model=model)
            await self.emit(child_event)
        await self._emit_subagent_progress(parent)

    async def _on_assistant(self, message: AssistantMessage) -> None:
        await self._attach_session(str(message.session_id or ""))
        await _report_context_usage(self.handle, self.turn_id, self.emit)
        self.assistant_message_index += 1
        # SDK message creation is not model request dispatch.
        timing: dict[str, Any] = {}
        for block_index, block in enumerate(message.content):
            handler = self._block_handler(block)
            if handler is not None:
                await handler(block, block_index, timing)

    async def _on_text_block(self, block: TextBlock, block_index: int, timing: dict[str, Any]) -> None:
        self.pending_text_items.append({
            "id": f"{self.turn_id}:{self.assistant_message_index}:text:{block_index}",
            "type": "agentMessage", "text": block.text,
            "_request_timing": timing,
        })

    async def _on_thinking_block(self, block: ThinkingBlock, block_index: int, timing: dict[str, Any]) -> None:
        await self.emit({
            "method": "item/completed",
            "params": {
                "turnId": self.turn_id,
                "request_timing": timing,
                "item": {
                    "id": f"{self.turn_id}:{self.assistant_message_index}:thinking:{block_index}",
                    "type": "reasoning",
                    "thinking": block.thinking,
                    "signature": block.signature,
                },
            },
        })

    async def _on_tool_use_block(
        self, block: ToolUseBlock | ServerToolUseBlock, _block_index: int, _timing: dict[str, Any],
    ) -> None:
        await self._flush_commentary()
        if block.name in SUBAGENT_TOOLS:
            arguments = block.input if isinstance(block.input, dict) else {}
            self.handle.subagent_runs.start(
                block.id, tool_use_id=block.id,
                agent_type=str(arguments.get("subagent_type") or ""),
                description=str(arguments.get("description") or ""),
                background=bool(arguments.get("run_in_background")),
                model=str(arguments.get("model") or ""),
                prompt=str(arguments.get("prompt") or ""),
            )
            await self._emit_subagent_progress(block.id)
        await self.emit(_tool_call_event(block))

    async def _on_tool_result_block(
        self, block: ToolResultBlock | ServerToolResultBlock, _block_index: int, _timing: dict[str, Any],
    ) -> None:
        await self.emit(_tool_output_event(block))

    async def _on_user(self, message: UserMessage) -> None:
        if getattr(message, "uuid", None) == self.prompt_uuid:
            self.prompt_seen = True
        # The CLI started on a steered message.
        self.handle.pending_steers.discard(str(getattr(message, "uuid", None) or ""))
        for block in message.content if isinstance(message.content, list) else []:
            if isinstance(block, (ToolResultBlock, ServerToolResultBlock)):
                await self.emit(_tool_output_event(block))
        for tool_use_id in _subagents_in_background(self.handle, message):
            await self._emit_subagent_progress(tool_use_id)
        for tool_use_id in _notified_runs(message):
            if tool_use_id in self.untold:
                self.untold.remove(tool_use_id)
            else:
                self.told.add(tool_use_id)
        for card in _subagent_cards(self.handle, message, self.turn_id):
            await self._emit_item(card)

    async def _on_result(self, message: ResultMessage) -> None:
        await self._attach_session(str(message.session_id or ""))
        if not self._ours(message):
            self.ended_on_earlier_result = True
            return
        self.result = message
        if not self.handle.pending_steers and not self.handle.subagent_runs.running_in_background() and not self.untold:
            self.handle.steering_open = False

    async def _attach_session(self, session_id: str) -> None:
        discovered = str(session_id or "").strip()
        if not discovered:
            return
        await self.daemon._attach_session_id(self.handle, discovered)
        if discovered == self.attached_event_session_id:
            return
        self.attached_event_session_id = discovered
        await self.emit({"method": "thread/attached", "params": {"threadId": discovered, "turnId": self.turn_id}})

    async def _emit_text(self, item: dict[str, Any]) -> None:
        item = dict(item)
        timing = item.pop("_request_timing", {})
        await self.emit({
            "method": "item/completed",
            "params": {"threadId": self.handle.session_id, "turnId": self.turn_id, "item": item, "request_timing": timing},
        })

    async def _emit_item(self, item: dict[str, Any]) -> None:
        await self.emit({"method": "item/completed", "params": {
            "threadId": self.handle.session_id, "turnId": self.turn_id, "item": item,
        }})

    async def _flush_commentary(self) -> None:
        for item in self.pending_text_items:
            await self._emit_text({**item, "phase": "commentary"})
        self.pending_text_items.clear()

    async def _emit_subagent_progress(self, tool_use_id: str) -> None:
        # The running card of a subagent the model started, when it changed.
        card = self.handle.subagent_runs.live(tool_use_id, turn_id=self.turn_id)
        if card is not None:
            await self.emit({"method": PROGRESS_METHOD, "params": {
                "threadId": self.handle.session_id, "turnId": self.turn_id, "item": card,
            }})


class ClaudeCompaction:
    """Claude Code's own ``/compact`` of a session, read up to its result."""

    def __init__(self, daemon: "ClaudeCodeDaemon", handle: SessionHandle, session_id: str) -> None:
        self.daemon = daemon
        self.handle = handle
        self.session_id = session_id
        self.prompt_uuid = ""
        self.prompt_seen = False
        self.outcome: dict[str, Any] = {"compacted": False, "reason": ""}
        self.result: ResultMessage | None = None

    async def run(self) -> dict[str, Any]:
        self.prompt_uuid = str(uuid.uuid4())
        await self.handle.client.query(_prompt_message("/compact", self.handle.session_id, self.prompt_uuid))
        while self.result is None:
            received = False
            async for message in self.handle.client.receive_response():
                received = True
                await self._take(message)
            if not received:
                break
        result = self.result
        if result is None:
            raise RuntimeError("Claude Code /compact ended without a result")
        if result.is_error:
            detail = "; ".join(result.errors or []) or str(result.result or result.subtype)
            raise RuntimeError(f"Claude Code compaction failed: {detail}")
        if not self.outcome["compacted"] and not self.outcome["reason"]:
            self.outcome["reason"] = str(result.result or "").strip() or "not_compacted"
        return self.outcome

    async def _take(self, message: Any) -> None:
        if isinstance(message, UserMessage) and getattr(message, "uuid", None) == self.prompt_uuid:
            self.prompt_seen = True
        if isinstance(message, SystemMessage):
            await self._on_system(message)
        elif isinstance(message, ResultMessage):
            self._on_result(message)

    async def _on_system(self, message: SystemMessage) -> None:
        data = message.data or {}
        observed_session_id = str(data.get("session_id") or "")
        if observed_session_id and observed_session_id != self.session_id:
            raise ValueError("Claude session identity changed")
        await self.daemon._attach_session_id(self.handle, observed_session_id)
        # The CLI may emit compact evidence before replaying this request's
        # user message. Keep it until the matching Result, or discard it with
        # an older Result.
        if message.subtype == "compact_boundary":
            metadata = data.get("compact_metadata") or {}
            self.outcome.update(
                compacted=True,
                reason="",
                pre_tokens=int(metadata.get("pre_tokens") or 0),
                context_tokens=int(metadata.get("post_tokens") or 0),
                duration_ms=int(metadata.get("duration_ms") or 0),
            )
        elif data.get("compact_result") == "failed":
            self.outcome = {
                "compacted": False,
                "reason": str(data.get("compact_error") or "compaction_failed"),
            }

    def _on_result(self, message: ResultMessage) -> None:
        if not self.prompt_seen and not message.is_error:
            self.outcome = {"compacted": False, "reason": ""}
            return
        if message.session_id and message.session_id != self.session_id:
            raise ValueError("Claude session identity changed")
        self.result = message


class ClaudeCodeDaemon:
    """The loaded Claude Code sessions and what the Worker asks of them.

    A session is one CLI process (``SessionHandle``), started for a session
    key and the options of its turns, kept while it is used; a turn runs on
    it as a ``ClaudeTurn``, a compaction as a ``ClaudeCompaction``.
    """

    def __init__(self) -> None:
        self.max_concurrency = max(1, int(os.getenv("PORTAL_CLAUDE_CODE_MAX_CONCURRENCY", "8")))
        self.max_loaded_sessions = max(1, int(os.getenv("PORTAL_CLAUDE_CODE_MAX_LOADED_SESSIONS", "24")))
        self.idle_seconds = max(60, int(os.getenv("PORTAL_CLAUDE_CODE_IDLE_SECONDS", "3600")))
        self.default_profile = os.getenv("PORTAL_CLAUDE_CODE_PROVIDER_PROFILE", DEFAULT_PROVIDER_PROFILE).strip()
        self._slots = asyncio.Semaphore(self.max_concurrency)
        self._sessions: dict[str, SessionHandle] = {}
        self._session_ids: dict[str, str] = {}
        self._session_setup_locks: dict[str, asyncio.Lock] = {}
        self._sessions_lock = asyncio.Lock()
        self._turn_tasks: dict[
            tuple[str, str],
            asyncio.Task[dict[str, Any]],
        ] = {}
        self._turn_connections: dict[tuple[str, str], TurnConnection] = {}
        self._turn_tasks_lock = asyncio.Lock()
        self.account_usage = ClaudeAccountUsage()

    @staticmethod
    def _signature(params: dict[str, Any], model: str) -> tuple[Any, ...]:
        return (
            str(params.get("cwd") or ""),
            str(params.get("config_dir") or ""),
            str(params.get("provider_profile") or ""),
            model,
            str(params.get("effort") or ""),
            bool(params.get("is_owner")),
            str(params.get("permission_profile") or ""),
            str(params.get("permission_mode") or ""),
            str(params.get("system_prompt_append") or ""),
            tuple(str(item) for item in params.get("add_dirs") or []),
            json.dumps(params.get("tools") or [], sort_keys=True),
            # Bind the native session to the canonical Resident identity.
            json.dumps(resident_identity_fingerprint(params.get("resident_environment")), sort_keys=True),
            _allows_subagents(params),
        )

    async def _create_handle(self, params: dict[str, Any]) -> SessionHandle:
        session_key, cwd, config_dir = _session_location(params)
        profile_id = str(params.get("provider_profile") or self.default_profile).strip()
        provider_env, profile = _provider_environment(profile_id)
        if claude_provider_catalog.is_native(profile):
            expected_dir = claude_provider_catalog.config_directory(profile).resolve(strict=False)
            if config_dir != expected_dir:
                raise ValueError("Claude login directory does not match the selected account route")
        config_dir.mkdir(parents=True, exist_ok=True)
        model = _validated_model(profile, str(params.get("model") or ""))
        effort = _validated_effort(profile, model, str(params.get("effort") or ""))
        if claude_provider_catalog.is_declared(profile):
            provider_env = {**provider_env, **dict.fromkeys(GATEWAY_MODEL_VARIABLES, model)}
        permission_mode = _permission_mode(params)
        session_id = str(params.get("session_id") or "").strip()
        if session_id:
            self._relocate_resumed_session_file(session_id, cwd, config_dir)
        auto_compact_window = _auto_compact_window(params)
        env = _cli_environment(params, provider_env, config_dir, auto_compact_window)
        bridge = ClaudeToolBridge(params.get("tools") or [])
        client = ClaudeSDKClient(_agent_options(
            params, model=model, effort=effort, cwd=cwd, session_id=session_id,
            permission_mode=permission_mode, env=env, bridge=bridge,
        ))
        await client.connect()
        return SessionHandle(
            key=session_key,
            signature=self._signature(params, model),
            client=client,
            session_id=session_id,
            tool_bridge=bridge,
            auto_compact_window=auto_compact_window,
        )

    async def _handle_for(self, params: dict[str, Any]) -> SessionHandle:
        session_key = str(params.get("session_key") or "").strip()
        async with self._sessions_lock:
            setup_lock = self._session_setup_locks.setdefault(session_key, asyncio.Lock())
        async with setup_lock:
            profile = _provider_profile(str(params.get("provider_profile") or self.default_profile).strip())
            model = _validated_model(profile, str(params.get("model") or ""))
            signature = self._signature(params, model)
            previous: SessionHandle | None = None
            async with self._sessions_lock:
                handle = self._sessions.get(session_key)
                if (
                    handle and handle.signature == signature and not handle.invalidated
                    and not _wants_earlier_compaction(handle, params)
                ):
                    handle.last_used = time.monotonic()
                    return handle
                if handle and handle.lock.locked():
                    raise RuntimeError("cannot change Claude Code session options during an active turn")
                if handle:
                    previous = handle
                    self._forget_session(session_key, handle)
            if previous:
                await _disconnect(previous)
            if previous is not None and previous.auto_compact_window:
                # The session keeps the earliest compaction it was given.
                requested = _auto_compact_window(params)
                params = {**params, "auto_compact_window_tokens": (
                    min(requested, previous.auto_compact_window)
                    if requested else previous.auto_compact_window
                )}
            handle = await self._create_handle(params)
            async with self._sessions_lock:
                self._sessions[session_key] = handle
                if handle.session_id:
                    self._session_ids[handle.session_id] = session_key
            return handle

    def _forget_session(self, session_key: str, handle: SessionHandle) -> None:
        """Unload a session (the caller holds ``_sessions_lock``)."""

        self._sessions.pop(session_key, None)
        if handle.session_id:
            self._session_ids.pop(handle.session_id, None)

    async def _loaded_session(self, params: dict[str, Any]) -> tuple[str, str, SessionHandle | None]:
        """The session key and CLI session id ``params`` give, and the loaded session either names."""

        session_key = str(params.get("session_key") or "").strip()
        session_id = str(params.get("session_id") or "").strip()
        async with self._sessions_lock:
            if not session_key and session_id:
                session_key = self._session_ids.get(session_id, "")
            return session_key, session_id, self._sessions.get(session_key)

    async def _attach_session_id(self, handle: SessionHandle, session_id: str) -> bool:
        discovered = str(session_id or "").strip()
        if not discovered or discovered == handle.session_id:
            return False
        async with self._sessions_lock:
            previous = handle.session_id
            handle.session_id = discovered
            if previous and self._session_ids.get(previous) == handle.key:
                self._session_ids.pop(previous, None)
            self._session_ids[discovered] = handle.key
        return True

    async def run_turn(
        self,
        params: dict[str, Any],
        emit: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> dict[str, Any]:
        session_key = str(params.get("session_key") or "").strip()
        turn_id = str(params.get("turn_id") or "").strip()
        if not session_key or not turn_id:
            raise ValueError("session_key and turn_id are required")
        key = (session_key, turn_id)

        async with self._turn_tasks_lock:
            task = self._turn_tasks.get(key)
            if task is None:
                connection = TurnConnection(emit, params.get("_tool_dispatch"))
                turn_params = {**params, "_tool_dispatch": connection.call_tool}
                task = asyncio.create_task(
                    self._run_turn_once(turn_params, connection.send_event),
                    name=f"claude-turn-{turn_id}",
                )
                self._turn_tasks[key] = task
                self._turn_connections[key] = connection

                async def discard_later() -> None:
                    try:
                        await asyncio.shield(task)
                    except BaseException:
                        pass
                    # Keep the result briefly for idempotent retries, but do
                    # not retain a closed Worker socket after the turn ends,
                    # unless it holds events the next Worker has not seen.
                    async with self._turn_tasks_lock:
                        if self._turn_tasks.get(key) is task and not connection.detached:
                            self._turn_connections.pop(key, None)
                    await asyncio.sleep(300)
                    async with self._turn_tasks_lock:
                        if self._turn_tasks.get(key) is task:
                            self._turn_tasks.pop(key, None)
                            self._turn_connections.pop(key, None)

                asyncio.create_task(discard_later())
            elif key in self._turn_connections:
                # A Worker handover leaves the turn running: the next Worker
                # attaches to it and receives the events it missed first.
                await self._turn_connections[key].attach(emit, params.get("_tool_dispatch"))
        return await asyncio.shield(task)

    async def _forget_turn_task(self, key: tuple[str, str]) -> None:
        """Drop the turn the current task runs, so that the turn may run again."""

        async with self._turn_tasks_lock:
            if self._turn_tasks.get(key) is asyncio.current_task():
                self._turn_tasks.pop(key, None)
                self._turn_connections.pop(key, None)

    async def _run_turn_once(
        self,
        params: dict[str, Any],
        emit: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> dict[str, Any]:
        prompt, media, blocks = await self._turn_prompt(params)
        handle = await self._handle_for(params)
        turn_id = str(params.get("turn_id") or f"claude_turn_{os.urandom(8).hex()}")
        delivery = await _turn_delivery(params, handle, turn_id, prompt, blocks, media)
        turn = ClaudeTurn(self, handle, params, emit, turn_id)
        async with handle.lock:
            async with self._slots:
                return await turn.run(delivery)

    async def _turn_prompt(self, params: dict[str, Any]) -> tuple[str, list[Any], Any]:
        """The turn's prompt, its images, and the content blocks that carry both (``None``: text only)."""

        prompt = str(params.get("prompt") or "")
        media = params.get("input_media") or []
        if not prompt and not media:
            raise ValueError("prompt required")
        if not media:
            return prompt, media, None
        profile = _provider_profile(str(params.get("provider_profile") or self.default_profile))
        if not claude_provider_catalog.model_catalog(profile)["capabilities"]["media"]:
            raise ValueError("Selected Claude route does not support images")
        return prompt, media, await asyncio.to_thread(claude_input_media.content_blocks, prompt, media)

    async def steer(self, params: dict[str, Any]) -> dict[str, Any]:
        """Give the running turn more input.

        The CLI queues a user message that arrives during a turn and takes it
        after the turn's current step, so the turn goes on with it.  When the
        turn has no further step the CLI answers it in a turn of its own right
        after; ``ClaudeTurn`` reads that turn as part of this one.  Once the
        turn's result has arrived steering is closed: a later message would
        start a turn that no Portal turn reads.
        """

        text = str(params.get("text") or "")
        if not text.strip():
            raise ValueError("Steering needs text")
        _session_key, session_id, handle = await self._loaded_session(params)
        if not handle or not handle.active_turn_id:
            return {"steered": False, "reason": "no_active_turn"}
        requested_turn = str(params.get("turn_id") or "").strip()
        if session_id and handle.session_id and session_id != handle.session_id:
            return {"steered": False, "reason": "session_changed"}
        if requested_turn and requested_turn != handle.active_turn_id:
            return {"steered": False, "reason": "turn_changed"}
        if not handle.steering_open or handle.interrupted_turn_id:
            return {"steered": False, "reason": "turn_closing"}
        key = str(params.get("control_id") or "").strip() or text
        if key in handle.steer_keys:
            return {"steered": True, "duplicate": True}
        message_uuid = str(uuid.uuid4())
        # Registered before the write: the turn must not finish without it.
        handle.steer_keys.add(key)
        handle.pending_steers.add(message_uuid)
        try:
            await handle.client.query(_prompt_message(text, handle.session_id, message_uuid))
        except Exception:
            handle.pending_steers.discard(message_uuid)
            handle.steer_keys.discard(key)
            raise
        return {"steered": True, "turn_id": handle.active_turn_id}

    async def stop_subagent(self, params: dict[str, Any]) -> dict[str, Any]:
        """Stop one subagent run the session's model started; its parent turn goes on.

        The CLI then reports the run stopped, and its card ends with that.
        """

        tool_use_id = str(params.get("tool_use_id") or "").strip()
        _session_key, session_id, handle = await self._loaded_session(params)
        if not handle or not handle.client:
            return {"stopped": False, "reason": "no_session"}
        if session_id and handle.session_id and session_id != handle.session_id:
            return {"stopped": False, "reason": "session_changed"}
        task_id = handle.subagent_tasks.get(tool_use_id, "")
        if not task_id:
            return {"stopped": False, "reason": "unknown_subagent"}
        handle.subagent_stops.add(tool_use_id)
        try:
            await handle.client.stop_task(task_id)
        except Exception:
            handle.subagent_stops.discard(tool_use_id)
            raise
        return {"stopped": True, "tool_use_id": tool_use_id, "task_id": task_id}

    async def interrupt(self, params: dict[str, Any]) -> dict[str, Any]:
        _session_key, session_id, handle = await self._loaded_session(params)
        if not handle or not handle.active_turn_id:
            return {"interrupted": False, "reason": "no active Claude Code turn"}
        requested_turn = str(params.get("turn_id") or "").strip()
        if session_id and session_id != handle.session_id:
            return {"interrupted": False, "reason": "Claude session identity changed"}
        if requested_turn and requested_turn != handle.active_turn_id:
            return {"interrupted": False, "reason": "Claude turn identity changed"}
        active_turn_id = handle.active_turn_id
        handle.interrupted_turn_id = active_turn_id
        try:
            await handle.client.interrupt()
        except Exception:
            if handle.interrupted_turn_id == active_turn_id:
                handle.interrupted_turn_id = ""
            raise
        await self._stop_background_subagents(handle)
        return {
            "interrupted": True,
            "session_key": handle.key,
            "session_id": handle.session_id,
            "turn_id": active_turn_id,
        }

    @staticmethod
    async def _stop_background_subagents(handle: SessionHandle) -> None:
        """Stop the subagents an interrupted turn left running in the background.

        Their work would otherwise go on where no Portal turn reads it.
        """

        for tool_use_id in handle.subagent_runs.running_in_background():
            task_id = handle.subagent_tasks.get(tool_use_id, "")
            if not task_id:
                continue
            handle.subagent_stops.add(tool_use_id)
            try:
                await handle.client.stop_task(task_id)
            except Exception:  # noqa: BLE001 - the turn stops all the same
                handle.subagent_stops.discard(tool_use_id)

    async def compact_session(self, params: dict[str, Any]) -> dict[str, Any]:
        """Compact the session's history now, with Claude Code's own ``/compact``.

        Runs between turns, on the session's live CLI when there is one (or a
        CLI that resumes the session).  Claude Code replaces the older history
        with a summary written by the session's model and keeps the newest
        messages.  ``compacted`` is false when the history was too short.
        """

        session_key = str(params.get("session_key") or "").strip()
        session_id = str(params.get("session_id") or "").strip()
        if not session_key:
            raise ValueError("session_key is required")
        if not session_id:
            return {"compacted": False, "reason": "no_session"}
        async with self._sessions_lock:
            handle = self._sessions.get(session_key)
        if handle is None or handle.invalidated:
            # Compaction does not depend on the turn's tools or instructions,
            # so a live CLI is kept whatever it was started with.
            handle = await self._handle_for(params)
        if handle.session_id and handle.session_id != session_id:
            raise ValueError("Claude session identity changed")
        compaction_id = f"compact:{params.get('request_id') or uuid.uuid4().hex}"
        async with handle.lock:
            async with self._slots:
                handle.active_turn_id = compaction_id
                handle.interrupted_turn_id = ""
                handle.last_used = time.monotonic()
                try:
                    outcome = await ClaudeCompaction(self, handle, session_id).run()
                except Exception as exc:
                    if handle.interrupted_turn_id == compaction_id:
                        raise ClaudeTurnInterrupted("Claude Code compaction interrupted") from exc
                    if isinstance(exc, ClaudeSDKError):
                        handle.invalidated = True
                    raise
                finally:
                    handle.active_turn_id = ""
                    handle.last_used = time.monotonic()
        return {"session_id": handle.session_id, **outcome}

    async def session_status(self, params: dict[str, Any]) -> dict[str, Any]:
        session_key, session_id, handle = await self._loaded_session(params)
        if not handle:
            return {
                "loaded": False,
                "active": False,
                "session_key": session_key,
                "session_id": session_id,
                "turn_id": "",
            }
        return {
            "loaded": True,
            "active": bool(handle.active_turn_id),
            "session_key": handle.key,
            "session_id": handle.session_id or session_id,
            "turn_id": handle.active_turn_id,
        }

    async def close_session(self, params: dict[str, Any]) -> dict[str, Any]:
        session_key = str(params.get("session_key") or "").strip()
        session_id = str(params.get("session_id") or "").strip()
        async with self._sessions_lock:
            if not session_key and session_id:
                session_key = self._session_ids.get(session_id, "")
            setup_lock = self._session_setup_locks.get(session_key) if session_key else None
        if setup_lock is None:
            return {"closed": False, "reason": "session not loaded"}
        async with setup_lock:
            async with self._sessions_lock:
                handle = self._sessions.get(session_key)
                if not handle:
                    return {"closed": False, "reason": "session not loaded", "session_key": session_key}
                if handle.active_turn_id or handle.lock.locked():
                    return {
                        "closed": False,
                        "reason": "session has an active turn",
                        "session_key": session_key,
                        "session_id": handle.session_id,
                    }
                self._forget_session(session_key, handle)
            await _disconnect(handle)
            async with self._sessions_lock:
                if self._session_setup_locks.get(session_key) is setup_lock:
                    self._session_setup_locks.pop(session_key, None)
            return {
                "closed": True,
                "session_key": session_key,
                "session_id": handle.session_id or session_id,
            }

    @staticmethod
    def _relocate_resumed_session_file(
        session_id: str,
        cwd: Path,
        config_dir: Path,
    ) -> Path | None:
        transcript_name = f"{session_id}.jsonl"
        if Path(transcript_name).name != transcript_name:
            raise ValueError("invalid Claude Code session id")
        projects_dir = config_dir / "projects"
        target_dir = projects_dir / project_key_for_directory(cwd)
        target_path = target_dir / transcript_name
        if target_path.is_file() and target_path.stat().st_size > 0:
            return target_path
        if not projects_dir.is_dir():
            return None

        candidates: list[Path] = []
        for project_dir in projects_dir.iterdir():
            candidate = project_dir / transcript_name
            try:
                if (
                    project_dir.is_dir()
                    and candidate != target_path
                    and candidate.is_file()
                    and candidate.stat().st_size > 0
                ):
                    candidates.append(candidate)
            except OSError:
                continue
        if not candidates:
            return None
        if len(candidates) != 1:
            raise RuntimeError(
                "Claude Code resume transcript lookup returned "
                f"{len(candidates)} files for session {session_id}"
            )
        if target_path.exists():
            raise RuntimeError(
                "Claude Code resume transcript target already exists but is empty: "
                f"{session_id}"
            )
        target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        candidates[0].rename(target_path)
        return target_path

    @staticmethod
    def _relocate_forked_session_file(
        session_id: str,
        cwd: Path,
        config_dir: Path,
    ) -> Path:
        projects_dir = config_dir / "projects"
        candidates = [
            path
            for path in projects_dir.glob(f"*/{session_id}.jsonl")
            if path.is_file() and path.stat().st_size > 0
        ]
        if len(candidates) != 1:
            raise RuntimeError(
                "Claude Code fork transcript lookup returned "
                f"{len(candidates)} files for session {session_id}"
            )
        source_path = candidates[0]
        target_dir = projects_dir / project_key_for_directory(cwd)
        target_path = target_dir / source_path.name
        if source_path == target_path:
            return target_path
        if target_path.exists():
            raise RuntimeError(
                f"Claude Code fork transcript already exists for target workspace: {session_id}"
            )
        target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        source_path.rename(target_path)
        return target_path

    @staticmethod
    async def _fork_session_file(
        source_session_id: str,
        cwd: Path,
        config_dir: Path,
        title: str,
    ) -> str:
        script = (
            "import json,sys\n"
            "from claude_agent_sdk import fork_session\n"
            "result=fork_session(sys.argv[1], title=(sys.argv[2] or None))\n"
            "print(json.dumps({'session_id': result.session_id}, separators=(',', ':')))\n"
        )
        env = dict(os.environ)
        env.pop("PORTAL_DATABASE_URL", None)
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            source_session_id,
            title,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=90)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError("Claude Code session fork timed out")
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"Claude Code session fork failed: {detail[:500]}")
        try:
            payload = json.loads(stdout.decode("utf-8"))
        except Exception as exc:
            raise RuntimeError("Claude Code session fork returned invalid output") from exc
        session_id = str(payload.get("session_id") or "") if isinstance(payload, dict) else ""
        if not session_id:
            raise RuntimeError("Claude Code session fork returned no session id")
        ClaudeCodeDaemon._relocate_forked_session_file(session_id, cwd, config_dir)
        return session_id

    async def fork_session(self, params: dict[str, Any]) -> dict[str, Any]:
        source_session_id = str(params.get("source_session_id") or "").strip()
        cwd = Path(str(params.get("cwd") or "")).expanduser().resolve(strict=False)
        config_dir = Path(str(params.get("config_dir") or "")).expanduser().resolve(strict=False)
        title = str(params.get("title") or "").strip()
        if not source_session_id or not cwd.is_absolute() or not config_dir.is_absolute():
            raise ValueError("source_session_id, absolute cwd, and absolute config_dir are required")
        if params.get("provider_profile"):
            profile = _provider_profile(str(params["provider_profile"]))
            if claude_provider_catalog.is_native(profile) and config_dir != claude_provider_catalog.config_directory(profile).resolve():
                raise ValueError("Claude fork directory does not match the selected account route")
        config_dir.mkdir(parents=True, exist_ok=True)
        async with self._sessions_lock:
            source_key = self._session_ids.get(source_session_id, "")
            source_handle = self._sessions.get(source_key) if source_key else None

        async def create() -> str:
            return await self._fork_session_file(source_session_id, cwd, config_dir, title)

        if source_handle:
            async with source_handle.lock:
                forked_session_id = await create()
        else:
            forked_session_id = await create()
        return {
            "session_id": forked_session_id,
            "forked_from_session_id": source_session_id,
        }

    def account_usage_snapshot(self, params: dict[str, Any]) -> dict[str, Any]:
        profile = _provider_profile(str(params.get("provider_profile") or ""))
        if not claude_provider_catalog.is_native(profile):
            raise ValueError("Account usage requires a subscription route")
        self.account_usage.request_refresh(profile, force=params.get("force_refresh") is True)
        return self.account_usage.snapshot(profile["id"], str(profile["config_dir"]))

    async def health(self) -> dict[str, Any]:
        async with self._sessions_lock:
            loaded = len(self._sessions)
            active = sum(1 for handle in self._sessions.values() if handle.active_turn_id)
        # This is daemon readiness, not an account check. Looking up a route
        # here requires registration/secrets before users can even log in, and
        # constructing its environment never proves authentication anyway.
        # Keep this endpoint independent of registry, credentials and network.
        return {
            "status": "ok",
            "health_scope": "runtime",
            "runtime_ready": True,
            "backend": "claude",
            "app_server_version": APP_SERVER_VERSION,
            "protocol_version": APP_SERVER_PROTOCOL_VERSION,
            "capabilities": {"harness_tools": True, "resident_environment": True, "account_usage": True, "input_media": True, "auto_compact_window": True, "turn_steer": True, "session_compact": True, "turn_reattach": True, "turn_continue": True, "subagent_switch": True},
            "provider_profile": self.default_profile,
            # Retain legacy keys, with null meaning untested rather than an
            # assertion that an account is authenticated or a route is broken.
            "provider_ready": None,
            "provider_check_status": "not_checked",
            "authentication_status": "not_checked",
            "provider_error": "",
            "enabled_models": [],
            "max_concurrency": self.max_concurrency,
            "active": active,
            "available": active < self.max_concurrency,
            "loaded_sessions": loaded,
            "max_loaded_sessions": self.max_loaded_sessions,
        }

    async def evict_idle_sessions(self) -> None:
        while True:
            await asyncio.sleep(60)
            now = time.monotonic()
            async with self._sessions_lock:
                handles = sorted(self._sessions.values(), key=lambda item: item.last_used)
                overflow = max(0, len(handles) - self.max_loaded_sessions)
                victims = [
                    handle
                    for index, handle in enumerate(handles)
                    if not handle.lock.locked()
                    and not handle.active_turn_id
                    and (now - handle.last_used >= self.idle_seconds or index < overflow)
                ]
                for handle in victims:
                    self._forget_session(handle.key, handle)
            for handle in victims:
                await _disconnect(handle)
            if victims:
                async with self._sessions_lock:
                    for handle in victims:
                        if handle.key not in self._sessions:
                            self._session_setup_locks.pop(handle.key, None)

    async def close(self) -> None:
        async with self._sessions_lock:
            handles = list(self._sessions.values())
            self._sessions.clear()
            self._session_ids.clear()
            self._session_setup_locks.clear()
        for handle in handles:
            await _disconnect(handle)


class WorkerRequest:
    """One Worker connection: a request, its result, and in between the
    events and Harness tool calls of a ``turn/run``."""

    def __init__(self, daemon: ClaudeCodeDaemon, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.daemon = daemon
        self.reader = reader
        self.writer = writer
        self.request_id = ""
        self._write_lock = asyncio.Lock()
        self._tool_lock = asyncio.Lock()
        self._methods: dict[str, Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]] = {
            "health": lambda _params: daemon.health(),
            "account/usage": self._account_usage,
            "turn/run": self._run_turn,
            "turn/steer": daemon.steer,
            "turn/interrupt": daemon.interrupt,
            "subagent/stop": daemon.stop_subagent,
            "session/compact": daemon.compact_session,
            "session/status": daemon.session_status,
            "session/close": daemon.close_session,
            "session/fork": daemon.fork_session,
        }

    async def serve(self) -> None:
        try:
            method, params = await self._read_request()
            handler = self._methods.get(method)
            if handler is None:
                raise ValueError(f"unsupported Claude Code backend method: {method}")
            result = await handler(params)
            await self.send({"type": "result", "result": result})
        except Exception as exc:
            try:
                await self.send({
                    "type": "error",
                    "error": {"class": exc.__class__.__name__, "message": str(exc)},
                })
            except Exception:
                pass
        finally:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception:
                pass

    async def send(self, payload: dict[str, Any]) -> None:
        async with self._write_lock:
            self.writer.write(json.dumps({"id": self.request_id, **payload}, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
            await self.writer.drain()

    async def _read_request(self) -> tuple[str, dict[str, Any]]:
        raw = await self.reader.readline()
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise ValueError("request must be an object")
        self.request_id = str(request.get("id") or "")
        method = str(request.get("method") or "").strip()
        params = request.get("params") if isinstance(request.get("params"), dict) else {}
        if not self.request_id or not method:
            raise ValueError("request id and method are required")
        return method, params

    async def _account_usage(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.daemon.account_usage_snapshot(params)

    async def _run_turn(self, params: dict[str, Any]) -> dict[str, Any]:
        params["_tool_dispatch"] = self.dispatch_tool
        return await self.daemon.run_turn(params, lambda event: self.send({"type": "event", "event": event}))

    async def dispatch_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        # Serialize reverse requests; never send worker credentials or
        # executable handlers into the provider process.
        async with self._tool_lock:
            call_id = os.urandom(16).hex()
            try:
                await self.send({"type": "tool_call", "call_id": call_id, "tool": name, "arguments": arguments})
                raw = await self.reader.readline()
            except OSError as exc:
                raise WorkerConnectionLost(str(exc)) from exc
            if not raw:
                # A Worker leaves between messages, so it did not take this
                # call (see NativeAgentClient detach).
                raise WorkerConnectionLost("Worker closed the turn connection")
            response = json.loads(raw)
            if (response.get("id") != self.request_id or response.get("type") != "tool_result"
                    or response.get("call_id") != call_id or not isinstance(response.get("result"), dict)):
                raise ValueError("Invalid Claude Harness tool response")
            return response["result"]


async def _serve(socket_path: Path) -> None:
    daemon = ClaudeCodeDaemon()
    socket_path.parent.mkdir(parents=True, exist_ok=True)

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await WorkerRequest(daemon, reader, writer).serve()

    server = await start_local_server(handle, socket_path, limit=16 * 1024 * 1024)
    eviction_task = asyncio.create_task(daemon.evict_idle_sessions())
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        if os.name == "nt":
            signal.signal(signum, lambda *_: loop.call_soon_threadsafe(stop.set))
        else:
            loop.add_signal_handler(signum, stop.set)
    async with server:
        serve_task = asyncio.create_task(server.serve_forever())
        await stop.wait()
        serve_task.cancel()
        await asyncio.gather(serve_task, return_exceptions=True)
    eviction_task.cancel()
    await asyncio.gather(eviction_task, return_exceptions=True)
    await daemon.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="TFI Portal Claude Code backend daemon")
    parser.add_argument("--socket", default=os.getenv("PORTAL_CLAUDE_CODE_SOCKET", str(DEFAULT_SOCKET_PATH)))
    args = parser.parse_args()
    asyncio.run(_serve(Path(args.socket)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
