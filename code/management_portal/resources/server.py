from __future__ import annotations

import argparse
import json
import os
import socket
import socketserver
from management_common.service_shutdown import DrainingStreamServer, serve_until_stopped, stop_workers
from management_common.service_endpoint import LocalServiceSocketMixin, service_endpoint
import sys
import threading
import time
from management_common.rpc_auth import RpcAuthenticator
from pathlib import Path
from typing import Any

from management_portal.notifications import (
    get_notification_publisher,
)

from .config import resource_rpc_signing_key_file, resource_service_socket
from .errors import resource_error_payload
from .rpc_auth import (
    PERMISSION_RPC_METHODS,
    RPC_ACTOR_METHODS,
    load_rpc_signing_key,
    rpc_auth_required,
)
from .runtime import get_resource_service


RESOURCE_SERVICE_SCHEMA = "tfi.resource-service.v3"
INSTRUCTION_METHOD = "instruction.execute"
PERMISSION_COMPLETE_INTERVAL_SECONDS = 3600


def _report_merges(merged: dict[str, Any]) -> None:
    """Log each automatic merge; every conflict and every row not written back on a line of its own."""
    for scheme_id, report in merged.items():
        if "error" in report:
            print(f"Permission scheme {scheme_id}: merge failed, nothing written: {report['error']}"[:800], file=sys.stderr, flush=True)
            continue
        print(f"Permission scheme {scheme_id}: merged {report['source_scheme_id']} {report['from_digest']} -> {report['to_digest']},"
              f" version {report['version']}, instance {report['instance'] or 'unchanged'}", file=sys.stderr, flush=True)
        for conflict in report["conflicts"]:
            print(f"Permission scheme {scheme_id}: conflict kept this scheme's {conflict['key']}: "
                  + json.dumps({side: conflict[side] for side in ("was", "now", "own")}, ensure_ascii=False)[:1500],
                  file=sys.stderr, flush=True)
        for entry in report["unrestored"]:
            print(f"Permission scheme {scheme_id}: entry not written back, {'/'.join(entry['missing'])} node gone: "
                  + json.dumps(entry, ensure_ascii=False), file=sys.stderr, flush=True)


