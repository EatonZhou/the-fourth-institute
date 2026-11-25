"""Share the account's ACP transport; each Worker owns one session at a time."""
from __future__ import annotations

import argparse
import asyncio
import json
from functools import partial
from pathlib import Path
import subprocess

from management_common.process_runtime import spawn_process, terminate_process_tree
from management_common.local_ipc import start_local_server
from management_portal.antigravity_client import acp_environment

# Includes base64 encoding of the maximum multi-image prompt.
FRAME_LIMIT = 80 * 1024 * 1024
# Google 1.5.2 retains loaded sessions and doesn't advertise session/close.
# Retire the process between executions after a bounded number of attachments.
MAX_CONCURRENT_CONNECTIONS = 12
ATTACHMENTS_PER_PROCESS = 96
CANCEL_GRACE_SECONDS = 10
DENIED = {"outcome": {"outcome": "cancelled"}}


def send(writer, message):
    writer.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode() + b"\n")


def result(identifier, value):
    return {"jsonrpc": "2.0", "id": identifier, "result": value}


def error(identifier, code):
    return {"jsonrpc": "2.0", "id": identifier, "error": {"code": code, "message": "ACP request failed"}}


class NativeAccount:
    """One native generation. Native request IDs are unique across all peers."""
    def __init__(self, process):
        self.process = process
        self.sessions = {}
        self.creating = 0
        self.initial_updates = {}
        self.pending = {}
        self.permissions = {}
        self.peers = set()
        self.sequence = 0
        self.handshake = {}
        self.login_required = False
        self.dead = False
        self.retiring = False
        self.terminating = None
        self.reader = asyncio.create_task(self.read_native())
        self.stderr = asyncio.create_task(self.detect_login())
        self.outgoing = asyncio.Queue()
        self.writer = asyncio.create_task(self.write_native())

    async def write_native(self):
        try:
            while (message := await self.outgoing.get()) is not None:
                frame = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
                await asyncio.to_thread(self.write_frame, frame)
        except (OSError, ValueError):
            if not self.dead:
                await self.stop()

    def write_frame(self, frame):
        self.process.stdin.write(frame)
        self.process.stdin.flush()

    async def detect_login(self):
        # Never forward or persist native stderr, which may carry a login URL.
        while line := await asyncio.to_thread(self.process.stderr.readline):
            if b"https://accounts.google.com/" in line and b"oauth" in line:
                self.login_required = True
                await self.stop()
                return

    async def read_native(self):
        try:
            while line := await asyncio.to_thread(self.process.stdout.readline, FRAME_LIMIT + 1):
                if len(line) > FRAME_LIMIT:
                    break
                message = json.loads(line)
                identifier = message.get("id")
                if "method" not in message:
                    future = self.pending.get(identifier)
                    if future is not None and not future.done():
                        future.set_result(message)
                    continue
                params = message.get("params") or {}
                sid = params.get("sessionId")
                peer = self.sessions.get(sid)
                if message["method"] == "session/update" and identifier is None:
                    if peer:
                        peer.deliver(message)
                    elif self.creating:
                        # Google can advertise the context window before replying
                        # to session/new. Deliver it only after the ID is bound.
                        update = params.get("update") or {}
                        kind = update.get("sessionUpdate")
                        if isinstance(sid, str) and (sid in self.initial_updates or len(self.initial_updates) < self.creating):
                            if kind == "usage_update" and type(update.get("size")) is int:
                                self.initial_updates.setdefault(sid, {})[kind] = {"sessionUpdate": kind, "size": update["size"]}
                            elif kind == "available_commands_update":
                                self.initial_updates.setdefault(sid, {})[kind] = update
                elif identifier is not None:
                    if message["method"] == "session/request_permission" and peer and peer.deliver(message):
                        self.permissions[identifier] = peer
                    else:
                        self.answer(result(identifier, DENIED) if message["method"] == "session/request_permission"
                                    else error(identifier, -32601))
        except (ValueError, ConnectionError):
            pass
        finally:
            self.dead = True
            for future in self.pending.values():
                if not future.done():
                    if self.login_required:
                        future.set_result(error(None, -32000))
                    else:
                        future.set_exception(ConnectionError("Native ACP closed"))
            for peer in tuple(self.peers):
                if not self.login_required:
                    peer.writer.close()

    def answer(self, message):
        if not self.dead:
            self.outgoing.put_nowait(message)

    async def request(self, method, params):
        if self.dead:
            raise ConnectionError("Native ACP closed")
        self.sequence += 1
        identifier = self.sequence
        future = self.pending[identifier] = asyncio.get_running_loop().create_future()
        try:
            self.answer({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params})
            return await future
        finally:
            self.pending.pop(identifier, None)

    async def setup(self, method):
        params = ({"protocolVersion": 1, "clientCapabilities": {},
                   "clientInfo": {"name": "tfi-portal", "version": "1.0"}} if method == "initialize"
                  else {"methodId": "oauth-personal"})
        if method not in self.handshake:
            self.handshake[method] = asyncio.create_task(self.request(method, params))
        task = self.handshake[method]
        reply = await asyncio.shield(task)
        if (method == "authenticate" or "error" in reply) and self.handshake.get(method) is task:
            self.handshake.pop(method)
        return reply

    def stop_if_abandoned(self):
        if self.retiring and all(peer.writer.is_closing() for peer in self.peers):
            self.stop()

    def stop(self):
        if self.terminating is None:
            self.terminating = asyncio.create_task(asyncio.to_thread(terminate_process_tree, self.process, timeout=5))
        return self.terminating

    async def close(self):
        self.outgoing.put_nowait(None)
        await self.stop()
        await asyncio.gather(self.reader, self.writer, self.stderr, *self.handshake.values(), return_exceptions=True)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()


