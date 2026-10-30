"""The Claude Code Adapter, on the Claude daemon's native agent socket.

What is Claude Code's own: aligning the daemon's live session with the
requested one, the compaction threshold in tokens, image input per route,
and reading compactions and usage from the session's transcript.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, Mapping

from management_portal.claude_code_client import shared_client as shared_claude_client

from .backend_contracts import BackendInvocation, _positive_int
from .claude_transcript_compaction import claude_transcript_path, read_claude_latest_compaction
from .harness import InvocationTransformResult
from .native_session_compaction import NativeCompaction
from .native_socket_backend import NativeSocketBackend
from .provider_catalog import AGENT_PROVIDERS
from .provider_turn_usage import ObservedTurnUsage, claude_turn_usage, read_claude_turn_usage

LOGGER = logging.getLogger(__name__)


class ClaudeBackend(NativeSocketBackend):
    provider_id = "claude"

    def prepare_native_session(
        self, *, session_key: str, backend_session_id: str, options: Mapping[str, Any] | None = None,
    ) -> None:
        """Run the turn in the requested Claude session, not a stale live one.

        The daemon keeps one live Claude session per session key and reuses it
        while the session options stay the same.  After a session restart the
        key's binding names a new session; an idle live session that still
        holds the old one is closed so that the turn resumes the requested one.
        """

        if not backend_session_id:
            return
        try:
            client = self.client_factory()
            status = client.session_status(session_key, backend_session_id)
            loaded = str(status.get("session_id") or "")
            if (
                status.get("loaded")
                and not status.get("active")
                and loaded
                and loaded != backend_session_id
            ):
                client.close_session(session_key, loaded)
        except Exception:  # noqa: BLE001 - the daemon then keeps its previous behaviour
            LOGGER.warning(
                "Claude session %s could not be aligned for key %s",
                backend_session_id,
                session_key,
                exc_info=True,
            )

    def latest_compaction(self, session_id, config_dir):
        path = claude_transcript_path(config_dir, session_id) if config_dir else None
        value = read_claude_latest_compaction(path) if path else None
        return (NativeCompaction(value.boundary_uuid or value.fingerprint, value.created_at,
                                 value.retain_from, value.replacement_history) if value else None)

    def read_turn_usage(self, session_id, config_dir, since):
        path = claude_transcript_path(config_dir, session_id) if config_dir else None
        return read_claude_turn_usage(path, since=since) if path else None

    def turn_usage(self, result: Any, observed: ObservedTurnUsage | None) -> dict[str, Any]:
        return claude_turn_usage(result.usage, observed, getattr(result, "model_usage", None))

    def configure_auto_compact_threshold(
        self,
        *,
        invocation: BackendInvocation,
        threshold_percent: int,
        max_context_tokens: int = 0,
    ) -> InvocationTransformResult:
        """Set the context size from which Claude Code compacts by itself.

        Claude Code takes the size in tokens when its CLI starts.  The Adapter
        learns a model's context window only from a finished turn, so a share
        of the window alone cannot be turned into tokens; the policy's token
        bound can.
        """

        del threshold_percent
        tokens = _positive_int(max_context_tokens)
        if invocation.request.operation != "turn":
            return InvocationTransformResult(invocation, False, "operation_not_supported")
        if not tokens:
            return InvocationTransformResult(invocation, False, "model_context_window_unavailable")
        options = dict(invocation.request.runtime_options)
        options["auto_compact_window_tokens"] = tokens
        return InvocationTransformResult(
            replace(invocation, request=replace(invocation.request, runtime_options=options)),
            True,
            "native_auto_compact_threshold_configured",
            {"auto_compact_token_limit": tokens},
        )

    def compaction_options(self, options: Mapping[str, Any], backend_session_id: str = "") -> dict[str, Any]:
        tokens = _positive_int(options.get("auto_compact_window_tokens"))
        return {"auto_compact_window_tokens": tokens} if tokens else {}


    def media_options(self, invocation: BackendInvocation) -> dict[str, Any]:
        if not invocation.request.input_media:
            return {}
        route = invocation.session.backend_route_id or invocation.request.runtime_options.get("provider_profile")
        if not AGENT_PROVIDERS[self.provider_id].catalog(route)["capabilities"]["media"]:
            raise ValueError("Selected Claude route does not support images")
        return {"input_media": [item.to_payload() for item in invocation.request.input_media]}

    @staticmethod
    def client_factory():
        return shared_claude_client()
