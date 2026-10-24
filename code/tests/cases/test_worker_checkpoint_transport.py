"""Claim-scoped transport faults must not replay an already running Provider."""

from copy import deepcopy
from collections import defaultdict
from types import SimpleNamespace
import threading

import pytest

from management_portal.execution.contracts import ExecutionRequest
from management_portal.execution.transport import (
    ExecutionClaimOwnershipLost,
    ExecutionServiceUnavailable,
    ExecutionResponseUnknown,
)
from management_portal.execution.runtime import ExecutionCancelled
from management_portal.execution.worker import ExecutionWorker


@pytest.mark.parametrize("fault", [ExecutionServiceUnavailable, ExecutionResponseUnknown])
def test_checkpoint_recovers_in_original_claim_without_replaying_runtime(fault):
    calls = []
    invocations = []
    receipts = []
    stopped = threading.Event()
    checkpoint = {"provider_session_id": "original-session", "cursor": {"offset": 7}}

    class Scheduler:
        def start(self, *args):
            return {}

        def wait_for_control(self, *args, **kwargs):
            stopped.wait(0.01)
            return {}

        def heartbeat(self, *args):
            return {}

        def checkpoint(self, execution_id, claim_token, payload):
            calls.append((execution_id, claim_token, deepcopy(payload)))
            if len(calls) == 1:
                raise fault("checkpoint transport interrupted")
            return {}

        def finish(self, result, token):
            receipts.append((result, token))
            stopped.set()
            return {"status": result.status}

    class Runtime:
        def execute(self, request, context):
            invocations.append(request.execution_id)
            context.checkpoint(checkpoint)
            return SimpleNamespace(output={"text": "original result"},
                                   backend_session_id="original-session",
                                   backend_route_id="route-1", usage={})

    request = ExecutionRequest.new(
        actor="sample_admin", prompt="same accepted input", provider="codex",
        service_type="agent_session", operation="turn",
    )
    worker = ExecutionWorker(build_id="test", worker_id="test-worker",
                             scheduler=Scheduler(), runtime=Runtime())
    claim = {"claim_token": "original-claim", "execution": {
        "execution_id": request.execution_id, "request": request.to_payload(),
        "checkpoint": {"prior": "preserved"},
    }}
    try:
        result = worker.execute_claim(claim, lease_seconds=5)
    finally:
        stopped.set()
    assert result["status"] == "completed"
    assert invocations == [request.execution_id]
    assert calls == [(request.execution_id, "original-claim", checkpoint)] * 2
    assert len(receipts) == 1 and receipts[0][0].status == "completed"


class SchedulerProbe:
    """Only substitute RPC boundaries; run the real Worker and heartbeat thread."""

    def __init__(self):
        self.calls = defaultdict(list)
        self.hooks = {}
        self.stopped = threading.Event()

    def invoke(self, name, *args):
        self.calls[name].append(deepcopy(args))
        hook = self.hooks.get(name)
        return hook(*args) if hook else {}

    def start(self, *args):
        return self.invoke("start", *args)

    def checkpoint(self, *args):
        return self.invoke("checkpoint", *args)

    def emit(self, *args):
        return self.invoke("emit", *args)

    def reassign_capacity(self, *args):
        return self.invoke("capacity", *args)

    def wait_for_control(self, *args, **kwargs):
        self.stopped.wait(0.01)
        return self.invoke("control", *args)

    def heartbeat(self, *args):
        return self.invoke("heartbeat", *args)

    def finish(self, result, claim_token):
        self.invoke("finish", result, claim_token)
        return {"status": result.status}


class ClaimProbe:
    def __init__(self, action):
        self.scheduler = SchedulerProbe()
        self.invocations = []
        self.interrupts = []
        self.interrupted = threading.Event()
        self.request = ExecutionRequest.new(
            actor="sample_admin", prompt="same accepted input", provider="codex",
            service_type="agent_session", operation="turn",
        )
        self.original_checkpoint = {"session": "original", "cursor": {"offset": 3}}
        self.claim = {"claim_token": "original-claim", "execution": {
            "execution_id": self.request.execution_id,
            "request": self.request.to_payload(),
            "checkpoint": deepcopy(self.original_checkpoint),
        }}
        probe = self

        class Runtime:
            def execute(self, request, context):
                probe.invocations.append(request.execution_id)
                assert context.recovery_checkpoint == probe.original_checkpoint
                action(context)
                return SimpleNamespace(output={"text": "one original result"},
                                       backend_session_id="original-session",
                                       backend_route_id="route-1", usage={})

            def interrupt(self, execution_id):
                probe.interrupts.append(execution_id)
                probe.interrupted.set()
                return True

        self.worker = ExecutionWorker(build_id="test", worker_id="test-worker",
                                      scheduler=self.scheduler, runtime=Runtime())

    def run(self, lease_seconds=5):
        try:
            return self.worker.execute_claim(self.claim, lease_seconds=lease_seconds)
        finally:
            self.scheduler.stopped.set()

    def assert_identity(self, invocations=1):
        assert self.invocations == [self.request.execution_id] * invocations
        assert self.claim["execution"]["checkpoint"] == self.original_checkpoint
        for name in ("start", "checkpoint", "heartbeat", "emit", "capacity"):
            assert all(args[:2] == (self.request.execution_id, "original-claim")
                       for args in self.scheduler.calls[name]), name


