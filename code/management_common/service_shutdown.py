"""Bounded shutdown for socket owners whose work has durable recovery records."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import faulthandler
import os
import signal
import socket
import socketserver
import sys
import threading
import time
from typing import Callable, Iterator

from management_common import cpu_profile


SHUTDOWN_SECONDS = 30.0
SLOW_SHUTDOWN_SECONDS = 3.0
REQUEST_DRAIN_SECONDS = 5.0
_request_stop: ContextVar[threading.Event | None] = ContextVar("request_stop", default=None)


def wait_for_shutdown(seconds: float) -> bool:
    """Wait between stream reads; a draining owner wakes its subscribers."""
    stopping = _request_stop.get()
    if stopping is None:
        time.sleep(seconds)
        return False
    return stopping.wait(seconds)


def shutdown_requested() -> bool:
    """Whether the owner running this thread has begun to stop.

    Long passes that the next start repeats anyway (a full reconcile) check
    it between items, so a stop waits for one item instead of the pass.
    """
    stopping = _request_stop.get()
    return stopping is not None and stopping.is_set()


@contextmanager
def stopping_with(stopping: threading.Event) -> Iterator[None]:
    """Run owner work whose shutdown checks and waits follow ``stopping``."""
    token = _request_stop.set(stopping)
    try:
        yield
    finally:
        _request_stop.reset(token)


class DrainingStreamServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    # Existing Unix-socket callers subclass this directly. Service owners that
    # select TCP set their instance's address_family before binding.
    address_family = getattr(socket, "AF_UNIX", socket.AF_INET)

    def __init__(self, *args, **kwargs):
        self.stopping = threading.Event()
        self._requests_lock = threading.Lock()
        self._requests_drained = threading.Condition(self._requests_lock)
        self._requests: set[socket.socket] = set()
        super().__init__(*args, **kwargs)

    def process_request_thread(self, request, client_address):
        with self._requests_lock:
            if self.stopping.is_set():
                self.shutdown_request(request)
                return
            self._requests.add(request)
        token = _request_stop.set(self.stopping)
        try:
            super().process_request_thread(request, client_address)
        finally:
            _request_stop.reset(token)
            with self._requests_lock:
                self._requests.discard(request)
                self._requests_drained.notify_all()

    def begin_shutdown(self, *, drain_seconds: float = REQUEST_DRAIN_SECONDS):
        with self._requests_lock:
            if self.stopping.is_set():
                return
            self.stopping.set()
            requests = list(self._requests)
        self.socket.close()
        # Wake idle readers and watches, while allowing an accepted operation
        # to send its result before pools and domain services are dismantled.
        for request in requests:
            try:
                request.shutdown(socket.SHUT_RD)
            except OSError:
                pass
        deadline = time.monotonic() + max(0.0, drain_seconds)
        with self._requests_drained:
            while self._requests:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._requests_drained.wait(remaining)
        with self._requests_lock:
            requests = list(self._requests)
        for request in requests:
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def stop_workers(callbacks: dict[str, Callable[[], None]], *, timeout: float = 24.0):
    """Signal all owners at once, then share one grace period for their joins."""
    deadline = time.monotonic() + timeout
    threads = []
    for name, callback in callbacks.items():
        thread = threading.Thread(target=callback, name=f"stop-{name}", daemon=True)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))


def serve_until_stopped(server, *, shutdown_seconds: float = SHUTDOWN_SECONDS):
    """The deadline includes Python's implicit ThreadPoolExecutor exit joins.

    Transactions left open roll back when the process exits. Committed jobs,
    receipts and leases remain owned by the existing recovery protocols.
    """
    previous = signal.getsignal(signal.SIGTERM)

    def stop(*_):
        raise KeyboardInterrupt

    def deadline():
        # Keep this daemon alive even after main returns: the interpreter may
        # still be joining a non-cooperative executor callback at that point.
        started = time.monotonic()
        if shutdown_seconds > SLOW_SHUTDOWN_SECONDS:
            # A stop normally ends within a second; show what a slow one waits for.
            time.sleep(SLOW_SHUTDOWN_SECONDS)
            os.write(2, b"Service shutdown still waiting; current threads:\n")
            faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        time.sleep(max(0.0, shutdown_seconds - (time.monotonic() - started)))
        try:
            os.write(2, b"Service shutdown deadline reached; leaving durable work for recovery.\n")
            faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        finally:
            os._exit(0)

    signal.signal(signal.SIGTERM, stop)
    # `kill -USR2 <pid>` logs where this service's CPU goes for the next 30 s.
    cpu_profile.install()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        # A repeated TERM must not interrupt cleanup or restart the deadline.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        threading.Thread(target=deadline, name="service-shutdown-deadline", daemon=True).start()
        server.begin_shutdown()
        server.server_close()
        signal.signal(signal.SIGTERM, previous)
