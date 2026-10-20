from __future__ import annotations

from importlib import import_module

from management_common.operation_errors import failure_payload

import argparse
import concurrent.futures
import inspect
import json
import multiprocessing
import os
import secrets
import socket
import socketserver
from management_common.service_shutdown import DrainingStreamServer, serve_until_stopped, stop_workers
from management_common.service_endpoint import LocalServiceSocketMixin, service_endpoint
import threading
import time
from concurrent.futures.process import BrokenProcessPool
from collections.abc import Mapping as MappingABC
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from management_portal.notifications import (
    get_notification_publisher,
)
from management_portal.execution import event_projection
from management_portal.society_service.runtime.backend_adapter import (
    build_backend_runtime_ports,
)
from management_portal.society_service.runtime.contracts import (
    ResidentTerminalFact,
    ResidentWakeRequest,
)

from .config import society_service_socket, runs_lineage_timers, syncs_template_structure
from .state_store import get_lineage_data_store
from .resident_conversation_stream import get_repository as get_resident_conversation_stream
from .changes import (
    collect_changes,
    publish_collected_changes,
    set_local_change_handler,
)
from .contracts import (
    DOMAIN_METHODS,
    SOCIETY_SERVICE_SCHEMA,
    Operation,
    operation_contract,
)
from . import lineage_message_events
from .coordinator import LineageCoordinator
from .lifecycle import (
    set_execution_cancellation_handler,
    set_execution_wake_handler,
)
from .active_agent_projection import publish_changed as publish_active_agents_changed
from .application import configure_default_domain_ports
from .resident_conversation_event_relay import ResidentConversationEventRelay
from .resident_resource_projection import ResidentResourceProjectionWorker
from . import execution_client, resource_client


def _initialize_read_worker() -> None:
    """Initialize one read-only Lineage projection process."""

    os.environ["PORTAL_LINEAGE_READ_WORKER"] = "1"
    configure_default_domain_ports()
    # Stores initialize on the first real read. Warming an otherwise idle
    # projection process opens several domain pools in each service generation.


def _read_worker_identity(delay: float = 0.0) -> int:
    if delay > 0:
        time.sleep(delay)
    return os.getpid()


def _execute_read_domain_call(params: dict[str, Any]) -> Any:
    from .read_snapshot import read_snapshot_scope
    from .resident_lineage import access_snapshot_scope

    with read_snapshot_scope(), access_snapshot_scope():
        return _execute_scoped_read_domain_call(params)


def _execute_scoped_read_domain_call(params: dict[str, Any]) -> Any:
    """Execute one canonical read in a process without runtime side effects."""

    domain = str(params.get("domain") or "")
    method = str(params.get("name") or "")
    args = params.get("args")
    kwargs = params.get("kwargs")
    handler, bound = _bind_domain_call(domain, method, args, kwargs)
    operation = operation_contract(domain, method)
    if operation.mode != "read":
        raise ValueError(f"unsupported SocietyService read: {domain}.{method}")

    lineage_id = _operation_lineage_id(operation, bound)
    lineage_module = _domain_module("lineage")
    state_scope = lineage_module.lineage_state_scope(lineage_id, f"{domain}.{method}") if lineage_id else nullcontext()
    with state_scope:
        return _invoke_domain(operation, handler, bound)


# Dev and Release each run a SocietyService on the same cores: twelve read
# processes apiece, three more for Inbox activity.  A busy read process holds
# a PostgreSQL connection only while a domain read is using it; the common
# database boundary releases idle read-worker connections immediately.
DEFAULT_READ_WORKERS = 12
CONVERSATION_INDEX_WORKERS = 3


def _read_domain_response(params: dict[str, Any]) -> dict[str, Any]:
    try:
        return {"ok": True, "result": _execute_read_domain_call(params)}
    except Exception as exc:
        return {"ok": False, "error": _error_payload(exc)}


