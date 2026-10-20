"""Closing a streamed response must release its remote read and Web worker.

:importance: P1
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest import mock

from management_portal import asgi
from management_portal.hatg import socket_rpc
from tests.cases.test_portal_asgi import _request


def test_remote_stream_cancellation_interrupts_blocking_read():
    """取消远程流式指令时中断正在阻塞的 socket 读取

    :importance: P2"""
    connection, peer = socket.socketpair()
    stream = connection.makefile("rb")
    cancellation = socket_rpc.StreamCancellation()
    opened = threading.Event()

    def open_stream(*_args):
        opened.set()
        return connection, stream

    try:
        with (
            mock.patch.object(socket_rpc, "request_params", return_value={}),
            mock.patch.object(socket_rpc, "_signed", return_value={}),
            mock.patch.object(socket_rpc, "_open", side_effect=open_stream),
            ThreadPoolExecutor(max_workers=1) as executor,
        ):
            future = executor.submit(lambda: list(socket_rpc.stream_remote(
                "lineage", None, None, cancellation=cancellation,
            )))
            assert opened.wait(timeout=1)
            cancellation.cancel()
            assert future.result(timeout=1) == []
    finally:
        peer.close()


def test_disconnected_sse_releases_worker_even_when_output_queue_is_full():
    """SSE 连接断开后即使输出队列已满也释放 Web 工作线程"""
    async def exercise():
        executor = ThreadPoolExecutor(max_workers=1)
        started = threading.Event()
        app = SimpleNamespace(state=SimpleNamespace(sse_payload_executor=executor))
        request = _request(
            "/api/instructions/execute", method="POST", app=app,
            body=json.dumps({
                "instruction": "WatchConversationEvents",
                "arguments": {"resident_id": "resident_111111111111", "after_cursor": 0},
            }).encode(),
        )

        def endless_stream(_call, _context, *, cancellation):
            started.set()
            while not cancellation.cancelled:
                yield [{"cursor": 1}]

        try:
            with (
                mock.patch.object(asgi, "_instruction_principal", return_value=("alice", "web")),
                mock.patch.object(asgi, "_same_origin", return_value=True),
                mock.patch("management_portal.hatg.routing.stream", side_effect=endless_stream),
            ):
                response = await asgi.instruction_execute(request)
                body = response.body_iterator
                assert await anext(body) == b"retry: 3000\n\n"
                assert await asyncio.to_thread(started.wait, 1)
                await asyncio.sleep(0.05)
                await body.aclose()
                assert await asyncio.wait_for(
                    asyncio.get_running_loop().run_in_executor(executor, lambda: "released"), 1,
                ) == "released"
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    asyncio.run(exercise())
