from __future__ import annotations
from management_common.portal_paths import path as portal_path

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


DEFAULT_SOCKET_PATH = (portal_path("staging_root") / "portal_locks" / "claude-code-daemon.sock")
# A compaction writes its summary with the session's model: 2-5 minutes on a
# long history in the 2026-10-03 measurements.  Portal waits 10 minutes for an
# explicit compaction (RESIDENT_AGENT_CODEX_COMPACT_TIMEOUT_SECONDS).
COMPACT_TIMEOUT_SECONDS = 590.0


class ClaudeCodeError(RuntimeError):
    pass


class ClaudeCodeUnavailable(ClaudeCodeError):
    pass


@dataclass
class ClaudeCodeTurnResult:
    session_id: str
    turn_id: str
    response: str
    usage: dict[str, Any]
    model_usage: dict[str, Any]
    duration_ms: int


from management_portal.native_agent_transport import NativeAgentClient
from management_portal.execution.native_options import ClaudeOptions


class ClaudeCodeClient(NativeAgentClient):
    error_type = ClaudeCodeError
    unavailable_type = ClaudeCodeUnavailable

    def __init__(self, socket_path: Path | str = DEFAULT_SOCKET_PATH) -> None:
        self.socket_path = Path(socket_path)

    def health(self) -> dict[str, Any]:
        return self._request("health", response_timeout=3.0)


    def account_usage(self, provider_profile: str, *, force_refresh: bool = False) -> dict[str, Any]:
        return self._request("account/usage", {"provider_profile": provider_profile, "force_refresh": force_refresh}, response_timeout=3.0)

    def run_turn(
        self,
        *,
        session_key: str,
        session_id: str,
        prompt: str,
        cwd: str,
        config_dir: str,
        provider_profile: str,
        model: str,
        is_owner: bool,
        permission_profile: str = "",
        permission_mode: str = "",
        system_prompt_append: str = "",
        add_dirs: list[str] | None = None,
        turn_id: str = "",
        effort: str = "",
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_callback: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        resident_environment: dict[str, str] | None = None,
        inherit_portal_database: bool = False,
        input_media: list[dict[str, str]] | None = None,
        auto_compact_window_tokens: int = 0,
        detach_requested: Callable[[], bool] | None = None,
        continue_prompt: str = "",
        allow_subagents: bool = True,
    ) -> ClaudeCodeTurnResult:
        """Run a turn, or attach to the running turn with this ``turn_id``.

        ``detach_requested`` lets a retiring Worker leave the turn running
        (``NativeAgentDetached``); the next Worker's call with the same
        session key and turn id attaches to it.  ``continue_prompt`` marks a
        turn an earlier attempt started: a daemon that no longer has it
        continues it with this prompt once the CLI had taken its input.
        """
        result = self._request(
            "turn/run",
            {
                "session_key": session_key,
                "session_id": session_id,
                "prompt": prompt,
                "cwd": cwd,
                "config_dir": config_dir,
                "provider_profile": provider_profile,
                "model": model,
                "effort": str(effort or ""),
                "is_owner": bool(is_owner),
                "permission_profile": str(permission_profile or ""),
                "permission_mode": str(permission_mode or ClaudeOptions().native_permission_mode({})),
                "system_prompt_append": str(system_prompt_append or ""),
                "add_dirs": add_dirs or [],
                "turn_id": turn_id,
                "tools": tools or [],
                "resident_environment": resident_environment or {},
                "inherit_portal_database": inherit_portal_database,
                "input_media": input_media or [],
                "auto_compact_window_tokens": int(auto_compact_window_tokens or 0),
                "allow_subagents": bool(allow_subagents),
                **({"continue_prompt": continue_prompt} if continue_prompt else {}),
            },
            event_callback=event_callback,
            tool_callback=tool_callback,
            detach_requested=detach_requested,
        )
        return ClaudeCodeTurnResult(
            session_id=str(result.get("session_id") or session_id),
            turn_id=str(result.get("turn_id") or turn_id),
            response=str(result.get("response") or ""),
            usage=result.get("usage") if isinstance(result.get("usage"), dict) else {},
            model_usage=result.get("model_usage") if isinstance(result.get("model_usage"), dict) else {},
            duration_ms=int(result.get("duration_ms") or 0),
        )

    def steer(
        self,
        session_key: str,
        session_id: str = "",
        *,
        turn_id: str,
        text: str,
        control_id: str = "",
    ) -> dict[str, Any]:
        """Give the running turn more input; Claude Code takes it after the current step."""

        return self._request("turn/steer", {
            "session_key": session_key, "session_id": session_id, "turn_id": turn_id,
            "text": text, "control_id": control_id,
        }, response_timeout=15.0)

    def compact_session(
        self,
        *,
        session_key: str,
        session_id: str,
        cwd: str,
        config_dir: str,
        provider_profile: str,
        model: str,
        is_owner: bool,
        permission_profile: str = "",
        permission_mode: str = "",
        add_dirs: list[str] | None = None,
        effort: str = "",
        resident_environment: dict[str, str] | None = None,
        inherit_portal_database: bool = False,
        auto_compact_window_tokens: int = 0,
        request_id: str = "",
        allow_subagents: bool = True,
    ) -> dict[str, Any]:
        """Compact the session's history now (Claude Code's ``/compact``).

        The settings start a CLI that resumes the session when none is live.
        Claude Code writes the summary with the session's model, which takes
        minutes on a long history.
        """

        return self._request("session/compact", {
            "session_key": session_key,
            "session_id": session_id,
            "cwd": cwd,
            "config_dir": config_dir,
            "provider_profile": provider_profile,
            "model": model,
            "effort": str(effort or ""),
            "is_owner": bool(is_owner),
            "permission_profile": str(permission_profile or ""),
            "permission_mode": str(permission_mode or ClaudeOptions().native_permission_mode({})),
            "add_dirs": add_dirs or [],
            "resident_environment": resident_environment or {},
            "inherit_portal_database": inherit_portal_database,
            "auto_compact_window_tokens": int(auto_compact_window_tokens or 0),
            "request_id": request_id,
            "allow_subagents": allow_subagents,
        }, response_timeout=COMPACT_TIMEOUT_SECONDS)

    def stop_subagent(self, session_key: str, session_id: str = "", *, tool_use_id: str) -> dict[str, Any]:
        """Stop one subagent run the session's model started (its Agent call id); the turn goes on."""

        return self._request("subagent/stop", {
            "session_key": session_key, "session_id": session_id, "tool_use_id": tool_use_id,
        }, response_timeout=10.0)

    def interrupt(self, session_key: str, session_id: str = "", *, turn_id: str = "") -> dict[str, Any]:
        return self._request("turn/interrupt", {
            "session_key": session_key, "session_id": session_id, "turn_id": turn_id,
        }, response_timeout=10.0)

    def session_status(self, session_key: str, session_id: str = "") -> dict[str, Any]:
        return self._request(
            "session/status",
            {"session_key": session_key, "session_id": session_id},
            response_timeout=3.0,
        )

    def close_session(self, session_key: str, session_id: str = "") -> dict[str, Any]:
        return self._request(
            "session/close",
            {"session_key": session_key, "session_id": session_id},
            response_timeout=10.0,
        )

    def fork_session(
        self,
        *,
        source_session_id: str,
        cwd: str,
        config_dir: str,
        title: str = "",
        provider_profile: str = "",
    ) -> dict[str, Any]:
        return self._request(
            "session/fork",
            {
                "source_session_id": source_session_id,
                "cwd": cwd,
                "config_dir": config_dir,
                "title": title,
                "provider_profile": provider_profile,
            },
        )


_CLIENT: ClaudeCodeClient | None = None
_CLIENT_LOCK = threading.Lock()


def shared_client() -> ClaudeCodeClient:
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            socket_path = os.getenv("PORTAL_CLAUDE_CODE_SOCKET", str(DEFAULT_SOCKET_PATH))
            _CLIENT = ClaudeCodeClient(socket_path)
        return _CLIENT
