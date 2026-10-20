"""The ``instruction.execute`` Unix socket method (decision_96a887d83e8a).

Every owning service answers ``instruction.execute`` on its own socket.  A
request carries one complete call, the entry kind and the caller identity the
entry has already established.  A non-streaming call gets one response line;
a streaming call gets one line per batch and a final ``end`` line, and the
subscription stops when the caller closes the connection.
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import threading
from pathlib import Path
from typing import Any, Callable, Iterator

from management_common.instance_paths import instance_root
from management_common.service_socket import connect_service

from .contracts import InstructionCall, InstructionContext, InstructionError, InstructionResult


METHOD = "instruction.execute"
DEFAULT_TIMEOUT_SECONDS = 2700.0
STREAM_IDLE_TIMEOUT_SECONDS = 360.0


class StreamCancellation:
    """Interrupt a remote stream's blocking read when its consumer disconnects."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancelled = False
        self._connection: socket.socket | None = None

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def bind(self, connection: socket.socket) -> None:
        with self._lock:
            self._connection = connection
            cancelled = self._cancelled
        if cancelled:
            self._shutdown(connection)

    def release(self, connection: socket.socket) -> None:
        with self._lock:
            if self._connection is connection:
                self._connection = None

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            connection = self._connection
        if connection is not None:
            self._shutdown(connection)

    @staticmethod
    def _shutdown(connection: socket.socket) -> None:
        try:
            connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def _instance() -> str:
    return str(os.getenv("PORTAL_INSTANCE") or "dev").strip() or "dev"


