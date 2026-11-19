"""An ExecutionService stack on a Unix socket for execution layer tests, and a binding helper."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path

from management_portal.execution.scheduler import Scheduler
from management_portal.execution.service import (
    ExecutionPreparation,
    ExecutionService,
    ExecutionServiceServer,
)
from management_portal.execution.session_manager import SessionManager
from management_portal.execution.transport import ExecutionServiceClient


@contextmanager
def legacy_empty_checkpoints(scheduler):
    """Use the deployed TEXT representation within one test, including on failure."""
    with scheduler.changes.connect() as connection:
        connection.execute("ALTER TABLE executions ALTER COLUMN checkpoint_json DROP DEFAULT")
        connection.execute(
            "ALTER TABLE executions ALTER COLUMN checkpoint_json TYPE TEXT USING checkpoint_json::text"
        )
        connection.execute("ALTER TABLE executions ALTER COLUMN checkpoint_json SET DEFAULT ''")
        connection.execute("UPDATE executions SET checkpoint_json = '' WHERE checkpoint_json = '{}'")
    try:
        yield
    finally:
        with scheduler.changes.connect() as connection:
            connection.execute("ALTER TABLE executions ALTER COLUMN checkpoint_json DROP DEFAULT")
            connection.execute(
                "ALTER TABLE executions ALTER COLUMN checkpoint_json TYPE JSONB "
                "USING COALESCE(NULLIF(checkpoint_json, ''), '{}')::jsonb"
            )
            connection.execute("ALTER TABLE executions ALTER COLUMN checkpoint_json SET DEFAULT '{}'")


def _bind_session(
    manager: SessionManager,
    binding_id: str,
    provider: str,
    backend_session_id: str,
    *,
    backend_route_id: str = "",
) -> None:
    session = manager.agent_data.ensure_provider_session(
        provider=provider,
        backend_route_id=backend_route_id,
        backend_session_id=backend_session_id,
        agent_id=binding_id,
        actor_ref="test",
    )
    manager.agent_data.bind_provider_session(
        binding_id,
        session["provider_session_ref"],
    )


def _stack(tmp_path: Path):
    socket_path = tmp_path / "execution-service.sock"
    scheduler = Scheduler(
        max_concurrency=2,
        dispatch_interval_min_seconds=0,
        dispatch_interval_max_seconds=0,
    )
    server = ExecutionServiceServer(
        socket_path,
        scheduler,
        instance="explore-1",
        build_id="test-build",
        preparation=ExecutionPreparation(),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert server.started.wait(1)
    client = ExecutionServiceClient(socket_path, timeout=2)
    service = ExecutionService(client=client)
    return scheduler, server, thread, client, service


def _close(server: ExecutionServiceServer, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
