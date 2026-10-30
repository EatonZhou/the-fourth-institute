"""The OpenCode Adapter: OpenCode's agent behind Portal's native agent socket.

The turn, its Portal tools, input during the turn and recovery are the
native socket Adapter's (``NativeSocketBackend``); the bridge
(``opencode_bridge``) speaks that protocol for OpenCode.  What is OpenCode's
own here: naming a new session after its first turn, and reading the turn's
requests and the session's compactions from OpenCode's record of the session.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from management_portal.execution.backend_contracts import BackendContext
from management_portal.execution.native_socket_backend import NativeSocketBackend, record_native_compaction
from management_portal.execution.native_session_compaction import NativeCompaction, summary_history
from management_portal.execution.provider_turn_usage import ObservedTurnUsage, RequestUsage, epoch_ms, usage_payload

LOGGER = logging.getLogger(__name__)
PROVIDER = "opencode"


def _iso(milliseconds: int) -> str:
    moment = datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _add(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
    return {key: int(left.get(key, 0)) + int(right.get(key, 0)) for key in {*left, *right}}


class OpenCodeBackend(NativeSocketBackend):
    provider_id = PROVIDER
    # OpenCode does not mark its last message as the answer.
    marks_answer_phases = False

    @staticmethod
    def client_factory():
        from management_portal.opencode_client import shared_client

        return shared_client()

    def interrupt(
        self,
        *,
        backend_session_id: str,
        turn_id: str,
        backend_route_id: str,
        runtime_options: dict[str, Any],
    ) -> bool:
        if not turn_id and runtime_options.get("interrupt_active_turn"):
            # A lost Worker may not have seen its turn start; Portal names the
            # turn, and a new session after it.
            from management_portal.opencode_client import session_for_turn

            turn_id = str(runtime_options.get("turn_id") or runtime_options.get("abandoned_execution_id") or "")
            if turn_id and not backend_session_id:
                backend_session_id = session_for_turn(turn_id)
        return super().interrupt(
            backend_session_id=backend_session_id, turn_id=turn_id,
            backend_route_id=backend_route_id, runtime_options=runtime_options,
        )

    def record_provider_compaction(
        self,
        *,
        context: BackendContext,
        backend_session_id: str,
        backend_route_id: str,
        config_dir: str,
    ) -> None:
        """Record the newest compaction OpenCode committed (on /compact or when the context filled up).

        OpenCode's summary replaces the history before it; the messages after it stay.
        """

        if not backend_session_id:
            return
        try:
            found = self.client_factory().latest_compaction(backend_session_id)
            if not found.get("summary"):
                return
            record_native_compaction(
                context, provider=PROVIDER, backend_session_id=backend_session_id, backend_route_id=backend_route_id,
                compaction=NativeCompaction(
                    ref=f"{PROVIDER}:{found['id']}", created_at=_iso(int(found["created_ms"])),
                    retain_from=_iso(int(found["retain_from_ms"])), replacement_history=summary_history(found["summary"]),
                ),
            )
        except Exception:  # noqa: BLE001 - never fail a user turn; a compact without its record fails on its own
            LOGGER.warning("OpenCode compaction could not be recorded for session %s", backend_session_id, exc_info=True)

    def observe_turn_usage(
        self,
        *,
        context: BackendContext,
        backend_session_id: str,
        config_dir: str,
    ) -> ObservedTurnUsage | None:
        """Every request of the turn (one per OpenCode step), also when the turn failed."""

        since_ms = epoch_ms(context.execution_started_at)
        if not backend_session_id or not since_ms:
            return None
        try:
            read = self.client_factory().requests(backend_session_id, since_ms=since_ms)
        except Exception:  # noqa: BLE001 - fall back to the reported usage
            LOGGER.warning("OpenCode turn usage could not be read for session %s", backend_session_id, exc_info=True)
            return None
        rows = [row for row in read.get("requests") or [] if row.get("usage")]
        if not rows:
            return None
        turn: dict[str, int] = {}
        for row in rows:
            turn = _add(turn, row["usage"])
        in_context = [row for row in rows if row.get("context", True)] or rows
        return ObservedTurnUsage(
            turn=turn,
            context=dict(in_context[-1]["usage"]),
            model=str(in_context[-1].get("model") or ""),
            requests=tuple(RequestUsage(f"{PROVIDER}:{row['id']}", dict(row["usage"]), _iso(int(row["at_ms"])))
                           for row in rows),
            context_window=int(read.get("context_window") or 0),
        )

    def turn_usage(self, result: Any, observed: ObservedTurnUsage | None) -> dict[str, Any]:
        if observed is not None:
            return usage_payload(observed.turn, observed.context, observed.context_window)
        return usage_payload(dict(getattr(result, "usage", None) or {}), dict(getattr(result, "context", None) or {}),
                             int(getattr(result, "context_window", 0) or 0))
