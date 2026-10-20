from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import socket
import socketserver
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping, cast

from .activity_redaction import redact_sensitive_mapping
from ..ai_provider_registry import AIProviderRegistry, shared_ai_provider_registry
from .config import (
    execution_service_socket,
)
from .agent_data_store import AgentDataStore, get_agent_data_store
from .accounts import default_execution_accounts
from .contracts import (
    TERMINAL_STATUSES,
    ExecutionInputControl,
    ExecutionTurnSettingsControl,
    ExecutionRequest,
    ExecutionResult,
    ExecutionServiceHealth,
    ExecutionSnapshot,
)
from .scheduler import Scheduler
from .session_manager import SessionManager
from .transport import (
    CONTROL_REQUEST_TIMEOUT_SECONDS,
    ExecutionServiceClient,
    ExecutionServiceUnavailable,
    ExecutionResponseUnknown,
)
from management_portal.notifications import (
    business_projection_subject,
    get_notification_publisher,
    notification_subject,
)
from management_portal.execution.event_projection import (
    append_execution_activity,
    execution_activity_path,
)


EXECUTION_SERVICE_SCHEMA = "tfi.execution-service.v1"
TERMINAL_EXECUTION_RETENTION_SECONDS = 24 * 60 * 60
EXECUTION_MAINTENANCE_INTERVAL_SECONDS = 5.0
# Turns a Provider takes on its own are counted from its own records
# (``AgentProvider.self_started_usage``, e.g. Claude Code's transcripts).
SELF_STARTED_USAGE_INTERVAL_SECONDS = 60.0
LOGGER = logging.getLogger(__name__)
_CANCELLATION_CHECK_SECONDS = 0.25


class ExecutionWaitTimeout(TimeoutError):
    """This observer exhausted its budget, not the durable execution lifetime."""

    def __init__(
        self, execution_id: str, *, snapshot: Mapping[str, Any] | None = None,
        after_sequence: int = 0, control_plane_error: str = "",
    ) -> None:
        self.execution_id = execution_id
        self.snapshot = copy.deepcopy(dict(snapshot or {}))
        self.after_sequence = after_sequence
        self.control_plane_error = control_plane_error
        suffix = f"; last control-plane error: {control_plane_error}" if control_plane_error else ""
        super().__init__(f"ExecutionService observation budget exhausted for {execution_id}{suffix}")


def _terminal_snapshot_has_matching_result(
    snapshot: Mapping[str, Any],
) -> bool:
    status = str(snapshot.get("status") or "").strip().lower()
    if status not in TERMINAL_STATUSES:
        return False
    result = (
        snapshot.get("result")
        if isinstance(snapshot.get("result"), Mapping)
        else {}
    )
    return str(result.get("status") or "").strip().lower() == status


class ExecutionPreparation:
    """Resolve technical execution routing before work enters the queue."""

    def __init__(
        self,
        *,
        account_registry=None,
        session_manager: SessionManager | None = None,
        provider_registry: AIProviderRegistry | None = None,
        accounts=None,
    ) -> None:
        self.provider_registry = provider_registry or shared_ai_provider_registry
        self.accounts = accounts or default_execution_accounts(account_registry=account_registry, provider_registry=self.provider_registry)
        self._session_manager_instance = session_manager
        self._lock = threading.RLock()

    def _session_manager(self) -> SessionManager:
        with self._lock:
            manager = self._session_manager_instance
            if manager is None:
                manager = SessionManager()
                self._session_manager_instance = manager
            return manager

    def prepare(self, request: ExecutionRequest) -> ExecutionRequest:
        request.validate()
        if request.service_type != "agent_session":
            return request
        incoming = self.accounts.resolve(request.provider, request.session.backend_route_id, request.runtime_options)
        if incoming and request.session.backend_route_id and incoming.route_id != request.session.backend_route_id:
            request = replace(request, session=replace(request.session, backend_route_id=incoming.route_id))
        runtime_session = self._session_manager().prepare(request)
        account = self.accounts.resolve(request.provider, runtime_session.backend_route_id, request.runtime_options, bound=bool(runtime_session.backend_session_id))
        if account is None or account.route_id == request.session.backend_route_id:
            return request
        return replace(request, session=replace(request.session, backend_route_id=account.route_id)).validate()


def _execution_activity_signature(execution_id: str) -> tuple[int, int] | None:
    """Identity and length of an Execution's activity log, or None when absent."""
    path = execution_activity_path(execution_id)
    if path is None:
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_ino, stat.st_size