@pytest.mark.parametrize("fault", [ExecutionServiceUnavailable, ExecutionResponseUnknown])
def test_checkpoint_retry_keeps_heartbeat_live_and_preserves_uncertain_write(fault):
    checkpoint = {"session": "same-session", "nested": {"cursor": 8}}
    probe = ClaimProbe(lambda context: context.checkpoint(checkpoint))
    observations = []
    persisted = []

    def write(*args):
        observations.append(len(probe.scheduler.calls["heartbeat"]))
        if len(observations) == 1:
            # Unknown includes the case where the first write DID commit.
            if fault is ExecutionResponseUnknown:
                persisted.append(deepcopy(args[-1]))
            raise fault("temporary checkpoint outage")
        persisted.append(deepcopy(args[-1]))
        return {}

    probe.scheduler.hooks["checkpoint"] = write
    assert probe.run()["status"] == "completed"
    probe.assert_identity()
    assert len(observations) == 2 and observations[1] > observations[0]
    assert persisted and all(item == checkpoint for item in persisted)
    assert [args[-1] for args in probe.scheduler.calls["checkpoint"]] == [checkpoint] * 2
    assert len(probe.scheduler.calls["finish"]) == 1


@pytest.mark.parametrize("operation", ["checkpoint", "start"])
@pytest.mark.parametrize("fault", [ExecutionServiceUnavailable, ExecutionResponseUnknown])
def test_transport_retry_stops_at_lease_without_business_failure(operation, fault):
    called = threading.Event()
    probe = ClaimProbe(lambda context: context.checkpoint({"cursor": 9}))

    def unavailable(*args):
        called.set()
        raise fault("offline until lease expires")

    def control_unavailable(*args):
        assert called.wait(2)
        raise ExecutionServiceUnavailable("heartbeat service also offline")

    probe.scheduler.hooks[operation] = unavailable
    probe.scheduler.hooks["control"] = control_unavailable
    assert probe.run(lease_seconds=0.08)["status"] == "claim_lost"
    probe.assert_identity(invocations=0 if operation == "start" else 1)
    assert probe.scheduler.calls[operation]
    assert not probe.scheduler.calls["finish"]
    assert probe.interrupted.is_set()


@pytest.mark.parametrize("loss_source", ["checkpoint", "heartbeat", "start"])
def test_ownership_loss_fences_without_failed_receipt_or_provider_replay(loss_source):
    called = threading.Event()
    probe = ClaimProbe(lambda context: context.checkpoint({"cursor": 9}))

    def write(*args):
        called.set()
        if loss_source == "checkpoint":
            raise ExecutionClaimOwnershipLost("another owner")
        raise ExecutionServiceUnavailable("service replacing socket")

    def lost(*args):
        raise ExecutionClaimOwnershipLost("another owner")

    def control(*args):
        assert called.wait(2)
        return {}

    probe.scheduler.hooks["checkpoint"] = write
    if loss_source == "heartbeat":
        probe.scheduler.hooks["control"] = control
        probe.scheduler.hooks["heartbeat"] = lost
    elif loss_source == "start":
        probe.scheduler.hooks["start"] = lost
    assert probe.run()["status"] == "claim_lost"
    probe.assert_identity(invocations=0 if loss_source == "start" else 1)
    assert not probe.scheduler.calls["finish"]
    assert probe.interrupted.is_set()


def test_checkpoint_nontransport_error_remains_business_failed_once():
    probe = ClaimProbe(lambda context: context.checkpoint({"cursor": 9}))

    def invalid(*args):
        raise ValueError("invalid checkpoint content")

    probe.scheduler.hooks["checkpoint"] = invalid
    assert probe.run()["status"] == "failed"
    probe.assert_identity()
    assert len(probe.scheduler.calls["checkpoint"]) == 1
    receipts = probe.scheduler.calls["finish"]
    assert len(receipts) == 1
    assert receipts[0][0].error == "invalid checkpoint content"


