"""Bounded MCP 2025-06-18 client transports, isolated for each discovery/call.

No reconnect/retry of tools/call: a broken response can hide a completed effect.
Server requests for roots, sampling and elicitation are explicitly unsupported.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import json
import os
import signal
import subprocess
import time
from typing import Any, Callable

import aiohttp

PROTOCOL = "2025-06-18"
MAX_BYTES = 2 * 1024 * 1024
MAX_MESSAGES = 512


class MCPError(ValueError):
    """Only fixed, non-secret reason codes cross the execution boundary."""

    def __init__(self, reason: str, *, status: str = "refused"):
        super().__init__(reason)
        self.reason = reason
        self.status = status


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def decode(data: bytes | str) -> Any:
    try:
        return json.loads(data, parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError):
        raise MCPError("invalid_json") from None


class Connection:
    def __init__(self, document: dict, *, env: dict, headers: dict,
                 is_cancelled: Callable[[], bool] | None):
        self.transport = document["transport"]
        self.env, self.headers = env, headers
        self.is_cancelled = is_cancelled or (lambda: False)
        self.deadline = time.monotonic() + document["timeout_seconds"]
        self.process = None
        self.http = None
        self.session_id = ""
        self.initialized = False
        self.sequence = 0
        self.pending_id = None

    def remaining(self) -> float:
        if self.is_cancelled():
            raise InterruptedError("MCP execution cancelled")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise MCPError("timeout", status="unknown")
        return remaining

    async def bounded(self, awaitable):
        task = asyncio.ensure_future(awaitable)
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=min(0.05, self.remaining()))
            self.remaining()
            return task.result()
        finally:
            if not task.done():
                task.cancel()
            with suppress(BaseException):
                await task

    async def start(self):
        self.remaining()
        if self.transport["type"] == "stdio":
            self.process = await asyncio.create_subprocess_exec(
                self.transport["command"], *self.transport.get("args", []),
                env=self.env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=subprocess.DEVNULL, start_new_session=True, limit=MAX_BYTES + 1,
            )
        else:
            self.http = aiohttp.ClientSession(trust_env=False,
                timeout=aiohttp.ClientTimeout(total=None), raise_for_status=False)
        result = await self.request("initialize", {
            "protocolVersion": PROTOCOL, "capabilities": {},
            "clientInfo": {"name": "tfi-resident-mcp", "version": "1"},
        })
        if result.get("protocolVersion") != PROTOCOL:
            raise MCPError("unsupported_protocol")
        if not isinstance(result.get("capabilities"), dict) or not isinstance(result.get("serverInfo"), dict):
            raise MCPError("invalid_initialize")
        if not isinstance(result["capabilities"].get("tools"), dict):
            raise MCPError("tools_not_supported", status="missing")
        self.initialized = True
        await self.notify("notifications/initialized", {})

    def http_headers(self) -> dict:
        headers = {**self.headers, "Accept": "application/json, text/event-stream",
                   "Content-Type": "application/json"}
        if self.initialized:
            headers["MCP-Protocol-Version"] = PROTOCOL
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    async def send_stdio(self, message):
        raw = encode(message).encode() + b"\n"
        if len(raw) > MAX_BYTES:
            raise MCPError("message_too_large")
        self.process.stdin.write(raw)
        await self.process.stdin.drain()

    async def http_post(self, message, *, notification=False):
        raw = encode(message).encode()
        if len(raw) > MAX_BYTES:
            raise MCPError("message_too_large")
        async with self.http.post(self.transport["url"], data=raw,
                                  headers=self.http_headers(), allow_redirects=False) as response:
            if 300 <= response.status < 400:
                raise MCPError("http_redirect_refused")
            if response.status in (401, 403):
                raise MCPError("access_denied")
            if response.status >= 400:
                # Surface expiry; never repeat an uncertain tools/call.
                reason = "session_expired" if response.status == 404 and self.session_id else "http_error"
                raise MCPError(reason, status="unknown")
            if notification:
                if response.status != 202:
                    raise MCPError("invalid_notification_ack")
                return None
            session_id = response.headers.get("Mcp-Session-Id", "")
            if session_id:
                if len(session_id) > 1024 or any(not 0x21 <= ord(char) <= 0x7E for char in session_id):
                    raise MCPError("invalid_session_id")
                if self.session_id and session_id != self.session_id:
                    raise MCPError("session_changed")
                if message.get("method") == "initialize":
                    self.session_id = session_id
            content_type = response.content_type
            if content_type == "application/json":
                body = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise MCPError("message_too_large")
                return self.result(decode(bytes(body)), message["id"])
            if content_type != "text/event-stream":
                raise MCPError("unsupported_content_type")
            fields, total, count = [], 0, 0
            while True:
                line = await response.content.readline()
                if not line:
                    raise MCPError("missing_response", status="unknown")
                total += len(line)
                if total > MAX_BYTES:
                    raise MCPError("message_too_large")
                line = line.rstrip(b"\r\n")
                if line.startswith(b"data:"):
                    fields.append(line[5:].removeprefix(b" "))
                elif not line and fields:
                    incoming = decode(b"\n".join(fields))
                    fields = []
                    count += 1
                    if count > MAX_MESSAGES:
                        raise MCPError("message_limit")
                    if await self.server_message(incoming):
                        continue
                    return self.result(incoming, message["id"])

    async def notify(self, method, params):
        message = {"jsonrpc": "2.0", "method": method, "params": params}
        if self.process:
            await self.bounded(self.send_stdio(message))
        else:
            await self.bounded(self.http_post(message, notification=True))

    async def server_message(self, message) -> bool:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise MCPError("invalid_response")
        if "method" not in message:
            return False
        if "id" in message:
            reply = {"jsonrpc": "2.0", "id": message["id"]}
            if message["method"] == "ping":
                reply["result"] = {}
            else:
                reply["error"] = {"code": -32601, "message": "Client capability not supported"}
            if self.process:
                await self.send_stdio(reply)
            else:
                await self.http_post(reply, notification=True)
        # Notifications never become prompts or tool results.
        return True

    @staticmethod
    def result(message, request_id):
        if (not isinstance(message, dict) or message.get("jsonrpc") != "2.0"
                or type(message.get("id")) is not int or message["id"] != request_id
                or ("result" in message) == ("error" in message)):
            raise MCPError("invalid_response")
        if "error" in message:
            raise MCPError("server_rpc_error", status="unknown")
        if not isinstance(message["result"], dict):
            raise MCPError("invalid_result")
        return message["result"]

    async def stdio_response(self, request_id):
        for _ in range(MAX_MESSAGES):
            raw = await self.process.stdout.readline()
            if not raw:
                raise MCPError("connection_closed", status="unknown")
            if len(raw) > MAX_BYTES:
                raise MCPError("message_too_large")
            message = decode(raw)
            if not await self.server_message(message):
                return self.result(message, request_id)
        raise MCPError("message_limit")

    async def request(self, method, params):
        self.remaining()
        self.sequence += 1
        message = {"jsonrpc": "2.0", "id": self.sequence, "method": method, "params": params}
        self.pending_id = self.sequence
        if self.process:
            await self.bounded(self.send_stdio(message))
            result = await self.bounded(self.stdio_response(self.sequence))
        else:
            result = await self.bounded(self.http_post(message))
        self.pending_id = None
        return result

    async def close(self):
        # Best effort cancellation only; bounded independently of the exhausted deadline.
        if self.pending_id and self.initialized:
            message = {"jsonrpc": "2.0", "method": "notifications/cancelled",
                       "params": {"requestId": self.pending_id, "reason": "Client stopped waiting"}}
            with suppress(Exception):
                operation = self.send_stdio(message) if self.process else self.http_post(message, notification=True)
                await asyncio.wait_for(operation, timeout=0.2)
        if self.process:
            if self.process.stdin:
                self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=0.2)
            except asyncio.TimeoutError:
                with suppress(ProcessLookupError):
                    os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=0.2)
                except asyncio.TimeoutError:
                    with suppress(ProcessLookupError):
                        os.killpg(self.process.pid, signal.SIGKILL)
                    await self.process.wait()
            # A server may exit cleanly while children (with closed stdio) live
            # on. The dedicated process group belongs to this operation too.
            with suppress(ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGKILL)
        if self.http:
            if self.session_id:
                with suppress(Exception):
                    async with self.http.delete(self.transport["url"], headers=self.http_headers(),
                        allow_redirects=False, timeout=aiohttp.ClientTimeout(total=0.2)):
                        pass
            await self.http.close()


@asynccontextmanager
async def connect(document, *, env, headers, is_cancelled):
    connection = Connection(document, env=env, headers=headers, is_cancelled=is_cancelled)
    try:
        await connection.start()
        yield connection
    except (MCPError, InterruptedError):
        raise
    except (OSError, aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        raise MCPError("transport_error", status="unknown") from None
    finally:
        await connection.close()