def _is_read_instruction(params: Any) -> bool:
    from management_portal.hatg.catalog import declaration
    from management_portal.hatg.contracts import InstructionCall
    from management_portal.hatg.contracts import InstructionError

    if not isinstance(params, dict) or "recovery_release" in params:
        return False
    try:
        target = declaration(InstructionCall.from_payload(params.get("call")).instruction)
    except (ValueError, TypeError, KeyError, InstructionError):
        return False
    return target.owner == "lineage" and not target.mutates and not target.streaming


def _read_instruction_response(params: dict[str, Any], request_id: str, verified_caller: str) -> dict[str, Any]:
    from management_portal.hatg.socket_rpc import serve
    from .read_snapshot import read_snapshot_scope
    from .resident_lineage import access_snapshot_scope

    if not _is_read_instruction(params):
        raise ValueError("Lineage read workers only accept non-streaming read instructions")
    responses: list[dict[str, Any]] = []
    with read_snapshot_scope(), access_snapshot_scope():
        serve(params, request_id, responses.append, owner="lineage", verified_caller=verified_caller)
    if len(responses) != 1:
        raise RuntimeError("A read instruction must return exactly one response")
    return responses[0]


class LineageReadProcessPool:
    """Fixed multi-process execution boundary for canonical read projections."""

    def __init__(self, workers: int) -> None:
        self.workers = max(0, int(workers))
        self._lock = threading.Lock()
        self._executor: concurrent.futures.ProcessPoolExecutor | None = None
        self._worker_pids: tuple[int, ...] = ()
        self._closed = False
        if self.workers:
            self._start()

    def _new_executor(self) -> concurrent.futures.ProcessPoolExecutor:
        return concurrent.futures.ProcessPoolExecutor(
            max_workers=self.workers,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=_initialize_read_worker,
        )

    def _start(self) -> None:
        executor = self._new_executor()
        futures = [
            executor.submit(_read_worker_identity, 0.05)
            for _ in range(self.workers)
        ]
        self._executor = executor
        self._worker_pids = tuple(sorted({future.result() for future in futures}))

    def _replace(self, failed: concurrent.futures.ProcessPoolExecutor) -> None:
        with self._lock:
            if self._closed or self._executor is not failed:
                return
            failed.shutdown(wait=False, cancel_futures=True)
            self._start()

    def _submit(self, function, *args) -> Any:
        if self._closed:
            raise RuntimeError("Lineage read pool is stopping")
        executor = self._executor
        if executor is None:
            return function(*args)
        else:
            try:
                return executor.submit(function, *args).result()
            except BrokenProcessPool:
                self._replace(executor)
                replacement = self._executor
                if replacement is None:
                    raise
                return replacement.submit(function, *args).result()

    def call_instruction(self, params: dict[str, Any], request_id: str, verified_caller: str) -> dict[str, Any]:
        return self._submit(_read_instruction_response, params, request_id, verified_caller)

    def call(self, params: dict[str, Any]) -> Any:
        response = self._submit(_read_domain_response, params)
        if not response["ok"]:
            from .transport import SocietyServiceClient

            SocietyServiceClient._raise_remote_error(response["error"])
        return response["result"]

    def health(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.workers),
            "workers": self.workers,
            "worker_pids": list(self._worker_pids),
        }

    def close(self) -> None:
        with self._lock:
            self._closed = True
            executor = self._executor
            self._executor = None
        if executor is not None:
            # Python 3.12 has no public terminate_workers API. These workers
            # only execute reads, so ending an in-flight read cannot lose a job.
            processes = list((executor._processes or {}).values())
            executor.shutdown(wait=False, cancel_futures=True)
            for process in processes:
                if process.is_alive():
                    process.terminate()
            deadline = time.monotonic() + 1.0
            for process in processes:
                process.join(max(0.0, deadline - time.monotonic()))
            for process in processes:
                if process.is_alive():
                    process.kill()
            for process in processes:
                process.join(timeout=0.2)


# Identifies this process as the holder of the Lineage timer lease.
_TIMER_LEASE_HOLDER = (
    f"{os.getenv('PORTAL_INSTANCE', 'dev') or 'dev'}:{socket.gethostname()}:{os.getpid()}:{secrets.token_hex(4)}"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, MappingABC):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    return value


