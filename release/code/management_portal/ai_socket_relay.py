#!/usr/bin/env python3
"""TFI Portal fixed-port TCP relay for LAN AI requests."""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import socket
import socketserver
import struct
import threading
import time
from typing import Any

from management_common.product_environment import read_product_env

from management_portal import model_api_store
from management_portal.ai_provider_registry import AIProviderRegistry


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18484
DEFAULT_ALLOWED_IPS = "127.0.0.1,::1"
DEFAULT_MAX_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_CONCURRENT = 16
DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_READ_TIMEOUT_SECONDS = 30.0


class RelayFrameError(ValueError):
    pass


def _parse_allowed_networks(raw: str) -> list[ipaddress._BaseNetwork]:
    networks: list[ipaddress._BaseNetwork] = []
    for item in raw.split(","):
        text = item.strip()
        if not text:
            continue
        try:
            networks.append(ipaddress.ip_network(text, strict=False))
        except ValueError as exc:
            raise SystemExit(f"invalid allowlist entry {text!r}: {exc}") from exc
    return networks


def _peer_allowed(peer_ip: str, networks: list[ipaddress._BaseNetwork]) -> bool:
    try:
        address = ipaddress.ip_address(peer_ip)
    except ValueError:
        return False
    return any(address in network for network in networks)


def _read_exact(sock: socket.socket, size: int, *, deadline: float | None = None) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        if deadline is not None:
            remaining_time = deadline - time.monotonic()
            if remaining_time <= 0:
                raise TimeoutError("frame read deadline exceeded")
            sock.settimeout(remaining_time)
        chunk = sock.recv(remaining)
        if not chunk:
            raise EOFError
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_frame(sock: socket.socket, max_bytes: int, *, deadline: float | None = None) -> dict[str, Any]:
    header = _read_exact(sock, 4, deadline=deadline)
    (length,) = struct.unpack(">I", header)
    if length <= 0:
        raise RelayFrameError("empty frame")
    if length > max_bytes:
        raise RelayFrameError("frame exceeds configured limit")
    body = _read_exact(sock, length, deadline=deadline)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise RelayFrameError("invalid frame JSON") from None
    if not isinstance(payload, dict):
        raise RelayFrameError("frame JSON must be an object")
    return payload


def _write_frame(sock: socket.socket, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sock.sendall(struct.pack(">I", len(body)) + body)


def _provider_registry_entry(provider_name: str) -> dict[str, Any]:
    requested = provider_name.strip().lower()
    candidates = [
        item
        for item in _read_llm_registry()
        if str(item.get("provider") or "").strip().lower() == requested
        and str(item.get("kind") or "").strip() == "openai_compatible_chat"
        and AIProviderRegistry.is_enabled(item)
    ]
    for item in candidates:
        if str(item.get("id") or "").strip() == f"{requested}_primary":
            return item
    if candidates:
        return candidates[0]
    raise ValueError(f"{requested} provider not configured")


def _read_llm_registry() -> list[dict[str, Any]]:
    return AIProviderRegistry().entries()


def _read_resource_registry() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in model_api_store.relay_resources():
        resource_id = str(item.get("id") or "").strip().lower()
        provider = str(item.get("provider") or "").strip().lower()
        model = str(item.get("model") or "").strip()
        if resource_id and provider and model and item.get("enabled") is not False:
            out[resource_id] = {"id": resource_id, **item, "provider": provider, "model": model}
    return out


def _resolve_resource(resource_id: str) -> dict[str, Any]:
    resources = _read_resource_registry()
    resource = resources.get(resource_id.strip().lower())
    if not resource:
        raise PermissionError(f"resource is not allowed: {resource_id}")
    return resource


def _forward_chat_completion(provider: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any], str]:
    from management_portal.execution.service import ExecutionService
    from management_portal.execution.requests import build_chat_completion_request

    item = _provider_registry_entry(provider)
    messages = [
        dict(message)
        for message in payload.get("messages") or []
        if isinstance(message, dict)
    ]
    user_prompt = next(
        (
            str(message.get("content") or "")
            for message in reversed(messages)
            if str(message.get("role") or "") == "user"
        ),
        "Run AI relay chat completion.",
    )
    timeout = max(1.0, float(item.get("timeout_s") or DEFAULT_TIMEOUT_SECONDS))
    retries = max(0, int(item.get("max_retries") or 0))
    request = build_chat_completion_request(
        actor="ai-socket-relay",
        provider=str(item.get("id") or ""),
        prompt=user_prompt,
        runtime_options={"model": str(payload.get("model") or ""), "messages": messages,
            "timeout_seconds": timeout, "max_retries": retries, "parameters": {
            key: value
            for key, value in payload.items()
            if key not in {"model", "messages", "stream"}
        }},
        metadata={"business_type": "ai_socket_relay"},
    )
    service = ExecutionService()
    submitted = service.submit(request)
    result = service.wait(submitted["execution_id"], actor=request.actor,
                          timeout_seconds=timeout * (retries + 1) + 30)
    if result.get("status") != "completed":
        raise RuntimeError("AI relay execution did not complete")
    output = (result.get("result") or {}).get("output") or {}
    response = (
        output.get("response")
        if isinstance(output.get("response"), dict)
        else {}
    )
    return 200, response, "application/json"


