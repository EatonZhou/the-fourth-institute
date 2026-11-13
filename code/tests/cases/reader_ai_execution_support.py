"""Local execution driver for the disposable cross-owner ``owners`` fixture.

Install before delivering input, then call ``run_until_idle(resident_id)`` after
the real HTTP/ISA send. Only the final Codex app-server client is synthetic;
admission, assembly, sessions, queue, worker, backend and completion stay real.
This is not evidence of a working external model or app-server protocol server.
"""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
from pathlib import Path
import time
from uuid import uuid4

from management_portal.codex_app_server_protocol import CodexTurnResult
from management_portal.execution.agent_data_projection import AgentDataProjector
from management_portal.execution.codex_backend import CodexBackend
from management_portal.execution.runtime import ExecutionRuntime
from management_portal.execution.scheduler import SchedulerClient
from management_portal.execution.worker import ExecutionWorker
from management_portal.execution import service as execution_service, transport as execution_transport
from management_portal.society_service import (
    lifecycle, resident_execution_domain, resident_execution_engine_domain,
    resident_execution_work, resident_conversation_stream,
)


class RecordedCodexTransport:
    """Finite app-server client substitute; never writes Society messages."""

    def __init__(self, response_text):
        self.response_text = response_text
        self.requests = []
        self.turns = []

    def start_thread(self, params, **kwargs):
        self.requests.append({"method": "thread/start", "params": deepcopy(params)})
        return {"thread": {"id": str(uuid4())}}

    def run_turn(self, thread_id, prompt, **kwargs):
        turn_id = str(uuid4())
        self.requests.append({"method": "turn/start", "thread_id": thread_id,
                              "prompt": prompt})
        self.turns.append({"thread_id": thread_id, "turn_id": turn_id, "prompt": prompt})
        kwargs["turn_started_callback"](turn_id)
        item = {"id": str(uuid4()), "type": "agentMessage", "phase": "final_answer",
                "text": self.response_text}
        turn = {"id": turn_id, "status": "completed", "items": [item]}
        emit = kwargs["event_callback"]
        emit({"method": "item/completed", "params": {
            "threadId": thread_id, "turnId": turn_id, "item": item}})
        emit({"method": "turn/completed", "params": {
            "threadId": thread_id, "turn": turn}})
        return CodexTurnResult(thread_id, turn_id, self.response_text, turn, {})

    def unsubscribe_thread(self, thread_id):
        self.requests.append({"method": "thread/unsubscribe", "thread_id": thread_id})
        return {}


class _LocalRpcConnection:
    """Byte transport only; use the production RPC handler and client codec."""

    def __init__(self, server):
        self.server = server
        self.response = BytesIO()

    def settimeout(self, timeout):
        pass

    def sendall(self, payload):
        handler = object.__new__(execution_service._ExecutionRequestHandler)
        handler.server = self.server
        handler.rfile = BytesIO(payload)
        handler.wfile = self.response
        handler.handle()
        self.response.seek(0)

    def makefile(self, mode):
        assert mode == "rb"
        return self.response

    def close(self):
        pass


class ReaderExecutionSupport:
    def __init__(self, owners, runtime, worker, transport):
        self.owners = owners
        self.runtime = runtime
        self.worker = worker
        self.transport = transport
        self.transport_requests = transport.requests
        self.outcomes = []
        self.wakeups = set()

    def drain_society(self, resident_id):
        """Wake only this isolated Resident using its real persisted work."""
        self.wakeups.discard(resident_id)
        return resident_execution_domain.resume_resident_execution(resident_id)

    def run_until_idle(self, resident_id, *, timeout=15):
        """Drive claims and join completion; never manufacture a reply.

        Return the terminal input state, execution ID and final control. Timeout and failed Worker
        results are errors, rather than treating an empty conversation as success.
        Call before the owners fixture tears down its temporary database.
        """
        deadline = time.monotonic() + timeout
        input_id = ""
        execution_id = ""
        control = {}
        state = {}
        while time.monotonic() < deadline:
            resumed = self.drain_society(resident_id)
            control = resident_execution_engine_domain.read_resident_execution_control(resident_id)
            input_id = str(control.get("user_message_id") or input_id)
            execution_id = str(control.get("execution_id") or execution_id)
            claim = self.worker.claim(wait_seconds=0)
            if claim.get("claimed"):
                outcome = self.worker.execute_claim(claim)
                self.outcomes.append(outcome)
                if outcome.get("status") != "completed":
                    raise AssertionError(f"Execution did not complete: {outcome.get('result')!r}")
                continue
            record = resident_execution_engine_domain.resident_record_for_execution(resident_id)
            thread = resident_execution_work.thread(record)
            if thread is not None and thread.is_alive():
                thread.join(min(.02, max(0, deadline - time.monotonic())))
                continue
            state = resident_conversation_stream.get_repository().input_state(resident_id, input_id) if input_id else {}
            if state.get("state") == "completed" and not resident_execution_work.is_active(control) and not control.get("queue"):
                return {"execution_id": execution_id, "input_state": state, "control": control}
            time.sleep(.01)
        try:
            snapshot = self.owners.scheduler.snapshot(execution_id) if execution_id else {}
        except KeyError:
            snapshot = {"status": "not_submitted"}
        record = resident_execution_engine_domain.resident_record_for_execution(resident_id)
        thread = resident_execution_work.thread(record)
        import sys, traceback
        from management_portal.society_service import resident_record_domain, resident_restart_handoff
        frame = sys._current_frames().get(thread.ident) if thread else None
        stack = ''.join(traceback.format_stack(frame)) if frame else ''
        raise TimeoutError(f"Resident {resident_id} did not finish; control={control!r}; input={state!r}; "
                           f"claim={claim!r}; execution={snapshot!r}; resumed={resumed!r}; "
                           f"registration={record.get('registration_state')!r}; "
                           f"settings={record.get('execution_settings')!r}; route={record.get('backend_route_id')!r}; "
                           f"lifecycle_pending={resident_record_domain.lifecycle_operations().pending(resident_id)!r}; "
                           f"restart_pending={resident_restart_handoff.pending(record, resident_execution_work.read(record))!r}; "
                           f"continuation_needed={resident_execution_work.continuation_needed(record, resident_execution_work.read(record))!r}; "
                           f"job={resident_execution_work.read(record)!r}; thread={stack}")

    # Short integration-facing names; same driver and real Society continuation.
    resume = drain_society
    drive = run_until_idle