def _domain_module(domain: str) -> Any:
    if domain == "resident":
        from . import resident_domain

        return resident_domain
    if domain == "lineage":
        from . import lineage_domain

        return lineage_domain
    raise ValueError(f"unsupported SocietyService domain: {domain}")


def _sync_template_structure() -> dict[str, Any]:
    from .templates import familize

    return familize.sync_all_generated_structure()


def _migrate_stored_runtime() -> dict[str, Any]:
    from . import lineage_program_runtime

    return lineage_program_runtime.migrate_stored_runtime()


def _domain_handler(domain: str, method: str) -> Any:
    operation = operation_contract(domain, method)
    return getattr(import_module(operation.implementation), method)


def _bind_domain_call(
    domain: str, method: str, args: object, kwargs: object,
) -> tuple[Any, inspect.BoundArguments]:
    if domain not in DOMAIN_METHODS or method not in DOMAIN_METHODS[domain]:
        raise ValueError(f"unsupported SocietyService call: {domain}.{method}")
    if not isinstance(args, list) or not isinstance(kwargs, dict):
        raise ValueError("domain call args/kwargs are invalid")
    handler = _domain_handler(domain, method)
    signature = inspect.signature(handler)
    try:
        bound = signature.bind(*args, **kwargs)
    except TypeError as exc:
        raise ValueError(f"invalid arguments for {domain}.{method}: {exc}") from exc
    bound.apply_defaults()
    return handler, bound


def _operation_lineage_id(operation: Operation, bound: inspect.BoundArguments) -> str:
    target = operation.target_id(bound.arguments)
    if operation.scope == "resident":
        return _domain_module("resident").resident_aggregate_lineage_id(target) if target else ""
    if operation.scope == "lineage":
        return target
    return ""


def _invoke_domain(operation: Operation, handler: Any, bound: inspect.BoundArguments) -> Any:
    result = handler(*bound.args, **bound.kwargs)
    if operation.result:
        from .result_contracts import validate_result
        validate_result(operation.result, result)
    return result


def _error_payload(exc: Exception) -> dict[str, Any]:
    return _json_value(failure_payload(exc))


def _validate_domain_handlers() -> None:
    invalid: list[str] = []
    for domain, methods in DOMAIN_METHODS.items():
        for method in methods:
            try:
                handler = _domain_handler(domain, method)
            except (AttributeError, ImportError):
                invalid.append(f"{domain}.{method} (missing)")
                continue
            if not callable(handler):
                invalid.append(f"{domain}.{method} (not callable)")
                continue
            operation = operation_contract(domain, method)
            if operation.mode not in {"read", "write"} or not operation.authority:
                invalid.append(f"{domain}.{method} (invalid operation semantics)")
            if operation.scope not in {"owner", "resident", "lineage"}:
                invalid.append(f"{domain}.{method} (invalid scope)")
            if operation.target and operation.target.split(".")[0] not in inspect.signature(handler).parameters:
                invalid.append(f"{domain}.{method} (missing scope argument {operation.target})")
            if operation.result:
                from .result_contracts import result_schema
                result_schema(operation.result)
    if invalid:
        raise RuntimeError(
            "invalid SocietyService domain contract: " + ", ".join(sorted(invalid))
        )


class _LocalDomain:
    def __init__(self, domain: str) -> None:
        self._domain = domain

    def __getattr__(self, method: str):
        if method not in DOMAIN_METHODS[self._domain]:
            raise AttributeError(f"unsupported {self._domain} method: {method}")
        def invoke(*args: Any, **kwargs: Any) -> Any:
            target, bound = _bind_domain_call(self._domain, method, list(args), kwargs)
            operation = operation_contract(self._domain, method)
            try:
                lineage_id = _operation_lineage_id(operation, bound)
                state_scope = (
                    _domain_module("lineage").lineage_state_scope(
                        lineage_id, f"{self._domain}.{method}",
                    ) if lineage_id else nullcontext()
                )
                with state_scope:
                    return _invoke_domain(operation, target, bound)
            except Exception as exc:
                from management_portal.resources.errors import ResourceServiceUnavailable
                if not isinstance(exc, ResourceServiceUnavailable):
                    raise
                from .transport import SocietyServiceClient

                SocietyServiceClient._raise_remote_error(_error_payload(exc))

        return invoke


