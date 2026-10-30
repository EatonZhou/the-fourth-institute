"""The DeepSeek Harness Adapter, on the DSH bridge's native agent socket.

What is DSH's own: a new agent process when the model, effort, compaction
threshold or subagent tool changed, the threshold as a share of the
window, and reading compactions and usage from the DSH session log.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, Mapping

from .backend_contracts import BackendInvocation, _positive_int
from .harness import InvocationTransformResult
from .native_session_compaction import dsh_session_path, read_dsh_latest_compaction, read_dsh_request_config
from .native_socket_backend import NativeSocketBackend
from .provider_catalog import AGENT_PROVIDERS
from .provider_turn_usage import (
    ObservedTurnUsage,
    epoch_ms,
    observed_turn_usage,
    read_dsh_context_window,
    read_dsh_turn_usage,
)

LOGGER = logging.getLogger(__name__)


class DshBackend(NativeSocketBackend):
    provider_id = "dsh"
    marks_answer_phases = False

    def interrupt(
        self,
        *,
        backend_session_id: str,
        turn_id: str,
        backend_route_id: str,
        runtime_options: dict[str, Any],
    ) -> bool:
        if not turn_id and runtime_options.get("interrupt_active_turn"):
            # A lost Worker may not have seen its turn start. Portal names a
            # DSH turn itself, and a new session after that turn.
            from management_portal.dsh_client import session_id_for

            turn_id = str(runtime_options.get("turn_id") or runtime_options.get("abandoned_execution_id") or "")
            if turn_id and not backend_session_id:
                backend_session_id = session_id_for(turn_id)
        return super().interrupt(
            backend_session_id=backend_session_id, turn_id=turn_id,
            backend_route_id=backend_route_id, runtime_options=runtime_options,
        )

    def prepare_native_session(
        self, *, session_key: str, backend_session_id: str, options: Mapping[str, Any] | None = None,
    ) -> None:
        """Start a new DSH agent when the requested model or effort changed.

        After its first request a DSH agent derives every request from the
        request header it logged, so a changed model or reasoning effort only
        takes effect in a new agent instance that resumes the session.  The
        compaction threshold and the subagent tool are fixed when the DSH
        process starts: an earlier threshold than the launched one, or a
        changed "允许开启 sub agent", starts a new process too.
        """

        if not backend_session_id:
            return
        requested = dict(options or {})
        model = str(requested.get("model") or "")
        effort = str(requested.get("effort") or "high")
        try:
            from management_portal.dsh_client import STATE, launched_compaction_ratio, launched_with_subagents

            wanted = self._compaction_ratio(requested, backend_session_id)
            launched = launched_compaction_ratio(backend_session_id)
            subagents = AGENT_PROVIDERS[self.provider_id].allows_subagents(requested)
            if (wanted and launched and wanted < launched - 0.001) or (
                launched_with_subagents(backend_session_id) is not subagents
            ):
                self.client_factory().close_session(session_key, backend_session_id)
                return
            path = dsh_session_path(STATE, backend_session_id)
            config = read_dsh_request_config(path) if path else {}
            if not config or not model:
                return
            logged_effort = (
                "" if config["_adapter_defaults"].get("reasoningEffort") is True
                else str(config.get("reasoningEffort") or "")
            )
            if config.get("model") != model or (logged_effort and logged_effort != effort):
                self.client_factory().close_session(session_key, backend_session_id)
        except Exception:  # noqa: BLE001 - the turn then keeps the previous settings
            LOGGER.warning(
                "DSH session %s could not apply its new runtime settings",
                backend_session_id,
                exc_info=True,
            )

    def configure_auto_compact_threshold(
        self,
        *,
        invocation: BackendInvocation,
        threshold_percent: int,
        max_context_tokens: int = 0,
    ) -> InvocationTransformResult:
        """Ask DSH to condense history at an earlier point than its default.

        DSH takes the threshold as a share of the model's window when its
        process starts; ``_compaction_ratio`` turns the request into that share.
        """

        if invocation.request.operation != "turn":
            return InvocationTransformResult(invocation, False, "operation_not_supported")
        options = dict(invocation.request.runtime_options)
        options["auto_compact_percent"] = max(1, min(99, int(threshold_percent)))
        options["auto_compact_max_tokens"] = _positive_int(max_context_tokens)
        return InvocationTransformResult(
            replace(invocation, request=replace(invocation.request, runtime_options=options)),
            True,
            "native_auto_compact_threshold_configured",
            {
                "requested_threshold_percent": options["auto_compact_percent"],
                "max_context_tokens": options["auto_compact_max_tokens"],
            },
        )

    @staticmethod
    def _compaction_ratio(options: Mapping[str, Any], backend_session_id: str) -> float:
        """The share of the window this turn asks DSH to compact at (0: none asked).

        A token bound becomes a share once the session's log tells the
        model's window; a new session has no log yet and uses the share alone.
        """

        from management_portal.dsh_client import STATE, compaction_ratio

        percent = _positive_int(options.get("auto_compact_percent"))
        bound = _positive_int(options.get("auto_compact_max_tokens"))
        if not percent and not bound:
            return 0.0
        ratio = (percent or 80) / 100
        path = dsh_session_path(STATE, backend_session_id) if backend_session_id else None
        window = read_dsh_context_window(path) if path else 0
        if bound and window:
            ratio = min(ratio, bound / window)
        return compaction_ratio(ratio)


    def compaction_options(self, options: Mapping[str, Any], backend_session_id: str = "") -> dict[str, Any]:
        from management_portal.dsh_client import launched_compaction_ratio

        wanted = self._compaction_ratio(options, backend_session_id)
        launched = launched_compaction_ratio(backend_session_id) if backend_session_id else 0.0
        # A session keeps the earliest threshold it was given.
        ratio = min(value for value in (wanted, launched) if value) if (wanted or launched) else 0.0
        return {"auto_compact_ratio": ratio} if ratio else {}

    def latest_compaction(self, session_id, config_dir):
        from management_portal.dsh_client import STATE
        path = dsh_session_path(STATE, session_id)
        return read_dsh_latest_compaction(path) if path else None

    def read_turn_usage(self, session_id, config_dir, since):
        from management_portal.dsh_client import STATE
        path = dsh_session_path(STATE, session_id)
        return read_dsh_turn_usage(path, session_id=session_id, since_ms=epoch_ms(since)) if path else None

    def turn_usage(self, result: Any, observed: ObservedTurnUsage | None) -> dict[str, Any]:
        return observed_turn_usage(result.usage, observed)

    @staticmethod
    def client_factory():
        from management_portal.dsh_client import shared_client
        return shared_client()