class _ResourceRequestHandler(socketserver.StreamRequestHandler):
    server: "ResourceServiceServer"

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
            actor = self.server.authenticate(method, params, request.get("auth"))
            if method == INSTRUCTION_METHOD:
                self._serve_instruction(params, request_id, actor if request.get("auth") else None)
                return
            from management_common.execution_association import execution_scope
            from management_common.resource_lifecycle import lifecycle_scope

            execution_id = params.pop("_execution_id", "")
            if execution_id and not request.get("auth"):
                raise PermissionError("Execution association requires authenticated RPC")
            lifecycle_id = params.pop("_resource_lifecycle_id", "")
            if lifecycle_id and not request.get("auth"):
                raise PermissionError("Resource lifecycle requires authenticated RPC")
            with execution_scope(execution_id), lifecycle_scope(lifecycle_id, actor=actor):
                result = self.server.dispatch(method, params, actor=actor)
            if (
                isinstance(result, tuple)
                and len(result) == 3
                and isinstance(result[0], bytes)
            ):
                data, content_type, resolved = result
                response = {
                    "id": request_id,
                    "ok": True,
                    "content": {
                        "length": len(data),
                        "content_type": str(content_type or "application/octet-stream"),
                        "resolved": resolved,
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
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    return
                return
            response = {"id": request_id, "ok": True, "result": result}
        except Exception as exc:
            response = {
                "id": request_id,
                "ok": False,
                "error": resource_error_payload(exc),
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


    def _serve_instruction(self, params: dict[str, Any], request_id: str, verified_caller: str | None) -> None:
        from management_portal.hatg.socket_rpc import serve

        def write_line(payload: dict[str, Any]) -> None:
            self.wfile.write(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
            )
            self.wfile.flush()

        try:
            serve(params, request_id, write_line, owner="resource", verified_caller=verified_caller)
        except (BrokenPipeError, ConnectionResetError):
            return


class ResourceServiceServer(LocalServiceSocketMixin, DrainingStreamServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = socket.SOMAXCONN

    def __init__(
        self,
        socket_path: Path | str,
        *,
        service: object | None = None,
        publisher: object | None = None,
    ) -> None:
        self.socket_path = Path(socket_path)
        self.endpoint = service_endpoint(self.socket_path)
        self.address_family = self.endpoint.family
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.endpoint.is_unix:
            self.socket_path.unlink(missing_ok=True)
        self.service = service or get_resource_service()
        self.publisher = publisher or get_notification_publisher()
        self.started = threading.Event()
        self._warm_lock = threading.Lock()
        self._warm_started = False
        self._warm_ready = False
        self._warm_error = ""
        self._background_stop = threading.Event()
        self._auto_registration_thread: threading.Thread | None = None
        self._rpc_auth_required = rpc_auth_required() or service is None
        self._rpc_signing_key = load_rpc_signing_key(
            required=self._rpc_auth_required,
            create=self._rpc_auth_required,
            path=resource_rpc_signing_key_file(socket_path=self.socket_path),
        )
        self.rpc_auth = (
            RpcAuthenticator(self.socket_path, key=self._rpc_signing_key)
            if self._rpc_signing_key is not None else None
        )
        self._status_cache: dict[str, Any] = {
            "schema": "tfi.portal.resource_index.v2",
            "resource_count": 0,
            "running_adapters": [],
            "pending_adapters": [],
            "adapters": {},
        }
        from management_portal.hatg.kernel import register_kernel
        from .isa import build_kernel

        from management_portal.hatg.maintenance import InstructionMaintenance

        kernel = build_kernel()
        register_kernel(kernel)
        self.instruction_maintenance = InstructionMaintenance(kernel)
        super().__init__(self.endpoint.address, _ResourceRequestHandler)
        if self.endpoint.is_unix:
            os.chmod(self.socket_path, 0o660)

    def serve_forever(self, poll_interval: float = 0.2) -> None:
        self.instruction_maintenance.start()
        self.started.set()
        self._start_warmup()
        self._start_background_maintenance()
        super().serve_forever(poll_interval)

    def _start_background_maintenance(self) -> None:
        if self._auto_registration_thread is not None:
            return
        from management_portal.resource_auto_registration import (
            start_auto_registration_worker,
        )

        self._auto_registration_thread = start_auto_registration_worker(
            service=self.service,
            stop_event=self._background_stop,
        )

    def _start_warmup(self) -> None:
        with self._warm_lock:
            if self._warm_started or self._warm_ready:
                return
            self._warm_started = True
            self._warm_error = ""

        def warm() -> None:
            try:
                status = dict(self.service.status() or {})
            except Exception as exc:
                with self._warm_lock:
                    self._warm_error = f"{exc.__class__.__name__}: {exc}"[:1000]
                    self._warm_started = False
                return
            with self._warm_lock:
                self._status_cache = status
                self._warm_ready = True
                self._warm_error = ""
            try:
                self.service.warm()
            except Exception as exc:
                print(f"ResourceService index warm-up failed: {exc.__class__.__name__}: {exc}"[:500], file=sys.stderr, flush=True)
            # The permission instance is generated from every object, the indexed ones included.
            try:
                generated = self.service.ensure_permission_instance()
            except Exception as exc:
                print(f"Permission instance generation failed: {exc.__class__.__name__}: {exc}"[:500], file=sys.stderr, flush=True)
            else:
                _report_merges(generated or {})
            # A release that changed built-in rules: their copies take the changes (§5.11), before the first 补齐.
            try:
                merged = self.service.merge_permission_sources()
            except Exception as exc:
                print(f"Permission scheme merge failed: {exc.__class__.__name__}: {exc}"[:800], file=sys.stderr, flush=True)
            else:
                _report_merges(merged)
            # 补齐 at startup and then every hour repairs any sync after commit that did not run (§5.7).
            self._complete_permissions()
            while not self._background_stop.wait(PERMISSION_COMPLETE_INTERVAL_SECONDS):
                self._complete_permissions()

        threading.Thread(
            target=warm,
            name="resource-service-warmup",
            daemon=True,
        ).start()

    def _complete_permissions(self) -> None:
        started = time.monotonic()
        try:
            report = self.service.complete_permission_instance()
        except Exception as exc:
            print(f"Permission instance completion failed: {exc.__class__.__name__}: {exc}"[:800], file=sys.stderr, flush=True)
            return
        if report and any(report.get(name) for name in ("added_nodes", "moved_nodes", "removed_nodes",
                                                        "added_entries", "removed_entries")):
            print(f"Permission instance completed in {time.monotonic() - started:.1f}s: {report}", file=sys.stderr, flush=True)

    def _status(self) -> dict[str, Any]:
        self._start_warmup()
        with self._warm_lock:
            ready = self._warm_ready
            status = dict(self._status_cache)
            error = self._warm_error
        if ready:
            try:
                status = dict(self.service.status() or {})
            except Exception as exc:
                error = f"{exc.__class__.__name__}: {exc}"[:1000]
        return {
            **status,
            "ready": ready,
            "warming": not ready and not error,
            "warm_error": error,
        }

    def server_close(self) -> None:
        self.begin_shutdown()
        self._background_stop.set()
        callbacks = {"instruction-maintenance": self.instruction_maintenance.stop}
        thread = self._auto_registration_thread
        if thread is not None:
            callbacks["resource-registration"] = lambda: thread.join(timeout=2.0)
        stop_workers(callbacks)
        super().server_close()
        if self.endpoint.is_unix:
            self.socket_path.unlink(missing_ok=True)

    @staticmethod
    def _required(params: dict[str, Any], key: str) -> str:
        value = str(params.get(key) or "").strip()
        if not value:
            raise ValueError(f"{key} is required")
        return value

    def authenticate(
        self,
        method: str,
        params: dict[str, Any],
        raw_auth: object,
    ) -> str:
        if raw_auth is None:
            from .rpc_auth import DOMAIN_PROJECTION_METHODS
            if self._rpc_auth_required or method in PERMISSION_RPC_METHODS or method in DOMAIN_PROJECTION_METHODS:
                raise PermissionError("authenticated RPC actor required")
            return str(params.pop("actor", "") or "").strip()
        if self._rpc_signing_key is None:
            raise PermissionError("authenticated RPC actor required")
        if method in RPC_ACTOR_METHODS and "actor" in params:
            raise PermissionError(
                "RPC actor must come from authenticated RPC actor assertion"
            )
        return self.rpc_auth.authenticate(method, params, raw_auth)

    def dispatch(
        self,
        method: str,
        params: dict[str, Any],
        *,
        actor: str = "",
    ) -> Any:
        if method in RPC_ACTOR_METHODS and "actor" in params:
            raise ValueError("RPC actor must be supplied by the transport")
        from .rpc_auth import DOMAIN_PROJECTION_METHODS
        if method in DOMAIN_PROJECTION_METHODS and actor != "society-service":
            raise PermissionError("Domain projection requires authenticated SocietyService")
        if method == "permission.prepare":
            # Asked while the permission system is still off, just before it is turned on.
            if params:
                raise ValueError("Preparing the permission instance takes no parameters")
            return self.service.prepare_permissions(actor=actor)
        if method == "resource.status":
            return {
                **self._status(),
                "build_id": str(os.getenv("PORTAL_BUILD_ID", "") or ""),
                "service": {
                    "schema": RESOURCE_SERVICE_SCHEMA,
                    "instance": str(os.getenv("PORTAL_INSTANCE", "") or ""),
                    "notifications": self.publisher.health(),
                },
            }
        if method == "resource.query":
            return self.service.query(actor=actor, **params)
        if method == "resource.suggest":
            return self.service.suggest(actor=actor, **params)
        if method == "resource.execution_content_changes":
            return self.service.execution_content_changes(self._required(params, "execution_id"), actor=actor)
        if method == "resource.get":
            return self.service.get(
                self._required(params, "resource_id"),
                actor=actor,
            )
        if method == "resource.collaboration.sync":
            if set(params) - {"descriptor"}:
                raise ValueError("Unexpected domain projection parameters")
            descriptor = params.get("descriptor")
            if not isinstance(descriptor, dict):
                raise ValueError("descriptor must be an object")
            result = self.service.sync_collaboration_resource(descriptor, domain_identity=actor)
            return result
        if method == "resource.collaboration.sync-many":
            if set(params) - {"resources"}:
                raise ValueError("Unexpected domain projection parameters")
            resources = params.get("resources")
            if not isinstance(resources, list):
                raise ValueError("resources must be an array")
            return self.service.sync_collaboration_resources(resources, domain_identity=actor)
        if method == "resource.collaboration.workspace_id_for_path":
            return self.service.workspace_id_for_path(
                self._required(params, "path"),
                preferred_id=str(params.get("preferred_id") or ""),
            )
        if method == "resource.resolve":
            return self.service.resolve(
                self._required(params, "address"),
                actor=actor,
            )
        if method == "resource.reference":
            return self.service.reference(
                self._required(params, "address"),
                actor=actor,
            )
        if method == "resource.read_content":
            return self.service.read_content(
                self._required(params, "address"),
                actor=actor,
                max_bytes=min(
                    512 * 1024 * 1024,
                    max(1, int(params.get("max_bytes") or 4 * 1024 * 1024)),
                ),
            )
        if method == "resource.open_content":
            return self.service.open_content(
                self._required(params, "address"),
                actor=actor,
                max_bytes=min(
                    512 * 1024 * 1024,
                    max(1, int(params.get("max_bytes") or 4 * 1024 * 1024)),
                ),
            )
        if method == "resource.read_content_range":
            return self.service.read_content_range(
                self._required(params, "address"),
                actor=actor,
                start=params.get("start"),
                end=params.get("end"),
            )
        if method == "resource.capabilities":
            return self.service.capabilities(
                self._required(params, "resource_type")
            )
        if method == "resource.refresh":
            return self.service.refresh(**params)
        if method == "resource.projection_state":
            return self.service.projection_state(
                self._required(params, "adapter")
            )
        if method == "resource.notify_source_changed":
            return self.service.notify_source_changed(**params)
        if method in {
            "resource.register",
            "resource.update",
            "resource.unregister",
            "resource.action",
        }:
            return self._dispatch_mutation(method, params, actor=actor)
        raise ValueError(f"unsupported ResourceService method: {method}")

    def _dispatch_mutation(
        self,
        method: str,
        params: dict[str, Any],
        *,
        actor: str = "",
    ) -> dict[str, Any]:
        resource_type = str(params.get("resource_type") or "").strip()
        kwargs = {
            key: value
            for key, value in params.items()
            if key
            not in {"resource_id", "resource_type", "payload", "action", "actor"}
        }
        kwargs["actor"] = actor
        if method == "resource.register":
            result = self.service.register(
                self._required(params, "resource_type"),
                dict(params.get("payload") or {}),
                **kwargs,
            )
        elif method == "resource.update":
            result = self.service.update(
                self._required(params, "resource_id"),
                dict(params.get("payload") or {}),
                resource_type=resource_type,
                **kwargs,
            )
        elif method == "resource.unregister":
            result = self.service.unregister(
                self._required(params, "resource_id"),
                resource_type=resource_type,
                **kwargs,
            )
        else:
            result = self.service.action(
                self._required(params, "resource_id"),
                self._required(params, "action"),
                dict(params.get("payload") or {}),
                resource_type=resource_type,
                **kwargs,
            )
        return dict(result or {})


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Run TFI Portal ResourceService")
    root.add_argument("--socket", type=Path, default=resource_service_socket())
    return root


def main() -> int:
    args = parser().parse_args()
    server = ResourceServiceServer(args.socket)
    serve_until_stopped(server)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
