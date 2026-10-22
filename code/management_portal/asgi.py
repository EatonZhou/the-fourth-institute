from __future__ import annotations

import asyncio
import re
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import threading
from contextlib import asynccontextmanager, suppress
from email.message import Message
from functools import partial, wraps
from pathlib import Path
from types import SimpleNamespace
from typing import AsyncIterator, Awaitable, Callable

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse
from starlette.routing import Route

from . import serve_management_portal as portal
from . import portal_features, resident_sidebar_backend
from .http_errors import exception_http_error, normalize_json_error
from .knowledge_archive_projection import (
    ensure_knowledge_archive_projection_watcher,
)
from .notifications import (
    AsyncNotificationFanout,
    RealtimeConnectionLifecycle,
    close_notifications,
    ensure_notification_listener,
    get_notification_hub,
)


MAX_REQUEST_BODY_BYTES = 520 * 1024 * 1024
SPOOL_MEMORY_BYTES = 2 * 1024 * 1024
SSE_HEARTBEAT_SECONDS = 10.0
SSE_PAYLOAD_TIMEOUT_SECONDS = 24.0
SSE_PAYLOAD_WORKERS = 4
SSE_SUBSCRIBER_QUEUE_SIZE = 64
REALTIME_DRAIN_TIMEOUT_SECONDS = 6.0
HTTP_HANDLER_WORKERS = max(
    16,
    int(os.getenv("PORTAL_HTTP_HANDLER_WORKERS", "32")),
)
INSTRUCTION_HANDLER_WORKERS = 16
STATIC_HANDLER_WORKERS = 4


def _json_error(status: int, message: str, **fields: object) -> JSONResponse:
    return JSONResponse(normalize_json_error(status, {"error": message, **fields}), status_code=status)


class _ResponseCapture:
    def __init__(self) -> None:
        self.status = 500
        self.headers: list[tuple[bytes, bytes]] = []
        self.body = tempfile.SpooledTemporaryFile(max_size=SPOOL_MEMORY_BYTES)
        self._header = bytearray()
        self._headers_complete = False

    def write(self, data: bytes) -> int:
        payload = bytes(data)
        if self._headers_complete:
            return self.body.write(payload)
        self._header.extend(payload)
        boundary = self._header.find(b"\r\n\r\n")
        if boundary < 0:
            if len(self._header) > 64 * 1024:
                raise RuntimeError("Portal response headers exceed 64 KiB")
            return len(payload)
        raw_headers = bytes(self._header[:boundary])
        body = bytes(self._header[boundary + 4 :])
        self._header.clear()
        lines = raw_headers.split(b"\r\n")
        try:
            self.status = int(lines[0].split(b" ", 2)[1])
        except (IndexError, ValueError) as exc:
            raise RuntimeError("Portal returned an invalid HTTP status line") from exc
        for line in lines[1:]:
            name, separator, value = line.partition(b":")
            if not separator:
                continue
            if name.lower() in {b"connection", b"transfer-encoding"}:
                continue
            self.headers.append((name.strip().lower(), value.strip()))
        self._headers_complete = True
        if body:
            self.body.write(body)
        return len(payload)

    def flush(self) -> None:
        return None

    def finish(self) -> None:
        if not self._headers_complete:
            raise RuntimeError("Portal handler completed without an HTTP response")
        self.body.seek(0)


async def _body_chunks(stream) -> AsyncIterator[bytes]:
    try:
        while True:
            # A completed response must not queue behind blocking domain RPCs
            # in asyncio's handler executor. Starlette's response I/O workers
            # have an independent, bounded capacity and retain backpressure.
            chunk = await run_in_threadpool(stream.read, 256 * 1024)
            if not chunk:
                return
            yield chunk
    finally:
        stream.close()


def _request_headers(request: Request) -> Message:
    headers = Message()
    for name, value in request.headers.raw:
        headers.add_header(
            name.decode("latin-1"),
            value.decode("latin-1"),
        )
    return headers