class ExecutionService:
    """Business-facing facade for generic AI execution requests."""

    def __init__(
        self,
        *,
        client: ExecutionServiceClient | None = None,
    ) -> None:
        self.client = client or ExecutionServiceClient()

    def health(self) -> ExecutionServiceHealth:
        return cast(ExecutionServiceHealth, self.client.health())

    def settings(self, *, actor: str) -> dict[str, Any]:
        return self.client.request("settings.get", {"actor": actor})

    def configure_settings(self, enabled: bool, *, actor: str) -> dict[str, Any]:
        return self.client.request("settings.update", {"actor": actor, "model_request_timing_enabled": enabled})

    def runtime_catalog(self, account_id: str = "", *, backend: str = "", include_backends: bool = False) -> dict[str, Any]:
        result = self.client.request("runtime.catalog", {"account_id": account_id, "backend": backend, "include_backends": include_backends})
        return result if isinstance(result, dict) else {}

    def account_usage(self) -> dict[str, Any]:
        return self.client.request("accounts.usage", {})

    def accounts(self, *, force_refresh: bool = False) -> dict[str, Any]:
        return self.client.request("accounts.list", {"force_refresh": force_refresh})

    def resolve_account(self, provider: str, backend_route_id: str = "", *, runtime_options=None) -> dict[str, Any]:
        return self.client.request("accounts.resolve", {"provider": provider, "backend_route_id": backend_route_id, "runtime_options": runtime_options or {}})

    def configure_account_scheduler(
        self,
        account_id: str,
        max_concurrency: int,
        dispatch_interval_min_seconds: float,
        dispatch_interval_max_seconds: float,
        *,
        actor: str,
        close_threshold: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = self.client.request(
            "scheduler.capacity.configure",
            {
                "account_id": str(account_id or "").strip(),
                "max_concurrency": max_concurrency,
                "dispatch_interval_min_seconds": float(
                    dispatch_interval_min_seconds
                ),
                "dispatch_interval_max_seconds": float(
                    dispatch_interval_max_seconds
                ),
                "actor": str(actor or "").strip(),
                **({"close_threshold": close_threshold} if close_threshold is not None else {}),
            },
        )
        return result if isinstance(result, dict) else {}

    def submit(self, request: ExecutionRequest) -> dict[str, Any]:
        request.validate()
        while True:
            try:
                result = self.client.request(
                    "execution.submit",
                    request.to_payload(),
                )
                return result if isinstance(result, dict) else {}
            except (ExecutionServiceUnavailable, ExecutionResponseUnknown):
                # Submission is idempotent on execution_id. A rolling socket
                # replacement must keep waiting for the same durable request.
                time.sleep(0.25)

    def configure_binding_quota_overrides(self, policies: list[dict[str, Any]]) -> dict[str, Any]:
        return self.client.request(
            "scheduler.quota_overrides.configure", {"policies": policies},
            retry_on_unavailable=False,
        )

    def binding_quota_states(self, bindings: Mapping[str, str]) -> dict[str, Any]:
        # Optional live projection: never wait through a service restart or
        # observe a peer instance's scheduling policy.
        return self.client.request("scheduler.quota_states", {"bindings": dict(bindings)},
                                   timeout=1.0, restart_wait=0.0, retry_on_unavailable=False)

    def forget_binding_quota_overrides(self, binding_ids: list[str]) -> dict[str, Any]:
        return self.client.request(
            "scheduler.quota_overrides.forget", {"binding_ids": binding_ids},
            retry_on_unavailable=False,
        )


    def resume(
        self,
        execution_id: str,
        *,
        actor: str,
        correlation_id: str = "",
        service_type: str = "",
        provider: str = "",
    ) -> dict[str, Any]:
        """Validate and return a previously submitted execution."""
        snapshot = self.status(execution_id, actor=actor)
        scheduler = (
            snapshot.get("scheduler")
            if isinstance(snapshot.get("scheduler"), dict)
            else {}
        )
        if (
            correlation_id
            and str(scheduler.get("correlation_id") or "") != correlation_id
        ):
            raise ValueError("execution correlation does not match the request")
        expected_capability = (
            f"{service_type}:{provider}"
            if service_type == "agent_session" and provider
            else service_type
        )
        if (
            expected_capability
            and str(scheduler.get("capability") or "") != expected_capability
        ):
            raise ValueError("execution capability does not match the request")
        return snapshot

    def record(self, execution_id: str, *, actor: str) -> dict[str, Any]:
        """Read durable execution semantics and input references, including after queue retention."""
        return self.client.request("execution.record", {"execution_id": execution_id, "actor": actor})

    def status(
        self,
        execution_id: str,
        *,
        actor: str = "",
        after_sequence: int = 0,
        include_events: bool = True,
        include_result: bool = True,
    ) -> ExecutionSnapshot:
        result = self.client.request(
            "execution.status",
            {
                "execution_id": str(execution_id or ""),
                "actor": str(actor or ""),
                "after_sequence": max(0, int(after_sequence)),
                "include_events": bool(include_events),
                "include_result": bool(include_result),
            },
        )
        return cast(
            ExecutionSnapshot,
            result if isinstance(result, dict) else {},
        )

    def states(self, executions: Mapping[str, str]) -> dict[str, Any]:
        """Small scheduler observations for the business owner's current turns."""
        return self.client.request("execution.states", {"executions": dict(executions)})


    def withdraw_unstarted(self, execution_id: str, *, actor: str, binding_id: str,
                           operation_id: str, replacement_execution_id: str, performed_by: str) -> dict[str, Any]:
        return self.client.request("execution.unstarted.withdraw", {
            "execution_id": execution_id, "actor": actor, "binding_id": binding_id,
            "operation_id": operation_id, "replacement_execution_id": replacement_execution_id,
            "performed_by": performed_by,
        }, timeout=CONTROL_REQUEST_TIMEOUT_SECONDS)

    def cancel(
        self,
        execution_id: str,
        *,
        actor: str,
        reason: str = "",
        performed_by: str = "",
        queued_only: bool = False,
    ) -> dict[str, Any]:
        result = self.client.request(
            "execution.cancel",
            {
                "execution_id": str(execution_id or ""),
                "actor": str(actor or ""),
                "reason": str(reason or ""),
                "performed_by": str(performed_by or actor),
                **({"queued_only": True} if queued_only else {}),
            },
            timeout=CONTROL_REQUEST_TIMEOUT_SECONDS,
        )
        return result if isinstance(result, dict) else {}

    def append_input(
        self,
        execution_id: str,
        *,
        actor: str,
        input: list[dict[str, Any]],
        expected_turn_id: str = "",
        control_id: str = "",
        source_segment_id: str = "",
    ) -> dict[str, Any]:
        control = ExecutionInputControl.new(
            execution_id=execution_id,
            actor=actor,
            input=input,
            expected_turn_id=expected_turn_id,
            control_id=control_id,
            source_segment_id=source_segment_id,
        )
        result = self.client.request(
            "execution.input.append",
            control.to_payload(),
        )
        return result if isinstance(result, dict) else {}

    def update_turn_settings(
        self,
        execution_id: str,
        *,
        actor: str,
        service_tier: str,
        expected_turn_id: str = "",
        control_id: str = "",
    ) -> dict[str, Any]:
        control = ExecutionTurnSettingsControl.new(
            execution_id=execution_id,
            actor=actor,
            settings={"service_tier": str(service_tier or "")},
            expected_turn_id=expected_turn_id,
            control_id=control_id,
        )
        result = self.client.request(
            "execution.turn_settings.update",
            control.to_payload(),
        )
        return result if isinstance(result, dict) else {}

    def input_control(self, control_id: str, *, agent_id: str) -> dict[str, Any] | None:
        result = self.client.request("execution.input.get", {
            "control_id": control_id, "agent_id": agent_id,
        })
        return result if isinstance(result, dict) else None

    def set_priority(
        self,
        execution_id: str,
        *,
        actor: str,
        priority: int,
        performed_by: str = "",
    ) -> dict[str, Any]:
        result = self.client.request(
            "execution.priority",
            {
                "execution_id": str(execution_id or ""),
                "actor": str(actor or ""),
                "priority": int(priority),
                "performed_by": str(performed_by or actor),
            },
        )
        return result if isinstance(result, dict) else {}

    def reassign_queued_route(
        self,
        execution_id: str,
        *,
        actor: str,
        backend_route_id: str,
        performed_by: str = "",
    ) -> dict[str, Any]:
        result = self.client.request(
            "execution.route.reassign",
            {
                "execution_id": str(execution_id or ""),
                "actor": str(actor or ""),
                "backend_route_id": str(backend_route_id or ""),
                "performed_by": str(performed_by or actor),
            },
        )
        return result if isinstance(result, dict) else {}

    def wait(
        self,
        execution_id: str,
        *,
        actor: str,
        after_sequence: int = 0,
        timeout_seconds: float | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
        on_events: Callable[[list[dict[str, Any]]], None] | None = None,
        on_snapshot: Callable[[dict[str, Any]], bool | None] | None = None,
    ) -> dict[str, Any]:
        """Observe a durable execution; only explicit cancellation sends Stop."""
        deadline = (
            time.monotonic() + max(0.1, float(timeout_seconds))
            if timeout_seconds is not None
            else None
        )
        after_sequence = max(0, int(after_sequence))
        after_updated_at = ""
        cancel_sent = False
        last_control_plane_error = ""
        snapshot: dict[str, Any] = {}
        last_projected_snapshot: dict[str, Any] | None = None
        last_activity_signature: tuple[int, int] | None = None
        while deadline is None or time.monotonic() < deadline:
            remaining = (
                max(0.1, deadline - time.monotonic())
                if deadline is not None
                else 30.0
            )
            wait_seconds = min(30.0, remaining)
            try:
                # Business cancellation is local to this waiter; it does not
                # wake a Scheduler wait until we deliver the control request.
                # A quiet provider/tool must not hide Stop behind a 30s RPC.
                if not cancel_sent and cancellation_requested is not None:
                    if cancellation_requested():
                        self.cancel(
                            execution_id,
                            actor=actor,
                            reason="execution cancelled by caller",
                        )
                        cancel_sent = True
                    else:
                        wait_seconds = min(
                            wait_seconds, _CANCELLATION_CHECK_SECONDS,
                        )
                reply = self.client.request(
                    "execution.wait",
                    {
                        "execution_id": str(execution_id or ""),
                        "actor": str(actor or ""),
                        "after_sequence": after_sequence,
                        "after_updated_at": after_updated_at,
                        "timeout_seconds": wait_seconds,
                        "include_events": True,
                        "include_result": True,
                        "quiet_reply": True,
                    },
                    timeout=wait_seconds + 2,
                )
                last_control_plane_error = ""
            except (ExecutionServiceUnavailable, ExecutionResponseUnknown) as exc:
                # ExecutionService is the durable control plane. A socket
                # replacement does not say anything about the Worker or model
                # turn, so preserve the same execution identity and cursor
                # until the service is reachable again.
                last_control_plane_error = str(exc)
                retry_delay = 0.25
                if deadline is not None:
                    retry_delay = min(
                        retry_delay,
                        max(0.0, deadline - time.monotonic()),
                    )
                if retry_delay > 0:
                    time.sleep(retry_delay)
                continue
            reply = reply if isinstance(reply, dict) else {}
            if reply.get("unchanged") is True:
                # Nothing changed since the last full snapshot; its events
                # were already handled.
                reply = (
                    {**snapshot, "events": []}
                    if snapshot
                    else dict(self.status(
                        execution_id,
                        actor=actor,
                        after_sequence=after_sequence,
                        include_events=True,
                        include_result=True,
                    ))
                )
            snapshot = reply
            terminal_status = str(snapshot.get("status") or "").strip().lower()
            if (
                terminal_status in TERMINAL_STATUSES
                and not _terminal_snapshot_has_matching_result(snapshot)
            ):
                # A business waiter may overlap another process finishing and
                # acknowledging the same job.  Do not project an incomplete
                # terminal observation: confirm against the canonical row so
                # a stale terminal projection cannot end a live Execution.
                snapshot = dict(
                    self.status(
                        execution_id,
                        actor=actor,
                        after_sequence=after_sequence,
                        include_events=True,
                        include_result=True,
                    )
                )
                confirmed_status = str(
                    snapshot.get("status") or ""
                ).strip().lower()
                if (
                    confirmed_status in TERMINAL_STATUSES
                    and not _terminal_snapshot_has_matching_result(snapshot)
                ):
                    raise RuntimeError(
                        "ExecutionService returned a terminal state without "
                        "its matching ExecutionResult"
                    )
            events = [
                dict(item)
                for item in snapshot.get("events") or []
                if isinstance(item, dict)
            ]
            if events:
                after_sequence = max(
                    after_sequence,
                    max(int(item.get("sequence") or 0) for item in events),
                )
                if on_events is not None:
                    on_events(events)
            projected_snapshot = {key: value for key, value in snapshot.items() if key != "events"}
            # A Provider activity (for example "Command started") is appended to
            # the shared activity log and announced only to the ExecutionService
            # the Worker reports to; Dev and Release share PostgreSQL, not memory.
            # A quiet tool changes neither events nor the row, so the growing log
            # itself is a change worth projecting.
            activity_signature = _execution_activity_signature(execution_id)
            if on_snapshot is not None and (
                events
                or projected_snapshot != last_projected_snapshot
                or activity_signature != last_activity_signature
            ):
                observed_snapshot = copy.deepcopy(projected_snapshot)
                if on_snapshot(dict(snapshot)) is not False:
                    last_projected_snapshot = observed_snapshot
                    last_activity_signature = activity_signature
            after_updated_at = str(
                (snapshot.get("scheduler") or {}).get("updated_at")
                or after_updated_at
            )
            if str(snapshot.get("status") or "") in TERMINAL_STATUSES:
                return snapshot
        if deadline is None:
            raise RuntimeError("unreachable execution wait state")
        raise ExecutionWaitTimeout(
            execution_id, snapshot=snapshot, after_sequence=after_sequence,
            control_plane_error=last_control_plane_error,
        )

    def acknowledge(self, execution_id: str, *, actor: str) -> dict[str, Any]:
        result = self.client.request(
            "execution.acknowledge",
            {
                "execution_id": str(execution_id or ""),
                "actor": str(actor or ""),
            },
        )
        return result if isinstance(result, dict) else {}


    def token_usage(
        self,
        *,
        start_hour: str,
        end_hour: str,
        provider: str = "codex",
        include_accounts: bool = False,
    ) -> dict[str, Any]:
        result = self.client.request(
            "execution.token_usage",
            {
                "start_hour": str(start_hour or ""),
                "end_hour": str(end_hour or ""),
                "provider": str(provider or ""),
                "include_accounts": include_accounts,
            },
        )
        return dict(result or {}) if isinstance(result, dict) else {}

class _ExecutionRequestHandler(socketserver.StreamRequestHandler):
    server: "ExecutionServiceServer"

    def handle(self) -> None:
        raw = self.rfile.readline()
        request_id = ""
        try:
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError("request envelope must be an object")
            request_id = str(request.get("id") or "")
            method = str(request.get("method") or "")
            params = request.get("params")
            if not isinstance(params, dict):
                raise ValueError("request params must be an object")
            actor = self.server.rpc_auth.authenticate(method, params, request.get("auth"))
            if method == "instruction.execute":
                self._serve_instruction(params, request_id, actor)
                return
            if "actor" in params and str(params["actor"] or "").strip() != actor:
                raise PermissionError("RPC actor must match the authenticated assertion")
            result = self.server.dispatch(method, params)
            response = {"id": request_id, "ok": True, "result": result}
        except Exception as exc:
            response = {
                "id": request_id,
                "ok": False,
                "error": {
                    "class": exc.__class__.__name__,
                    "message": str(exc),
                },
            }
        try:
            self.wfile.write(
                json.dumps(
                    response,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n"
            )
        except (BrokenPipeError, ConnectionResetError):
            return


    def _serve_instruction(self, params: dict[str, Any], request_id: str, verified_caller: str) -> None:
        from management_portal.hatg.socket_rpc import serve

        def write_line(payload: dict[str, Any]) -> None:
            self.wfile.write(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
            )
            self.wfile.flush()

        try:
            serve(params, request_id, write_line, owner="execution", verified_caller=verified_caller)
        except (BrokenPipeError, ConnectionResetError):
            return


from management_common.service_endpoint import LocalServiceSocketMixin


class ExecutionServiceServer(LocalServiceSocketMixin, socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = False
    request_queue_size = socket.SOMAXCONN

    def __init__(
        self,
        socket_path: Path | str,
        scheduler: Scheduler,
        *,
        instance: str = "",
        build_id: str = "",
        preparation: ExecutionPreparation | None = None,
        agent_data: AgentDataStore | None = None,
        timing_proxy: Any = None,
        request_timing: Any = None,
        inherited_socket: int | None = None,
        maintenance_interval_seconds: float = (
            EXECUTION_MAINTENANCE_INTERVAL_SECONDS
        ),
    ) -> None:
        self.socket_path = Path(socket_path)
        from management_common.service_endpoint import service_endpoint
        self.endpoint = service_endpoint(self.socket_path)
        self.address_family = self.endpoint.family
        if inherited_socket is not None and not self.endpoint.is_unix:
            raise ValueError("Inherited ExecutionService sockets require a Unix endpoint")
        from management_common.rpc_auth import RpcAuthenticator
        self.rpc_auth = RpcAuthenticator(self.socket_path)
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self.preserve_socket = inherited_socket is not None
        if inherited_socket is None and self.endpoint.is_unix:
            self.socket_path.unlink(missing_ok=True)
        self.scheduler = scheduler
        self.timing_proxy = timing_proxy
        from .request_timing import RequestTimingModule
        self.request_timing = request_timing if request_timing is not None else RequestTimingModule()
        self.agent_data = agent_data or get_agent_data_store()
        self.preparation = preparation or ExecutionPreparation(
            session_manager=SessionManager(agent_data=self.agent_data), accounts=scheduler.accounts,
        )
        from .account_monitor import AccountMonitor
        self.accounts = self.preparation.accounts
        self.quota_monitor = AccountMonitor(self.accounts, scheduler)
        self.instance = str(instance or "")
        self.build_id = str(build_id or "")
        self.notification_publisher = get_notification_publisher()
        self._maintenance_interval_seconds = max(
            0.1,
            float(maintenance_interval_seconds),
        )
        self._next_maintenance_at = time.monotonic()
        self.started = threading.Event()
        self._closing = threading.Event()
        super().__init__(self.endpoint.address, _ExecutionRequestHandler,
                         bind_and_activate=inherited_socket is None)
        if inherited_socket is not None:
            self.socket.close()
            self.socket = socket.socket(fileno=inherited_socket)
        # Generations briefly share the accept queue during handover. A second
        # acceptor can consume readiness, so accept must not block shutdown.
        self.socket.setblocking(False)
        if self.endpoint.is_unix:
            os.chmod(self.socket_path, 0o660)
        from management_portal.hatg.kernel import register_kernel
        from . import isa

        isa.attach_server(self)
        from management_portal.hatg.maintenance import InstructionMaintenance

        kernel = isa.build_kernel()
        register_kernel(kernel)
        self.instruction_maintenance = InstructionMaintenance(kernel)

    def serve_forever(self, poll_interval: float = 0.2) -> None:
        if self.timing_proxy is not None:
            self.timing_proxy.start()
        self.instruction_maintenance.start()
        self.quota_monitor.start()
        self._next_maintenance_at = time.monotonic()
        threading.Thread(
            target=self._count_self_started_usage, name="execution-self-started-usage", daemon=True,
        ).start()
        self.started.set()
        super().serve_forever(poll_interval)

    def _count_self_started_usage(self) -> None:
        from .provider_catalog import AGENT_PROVIDERS

        while not self._closing.wait(SELF_STARTED_USAGE_INTERVAL_SECONDS):
            for definition in AGENT_PROVIDERS.values():
                try:
                    count = definition.self_started_usage_counter()
                    if count is not None:
                        count(self.agent_data, self.scheduler)
                except Exception:
                    LOGGER.exception("Self-started %s usage could not be counted", definition.label)

    def run_maintenance(self) -> dict[str, int]:
        # Finished work reaches its canonical execution before old terminal
        # executions are deleted (the first maintenance runs at start, after
        # any downtime); the last pass projects what maintenance finished.
        self._reconcile_terminal_agent_data()
        self.scheduler.prune_terminal(
            older_than_epoch=(
                time.time() - TERMINAL_EXECUTION_RETENTION_SECONDS
            )
        )
        result = self.scheduler.maintain()
        if time.monotonic() >= getattr(self, '_next_permission_cleanup_at', 0):
            try:
                from management_portal import permission_system
                permission_system.maintain()
                self._next_permission_cleanup_at = time.monotonic() + 86400
            except Exception:
                self._next_permission_cleanup_at = time.monotonic() + 300
                LOGGER.exception('Expired temporary credential cleanup failed')
        self._reconcile_terminal_agent_data()
        return result

    def service_actions(self) -> None:
        now = time.monotonic()
        if now < self._next_maintenance_at:
            return
        self._next_maintenance_at = (
            now + self._maintenance_interval_seconds
        )
        try:
            self.run_maintenance()
        except Exception:
            LOGGER.exception("ExecutionService maintenance failed")

    def server_close(self) -> None:
        maintenance = getattr(self, 'instruction_maintenance', None)
        if maintenance is not None:
            maintenance.stop()
        self._closing.set()
        self.quota_monitor.close()
        if self.timing_proxy is not None:
            self.timing_proxy.close()
        super().server_close()
        if not self.preserve_socket and self.endpoint.is_unix:
            self.socket_path.unlink(missing_ok=True)

    @staticmethod
    def _required(params: dict[str, Any], key: str) -> str:
        value = str(params.get(key) or "").strip()
        if not value:
            raise ValueError(f"{key} is required")
        return value

    def _changed(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        execution_id = str(snapshot.get("execution_id") or "")
        if execution_id:
            request = (
                snapshot.get("request")
                if isinstance(snapshot.get("request"), dict)
                else {}
            )
            self.notification_publisher.publish(
                notification_subject("execution", execution_id),
                {
                    "kind": "changed",
                    "status": str(snapshot.get("status") or ""),
                    "actor": str(
                        request.get("actor") or snapshot.get("actor") or ""
                    ),
                },
            )
        return snapshot

    def _record_accounted_usage(self, snapshot: Mapping[str, Any]) -> None:
        """Give an ended Execution's record the usage the Scheduler counted for it."""

        result = snapshot.get("result") if isinstance(snapshot.get("result"), dict) else {}
        accounted = result.get("accounted_usage")
        if not isinstance(accounted, dict) or not accounted:
            return
        try:
            self.agent_data.record_accounted_usage(str(snapshot.get("execution_id") or ""), accounted)
        except Exception:  # noqa: BLE001 - the ledger already holds the count; the record is display
            LOGGER.warning("Counted usage of %s could not be recorded", snapshot.get("execution_id"), exc_info=True)

    def _reconcile_terminal_agent_data(self) -> None:
        for canonical in self.agent_data.running_executions():
            clean_execution_id = str(
                canonical.get("execution_id") or ""
            ).strip()
            if not clean_execution_id:
                continue
            try:
                snapshot = self.scheduler.snapshot(clean_execution_id)
            except KeyError:
                continue
            status = str(snapshot.get("status") or "").strip()
            if status not in TERMINAL_STATUSES:
                continue
            result = (
                snapshot.get("result")
                if isinstance(snapshot.get("result"), dict)
                else {}
            )
            scheduler_state = (
                snapshot.get("scheduler")
                if isinstance(snapshot.get("scheduler"), dict)
                else {}
            )
            changed = self.agent_data.finish_execution(
                clean_execution_id,
                status=status,
                only_if_running=True,
                finished_at=str(scheduler_state.get("updated_at") or ""),
                usage=(
                    result.get("usage")
                    if isinstance(result.get("usage"), dict)
                    else {}
                ),
            )
            self._record_accounted_usage(snapshot)
            if changed:
                self._changed(snapshot)

    def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method in {"settings.get", "settings.update"}:
            from management_portal import permission_system
            instruction = "UpdateExecutionSettings" if method == "settings.update" else "GetExecutionSettings"
            if not permission_system.allows(self._required(params, "actor"), instruction):
                raise PermissionError(f"无权执行 {instruction}")
            if method == "settings.update":
                return self.request_timing.configure(params.get("model_request_timing_enabled"))
            return self.request_timing.settings.get()
        if method == "health":
            scheduler_health = self.scheduler.health()
            health: ExecutionServiceHealth = {
                "ok": True,
                "schema": EXECUTION_SERVICE_SCHEMA,
                "instance": self.instance,
                "build_id": self.build_id,
                "scheduler": scheduler_health,
                "notifications": self.notification_publisher.health(),
                "agent_data": self.agent_data.health(),
            }
            return health
        if method == "accounts.usage":
            return self.quota_monitor.usage_snapshot()
        if method == "accounts.list":
            if params.get("force_refresh"):
                self.quota_monitor.request_refresh()
            return self.quota_monitor.snapshot()
        if method == "accounts.resolve":
            return self.accounts.configuration(self._required(params, "provider"), str(params.get("backend_route_id") or ""), params.get("runtime_options") or {})
        if method == "runtime.catalog":
            return self.accounts.runtime_catalog(str(params.get("account_id") or ""), backend=str(params.get("backend") or ""), include_backends=bool(params.get("include_backends")))
        if method == "execution.submit":
            request = ExecutionRequest.from_payload(params)
            return self._changed(self.scheduler.submit(request, prepare=self.preparation.prepare))
        if method == "execution.states":
            executions = params.get("executions")
            if not isinstance(executions, dict) or len(executions) > 500:
                raise ValueError("execution.states requires at most 500 execution/actor pairs")
            if not all(isinstance(key,str) and key and isinstance(actor,str) and actor for key,actor in executions.items()):
                raise ValueError("execution.states requires execution and actor IDs")
            return self.scheduler.states(executions)
        if method == "execution.status":
            execution_id = self._required(params, "execution_id")
            actor = str(params.get("actor") or "")
            result = self.scheduler.snapshot(
                execution_id,
                after_sequence=int(params.get("after_sequence") or 0),
                include_events=bool(params.get("include_events", True)),
                include_result=bool(params.get("include_result", True)),
            )
            request = (
                result.get("request")
                if isinstance(result.get("request"), dict)
                else {}
            )
            if actor and str(
                request.get("actor") or result.get("actor") or ""
            ) != actor:
                raise KeyError(execution_id)
            return result
        if method == "execution.record":
            execution_id = self._required(params, "execution_id")
            actor = self._required(params, "actor")
            result = self.agent_data.execution(execution_id)
            if result is None or result["actor_ref"] != actor:
                raise KeyError(execution_id)
            return result
        if method == "execution.token_usage":
            return self.scheduler.token_usage(
                start_hour=self._required(params, "start_hour"),
                end_hour=self._required(params, "end_hour"),
                provider=self._required(params, "provider"),
                include_accounts=params.get("include_accounts") is True,
            )
        if method == "execution.wait":
            execution_id = self._required(params, "execution_id")
            actor = self._required(params, "actor")
            # The row's actor is the submitted request's actor. Reading only
            # it keeps each poll from parsing the whole request under the
            # Scheduler lock.
            if self.scheduler.actor_of(execution_id) != actor:
                raise KeyError(execution_id)
            result = self.scheduler.wait_for_change(
                execution_id,
                after_updated_at=str(params.get("after_updated_at") or ""),
                after_sequence=int(params.get("after_sequence") or 0),
                include_events=bool(params.get("include_events", True)),
                include_result=bool(params.get("include_result", True)),
                timeout_seconds=min(
                    60.0,
                    max(0.1, float(params.get("timeout_seconds") or 30.0)),
                ),
                quiet_reply=params.get("quiet_reply") is True,
            )
            return result
        if method == "execution.input.append":
            control = ExecutionInputControl.from_payload(params)
            return self.scheduler.submit_input_control(control)
        if method == "execution.turn_settings.update":
            control = ExecutionTurnSettingsControl.from_payload(params)
            return self.scheduler.submit_turn_settings_control(control)
        if method == "execution.input.get":
            control_id = self._required(params, "control_id")
            agent_id = self._required(params, "agent_id")
            receipt = self.scheduler.input_control(control_id)
            if receipt is None:
                return None
            execution = self.agent_data.execution(str(receipt["execution_id"]))
            if not execution or execution.get("agent_id") != agent_id:
                raise KeyError(control_id)
            return receipt
        if method == "execution.unstarted.withdraw":
            return self.scheduler.withdraw_unstarted(**{
                key: self._required(params, key) for key in
                ("execution_id", "actor", "binding_id", "operation_id", "replacement_execution_id", "performed_by")
            })
        if method == "execution.cancel":
            execution_id = self._required(params, "execution_id")
            actor = self._required(params, "actor")
            snapshot = self.scheduler.cancel(
                execution_id,
                actor=actor,
                reason=str(params.get("reason") or ""),
                performed_by=str(params.get("performed_by") or actor),
                **({"queued_only": True} if params.get("queued_only") is True else {}),
            )
            return self._changed(snapshot)
        if method == "execution.priority":
            execution_id = self._required(params, "execution_id")
            actor = self._required(params, "actor")
            priority = int(params.get("priority") or 0)
            snapshot = self.scheduler.set_priority(
                execution_id,
                actor=actor,
                priority=priority,
                performed_by=str(params.get("performed_by") or actor),
            )
            return self._changed(snapshot)
        if method == "execution.route.reassign":
            execution_id = self._required(params, "execution_id")
            actor = self._required(params, "actor")
            backend_route_id = self._required(params, "backend_route_id")
            snapshot = self.scheduler.reassign_queued_route(
                execution_id,
                actor=actor,
                backend_route_id=backend_route_id,
                performed_by=str(params.get("performed_by") or actor),
            )
            return self._changed(snapshot)
        if method == "execution.acknowledge":
            result = self.scheduler.acknowledge(
                self._required(params, "execution_id"),
                actor=self._required(params, "actor"),
            )
            return result
        if method == "scheduler.worker.register":
            return self.scheduler.register_worker(
                worker_id=self._required(params, "worker_id"),
                build_id=self._required(params, "build_id"),
                capabilities=[
                    str(item)
                    for item in params.get("capabilities") or []
                    if str(item)
                ],
                pid=int(params.get("pid") or 0),
                state=self._required(params, "state"),
            )
        if method == "scheduler.worker.activate":
            return self.scheduler.activate_worker(
                self._required(params, "worker_id"),
                previous_worker_id=str(params.get("previous_worker_id") or ""),
            )
        if method == "scheduler.worker.state":
            self.scheduler.set_worker_state(
                self._required(params, "worker_id"),
                self._required(params, "state"),
            )
            return {"ok": True}
        if method == "scheduler.worker.handover":
            return self.scheduler.handover_worker(
                self._required(params, "worker_id")
            )
        if method == "scheduler.worker.unregister":
            return {
                "ok": True,
                "removed": self.scheduler.unregister_worker(
                    self._required(params, "worker_id")
                ),
            }
        if method == "scheduler.claim.wait":
            claim_params = {
                "worker_id": self._required(params, "worker_id"),
                "capabilities": [
                    str(item)
                    for item in params.get("capabilities") or []
                    if str(item)
                ],
                "lease_seconds": int(params.get("lease_seconds") or 90),
            }
            claim_params["timeout_seconds"] = min(
                30.0,
                max(0.1, float(params.get("timeout_seconds") or 15.0)),
            )
            claimed = self.scheduler.claim_wait(
                **claim_params,
            )
            execution = (
                claimed.get("execution")
                if isinstance(claimed.get("execution"), dict)
                else None
            )
            if execution is not None:
                self._changed(execution)
            return claimed
        if method == "scheduler.start":
            execution_id = self._required(params, "execution_id")
            worker_id = self._required(params, "worker_id")
            snapshot = self._changed(
                self.scheduler.start(
                    execution_id,
                    worker_id=worker_id,
                    claim_token=self._required(params, "claim_token"),
                )
            )
            return snapshot
        if method == "scheduler.heartbeat":
            return self.scheduler.heartbeat(
                self._required(params, "execution_id"),
                worker_id=self._required(params, "worker_id"),
                claim_token=self._required(params, "claim_token"),
                lease_seconds=int(params.get("lease_seconds") or 90),
            )
        if method == "scheduler.handover.prepare":
            return self.scheduler.prepare_handover(
                self._required(params, "execution_id"),
                worker_id=self._required(params, "worker_id"),
                claim_token=self._required(params, "claim_token"),
            )
        if method == "scheduler.requeue":
            snapshot = self.scheduler.requeue_claim(
                self._required(params, "execution_id"),
                worker_id=self._required(params, "worker_id"),
                claim_token=self._required(params, "claim_token"),
                reason=str(params.get("reason") or ""),
                retry_after_seconds=float(
                    params.get("retry_after_seconds") or 0
                ),
            )
            return self._changed(snapshot)
        if method == "scheduler.control.wait":
            return self.scheduler.wait_for_worker_control(
                self._required(params, "execution_id"),
                worker_id=self._required(params, "worker_id"),
                claim_token=self._required(params, "claim_token"),
                timeout_seconds=min(
                    60.0,
                    max(
                        0.1,
                        float(params.get("timeout_seconds") or 30.0),
                    ),
                ),
            )
        if method == "scheduler.input.complete":
            receipt = self.scheduler.complete_input_control(
                self._required(params, "control_id"),
                execution_id=self._required(params, "execution_id"),
                worker_id=self._required(params, "worker_id"),
                claim_token=self._required(params, "claim_token"),
                delivered=bool(params.get("delivered")),
                error=str(params.get("error") or ""),
            )
            if receipt.get("status") == "delivered":
                from .appended_input_projection import materialize_delivered_input

                materialize_delivered_input(self.agent_data, receipt)
            return receipt
        if method == "scheduler.event":
            details = params.get("details")
            execution_id = self._required(params, "execution_id")
            worker_id = self._required(params, "worker_id")
            event_type = self._required(params, "event_type")
            if event_type == "token_usage_delta":
                detail_payload = (
                    dict(details) if isinstance(details, dict) else {}
                )
                return self.scheduler.record_token_usage_delta(
                    execution_id,
                    worker_id=worker_id,
                    claim_token=self._required(params, "claim_token"),
                    event_id=self._required(detail_payload, "event_id"),
                    usage=detail_payload.get("usage"),
                    # Adapters that read usage from a Provider session file
                    # after the turn report when each request was made.
                    occurred_at=str(detail_payload.get("occurred_at") or ""),
                )
            event_details = dict(details) if isinstance(details, dict) else {}
            if event_type == "provider_event":
                event_details = redact_sensitive_mapping(event_details)
            activity = self.scheduler.append_event(
                execution_id,
                worker_id=worker_id,
                claim_token=self._required(params, "claim_token"),
                event_type=event_type,
                details=event_details,
            )
            activity_path = execution_activity_path(execution_id)
            if activity_path is None:
                raise RuntimeError("execution activity binding is invalid")
            append_execution_activity(activity_path, [activity])
            self.notification_publisher.publish(
                notification_subject(
                    "execution",
                    self._required(params, "execution_id"),
                    scope="activity",
                ),
                {
                    "kind": "activity",
                    "type": event_type,
                    "actor": str(activity.get("actor") or ""),
                },
            )
            return {"ok": True}
        if method == "scheduler.checkpoint":
            checkpoint = params.get("checkpoint")
            if not isinstance(checkpoint, dict):
                raise ValueError("execution checkpoint must be an object")
            return self._changed(self.scheduler.checkpoint(
                self._required(params, "execution_id"),
                worker_id=self._required(params, "worker_id"),
                claim_token=self._required(params, "claim_token"),
                checkpoint=checkpoint,
            ))
        if method == "scheduler.capacity.reassign":
            return self._changed(self.scheduler.reassign_capacity(
                self._required(params, "execution_id"),
                worker_id=self._required(params, "worker_id"),
                claim_token=self._required(params, "claim_token"),
                backend_route_id=self._required(
                    params,
                    "backend_route_id",
                ),
            ))
        if method == "scheduler.quota_states":
            return self.scheduler.binding_quota_states(params.get("bindings"))
        if method == "scheduler.quota_overrides.configure":
            return self._changed(self.scheduler.configure_binding_quota_overrides(params.get("policies")))
        if method == "scheduler.quota_overrides.forget":
            return self.scheduler.forget_binding_quota_overrides(params.get("binding_ids"))
        if method == "scheduler.capacity.configure":
            account_id = self._required(params, "account_id")
            account = self.accounts.get(account_id)
            for key in (
                "max_concurrency",
                "dispatch_interval_min_seconds",
                "dispatch_interval_max_seconds",
            ):
                if key not in params:
                    raise ValueError(f"{key} is required")
            setting = self.scheduler.configure_capacity(
                account.capacity_key,
                params.get("max_concurrency"),
                float(params.get("dispatch_interval_min_seconds") or 0),
                float(params.get("dispatch_interval_max_seconds") or 0),
                actor=self._required(params, "actor"),
                close_threshold=params.get("close_threshold"),
            )
            return {
                "ok": True,
                "account_id": account.account_id,
                "close_threshold": setting.get("close_threshold"),
                "max_concurrency": int(setting["limit"]),
                "dispatch_interval_min_seconds": float(
                    setting["dispatch_interval_min_seconds"]
                ),
                "dispatch_interval_max_seconds": float(
                    setting["dispatch_interval_max_seconds"]
                ),
                "updated_by": str(setting["updated_by"]),
                "updated_at": str(setting["updated_at"]),
            }
        if method == "scheduler.finish":
            result = params.get("result")
            if not isinstance(result, dict):
                raise ValueError("execution result must be an object")
            execution_result = ExecutionResult.from_payload(result)
            finished = self.scheduler.finish(
                execution_result,
                worker_id=self._required(params, "worker_id"),
                claim_token=self._required(params, "claim_token"),
            )
            self._record_accounted_usage(finished)
            snapshot = self._changed(finished)
            if execution_result.usage:
                self.notification_publisher.publish(
                    business_projection_subject("token-usage", "snapshot"),
                    {"kind": "changed", "fields": ["usage"]},
                )
            return snapshot
        raise ValueError(f"unsupported ExecutionService method: {method}")


def get_execution_service() -> ExecutionService:
    return ExecutionService()


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Run the TFI Portal ExecutionService control plane."
    )
    root.add_argument(
        "--socket",
        type=Path,
        default=execution_service_socket(),
    )
    root.add_argument(
        "--max-concurrency",
        type=int,
        default=int(os.getenv("PORTAL_EXECUTION_MAX_CONCURRENCY", "8")),
    )
    root.add_argument(
        "--dispatch-interval-min-seconds",
        type=float,
        default=float(
            os.getenv(
                "PORTAL_EXECUTION_DISPATCH_INTERVAL_MIN_SECONDS",
                "2",
            )
        ),
    )
    root.add_argument(
        "--dispatch-interval-max-seconds",
        type=float,
        default=float(
            os.getenv(
                "PORTAL_EXECUTION_DISPATCH_INTERVAL_MAX_SECONDS",
                "6",
            )
        ),
    )
    return root


def main() -> int:
    args = parser().parse_args()
    from .provider_timing_proxy import TimingProxyModule
    from .request_timing import LegacyRouteSettings
    from .service_lifecycle import ServiceLifecycle, inherited_descriptors
    inherited = inherited_descriptors()
    accounts = default_execution_accounts()
    scheduler = Scheduler(
        accounts=accounts,
        quota_reserve_enabled=True,
        max_concurrency=args.max_concurrency,
        dispatch_interval_min_seconds=(
            args.dispatch_interval_min_seconds
        ),
        dispatch_interval_max_seconds=(
            args.dispatch_interval_max_seconds
        ),
    )
    server = ExecutionServiceServer(
        args.socket,
        scheduler,
        instance=os.getenv("PORTAL_INSTANCE", ""),
        build_id=os.getenv("PORTAL_BUILD_ID", ""),
        agent_data=get_agent_data_store(),
        # Carry only already-issued routes through a live upgrade. No fresh
        # process starts a timing proxy and no adapter issues new routes.
        timing_proxy=(TimingProxyModule(LegacyRouteSettings(), inherited=inherited['proxy'])
                      if inherited.get('proxy') else None),
        inherited_socket=inherited.get('rpc'),
    )
    return ServiceLifecycle(server, inherited).run()


if __name__ == "__main__":
    raise SystemExit(main())
