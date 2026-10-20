"""Connecting to a Portal service socket across the service's restart."""

from __future__ import annotations

import socket
import time
from collections.abc import Iterable, Iterator
from pathlib import Path

from management_common.service_endpoint import ServiceEndpoint, service_endpoint
from management_common.local_ipc import connect_local

# A service socket that is absent or refuses connections belongs to a service
# systemd is restarting: SocietyService, ResourceService and ExecutionService
# come back within seconds.  The request has not reached the service, so the
# caller waits for it this long instead of failing at once; nothing is sent
# twice.  A probe (health, a wake-up) passes ``restart_wait=0``.
SERVICE_RESTART_WAIT_SECONDS = 90.0
# While it is away the caller backs off: the first retry after 0.25 s, each
# later one twice as long, at most 2 s apart.
RETRY_FIRST_SECONDS = 0.25
RETRY_MAX_SECONDS = 2.0
# The connection errors of a service that is not accepting yet; a full listen
# backlog (EAGAIN) leaves the request undelivered too.
NOT_ACCEPTING = (ConnectionRefusedError, FileNotFoundError, BlockingIOError)


def retry_delays() -> Iterator[float]:
    """The pauses between connection attempts: 0.25, 0.5, 1, 2, 2, ... seconds."""

    delay = RETRY_FIRST_SECONDS
    while True:
        yield delay
        delay = min(delay * 2, RETRY_MAX_SECONDS)


def connect_service(
    paths: Path | str | ServiceEndpoint | Iterable[Path | str | ServiceEndpoint],
    *,
    timeout: float,
    restart_wait: float = SERVICE_RESTART_WAIT_SECONDS,
) -> tuple[socket.socket, Path]:
    """A socket connected to the first of ``paths`` that accepts, and its path.

    A path that fails moves the attempt on to the next one.  While every path
    is merely not accepting, this waits up to ``restart_wait`` seconds and
    then raises the last refusal; another connection error is raised once
    every path has been tried.
    """

    inputs = [paths] if isinstance(paths, (str, Path, ServiceEndpoint)) else list(paths)
    candidates = [item if isinstance(item, ServiceEndpoint) else service_endpoint(item) for item in inputs]
    if not candidates:
        raise FileNotFoundError("no service socket to connect to")
    deadline = time.monotonic() + max(0.0, float(restart_wait))
    delays = retry_delays()
    while True:
        failure: OSError | None = None
        refusal: OSError | None = None
        for endpoint in candidates:
            connection = None
            try:
                if endpoint.private_local:
                    connection = connect_local(endpoint.logical_path, timeout=timeout, transport="tcp")
                else:
                    connection = socket.socket(endpoint.family, socket.SOCK_STREAM)
                    connection.settimeout(timeout)
                    connection.connect(endpoint.address)
                return connection, endpoint.logical_path
            except NOT_ACCEPTING as exc:
                if connection is not None:
                    connection.close()
                refusal = exc
            except OSError as exc:
                if connection is not None:
                    connection.close()
                failure = failure or exc
            except BaseException:
                if connection is not None:
                    connection.close()
                raise
        if failure is not None:
            raise failure
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise refusal  # type: ignore[misc]
        time.sleep(min(next(delays), remaining))