class WorkerConnection:
    def __init__(self, native, writer):
        self.native, self.writer = native, writer
        self.sid = ""
        self.busy = None
        self.method = ""
        self.authenticated = False

    def deliver(self, message):
        if self.writer.is_closing():
            return False
        # A stalled Worker must not block the native reader or its siblings.
        if self.writer.transport.get_write_buffer_size() > FRAME_LIMIT:
            self.writer.close()
            return False
        send(self.writer, message)
        return True

    async def handle(self, reader):
        self.native.peers.add(self)
        try:
            async for line in reader:
                message = json.loads(line)
                method, identifier = message.get("method"), message.get("id")
                params = message.get("params") or {}
                if not method:
                    if identifier in self.native.permissions and self.native.permissions[identifier] is self:
                        del self.native.permissions[identifier]
                        self.native.answer(message)
                elif identifier is None:
                    if method == "session/cancel" and self.sid and params.get("sessionId") == self.sid:
                        self.native.answer(message)
                elif self.busy and not self.busy.done():
                    self.deliver(error(identifier, -32600))
                else:
                    self.method = method
                    self.busy = asyncio.create_task(self.forward(identifier, method, params))
        except (ValueError, ConnectionError):
            pass
        finally:
            self.writer.close()
            for identifier, owner in tuple(self.native.permissions.items()):
                if owner is self:
                    self.native.permissions.pop(identifier)
                    self.native.answer(result(identifier, DENIED))
            if self.busy and not self.busy.done() and self.method == "session/prompt" and self.sid:
                self.native.answer({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": self.sid}})
            self.native.stop_if_abandoned()
            # Keep ownership until Google settles an outstanding prompt, even
            # if its caller dies. A late update must never reach the next turn.
            if self.busy:
                done, _ = await asyncio.wait({self.busy}, timeout=CANCEL_GRACE_SECONDS)
                if not done:
                    # No new admissions. Let attached siblings finish, then
                    # reap the stuck native generation and all its children.
                    self.native.retiring = True
                    self.native.stop_if_abandoned()
                await self.busy
            if self.native.sessions.get(self.sid) is self:
                del self.native.sessions[self.sid]
            self.native.peers.discard(self)

    async def forward(self, identifier, method, params):
        native = self.native
        sid = params.get("sessionId")
        try:
            if method in {"initialize", "authenticate"}:
                reply = await native.setup(method)
                if method == "authenticate":
                    self.authenticated = "result" in reply
            elif method in {"session/new", "session/load"} and self.authenticated and not self.sid:
                if method == "session/load":
                    if not isinstance(sid, str) or not sid or sid in native.sessions:
                        self.deliver(error(identifier, -32602))
                        return
                    native.sessions[sid] = self
                    self.sid = sid
                if method == "session/new":
                    native.creating += 1
                try:
                    reply = await native.request(method, params)
                finally:
                    if method == "session/new":
                        native.creating -= 1
                if method == "session/new" and "result" in reply:
                    sid = reply["result"].get("sessionId")
                    if not isinstance(sid, str) or not sid or sid in native.sessions:
                        self.deliver(error(identifier, -32603))
                        return
                    self.sid = sid
                    native.sessions[sid] = self
                    for update in native.initial_updates.pop(sid, {}).values():
                        self.deliver({"jsonrpc": "2.0", "method": "session/update",
                                      "params": {"sessionId": sid, "update": update}})
                if method == "session/load" and "error" in reply:
                    native.sessions.pop(self.sid, None)
                    self.sid = ""
                if not native.creating:
                    native.initial_updates.clear()
            elif self.sid and self.sid == sid and method in {"session/set_model", "session/set_mode", "session/prompt"}:
                reply = await native.request(method, params)
            else:
                self.deliver(error(identifier, -32602))
                return
            if "error" in reply:
                self.deliver(error(identifier, reply["error"].get("code", -32603)))
            else:
                self.deliver({**reply, "id": identifier})
        except (ConnectionError, OSError):
            self.writer.close()


class AccountServer:
    def __init__(self, command, env, cwd, *, max_attachments=ATTACHMENTS_PER_PROCESS, configuration=None):
        self.command, self.env, self.cwd = command, env, cwd
        self.configuration = configuration
        self.max_attachments = max_attachments
        self.native = None
        self.attached = 0
        self.active = 0
        self.closed = False
        self.condition = asyncio.Condition()

    def start_native(self):
        process = spawn_process(self.command, env=self.env, cwd=self.cwd, start_new_session=True,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.native = NativeAccount(process)
        self.attached = 0

    async def handle(self, reader, writer):
        try:
            launch = await asyncio.to_thread(self.configuration) if self.configuration else (self.command, self.env, self.cwd)
            async with self.condition:
                if self.native and launch != (self.command, self.env, self.cwd):
                    self.native.retiring = True
                await self.condition.wait_for(lambda: self.closed or not self.active or
                                              (self.active < MAX_CONCURRENT_CONNECTIONS and not self.native.dead
                                               and not self.native.retiring and self.attached < self.max_attachments))
                if self.closed:
                    writer.close()
                    return
                if self.native is None or self.native.dead or self.native.retiring or self.attached >= self.max_attachments:
                    if self.native:
                        await self.native.close()
                    if self.configuration:
                        launch = await asyncio.to_thread(self.configuration)
                    self.command, self.env, self.cwd = launch
                    self.start_native()
                peer = WorkerConnection(self.native, writer)
                self.attached += 1
                self.active += 1
        except Exception:
            writer.close()
            raise
        try:
            await peer.handle(reader)
        finally:
            async with self.condition:
                self.active -= 1
                self.condition.notify_all()

    async def close(self):
        async with self.condition:
            self.closed = True
            self.condition.notify_all()
            if self.native:
                for peer in tuple(self.native.peers):
                    peer.writer.close()
                await self.native.close()


def account_configuration(account_id):
    from management_common.provider_account_paths import registered_home
    from management_portal import antigravity_accounts as accounts
    from management_portal.antigravity_runtime import command
    from management_portal.resident_shell_environment import resident_process_environment

    row = accounts.account_for_route(account_id)
    env, _ = resident_process_environment({})
    env.update(accounts.environment(row["account_id"]))
    env["PORTAL_SHARED_ACP"] = "1"
    executable, *args = command()
    return [str(Path(executable).resolve(strict=True)), *args], acp_environment(env), registered_home(row)


async def serve(account_id):
    from management_common.process_runtime import install_shutdown_handlers, restore_shutdown_handlers
    from management_portal.antigravity_account_host import socket_path

    configuration = partial(account_configuration, account_id)
    host = AccountServer(*configuration(), configuration=configuration)
    loop = asyncio.get_running_loop()
    stopped = asyncio.Event()
    handlers = install_shutdown_handlers(lambda *_: loop.call_soon_threadsafe(stopped.set))
    async with await start_local_server(host.handle, socket_path(account_id), limit=FRAME_LIMIT):
        try:
            host.start_native()
            await stopped.wait()
        finally:
            restore_shutdown_handlers(handlers)
            await host.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--account-id", required=True)
    asyncio.run(serve(parser.parse_args().account_id))
