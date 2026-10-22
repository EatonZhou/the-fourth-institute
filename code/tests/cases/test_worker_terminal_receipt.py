"""Retain a produced result across control-plane replacement without input replay."""
import threading
from types import SimpleNamespace

import pytest

from management_portal.execution.contracts import ExecutionRequest
from management_portal.execution.transport import (
    ExecutionClaimOwnershipLost, ExecutionResponseUnknown, ExecutionServiceUnavailable,
)
from management_portal.execution.worker import ExecutionWorker


def exercise(failures, *, lease_seconds=5, backend_failure=False):
    done = threading.Event()
    attempts = []
    invocations = []

    class Scheduler:
        def start(self, *_args):
            return {}

        def wait_for_control(self, *_args, **_kwargs):
            done.wait(0.01)
            return {}

        def heartbeat(self, *_args):
            return {}

        def finish(self, result, token):
            attempts.append((result, token))
            if failures:
                raise failures.pop(0)
            done.set()
            return {"status": result.status}

    class Runtime:
        def execute(self, request, _context):
            invocations.append(request.execution_id)
            if backend_failure:
                raise ValueError("provider failed once")
            return SimpleNamespace(output={"message": "one result"}, backend_session_id="session-1",
                                   backend_route_id="route-1", usage={})

    request = ExecutionRequest.new(actor="sample_admin", prompt="single accepted input",
                                   provider="codex", service_type="agent_session", operation="turn")
    worker = ExecutionWorker(build_id="test", worker_id="test-worker", scheduler=Scheduler(), runtime=Runtime())
    claim = {"claim_token": "same-claim", "execution": {
        "execution_id": request.execution_id, "request": request.to_payload(), "checkpoint": {},
    }}
    try:
        outcome = worker.execute_claim(claim, lease_seconds=lease_seconds)
        return outcome, attempts, invocations
    finally:
        done.set()
        assert invocations == [request.execution_id], "terminal retries must not replay the Provider input"
        assert all(result is attempts[0][0] and token == "same-claim" for result, token in attempts)


@pytest.mark.parametrize("backend_failure", [False, True])
def test_terminal_write_survives_socket_replacement_with_the_same_result(backend_failure):
    outcome, attempts, _ = exercise([ExecutionServiceUnavailable("socket replaced")], backend_failure=backend_failure)
    assert outcome["status"] == ("failed" if backend_failure else "completed")
    assert len(attempts) == 2
    assert attempts[-1][0].error == ("provider failed once" if backend_failure else "")


def test_lost_terminal_reply_is_fenced_without_replaying_or_overwriting():
    outcome, attempts, _ = exercise([ExecutionResponseUnknown("reply lost"),
                                    ExecutionClaimOwnershipLost("already committed or replaced")])
    assert outcome["status"] == "claim_lost"
    assert len(attempts) == 2


def test_terminal_retry_stops_at_known_lease_deadline():
    with pytest.raises(ExecutionServiceUnavailable, match="offline"):
        exercise([ExecutionServiceUnavailable("offline") for _ in range(20)], lease_seconds=0.03)


def test_business_error_is_not_retried_as_transport_outage():
    with pytest.raises(ValueError, match="invalid result"):
        exercise([ValueError("invalid result")])