@pytest.mark.parametrize("operation", ["emit", "capacity"])
@pytest.mark.parametrize("fault", [ExecutionServiceUnavailable, ExecutionResponseUnknown])
def test_nonidempotent_operations_retry_only_definitely_unsent_requests(operation, fault):
    def act(context):
        if operation == "emit":
            context.emit("provider.event", {"sequence": 1})
        else:
            context.reassign_backend_route("original-route")

    probe = ClaimProbe(act)
    committed = []

    def write(*args):
        if len(probe.scheduler.calls[operation]) == 1:
            if fault is ExecutionResponseUnknown:
                committed.append(deepcopy(args))
            raise fault("transport fault")
        committed.append(deepcopy(args))
        return {"changed": True}

    probe.scheduler.hooks[operation] = write
    outcome = probe.run()
    probe.assert_identity()
    assert len(committed) == 1
    if fault is ExecutionResponseUnknown:
        assert len(probe.scheduler.calls[operation]) == 1
        assert outcome["status"] == "failed"
    else:
        assert len(probe.scheduler.calls[operation]) == 2
        assert probe.scheduler.calls[operation][0] == probe.scheduler.calls[operation][1]
        assert outcome["status"] == "completed"


@pytest.mark.parametrize("fault", [ExecutionServiceUnavailable, ExecutionResponseUnknown])
def test_start_can_recover_before_running_provider_once(fault):
    probe = ClaimProbe(lambda context: None)
    heartbeats = []

    def start(*args):
        heartbeats.append(len(probe.scheduler.calls["heartbeat"]))
        assert not probe.invocations
        if len(heartbeats) == 1:
            raise fault("start reply unavailable")
        return {"status": "running"}

    probe.scheduler.hooks["start"] = start
    assert probe.run()["status"] == "completed"
    probe.assert_identity()
    assert len(heartbeats) == 2 and heartbeats[1] > heartbeats[0]


def test_cancellation_does_not_discard_final_checkpoint_retry():
    checkpoint_called = threading.Event()

    def act(context):
        context.checkpoint({"final_provider_cursor": 19})
        assert probe.interrupted.wait(2)
        assert context.is_cancelled()
        raise ExecutionCancelled("cancelled by user")

    probe = ClaimProbe(act)

    def checkpoint(*args):
        checkpoint_called.set()
        if len(probe.scheduler.calls["checkpoint"]) == 1:
            raise ExecutionServiceUnavailable("final checkpoint temporarily offline")
        return {}

    def control(*args):
        assert checkpoint_called.wait(2)
        return {"cancel_requested": True}

    probe.scheduler.hooks["control"] = control
    probe.scheduler.hooks["checkpoint"] = checkpoint
    assert probe.run()["status"] == "cancelled"
    probe.assert_identity()
    assert len(probe.scheduler.calls["checkpoint"]) == 2
    receipts = probe.scheduler.calls["finish"]
    assert len(receipts) == 1 and receipts[0][0].status == "cancelled"


def test_concurrent_checkpoints_keep_order_and_detach_nested_payloads():
    first_entered = threading.Event()
    second_attempted = threading.Event()
    release_first = threading.Event()
    second_done = threading.Event()
    failures = []
    first = {"nested": {"cursor": 1}}
    second = {"nested": {"cursor": 2}}
    persisted = []

    def act(context):
        def save(payload, attempted=None, done=None):
            try:
                if attempted:
                    attempted.set()
                context.checkpoint(payload)
            except BaseException as exc:
                failures.append(exc)
            finally:
                if done:
                    done.set()

        writer1 = threading.Thread(target=save, args=(first,), daemon=True)
        writer2 = threading.Thread(target=save, args=(second, second_attempted, second_done), daemon=True)
        writer1.start()
        try:
            assert first_entered.wait(2)
            first["nested"]["cursor"] = 100
            writer2.start()
            assert second_attempted.wait(2)
            # A second callback cannot overtake the outstanding first write.
            assert not second_done.wait(0.05)
            second["nested"]["cursor"] = 200
        finally:
            release_first.set()
            writer1.join(3)
            if writer2.ident is not None:
                writer2.join(3)
        assert not writer1.is_alive() and not writer2.is_alive()
        assert not failures

    probe = ClaimProbe(act)

    def checkpoint(*args):
        payload = args[-1]
        if len(probe.scheduler.calls["checkpoint"]) == 1:
            first_entered.set()
            assert release_first.wait(2)
            persisted.append(deepcopy(payload))
            raise ExecutionResponseUnknown("write committed but reply lost")
        persisted.append(deepcopy(payload))
        return {}

    probe.scheduler.hooks["checkpoint"] = checkpoint
    assert probe.run()["status"] == "completed"
    probe.assert_identity()
    expected = [{"nested": {"cursor": 1}}, {"nested": {"cursor": 1}},
                {"nested": {"cursor": 2}}]
    assert [args[-1] for args in probe.scheduler.calls["checkpoint"]] == expected
    assert persisted == expected