def _run_sync_handler(
    request: Request,
    body,
) -> _ResponseCapture:
    capture = _ResponseCapture()
    handler = portal.Handler.__new__(portal.Handler)
    handler.command = request.method.upper()
    handler.path = request.url.path + (
        f"?{request.url.query}" if request.url.query else ""
    )
    handler.request_version = "HTTP/1.1"
    handler.requestline = f"{handler.command} {handler.path} HTTP/1.1"
    handler.close_connection = True
    handler.directory = str(portal.ROOT)
    handler.headers = _request_headers(request)
    handler.rfile = body
    handler.wfile = capture
    client = request.client or SimpleNamespace(host="127.0.0.1", port=0)
    handler.client_address = (str(client.host), int(client.port or 0))
    handler._asgi_request_scheme = request.url.scheme
    server_host, server_port = request.scope.get("server") or ("127.0.0.1", 0)
    handler.server = SimpleNamespace(server_address=(server_host, int(server_port or 0)))
    method = getattr(handler, f"do_{handler.command}", None)
    if method is None:
        if portal._is_json_api_path(handler.path):
            portal._serve_http_error(handler, 405, "Method Not Allowed")
        else:
            handler.send_error(405, "Method Not Allowed")
    else:
        method()
    capture.finish()
    return capture


async def portal_http(request: Request) -> StreamingResponse:
    body = tempfile.SpooledTemporaryFile(max_size=SPOOL_MEMORY_BYTES)
    total = 0
    try:
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_REQUEST_BODY_BYTES:
                body.close()
                return _json_error(413, f"request body exceeds {MAX_REQUEST_BODY_BYTES // (1024 * 1024)} MiB")
            body.write(chunk)
        body.seek(0)
        app = request.scope.get("app")
        handler_executor = getattr(
            getattr(app, "state", None),
            "http_handler_executor",
            None,
        )
        if request.method in {"GET", "HEAD"} and request.url.path.startswith("/static/"):
            # Slow domain RPCs must not starve the scripts/styles needed to show
            # their error or recovery UI. Keep the existing handler and its
            # routing/authorization rules; only isolate the bounded I/O capacity.
            handler_executor = getattr(
                getattr(app, "state", None), "static_handler_executor", None,
            ) or handler_executor
        if handler_executor is None:
            capture = await asyncio.to_thread(_run_sync_handler, request, body)
        else:
            capture = await asyncio.get_running_loop().run_in_executor(
                handler_executor,
                _run_sync_handler,
                request,
                body,
            )
    except Exception as exc:
        if portal._is_json_api_path(request.url.path):
            status, payload = exception_http_error(exc)
            return JSONResponse(payload, status_code=status)
        raise
    finally:
        if not body.closed:
            body.close()
    response = StreamingResponse(
        _body_chunks(capture.body),
        status_code=capture.status,
    )
    response.raw_headers = capture.headers
    return response


def _requested_domains(request: Request) -> set[str]:
    supported = {"execution", "resource", "resident", "lineage", "projection"}
    requested = {
        item.strip().lower()
        for value in request.query_params.getlist("domains")
        for item in value.split(",")
        if item.strip()
    }
    return requested & supported if requested else supported


def _sse(event: str, payload: dict, *, event_id: str = "") -> bytes:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    identity = f"id: {event_id}\n" if event_id else ""
    return f"{identity}event: {event}\ndata: {encoded}\n\n".encode("utf-8")


def _event_subscription_predicate(request: Request, domains: set[str]):
    resident_ids = {
        resident_id.strip()
        for value in request.query_params.getlist("resident_ids")
        for resident_id in value.split(",")
        if resident_id.strip()
    }

    def accepts(notification) -> bool:
        if notification.subject == "portal.gateway.reconnected":
            return True
        domain, target_id = portal._notification_target(notification.subject)
        if domain not in domains:
            return False
        if domain != "resident" or not notification.subject.endswith(".conversation"):
            return True
        if target_id in resident_ids:
            return True
        event = notification.payload.get("event")
        return (
            notification.payload.get("kind") == "conversation_event"
            and isinstance(event, dict)
            and event.get("event_type") == "resident.state.changed"
        )

    return accepts


