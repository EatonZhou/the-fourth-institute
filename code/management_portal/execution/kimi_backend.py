"""The Kimi Code Adapter, on Kimi Code's server.

What is Kimi Code's own: sessions it creates per account route, Portal
tools offered through its tool catalog and gateway and settled per session,
and reading compactions and usage from the session's wire file.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from management_portal import kimi_portal_tools
from management_portal.kimi_code_client import KimiCodeDetached, shared_client as shared_kimi_client

from .backend_contracts import BackendContext, BackendInvocation, BackendResult, _positive_int
from .contracts import ExecutionRecoveryDeferred
from .kimi_tool_gateway import KimiToolGateway, publish_catalog
from .native_session_compaction import NativeCompaction, kimi_wire_path, read_kimi_latest_compaction
from .native_socket_backend import NativeSocketBackend
from .provider_catalog import AGENT_PROVIDERS
from .provider_turn_usage import epoch_ms, kimi_turn_usage, read_kimi_turn_usage

LOGGER = logging.getLogger(__name__)


def _kimi_home() -> str:
    from management_portal import kimi_code_server

    return os.getenv("PORTAL_KIMI_CODE_HOME") or str(kimi_code_server.DEFAULT_HOME)


def _kimi_wire(session_id: str) -> Path | None:
    return kimi_wire_path(_kimi_home(), session_id)


def kimi_latest_compaction(session_id: str) -> NativeCompaction | None:
    """The newest compaction of a Kimi Code session, read from its wire file."""

    path = _kimi_wire(session_id)
    return read_kimi_latest_compaction(path) if path else None


# How long a Kimi turn waits for Kimi Code to register Portal tools that are new to it.
PORTAL_TOOL_REGISTRATION_WAIT_SECONDS = 36.0


class KimiBackend(NativeSocketBackend):
    provider_id = "kimi"
    scopes_item_ids = False

    def __init__(self, client=None):
        self._account_client = client
        if client is not None:
            self.client_factory = lambda: client

    def account_home(self):
        return self._account_client.home if self._account_client is not None else _kimi_home()

    @staticmethod
    def client_factory():
        return shared_kimi_client()

    def media_options(self, invocation):
        # Kimi reads the paths already attached to the prompt.
        return {}

    def execute(self, invocation, context):
        request, session = invocation.request, invocation.session
        route = str(request.runtime_options.get("provider_profile") or session.backend_route_id or "")
        if session.backend_route_id and session.backend_route_id != route:
            raise ValueError("Provider route changes require a session migration")
        if route and self._account_client is None:
            from management_portal.kimi_account_runtime import client_for_route, remember_session
            result = KimiBackend(client_for_route(route)).execute(invocation, context)
            remember_session(result.backend_session_id, route)
            return result
        if not session.backend_session_id and request.operation in {"create", "turn"}:
            options = request.runtime_options
            created = self.client_factory().create_session(
                cwd=request.cwd, title=str(options.get("title") or "Portal execution"),
                system_prompt=invocation.system_context if invocation.attach_system_context else "",
                tools=list(options.get("tools") or []),
                permission_mode=str(options.get("permission_mode") or request.permission_profile),
                model=str(options.get("model") or ""),
            )
            session_id = str(created.get("id") or "")
            if not session_id:
                raise RuntimeError("Kimi session creation returned no session id")
            if route:
                from management_portal.kimi_account_runtime import remember_session
                remember_session(session_id, route)
            session = replace(session, backend_session_id=session_id)
            invocation = replace(invocation, session=session)
        if request.operation == "create":
            context.set_interrupt_target(session.backend_session_id)
            return BackendResult(output={"message": "", "provider": self.provider_id, "operation": "create"},
                                 backend_session_id=session.backend_session_id, backend_route_id=route)
        return super().execute(invocation, context)

    def deliver_turn_input(self, **kwargs):
        if self._account_client is None:
            from management_portal.kimi_account_runtime import client_for_route, session_route
            route = session_route(kwargs["session_id"])
            if route:
                return KimiBackend(client_for_route(route, for_control=True)).deliver_turn_input(**kwargs)
        return super().deliver_turn_input(**kwargs)

    def interrupt(self, **kwargs):
        route = kwargs["backend_route_id"] or kwargs["runtime_options"].get("provider_profile")
        if route and self._account_client is None:
            from management_portal.kimi_account_runtime import client_for_route
            return KimiBackend(client_for_route(route, for_control=True)).interrupt(**kwargs)
        return super().interrupt(**kwargs)

    def prepare_turn_tools(self, invocation, context):
        return self.offer_portal_tools(invocation, context, home=self.account_home())

    def turn_scope(self, session_id, catalog, dispatch):
        return KimiToolGateway(session_id, dispatch if catalog else None)

    def client_turn_options(self, invocation, context, catalog):
        from .native_options import KIMI_SUBAGENT_TOOLS
        options = invocation.request.runtime_options
        tools = list(options.get("tools") or [])
        profile = {"tools": tools, "permission_mode": str(options.get("permission_mode") or invocation.request.permission_profile),
                   "model": str(options.get("model") or "")}
        disabled = self.settle_portal_tools(invocation.session.backend_session_id, catalog,
                                           profile=profile, cancelled=context.is_cancelled, client=self.client_factory())
        if not AGENT_PROVIDERS[self.provider_id].allows_subagents(dict(options)):
            disabled += sorted(KIMI_SUBAGENT_TOOLS)
        return {"native_tools": tools, "disabled_tools": disabled, "cancelled": context.is_cancelled,
                "prepend_system_prompt": invocation.attach_system_context and not invocation.is_new_backend_session}

    def compaction_options(self, options, backend_session_id=""):
        return {}

    def compaction_control(self, context):
        return {"cancelled": context.is_cancelled, "detach_requested": context.is_handover}

    def compact_native_session(self, invocation, context, **kwargs):
        # The HTTP operation keeps running after this worker detaches.
        try:
            return super().compact_native_session(invocation, context, **kwargs)
        except KimiCodeDetached as exc:
            raise ExecutionRecoveryDeferred("Worker handover: Kimi compaction keeps running") from exc

    def latest_compaction(self, session_id, config_dir):
        path = kimi_wire_path(self.account_home(), session_id)
        return read_kimi_latest_compaction(path) if path else None

    def read_turn_usage(self, session_id, config_dir, since):
        path = kimi_wire_path(self.account_home(), session_id)
        observed = read_kimi_turn_usage(path, since_ms=epoch_ms(since)) if path else None
        if observed is None:
            return None
        status = self.client_factory().get_status(session_id)
        return replace(observed, context_window=_positive_int(status.get("max_context_tokens")))

    def turn_usage(self, result, observed):
        return kimi_turn_usage(result.usage, observed)

    @staticmethod
    def offer_portal_tools(
        invocation: BackendInvocation,
        context: BackendContext,
        *, home=None,
    ) -> tuple[list[dict[str, Any]], Callable[[dict[str, Any]], dict[str, Any]] | None]:
        """List this turn's Portal tools in the catalog Kimi Code reads.

        Returns the turn's catalog and the dispatcher the tool gateway
        serves. An optional tool failure leaves the turn without Portal tools;
        a required capability failure stops before the model turn starts.
        """

        from .native_harness_tools import prepare_tools
        from .resident_capability_assembly import require_offered_tools

        if not getattr(invocation, "harness_tools", ()):
            require_offered_tools(invocation, ())
            return [], None
        home = home if home is not None else _kimi_home()
        if not kimi_portal_tools.service_ready(home):
            # Without the hook Portal cannot tell which session makes a call.
            LOGGER.warning("The Kimi Code service runs without Portal's tool server and hook; restart it to offer Portal tools")
            require_offered_tools(invocation, ())
            return [], None
        try:
            catalog, dispatch = prepare_tools(invocation, context)
            # Kimi Code connects again, and lists the tools again, when the catalog grew.
            publish_catalog(catalog, home)
        except Exception:  # noqa: BLE001 - Portal tools are an addition to the turn
            LOGGER.warning("Portal tools could not be offered to Kimi Code", exc_info=True)
            require_offered_tools(invocation, ())
            return [], None
        require_offered_tools(invocation, {tool["name"] for tool in catalog})
        return catalog, dispatch

    @staticmethod
    def settle_portal_tools(
        session_id: str,
        catalog: list[dict[str, Any]],
        *,
        profile: dict[str, Any],
        cancelled: Callable[[], bool],
        client=None,
    ) -> list[str]:
        """The tools to switch off for the session's next prompt.

        Kimi Code lists Portal's tools once per workspace, for every session
        there, so a turn switches off the ones it does not have.  When the
        catalog has just grown, Kimi Code is still connecting again: the turn
        waits a moment for its own tools to be registered.
        """

        prefix = kimi_portal_tools.TOOL_NAME_PREFIX
        everything = [f"{prefix}*"]
        wanted = {f"{prefix}{tool['name']}" for tool in catalog}
        client = client if client is not None else shared_kimi_client()
        try:
            # A session is listed with its tools only once Kimi Code has loaded it.
            client.update_session_profile(session_id, **profile)
            deadline = time.monotonic() + (PORTAL_TOOL_REGISTRATION_WAIT_SECONDS if wanted else 0.0)
            while True:
                registered = {
                    str(tool.get("name") or "") for tool in client.session_tools(session_id)
                    if str(tool.get("name") or "").startswith(prefix)
                }
                if wanted <= registered or time.monotonic() >= deadline or cancelled():
                    break
                time.sleep(0.25)
        except Exception:  # noqa: BLE001 - the turn runs; tools Portal cannot vouch for stay off
            LOGGER.warning("Portal tools of Kimi session %s could not be settled", session_id, exc_info=True)
            return [] if wanted else everything
        if wanted - registered:
            LOGGER.warning(
                "Kimi Code has not registered Portal tools %s for session %s",
                sorted(wanted - registered), session_id,
            )
        return sorted(registered - wanted)