def install_execution_support(owners, monkeypatch, tmp_path=None, *,
                              response_text="Synthetic Reader answer."):
    """Use the fixture's registered isolated Codex routes, server and AgentData.

    No network/subprocess guards are relaxed. The local wake handler is restored
    by monkeypatch; caller must drain its accepted work before fixture teardown.
    """
    root = Path(tmp_path or owners.root) / "reader-execution"
    original_connect = execution_transport.connect_service
    endpoint = Path(owners.server.socket_path)

    def connect_local(paths, **kwargs):
        if tuple(map(Path, paths)) == (endpoint,):
            return _LocalRpcConnection(owners.server), endpoint
        return original_connect(paths, **kwargs)

    monkeypatch.setattr(execution_transport, "connect_service", connect_local)
    # Both Society and Worker now decode the same wire error envelopes. In
    # particular a fenced heartbeat becomes ExecutionClaimOwnershipLost.
    monkeypatch.setattr(owners.execution, "client", execution_transport.ExecutionServiceClient(endpoint))
    # Research Residents use quota-aware admission. The generic cross-owner
    # fixture deliberately has an empty account monitor; expose only its
    # registered isolated Codex routes so real admission can find the account.
    from management_portal.execution.accounts import ExecutionAccounts
    from management_portal.execution.account_adapters import CodexAccounts
    accounts = ExecutionAccounts((CodexAccounts(),))
    monkeypatch.setattr(owners.server, "accounts", accounts)
    monkeypatch.setattr(owners.server.preparation, "accounts", accounts)
    monkeypatch.setattr(owners.server.quota_monitor, "accounts", accounts)
    from management_portal import codex_app_server_threads
    monkeypatch.setattr(codex_app_server_threads, "THREAD_LOCK_ROOT", root / "thread-locks")
    transport = RecordedCodexTransport(response_text)
    backend = CodexBackend(client_factory=lambda _socket: transport)
    runtime = ExecutionRuntime(agent_providers={"codex": backend}, state_root=root,
                               agent_data_projector=AgentDataProjector(owners.data))
    scheduler = SchedulerClient(worker_id="isolated:reader-ai", build_id="reader-ai-test",
                                capabilities=runtime.capabilities(),
                                service_client=owners.execution.client)
    worker = ExecutionWorker(worker_id="isolated:reader-ai", build_id="reader-ai-test",
                             scheduler=scheduler, runtime=runtime, state_root=root)
    support = ReaderExecutionSupport(owners, runtime, worker, transport)
    monkeypatch.setenv("PORTAL_SOCIETY_SERVICE_MODE", "local")
    # Production coordinator schedules wakes; invoking resume inline from its
    # commit callback would recursively re-enter completion preparation.
    monkeypatch.setattr(lifecycle, "_WAKE", support.wakeups.add)
    worker.register()
    return support


attach = install_execution_support


# Explicitly runnable contract check in this auxiliary file, so callers need no
# changes to their browser integration test to verify the execution assembly.
from tests.cases.test_cross_owner_storage_integration import owners, ACTOR  # noqa: E402,F401


def test_real_delivery_worker_and_society_completion(owners, monkeypatch, tmp_path):
    import pytest
    from management_portal.society_service import resident_protocol

    support = install_execution_support(owners, monkeypatch, tmp_path)
    accepted = resident_protocol.deliver_resident_input({
        "resident_id": owners.resident_id,
        "message": {"message_id": "msg_" + uuid4().hex, "role": "user",
                    "content_parts": [{"type": "text", "text": "Explain a synthetic sentence."}]},
    }, ACTOR)
    assert accepted
    control = support.run_until_idle(owners.resident_id)
    assert len(support.transport.turns) == 1
    execution = owners.scheduler.snapshot(control["execution_id"])
    assert execution["status"] == "completed"
    assert support.outcomes[-1]["snapshot"]["request"]["agent_id"] == owners.resident_id
    snapshot = resident_protocol.get_resident_conversation_snapshot(owners.resident_id, ACTOR)
    replies = [message for message in snapshot["messages"] if message.get("role") == "agent"]
    assert any(support.transport.response_text in str(message["content_parts"]) for message in replies)
    assert control["input_state"]["state"] == "completed"
    with pytest.raises(execution_transport.ExecutionClaimOwnershipLost,
                       match="execution claim ownership changed"):
        support.worker.scheduler.heartbeat(control["execution_id"], "expired-test-claim")