async def portal_events(request: Request):
    actor = portal._get_session_username(
        SimpleNamespace(headers=_request_headers(request))
    ) or ""
    if not actor:
        return RedirectResponse("/login", status_code=302)
    domains = _requested_domains(request)
    ensure_notification_listener()
    ensure_knowledge_archive_projection_watcher()
    fanout = getattr(request.app.state, "notification_fanout", None)
    payload_executor = getattr(request.app.state, "sse_payload_executor", None)
    realtime_lifecycle = getattr(
        request.app.state, "realtime_lifecycle", None
    )
    if (
        not isinstance(realtime_lifecycle, RealtimeConnectionLifecycle)
        or not realtime_lifecycle.accepting
        or not isinstance(fanout, AsyncNotificationFanout)
        or payload_executor is None
    ):
        return _json_error(503, "Portal realtime notifications are unavailable")

    async def events() -> AsyncIterator[bytes]:
        connection_id = realtime_lifecycle.open_connection()
        if connection_id is None:
            return
        subscription = fanout.subscribe(_event_subscription_predicate(request, domains))
        drain_wait = asyncio.create_task(realtime_lifecycle.drain_event.wait())
        try:
            yield b"retry: 1500\n\n"
            yield _sse("portal-ready", {"snapshot_required": True})
            while True:
                batch = await subscription.wait(SSE_HEARTBEAT_SECONDS)
                if batch is None:
                    yield _sse("heartbeat", {"at": portal.time.time()})
                    continue
                if batch.closed:
                    return
                if batch.snapshot_required:
                    yield _sse(
                        "portal-change",
                        {
                            "domains": [],
                            "changed": {},
                            "snapshot_required": True,
                        },
                    )
                    continue
                loop = asyncio.get_running_loop()
                payload_future = loop.run_in_executor(
                    payload_executor,
                    partial(
                        portal._portal_change_payload,
                        actor,
                        list(batch.notifications),
                        domains,
                    ),
                )
                try:
                    done, _pending = await asyncio.wait(
                        {payload_future, drain_wait},
                        timeout=SSE_PAYLOAD_TIMEOUT_SECONDS,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if drain_wait in done or realtime_lifecycle.drain_event.is_set():
                        payload_future.cancel()
                        return
                    if payload_future not in done:
                        payload_future.cancel()
                        yield _sse(
                            "portal-change",
                            {
                                "domains": [],
                                "changed": {},
                                "snapshot_required": True,
                            },
                        )
                        continue
                except asyncio.CancelledError:
                    payload_future.cancel()
                    raise
                payload = await payload_future
                if (
                    realtime_lifecycle.drain_event.is_set()
                    or not realtime_lifecycle.accepting
                ):
                    return
                if payload["domains"] or payload["snapshot_required"]:
                    yield _sse("portal-change", payload)
        finally:
            drain_wait.cancel()
            with suppress(asyncio.CancelledError):
                await drain_wait
            subscription.close()
            realtime_lifecycle.close_connection(connection_id)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
        },
    )


def _instruction_principal(request: Request) -> tuple[str, str]:
    """(caller, entry) of an ``/api/instructions/execute`` request.

    Pages authenticate with the login session; the command-line client with
    the bearer token Portal issued to the Resident.
    """

    handler = SimpleNamespace(headers=_request_headers(request))
    identity = portal._get_agent_api_auth(handler, scope="instructions")
    if identity is None:
        return "", ""
    return str(identity["id"]), "web" if identity["type"] == "human" else "cli"