def _handle_request(frame: dict[str, Any]) -> dict[str, Any]:
    request_id = str(frame.get("request_id") or "")
    resource_id = str(frame.get("resource") or "").strip().lower()
    payload = frame.get("payload")
    if type(frame.get("version")) is not int or frame.get("version") != 1:
        return {"type": "error", "request_id": request_id, "status": 400, "error": "unsupported version"}
    if not resource_id:
        return {"type": "error", "request_id": request_id, "status": 400, "error": "resource is required"}
    if not isinstance(payload, dict):
        return {"type": "error", "request_id": request_id, "status": 400, "error": "payload must be an object"}
    if payload.get("stream"):
        return {"type": "error", "request_id": request_id, "status": 400, "error": "streaming responses are not supported"}
    try:
        resource = _resolve_resource(resource_id)
    except PermissionError as exc:
        return {"type": "error", "request_id": request_id, "resource": resource_id, "status": 403, "error": str(exc)}
    requested_model = str(payload.get("model") or "").strip()
    if requested_model and requested_model != resource["model"]:
        return {
            "type": "error",
            "request_id": request_id,
            "resource": resource_id,
            "status": 403,
            "error": "payload model does not match the allowed resource model",
        }
    payload = {**payload, "model": resource["model"]}
    status, body_json, content_type = _forward_chat_completion(resource["provider"], payload)
    return {
        "type": "response",
        "request_id": request_id,
        "resource": resource_id,
        "provider": resource["provider"],
        "status": status,
        "content_type": content_type,
        "body_json": body_json,
    }


class RelayTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], handler_class: type[socketserver.BaseRequestHandler], *, allowed_networks: list[ipaddress._BaseNetwork], max_bytes: int, semaphore: threading.BoundedSemaphore):
        super().__init__(server_address, handler_class)
        self.allowed_networks = allowed_networks
        self.max_bytes = max_bytes
        self.semaphore = semaphore


class RelayHandler(socketserver.BaseRequestHandler):
    server: RelayTCPServer

    def handle(self) -> None:
        peer_ip = self.client_address[0]
        self.request.settimeout(DEFAULT_READ_TIMEOUT_SECONDS)
        if not _peer_allowed(peer_ip, self.server.allowed_networks):
            logging.warning("reject peer_ip=%s reason=not_allowed", peer_ip)
            try:
                _write_frame(self.request, {"type": "error", "status": 403, "error": "peer IP is not allowed"})
            except OSError:
                pass
            return
        if not self.server.semaphore.acquire(blocking=False):
            logging.warning("reject peer_ip=%s reason=too_many_concurrent", peer_ip)
            try:
                _write_frame(self.request, {"type": "error", "status": 429, "error": "too many concurrent requests"})
            except OSError:
                pass
            return
        try:
            while True:
                started = time.monotonic()
                request_id = ""
                resource = ""
                model = ""
                try:
                    try:
                        frame = _read_frame(self.request, self.server.max_bytes, deadline=time.monotonic() + DEFAULT_READ_TIMEOUT_SECONDS)
                    except TimeoutError:
                        return
                    self.request.settimeout(DEFAULT_READ_TIMEOUT_SECONDS)
                    request_id = str(frame.get("request_id") or "")
                    resource = str(frame.get("resource") or "")
                    payload = frame.get("payload")
                    if isinstance(payload, dict):
                        model = str(payload.get("model") or "")
                    response = _handle_request(frame)
                    elapsed_ms = int((time.monotonic() - started) * 1000)
                    logging.info(
                        "request peer_ip=%s request_id=%s resource=%s requested_model=%s status=%s elapsed_ms=%s",
                        peer_ip,
                        ascii(request_id)[:160],
                        ascii(resource)[:160],
                        ascii(model)[:160],
                        response.get("status"),
                        elapsed_ms,
                    )
                    _write_frame(self.request, response)
                except EOFError:
                    return
                except ConnectionError:
                    return
                except Exception as exc:
                    elapsed_ms = int((time.monotonic() - started) * 1000)
                    protocol_error = isinstance(exc, RelayFrameError)
                    status = 400 if protocol_error else 502
                    logging.warning(
                        "request_error peer_ip=%s request_id=%s resource=%s requested_model=%s status=%s elapsed_ms=%s error_type=%s",
                        peer_ip,
                        ascii(request_id)[:160],
                        ascii(resource)[:160],
                        ascii(model)[:160],
                        status,
                        elapsed_ms,
                        type(exc).__name__,
                    )
                    error = "invalid request frame" if protocol_error else "upstream request failed"
                    try:
                        _write_frame(self.request, {"type": "error", "request_id": request_id, "status": status, "error": error})
                    except OSError:
                        return
                    if protocol_error:
                        return
        finally:
            self.server.semaphore.release()


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TFI Portal LAN TCP AI relay")
    parser.add_argument("--host", default=read_product_env("PORTAL_AI_RELAY_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(read_product_env("PORTAL_AI_RELAY_PORT", str(DEFAULT_PORT))))
    parser.add_argument("--allowed-ips", default=read_product_env("PORTAL_AI_RELAY_ALLOWED_IPS", DEFAULT_ALLOWED_IPS))
    parser.add_argument("--max-bytes", type=int, default=int(read_product_env("PORTAL_AI_RELAY_MAX_BYTES", str(DEFAULT_MAX_BYTES))))
    parser.add_argument("--max-concurrent", type=int, default=int(read_product_env("PORTAL_AI_RELAY_MAX_CONCURRENT", str(DEFAULT_MAX_CONCURRENT))))
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _build_arg_parser().parse_args()
    allowed_networks = _parse_allowed_networks(args.allowed_ips)
    semaphore = threading.BoundedSemaphore(max(1, args.max_concurrent))
    server = RelayTCPServer(
        (args.host, args.port),
        RelayHandler,
        allowed_networks=allowed_networks,
        max_bytes=max(1024, args.max_bytes),
        semaphore=semaphore,
    )
    logging.info(
        "ai_socket_relay listening host=%s port=%s allowed_ips=%s max_bytes=%s max_concurrent=%s",
        args.host,
        args.port,
        args.allowed_ips,
        args.max_bytes,
        args.max_concurrent,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("ai_socket_relay stopping")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