def web_service_socket() -> Path:
    configured = str(os.getenv("PORTAL_WEB_SERVICE_SOCKET") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return instance_root() / _instance() / "web-service.sock"


def owner_socket(owner: str) -> Path:
    if owner == "lineage":
        from management_portal.society_service.config import society_service_socket

        return society_service_socket()
    if owner == "resource":
        from management_portal.resources.config import resource_service_socket

        return resource_service_socket()
    if owner == "execution":
        from management_portal.execution.config import execution_service_socket

        return execution_service_socket()
    if owner == "web":
        return web_service_socket()
    raise ValueError(f"unknown instruction owner: {owner}")


def request_params(call: InstructionCall, context: InstructionContext) -> dict[str, Any]:
    """The call and its entry context; the owner checks the caller's own permissions."""

    return {"call": call.to_payload(), **_context_params(context)}


def _context_params(context: InstructionContext) -> dict[str, Any]:
    return {"context": context.to_payload()}


def _signed(owner: str, params: dict[str, Any], context: InstructionContext) -> dict[str, Any]:
    """Every owning service receives the authenticated entry's caller assertion."""
    from management_common.rpc_auth import request_auth

    return {"auth": request_auth(METHOD, params, socket_path=owner_socket(owner), actor=context.caller)}


def _open(owner: str, payload: dict[str, Any], timeout: float) -> tuple[socket.socket, Any]:
    connection: socket.socket | None = None
    try:
        # An owner that is restarting gets waited for, as a request to it has
        # not been sent yet.
        connection, _path = connect_service(owner_socket(owner), timeout=timeout)
        connection.sendall(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        )
    except OSError as exc:
        if connection is not None:
            connection.close()
        raise InstructionError(
            "instruction.owner_unavailable",
            f"拥有该指令的服务暂时不可用（{owner}）",
            details={"owner": owner},
            retryable=True,
            commit_state="not_committed",
        ) from exc
    return connection, connection.makefile("rb")


def _unavailable(owner: str, message: str) -> InstructionError:
    return InstructionError(
        "instruction.owner_unavailable",
        message,
        details={"owner": owner},
        retryable=True,
        commit_state="unknown",
    )


def _decode(owner: str, raw: bytes, request_id: str) -> dict[str, Any]:
    if not raw:
        raise _unavailable(owner, f"{owner} 关闭了连接，没有返回结果")
    try:
        response = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _unavailable(owner, f"{owner} 返回了无法解析的结果") from exc
    if not isinstance(response, dict) or response.get("id") != request_id:
        raise _unavailable(owner, f"{owner} 返回了不匹配的响应")
    if not response.get("ok"):
        error = response.get("error") if isinstance(response.get("error"), dict) else {}
        raise InstructionError(
            str(error.get("code") or "instruction.failed"),
            str(error.get("message") or "指令执行失败"),
            details=error.get("details") if isinstance(error.get("details"), dict) else {},
            retryable=bool(error.get("retryable")),
            commit_state=str(error.get("commit_state") or "unknown"),
        )
    return response


def call_remote(
    owner: str, call: InstructionCall, context: InstructionContext, *, timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> InstructionResult:
    request_id = f"isa-{secrets.token_hex(10)}"
    params = request_params(call, context)
    payload = {"id": request_id, "method": METHOD, "params": params, **_signed(owner, params, context)}
    connection, stream = _open(owner, payload, timeout)
    try:
        try:
            raw = stream.readline()
        except (OSError, TimeoutError) as exc:
            raise _unavailable(owner, f"等待 {owner} 返回结果超时或连接中断") from exc
        response = _decode(owner, raw, request_id)
    finally:
        stream.close()
        connection.close()
    result = response.get("result")
    if not isinstance(result, dict):
        raise _unavailable(owner, f"{owner} 返回的结果格式无效")
    return InstructionResult.from_payload(result)


def release_recovery_remote(owner: str, recovery_key: str, instruction_ids: list[str], context: InstructionContext) -> int:
    """Use the existing authenticated owner channel for a runtime acknowledgement."""
    request_id = f"isa-ack-{secrets.token_hex(10)}"
    params = _context_params(context)
    params["recovery_release"] = {"key": recovery_key, "instruction_ids": instruction_ids}
    payload = {"id": request_id, "method": METHOD, "params": params, **_signed(owner, params, context)}
    # This is a short metadata acknowledgement, not an instruction that may
    # execute a long-running script. Failed acknowledgements remain queued.
    connection, stream = _open(owner, payload, 10.0)
    try:
        response = _decode(owner, stream.readline(), request_id)
        return int(response["result"]["released"])
    finally:
        stream.close()
        connection.close()


def stream_remote(
    owner: str, call: InstructionCall, context: InstructionContext, *, idle_timeout: float = STREAM_IDLE_TIMEOUT_SECONDS,
    cancellation: StreamCancellation | None = None,
) -> Iterator[list[dict[str, Any]]]:
    if cancellation is not None and cancellation.cancelled:
        return
    request_id = f"isa-{secrets.token_hex(10)}"
    params = request_params(call, context)
    payload = {"id": request_id, "method": METHOD, "params": params, **_signed(owner, params, context)}
    connection, stream = _open(owner, payload, idle_timeout)
    if cancellation is not None:
        cancellation.bind(connection)
    try:
        while True:
            if cancellation is not None and cancellation.cancelled:
                return
            try:
                raw = stream.readline()
            except (OSError, TimeoutError) as exc:
                if cancellation is not None and cancellation.cancelled:
                    return
                raise _unavailable(owner, f"{owner} 的订阅连接中断") from exc
            if cancellation is not None and cancellation.cancelled:
                return
            response = _decode(owner, raw, request_id)
            if response.get("end"):
                return
            batch = response.get("batch")
            yield list(batch) if isinstance(batch, list) else []
    finally:
        if cancellation is not None:
            cancellation.release(connection)
        stream.close()
        connection.close()


def serve(
    params: Any,
    request_id: str,
    write_line: Callable[[dict[str, Any]], None],
    *,
    owner: str,
    verified_caller: str | None = None,
) -> None:
    """Answer one ``instruction.execute`` request on an owning service's socket."""

    from .routing import execute_local, stream_local

    def failure(exc: BaseException) -> dict[str, Any]:
        from .contracts import normalize_error

        error = normalize_error(exc)
        return {"id": request_id, "ok": False, "error": error.to_dict()}

    try:
        if not isinstance(params, dict):
            raise InstructionError("instruction.invalid_arguments", "params 必须是对象")
        context = InstructionContext.from_payload(params.get("context"))
        if verified_caller is None or verified_caller != context.caller:
            raise InstructionError("instruction.forbidden", "调用者断言与签名不一致")
        if "recovery_release" in params:
            ack = params["recovery_release"]
            if (not isinstance(ack, dict) or context.entry != "lineage" or not context.recovery_key
                    or ack.get("key") != context.recovery_key or "call" in params):
                raise InstructionError("instruction.forbidden", "恢复依赖释放缺少对应的运行身份")
            ids = ack.get("instruction_ids")
            if not isinstance(ids, list) or len(ids) > 4096 or not all(isinstance(i, str) for i in ids):
                raise InstructionError("instruction.invalid_arguments", "恢复依赖必须为有限的指令 ID 列表")
            from .routing import kernel_for
            released = kernel_for(owner, required=True).store.release_recovery(
                caller=context.caller, recovery_key=context.recovery_key, instruction_ids=ids,
            )
            write_line({"id": request_id, "ok": True, "result": {"released": released}})
            return
        call = InstructionCall.from_payload(params.get("call"))
        from .catalog import declaration

        target = declaration(call.instruction)
    except Exception as exc:  # noqa: BLE001
        write_line(failure(exc))
        return
    if not target.streaming:
        result = execute_local(call, context, owner=owner)
        write_line({"id": request_id, "ok": True, "result": result.to_payload()})
        return
    try:
        for batch in stream_local(call, context, owner=owner):
            write_line({"id": request_id, "ok": True, "batch": batch})
    except (BrokenPipeError, ConnectionResetError):
        return
    except Exception as exc:  # noqa: BLE001
        try:
            write_line(failure(exc))
        except (BrokenPipeError, ConnectionResetError):
            pass
        return
    write_line({"id": request_id, "ok": True, "end": True})