class LocalSocietyService:
    """In-process facade reserved for the SocietyService process and tests."""

    def __init__(self) -> None:
        configure_default_domain_ports()
        self.resident = _LocalDomain("resident")
        self.workspace = _LocalDomain("workspace")
        self.project = _LocalDomain("project")
        self.lineage = _LocalDomain("lineage")
        self.projection = _LocalDomain("projection")
        self.service_invocation = _LocalDomain("service_invocation")

    @staticmethod
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "schema": SOCIETY_SERVICE_SCHEMA,
            "mode": "in_process",
        }


def _start_resident_context_worker() -> None:
    """Resident discovery's mechanical work; a failure here never stops the service."""

    import logging

    try:
        from .resident_context.worker import start_worker

        start_worker()
    except Exception:  # noqa: BLE001 - discovery is optional for every other operation
        logging.getLogger(__name__).warning("Resident discovery worker did not start", exc_info=True)


def _refresh_builtin_templates() -> None:
    import logging

    try:
        from .templates.store import ensure_builtins

        ensure_builtins()
    except Exception:  # noqa: BLE001 - the next start retries; stored built-ins keep working
        logging.getLogger(__name__).warning("Built-in template refresh failed", exc_info=True)


class _LineageRequestHandler(socketserver.StreamRequestHandler):
    server: "SocietyServiceServer"

    def handle(self) -> None:
        request_id = ""
        try:
            request = json.loads(self.rfile.readline())
            if not isinstance(request, dict):
                raise ValueError("request envelope must be an object")
            request_id = str(request.get("id") or "")
            params = request.get("params")
            if not isinstance(params, dict):
                raise ValueError("request params must be an object")
            method = str(request.get("method") or "")
            actor = self.server.rpc_auth.authenticate(method, params, request.get("auth"))
            if method == "instruction.execute":
                self.server.serve_instruction(params, request_id, self._write_line, verified_caller=actor)
                return
            result = self.server.dispatch(method, params)
            response = {"id": request_id, "ok": True, "result": _json_value(result)}
        except Exception as exc:
            response = {"id": request_id, "ok": False, "error": _error_payload(exc)}
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


    def _write_line(self, payload: dict[str, Any]) -> None:
        self.wfile.write(
            json.dumps(_json_value(payload), ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        )
        self.wfile.flush()


class SocietyServiceServer(LocalServiceSocketMixin, DrainingStreamServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = socket.SOMAXCONN

    def __init__(
        self,
        socket_path: Path | str,
        *,
        publisher: object | None = None,
        housekeeping_seconds: float = 300.0,
        enable_projections: bool | None = None,
        read_workers: int = 0,
    ) -> None:
        _validate_domain_handlers()
        configure_default_domain_ports()
        self.socket_path = Path(socket_path)
        self.endpoint = service_endpoint(self.socket_path)
        self.address_family = self.endpoint.family
        from management_common.rpc_auth import RpcAuthenticator
        self.rpc_auth = RpcAuthenticator(self.socket_path)
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.endpoint.is_unix:
            self.socket_path.unlink(missing_ok=True)
        self.publisher = publisher or get_notification_publisher()
        self.projections_enabled = (
            str(os.getenv("PORTAL_SOCIETY_SERVICE_PROCESS", "") or "").strip() == "1"
            if enable_projections is None
            else bool(enable_projections)
        )
        self.resident_conversation_event_relay = ResidentConversationEventRelay(
            get_lineage_data_store(),
            self.publisher,
        )
        # One bundle per claim: a Resident's own confirmation, which claims
        # its bundle directly, then waits for at most one bundle the
        # background worker took, never for a whole batch of new Residents.
        self.resident_resource_projection = ResidentResourceProjectionWorker(
            get_lineage_data_store(),
            resource_client,
            batch_size=1,
        )
        from .effect_recovery_worker import EffectRecoveryWorker
        self.effect_recovery_worker = EffectRecoveryWorker()
        from .family_resource_projection import FamilyResourceProjectionWorker
        self.family_resource_projection = FamilyResourceProjectionWorker()
        self.runtime_ports = build_backend_runtime_ports()
        from .domain_ports import resident_terminal_port

        resident_terminal_port.bind(self._report_resident_terminal)
        def housekeeping() -> None:
            event_projection.prune_execution_activity(
                older_than_epoch=(
                    time.time()
                    - event_projection.ACTIVITY_TRACE_TTL_SECONDS
                ),
                time_budget_seconds=2.0,
            )
            delivered_before = datetime.fromtimestamp(time.time() - 3600, timezone.utc)
            get_resident_conversation_stream().prune_event_deliveries(
                older_than=delivered_before.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            )

        self.coordinator = LineageCoordinator(
            resume_residents=lambda: _domain_module(
                "resident"
            ).resume_pending_resident_executions(),
            resume_resident=lambda resident_id: _domain_module(
                "resident"
            ).resume_resident_execution(resident_id),
            reconcile_all=self.runtime_ports.system_events.poll_all,
            recover_program=lambda: _domain_module(
                "lineage"
            ).recover_pending_program_work_as_service(),
            reconcile_lineage=lambda lineage_id: _domain_module(
                "lineage"
            ).reconcile_lineage_system_events_as_service(lineage_id),
            consume_terminal=lambda payload: self._consume_resident_terminal(
                ResidentTerminalFact.from_payload(payload)
            ),
            housekeeping=housekeeping,
            housekeeping_seconds=housekeeping_seconds,
            lifecycle_ready=lambda: _domain_module("resident").ready_resident_lifecycle_operations(),
            lifecycle_run=lambda operation_id: _domain_module("resident").resume_resident_lifecycle_operation(operation_id),
            cancel_execution=lambda execution_id, actor: execution_client.service().cancel(
                execution_id, actor=actor, reason="execution cancelled by caller",
            ),
            dispatch_arrival=lambda lineage_id, resident_id, message_id: _domain_module(
                "lineage"
            ).dispatch_message_arrival(lineage_id, resident_id, message_id),
            wake_for_due_sources=runs_lineage_timers(),
            timer_lease=lambda: get_lineage_data_store().hold_lineage_timer_lease(
                _TIMER_LEASE_HOLDER, ttl_seconds=90,
            ),
            sync_templates=_sync_template_structure if syncs_template_structure() else None,
            # The instance that syncs published templates also migrates stored
            # runtime: Dev may run an unpublished normalization rule.
            migrate_runtime=_migrate_stored_runtime if syncs_template_structure() else None,
        )
        set_execution_cancellation_handler(self.coordinator.notify_execution_cancellation)
        set_execution_wake_handler(self.coordinator.notify_resident)
        lineage_message_events.set_arrival_handler(self.coordinator.notify_message_arrival)
        if self.projections_enabled:
            set_local_change_handler(self._handle_background_change)
        self.started_at = _now_iso()
        from management_portal.hatg.kernel import register_kernel
        from .isa import build_kernel

        from management_portal.hatg.maintenance import InstructionMaintenance

        kernel = build_kernel()
        register_kernel(kernel)
        self.instruction_maintenance = InstructionMaintenance(kernel)
        super().__init__(self.endpoint.address, _LineageRequestHandler)
        if self.endpoint.is_unix:
            os.chmod(self.socket_path, 0o660)
        self.read_pool = LineageReadProcessPool(read_workers)
        # Full Inbox activity pages cannot occupy the workers that serve Chat;
        # several pages are read at once (one process queued them behind
        # each other into 504s).
        self.conversation_index_pool = LineageReadProcessPool(min(CONVERSATION_INDEX_WORKERS, read_workers))

    def serve_forever(self, poll_interval: float = 0.2) -> None:
        from .system_workflow_switches import restore
        restore()
        self.instruction_maintenance.start()
        if self.projections_enabled:
            # Establish the durable event-log tail before any coordinator work
            # can commit new Conversation events.
            self.resident_conversation_event_relay.start()
            self.resident_resource_projection.start()
            self.family_resource_projection.start()
            self.effect_recovery_worker.start()
        self.coordinator.start()
        if self.projections_enabled:
            # Built-in Function Blocks follow their code (decision_5b5653c3eaa5).
            threading.Thread(target=_refresh_builtin_templates, name="template-builtins", daemon=True).start()
            _start_resident_context_worker()
            from .script_runs.worker import start_worker as start_script_worker
            start_script_worker()
        super().serve_forever(poll_interval)

    def server_close(self) -> None:
        self.begin_shutdown()
        set_execution_cancellation_handler(None)
        set_execution_wake_handler(None)
        lineage_message_events.set_arrival_handler(None)
        if self.projections_enabled:
            set_local_change_handler(None)
        from .domain_ports import resident_terminal_port

        resident_terminal_port.bind(None)
        callbacks = {
            "instruction-maintenance": self.instruction_maintenance.stop,
            "coordinator": self.coordinator.stop,
            "read-pool": self.read_pool.close,
            "conversation-index-pool": self.conversation_index_pool.close,
        }
        if self.projections_enabled:
            from .resident_context.worker import stop_worker
            from .script_runs.worker import stop_worker as stop_script_worker
            callbacks.update({
                "resource-projection": self.resident_resource_projection.stop,
                "family-resource-projection": self.family_resource_projection.stop,
                "effect-recovery": self.effect_recovery_worker.stop,
                "conversation-relay": self.resident_conversation_event_relay.stop,
                "resident-context": stop_worker,
                "script-runs": stop_script_worker,
            })
        stop_workers(callbacks)
        super().server_close()
        if self.endpoint.is_unix:
            self.socket_path.unlink(missing_ok=True)

    def _apply_change(
        self,
        changed_domain: str,
        resource_id: str,
        scope: str,
    ) -> None:
        if changed_domain == "resident" and self.projections_enabled:
            publish_active_agents_changed(self.publisher, resource_id)
        if changed_domain == "lineage":
            self.coordinator.notify_lineage(resource_id)

    def _handle_background_change(
        self,
        changed_domain: str,
        resource_id: str,
        scope: str,
    ) -> None:
        self._apply_change(changed_domain, resource_id, scope)

    def _finish_changes(
        self,
        changes: list[tuple[str, str, str, dict[str, Any]]],
    ) -> None:
        for changed_domain, resource_id, scope, _payload in changes:
            self._apply_change(changed_domain, resource_id, scope)
        publish_collected_changes(self.publisher, changes)

    def _consume_resident_terminal(
        self,
        fact: ResidentTerminalFact,
    ) -> dict[str, Any]:
        with collect_changes() as changes:
            try:
                result = self.runtime_ports.resident_terminals.consume_terminal(fact)
                return result
            finally:
                self._finish_changes(changes)

    def _report_resident_terminal(
        self,
        resident_id: str,
        actor: str,
        message_id: str,
        **details: Any,
    ) -> dict[str, Any]:
        fact = ResidentTerminalFact(
            resident_id=resident_id,
            actor=actor,
            message_id=message_id,
            completed_at=str(details.get("completed_at") or ""),
            terminal_status=str(details.get("terminal_status") or "completed"),
            lineage_id=str(details.get("lineage_id") or ""),
            business_ref=str(details.get("business_ref") or ""),
        )
        fact.validate()
        self.coordinator.notify_terminal(fact.to_payload())
        return {
            "status": "accepted",
            "resident_id": fact.resident_id,
            "message_id": fact.message_id,
        }

    def serve_instruction(self, params: dict[str, Any], request_id: str, write_line: Any, *, verified_caller: str) -> None:
        """``instruction.execute`` (decision_96a887d83e8a); notifications follow the commit."""

        from management_portal.hatg.socket_rpc import serve

        if self.read_pool.workers and _is_read_instruction(params):
            try:
                write_line(self.read_pool.call_instruction(params, request_id, verified_caller))
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        with collect_changes() as changes:
            try:
                serve(params, request_id, write_line, owner="lineage", verified_caller=verified_caller)
            except (BrokenPipeError, ConnectionResetError):
                return
            finally:
                self._finish_changes(changes)

    def _domain_call(self, params: dict[str, Any]) -> Any:
        domain = str(params.get("domain") or "")
        method = str(params.get("name") or "")
        args = params.get("args")
        kwargs = params.get("kwargs")
        handler, bound = _bind_domain_call(domain, method, args, kwargs)
        operation = operation_contract(domain, method)
        if operation.mode == "read" and self.read_pool.workers and not operation.in_process:
            if (domain == "resident" and method == "list_resident_conversation_activity"
                    and bound.arguments["page_size"] is not None):
                return self.conversation_index_pool.call(params)
            return self.read_pool.call(params)
        lineage_id = _operation_lineage_id(operation, bound)
        lineage_module = _domain_module("lineage")
        state_scope = lineage_module.lineage_state_scope(lineage_id, f"{domain}.{method}") if lineage_id else nullcontext()
        with collect_changes() as changes:
            try:
                with state_scope:
                    return _invoke_domain(operation, handler, bound)
            finally:
                self._finish_changes(changes)

    def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "health":
            coordinator = self.coordinator.snapshot()
            conversation_relay = self.resident_conversation_event_relay.health()
            resource_projection = self.resident_resource_projection.health()
            lifecycle = _domain_module("resident").resident_lifecycle_health()
            lineage_module = _domain_module("lineage")
            return {
                "ok": not bool(
                    coordinator.get("last_error")
                    or coordinator.get("last_housekeeping_error")
                    or conversation_relay.get("last_error")
                    or resource_projection.get("last_error")
                    or lifecycle.get("last_error")
                )
                and bool(coordinator.get("thread_alive"))
                and (
                    not self.projections_enabled
                    or (
                        bool(conversation_relay.get("running"))
                        and bool(resource_projection.get("running"))
                    )
                ),
                "schema": SOCIETY_SERVICE_SCHEMA,

                "instance": str(os.getenv("PORTAL_INSTANCE", "") or ""),
                "build_id": str(os.getenv("PORTAL_BUILD_ID", "") or ""),
                "pid": os.getpid(),
                "started_at": self.started_at,
                "read_pool": self.read_pool.health(),
                "conversation_index_pool": self.conversation_index_pool.health(),
                "coordinator": coordinator,
                "state_locks": lineage_module.state_lock_health(),
                "notifications": self.publisher.health(),
                "resident_conversation_event_relay": conversation_relay,
                "resident_resource_projection": resource_projection,
                "resident_lifecycle": lifecycle,
                "lineage_data": get_lineage_data_store().health(),
            }
        if method == "domain.call":
            return self._domain_call(params)
        if method == "runtime.resident.wake":
            request = ResidentWakeRequest.from_payload(params)
            self.coordinator.notify_resident(request.resident_id)
            return {"accepted": True}
        raise ValueError(f"unsupported SocietyService method: {method}")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Run TFI Portal SocietyService")
    root.add_argument("--socket", type=Path, default=society_service_socket())
    root.add_argument(
        "--housekeeping-seconds",
        type=float,
        default=float(
            os.getenv("PORTAL_LINEAGE_HOUSEKEEPING_SECONDS", "300")
        ),
    )
    root.add_argument(
        "--read-workers",
        type=int,
        default=max(
            1,
            int(
                os.getenv(
                    "PORTAL_LINEAGE_READ_WORKERS",
                    str(min(DEFAULT_READ_WORKERS, os.cpu_count() or 1)),
                )
            ),
        ),
    )
    return root


def main() -> int:
    args = parser().parse_args()
    os.environ.setdefault("PORTAL_SOCIETY_SERVICE_PROCESS", "1")
    get_lineage_data_store()
    get_resident_conversation_stream()
    server = SocietyServiceServer(
        args.socket,
        housekeeping_seconds=args.housekeeping_seconds,
        enable_projections=True,
        read_workers=args.read_workers,
    )
    serve_until_stopped(server)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