def _execution_id(request: Request, entry: str) -> str:
    """The Resident execution a command-line call runs in, so the resource
    content it changes is recorded with that execution."""

    from management_portal.runtime_identity_headers import runtime_identity_header
    return runtime_identity_header(request.headers, "X-Portal-Execution-ID") if entry == "cli" else ""


def _same_origin(request: Request) -> bool:
    handler = SimpleNamespace(headers=_request_headers(request), _asgi_request_scheme=request.url.scheme)
    return bool(portal._same_origin_request(handler))


async def instruction_execute(request: Request):
    """The one instruction address (decision_898b8911a2ff “调用入口”)."""

    from .hatg import routing
    from .hatg.contracts import ERROR_STATUS, InstructionCall, InstructionContext, InstructionError, failed_result
    from .hatg.kernel import new_instruction_id

    caller, entry = await asyncio.to_thread(_instruction_principal, request)
    if not caller:
        # An agent that arrives without credentials learns where the joining and calling rules are.
        return _json_error(401, "需要登录，或使用有效的 User / Resident 凭证",
                           details={"guide": "/guide/agent.md"})
    if entry == "web" and not _same_origin(request):
        return _json_error(403, "跨站请求被拒绝")
    payload = None
    try:
        payload = await request.json()
        call = InstructionCall.from_payload(payload)
        from management_common.principal import actor_principal
        execution_id = _execution_id(request, entry) if actor_principal(caller).kind == "resident" else ""
        context = InstructionContext(caller=caller, entry=entry, execution_id=execution_id)
        declaration = routing.declaration_for(call.instruction)
    except InstructionError as exc:
        name = str(payload.get("instruction") or "") if isinstance(payload, dict) else ""
        return JSONResponse(failed_result(new_instruction_id("entry"), name, exc).to_payload(),
                            status_code=ERROR_STATUS.get(exc.code, 400))
    except Exception as exc:  # noqa: BLE001 - malformed JSON or call
        name = str(payload.get("instruction") or "") if isinstance(payload, dict) else ""
        error = InstructionError("instruction.invalid_arguments", str(exc) or "调用格式无效")
        return JSONResponse(failed_result(new_instruction_id("entry"), name, error).to_payload(), status_code=400)
    if not declaration.streaming:
        executor = getattr(request.app.state, "instruction_executor", None)
        loop = asyncio.get_running_loop()

        def execute():
            return routing.execute(call, context)

        result = await loop.run_in_executor(executor, execute)
        body = result.to_payload()
        status = 200 if result.status == "committed" else ERROR_STATUS.get(str((result.error or {}).get("code")), 500)
        return JSONResponse(body, status_code=status)

    async def events() -> AsyncIterator[bytes]:
        from .hatg.socket_rpc import StreamCancellation

        queue: asyncio.Queue = asyncio.Queue(maxsize=16)
        loop = asyncio.get_running_loop()
        stop = threading.Event()
        cancellation = StreamCancellation()

        def publish(item) -> bool:
            if stop.is_set():
                return False
            pending = asyncio.run_coroutine_threadsafe(queue.put(item), loop)
            while not stop.is_set():
                try:
                    pending.result(timeout=0.1)
                    return True
                except FutureTimeoutError:
                    continue
            pending.cancel()
            return False

        def pump() -> None:
            try:
                for batch in routing.stream(call, context, cancellation=cancellation):
                    if not publish(("batch", batch)):
                        return
                publish(("end", None))
            except Exception as exc:  # noqa: BLE001
                if not stop.is_set():
                    from .hatg.contracts import normalize_error

                    publish(("error", normalize_error(exc).to_dict()))

        worker = loop.run_in_executor(getattr(request.app.state, "sse_payload_executor", None), pump)
        try:
            yield b"retry: 3000\n\n"
            while True:
                try:
                    kind, value = await asyncio.wait_for(queue.get(), timeout=SSE_HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield _sse("heartbeat", {"at": portal.time.time()})
                    continue
                if kind == "batch":
                    cursor = value[-1].get("cursor") if value and isinstance(value[-1], dict) else None
                    yield _sse("batch", {"items": value}, event_id=str(cursor or ""))
                elif kind == "error":
                    yield _sse("error", value)
                    return
                else:
                    yield _sse("end", {})
                    return
        finally:
            stop.set()
            cancellation.cancel()
            worker.cancel()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"},
    )


async def upload_bytes(request: Request):
    """Data channel of ``CreateUpload``: the creator writes the declared bytes once."""

    from .resources.isa import MAX_UPLOAD_BYTES, complete_upload

    caller, entry = await asyncio.to_thread(_instruction_principal, request)
    if not caller:
        return _json_error(401, "需要登录")
    if entry == "web" and not _same_origin(request):
        return _json_error(403, "跨站请求被拒绝")
    declared = str(request.headers.get("content-length") or "")
    if declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        return _json_error(413, "上传超过大小上限")  # refused before reading the body
    chunks = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            return _json_error(413, "上传超过大小上限")
        chunks.append(chunk)
    try:
        result = await asyncio.to_thread(
            complete_upload, request.path_params["upload_id"], b"".join(chunks), actor=caller,
        )
    except Exception as exc:  # noqa: BLE001
        status, payload = exception_http_error(exc)
        return JSONResponse(payload, status_code=status)
    return JSONResponse(result)


def _upload_readable(meta: dict, caller: str) -> bool:
    """The HTTP entry already authenticates the caller."""
    return bool(caller)


async def upload_content(request: Request):
    """Read back an upload's bytes (attachment previews)."""

    from .resources.isa import read_upload

    caller, _entry = await asyncio.to_thread(_instruction_principal, request)
    if not caller:
        return _json_error(401, "需要登录")
    try:
        meta = await asyncio.to_thread(read_upload, request.path_params["upload_id"])
    except Exception as exc:  # noqa: BLE001
        status, payload = exception_http_error(exc)
        return JSONResponse(payload, status_code=status)
    if not meta.get("ready") or not await asyncio.to_thread(_upload_readable, meta, caller):
        return _json_error(404, "上传不存在")
    try:
        data = await asyncio.to_thread(Path(str(meta.get("path") or "")).read_bytes)
    except OSError:
        return _json_error(404, "上传不存在")
    return Response(
        data,
        media_type=str(meta.get("media_type") or "application/octet-stream"),
        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"},
    )


async def resource_download(request: Request):
    """Download address of an oversized ``ReadResource`` result: its caller only, until it expires.

    A single ``Range: bytes=a-b`` is served as a segment; every read passes the
    resource's permission check again.
    """

    from .resources.isa import iter_content, read_download

    caller, _entry = await asyncio.to_thread(_instruction_principal, request)
    if not caller:
        return _json_error(401, "需要登录，或使用 Portal 发给 Resident 的凭证")
    try:
        record = await asyncio.to_thread(read_download, request.path_params["token"], caller)
    except Exception as exc:  # noqa: BLE001
        status, payload = exception_http_error(exc)
        return JSONResponse(payload, status_code=status)
    size = int(record["size"])
    start, end, status = 0, size - 1, 200
    ranged = re.fullmatch(r"bytes=(\d*)-(\d*)", str(request.headers.get("range") or "").strip())
    if ranged and (ranged.group(1) or ranged.group(2)):
        if ranged.group(1):
            start = int(ranged.group(1))
            end = min(int(ranged.group(2)), size - 1) if ranged.group(2) else size - 1
        else:
            start = max(0, size - int(ranged.group(2)))
        if start > end or start >= size:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        status = 206
    chunks = iter_content(record["address"], caller, size, start, end)

    async def body() -> AsyncIterator[bytes]:
        while True:
            chunk = await asyncio.to_thread(next, chunks, None)
            if chunk is None:
                return
            yield chunk

    headers = {
        "Accept-Ranges": "bytes", "Content-Length": str(end - start + 1),
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        "ETag": f'"{record["version"]}"',
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(body(), status_code=status, media_type=str(record.get("content_type") or "application/octet-stream"),
                             headers=headers)


def _is_direct_local_request(request: Request) -> bool:
    client_host = str(request.client.host if request.client else "")
    return (
        client_host in {"127.0.0.1", "::1"}
        and "x-forwarded-for" not in request.headers
    )


async def drain_realtime(request: Request):
    if not _is_direct_local_request(request):
        return _json_error(403, "local request required")
    realtime_lifecycle = getattr(
        request.app.state, "realtime_lifecycle", None
    )
    if not isinstance(realtime_lifecycle, RealtimeConnectionLifecycle):
        return _json_error(503, "Portal realtime lifecycle is unavailable")
    started_connections = realtime_lifecycle.begin_drain()
    fanout = getattr(request.app.state, "notification_fanout", None)
    if isinstance(fanout, AsyncNotificationFanout):
        fanout.close()
    result = await realtime_lifecycle.wait_until_drained(
        REALTIME_DRAIN_TIMEOUT_SECONDS
    )
    payload = {
        "ok": result.drained,
        "drained": result.drained,
        "started_realtime_connections": started_connections,
        "closed_realtime_connections": (
            started_connections - result.remaining_connections
        ),
        "remaining_realtime_connections": result.remaining_connections,
        "elapsed_seconds": result.elapsed_seconds,
    }
    if not result.drained:
        return _json_error(503, "Portal realtime drain timed out", **payload)
    return JSONResponse(payload)


async def publish_restart_notice(request: Request):
    if not _is_direct_local_request(request):
        return _json_error(403, "local request required")
    fanout = getattr(request.app.state, "notification_fanout", None)
    if (
        not isinstance(
            getattr(request.app.state, "realtime_lifecycle", None),
            RealtimeConnectionLifecycle,
        )
        or not request.app.state.realtime_lifecycle.accepting
        or not isinstance(fanout, AsyncNotificationFanout)
    ):
        return _json_error(503, "Portal realtime notifications are unavailable")
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("JSON object required")
        notice = await asyncio.to_thread(
            portal._publish_restart_notice,
            payload,
            scheduled_by="local-restart",
            restart_requested=False,
        )
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        return _json_error(400, str(exc))
    return JSONResponse({
        "ok": True,
        "projection_published": True,
        "subscriber_count": fanout.subscriber_count,
        "notice": notice,
    })


@asynccontextmanager
async def lifespan(_app: Starlette):
    # Features run their background work while Web runs (``web.lifespan``).
    feature_stops = [stop for _feature, start in portal_features.contributions("web.lifespan") if (stop := start())]
    notification_fanout = AsyncNotificationFanout(
        get_notification_hub(),
        subscriber_queue_size=SSE_SUBSCRIBER_QUEUE_SIZE,
    )
    notification_fanout.start()
    ensure_notification_listener()

    def invalidate_sidebar_projection(notification) -> None:
        subject = str(getattr(notification, "subject", "") or "")
        if subject.endswith(".conversation"):
            portal._invalidate_agent_conversation_cache()
            return
        if subject.startswith("portal.execution."):
            return
        resident_sidebar_backend.invalidate_snapshot_cache()

    unsubscribe_sidebar_cache = get_notification_hub().subscribe(
        invalidate_sidebar_projection
    )
    http_handler_executor = ThreadPoolExecutor(
        max_workers=HTTP_HANDLER_WORKERS,
        thread_name_prefix="portal-http",
    )
    instruction_executor = ThreadPoolExecutor(
        max_workers=INSTRUCTION_HANDLER_WORKERS,
        thread_name_prefix="portal-instruction",
    )
    static_handler_executor = ThreadPoolExecutor(
        max_workers=STATIC_HANDLER_WORKERS,
        thread_name_prefix="portal-static",
    )
    sse_payload_executor = ThreadPoolExecutor(
        max_workers=SSE_PAYLOAD_WORKERS,
        thread_name_prefix="portal-sse-payload",
    )
    _app.state.notification_fanout = notification_fanout
    _app.state.http_handler_executor = http_handler_executor
    _app.state.static_handler_executor = static_handler_executor
    _app.state.instruction_executor = instruction_executor
    _app.state.sse_payload_executor = sse_payload_executor
    _app.state.realtime_lifecycle = RealtimeConnectionLifecycle()
    from .web_instruction_server import start_web_instruction_server

    instruction_server = await asyncio.to_thread(start_web_instruction_server)
    try:
        yield
    finally:
        _app.state.realtime_lifecycle.begin_drain()
        if instruction_server is not None:
            await asyncio.to_thread(instruction_server.stop)
        unsubscribe_sidebar_cache()
        resident_sidebar_backend.invalidate_snapshot_cache()
        for stop in reversed(feature_stops):
            await asyncio.to_thread(stop)
        notification_fanout.close()
        http_handler_executor.shutdown(wait=False, cancel_futures=True)
        static_handler_executor.shutdown(wait=False, cancel_futures=True)
        instruction_executor.shutdown(wait=False, cancel_futures=True)
        sse_payload_executor.shutdown(wait=False, cancel_futures=True)
        await asyncio.to_thread(close_notifications)


async def _route_http_error(request: Request, exc: HTTPException):
    if exc.status_code in {204, 304}:
        return Response(status_code=exc.status_code, headers=exc.headers)
    if portal._is_json_api_path(request.url.path):
        response = _json_error(exc.status_code, str(exc.detail))
        if exc.headers:
            response.headers.update(exc.headers)
        return response
    return PlainTextResponse(exc.detail, status_code=exc.status_code, headers=exc.headers)


def _independent_api_error_boundary(
    endpoint: Callable[[Request], Awaitable[Response]],
) -> Callable[[Request], Awaitable[Response]]:
    @wraps(endpoint)
    async def guarded(request: Request) -> Response:
        try:
            response = await endpoint(request)
        except HTTPException:
            raise
        except Exception as exc:
            status, payload = exception_http_error(exc)
            response = JSONResponse(payload, status_code=status)
        # Instruction results and API errors are current facts and are never
        # cached; an endpoint that chose its own policy (upload content,
        # downloads, event streams) keeps it (decision_f25266e5a0e7 “HTTP 缓存”).
        response.headers.setdefault("cache-control", "no-store")
        return response

    return guarded


app = Starlette(
    lifespan=lifespan,
    exception_handlers={HTTPException: _route_http_error},
    routes=[
        Route("/api/events", _independent_api_error_boundary(portal_events), methods=["GET"]),
        Route("/api/instructions/execute", _independent_api_error_boundary(instruction_execute), methods=["POST"]),
        Route("/api/uploads/{upload_id}", _independent_api_error_boundary(upload_bytes), methods=["PUT", "POST"]),
        Route("/api/uploads/{upload_id}", _independent_api_error_boundary(upload_content), methods=["GET"]),
        Route("/api/resources/download/{token}", _independent_api_error_boundary(resource_download), methods=["GET"]),
        Route(
            "/api/internal/restart-notice",
            _independent_api_error_boundary(publish_restart_notice),
            methods=["POST"],
        ),
        Route(
            "/api/internal/drain-realtime",
            _independent_api_error_boundary(drain_realtime),
            methods=["POST"],
        ),
        Route(
            "/{path:path}",
            portal_http,
            methods=["GET", "HEAD", "POST", "OPTIONS"],
        ),
    ]
)


from .localization import PortalLocaleMiddleware
app.add_middleware(PortalLocaleMiddleware)
