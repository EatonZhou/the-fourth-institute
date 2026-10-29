"""A Fork's inherited context reaches its Provider session exactly once.

A Fork's first turn carries the source's effective context (the seed).  When
a Worker handover recovers that turn, the Provider session already holds the
seed; sending it again adds the whole inherited context once more per
recovery until Provider compaction can no longer make room.  Compactions made
during a turn that then fails are recorded too, and a session restart hands
the last compaction summary to the new session.

:module: resident.fork-handoff
:importance: P1
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

import pytest

from management_portal.execution import backends
from management_portal.execution.agent_data_session_capture import persist_provider_compaction
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.backends import BackendInvocation
from management_portal.execution.context_seed import CONTEXT_SEED_SCHEMA, realize_portable_seed
from management_portal.execution.requests import build_agent_turn_request
from management_portal.execution.session_manager import RuntimeSession
from management_portal.society_service import resident_restart_domain

SEEDED_PROMPT = "<portable_full_context>inherited window</portable_full_context>\n\ncontinue the task"


def _seeded_invocation(recovery_checkpoint: dict[str, str] | None = None) -> BackendInvocation:
    request = build_agent_turn_request(
        actor="sample_admin",
        prompt="continue the task",
        provider="claude",
        correlation_id="fork-seed-recovery",
        cwd="/tmp/fork-seed-recovery",
        binding_id="resident:fork-seed-recovery",
        metadata={"fork_context_input": {
            "schema": CONTEXT_SEED_SCHEMA,
            "segments": [{"segment_id": "segment-source-window"}],
            "prompt": "continue the task",
            "portable_prompt": SEEDED_PROMPT,
        }},
    )
    return BackendInvocation(
        request=request,
        session=RuntimeSession(
            mode="persistent", binding_id="resident:fork-seed-recovery",
            backend_session_id="", is_new_backend_session=True,
        ),
        user_prompt=request.prompt,
        system_context="",
        attach_system_context=False,
        is_new_backend_session=True,
        recovery_checkpoint=dict(recovery_checkpoint or {}),
    )


def test_first_attempt_sends_the_seed() -> None:
    """Fork 继承的上下文只送达 Provider 一次，失败轮次的压缩照样记录"""
    assert realize_portable_seed(_seeded_invocation()).user_prompt == SEEDED_PROMPT


def test_recovered_turn_resumes_without_sending_the_seed_again() -> None:
    """Fork 继承的上下文只送达 Provider 一次，失败轮次的压缩照样记录"""
    recovered = _seeded_invocation({"turn_id": "turn-1", "backend_session_id": "claude-session-1"})
    assert realize_portable_seed(recovered).user_prompt == "continue the task"


def test_attempt_that_never_reached_the_provider_still_sends_the_seed() -> None:
    """Fork 继承的上下文只送达 Provider 一次，失败轮次的压缩照样记录"""
    # The Provider session did not exist yet, so nothing holds the seed.
    unstarted = _seeded_invocation({"turn_id": "turn-1", "backend_session_id": ""})
    assert realize_portable_seed(unstarted).user_prompt == SEEDED_PROMPT


def test_compactions_of_a_failed_turn_are_recorded() -> None:
    """Fork 继承的上下文只送达 Provider 一次，失败轮次的压缩照样记录"""
    invocation = _seeded_invocation()
    invocation = replace(invocation, request=replace(
        invocation.request, runtime_options={"provider_profile": "claude_one"},
    ))

    def run_turn(**kwargs):
        kwargs["event_callback"]({
            "method": "turn/started",
            "params": {"threadId": "claude-session-1", "turn": {"id": "turn-1"}},
        })
        raise RuntimeError("Claude Code turn failed: Autocompact is thrashing")

    client = SimpleNamespace(run_turn=run_turn)
    with mock.patch.object(backends, "shared_claude_client", return_value=client), \
            mock.patch("management_portal.execution.native_harness_tools.prepare_tools",
                       return_value=([], None)), \
            mock.patch.object(backends.ClaudeBackend, "record_provider_compaction") as record:
        with pytest.raises(RuntimeError, match="thrashing"):
            backends.ClaudeBackend().execute(invocation, mock.Mock())

    record.assert_called_once()
    assert record.call_args.kwargs["backend_session_id"] == "claude-session-1"


def test_latest_context_compaction_is_the_sessions_newest_compaction() -> None:
    """Fork 继承的上下文只送达 Provider 一次，失败轮次的压缩照样记录"""
    store = AgentDataStore()
    agent_id = "resident_fork_seed_recovery"
    session = store.ensure_provider_session(
        provider="claude", backend_route_id="claude_one",
        backend_session_id="claude-session-summary", agent_id=agent_id, actor_ref="sample_admin",
    )
    store.begin_execution(
        execution_id="execution-fork-seed-recovery", agent_id=agent_id,
        actor_ref="sample_admin", provider="claude", started_at="2026-09-30T10:00:00Z",
    )
    store.bind_execution_provider_session(
        "execution-fork-seed-recovery", provider_session_ref=session["provider_session_ref"],
    )
    assert store.latest_context_compaction(agent_id) is None

    summary = {"replacement_history": [{"type": "message", "role": "user",
                                        "content": [{"type": "input_text", "text": "Progress so far."}]}]}
    segment_id, _created = persist_provider_compaction(
        store, provider_session_ref=session["provider_session_ref"], agent_id=agent_id,
        operation_id="execution-fork-seed-recovery", actor_ref="sample_admin", provider="claude",
        content=summary, created_at="2026-09-30T10:05:00Z",
    )

    compaction = store.latest_context_compaction(agent_id)
    assert compaction is not None
    assert compaction["segment_id"] == segment_id
    assert compaction["content"] == summary


def test_restart_handoff_carries_the_previous_compaction_summary(monkeypatch) -> None:
    """Fork 继承的上下文只送达 Provider 一次，失败轮次的压缩照样记录"""
    summary = {"content": {"replacement_history": [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Kept request."}]},
        {"type": "compaction", "encrypted_content": "opaque"},
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Summary of all work."}]},
    ]}}
    requested = []
    monkeypatch.setattr(resident_restart_domain, "execution_port", SimpleNamespace(
        latest_context_compaction=lambda resident_id, since="": requested.append(resident_id) or summary,
    ))

    prompt = resident_restart_domain._session_restart_handoff_prompt(
        {"resident_id": "resident_aaaaaaaaaaaa", "codex_goal": {"objective": "polish specs"}},
        {"messages": [{"role": "agent", "content_parts": [{"type": "text", "text": "visible progress"}]}]},
        strategy="compact",
    )

    assert requested == ["resident_aaaaaaaaaaaa"]
    assert "Kept request.\n\nSummary of all work." in prompt
    assert "opaque" not in prompt
    assert prompt.index("Summary of all work.") < prompt.index("--- 可移植对话记录 ---")


def test_restart_handoff_without_a_compaction_is_unchanged(monkeypatch) -> None:
    """Fork 继承的上下文只送达 Provider 一次，失败轮次的压缩照样记录"""
    monkeypatch.setattr(resident_restart_domain, "execution_port", SimpleNamespace(
        latest_context_compaction=lambda _resident_id, since="": None,
    ))
    prompt = resident_restart_domain._session_restart_handoff_prompt(
        {"resident_id": "resident_aaaaaaaaaaaa", "codex_goal": {"objective": "polish specs"}},
        {"messages": [{"role": "agent", "content_parts": [{"type": "text", "text": "visible progress"}]}]},
        strategy="compact",
    )
    assert "压缩摘要" not in prompt


class _DaemonClient:
    def __init__(self, loaded: str, *, active: bool = False) -> None:
        self.loaded, self.active, self.closed = loaded, active, []

    def session_status(self, session_key: str, session_id: str = "") -> dict:
        return {"loaded": bool(self.loaded), "active": self.active,
                "session_key": session_key, "session_id": self.loaded or session_id}

    def close_session(self, session_key: str, session_id: str = "") -> dict:
        self.closed.append((session_key, session_id))
        return {"closed": True}


@pytest.mark.parametrize("loaded,active,requested,closed", [
    ("claude-session-old", False, "claude-session-new", True),   # stale idle session is replaced
    ("claude-session-new", False, "claude-session-new", False),  # already the requested session
    ("claude-session-old", True, "claude-session-new", False),   # never close a running turn
    ("claude-session-old", False, "", False),                    # a new session has nothing to align
])
def test_claude_turn_runs_in_the_requested_session(loaded, active, requested, closed) -> None:
    """会话重启真正换到新会话，并交接未绑定首轮留下的工作"""
    client = _DaemonClient(loaded, active=active)
    backend = backends.ClaudeBackend()
    backend.client_factory = lambda: client  # type: ignore[method-assign]

    backend.prepare_native_session(session_key="resident:resident_x", backend_session_id=requested)

    assert client.closed == ([("resident:resident_x", "claude-session-old")] if closed else [])


def test_restart_after_a_failed_unbound_first_turn_hands_its_work_over(monkeypatch) -> None:
    """会话重启真正换到新会话，并交接未绑定首轮留下的工作"""
    from tests.cases.test_session_restart import _restart_domain
    from management_portal.society_service import resident_backend, resident_domain

    record, operations, _saved = _restart_domain(monkeypatch)
    monkeypatch.setattr(resident_backend, "session_record", lambda *_a, **_kw: {})
    monkeypatch.setattr(resident_restart_domain.execution_port, "latest_provider_session_ref",
                        lambda _resident_id, since="": "provider_session_failed_first_turn", raising=False)
    monkeypatch.setattr(resident_restart_domain.execution_port, "latest_context_compaction",
                        lambda _resident_id, since="": None, raising=False)

    with pytest.raises(ValueError, match="尚未绑定"):
        resident_domain.restart_resident_session(
            record["resident_id"], "alice", {"context_strategy": "native"},
        )
    result = resident_domain.restart_resident_session(record["resident_id"], "alice", {})

    assert not result.get("configuration_only")
    # A restart keeps the full effective context by default (decision_ccbb1cac24aa): the new
    # session starts empty and receives the unbound turn's work as its seed, with no model handoff turn.
    assert result["context_strategy"] == "preserve"
    assert operations[0]["operation"] == "create" and not operations[0]["source_binding_id"]
    seed = operations[0]["step"]["request"]["metadata"]["fork_context_input"]
    assert seed["segments"] and seed["empty_context"] is False


def test_restart_of_a_resident_that_never_had_a_session_only_configures(monkeypatch) -> None:
    """会话重启真正换到新会话，并交接未绑定首轮留下的工作"""
    from tests.cases.test_session_restart import _restart_domain
    from management_portal.society_service import resident_backend, resident_domain

    record, operations, _saved = _restart_domain(monkeypatch)
    monkeypatch.setattr(resident_backend, "session_record", lambda *_a, **_kw: {})
    monkeypatch.setattr(resident_restart_domain.execution_port, "latest_provider_session_ref",
                        lambda _resident_id, since="": "", raising=False)

    result = resident_domain.restart_resident_session(record["resident_id"], "alice", {})

    assert result["configuration_only"] is True
    assert operations == []


class _FailingClaude:
    provider_id = "claude"

    def __init__(self, *, started_session: str) -> None:
        self.started_session = started_session

    def prepare_context_seed(self, invocation):
        return invocation

    def execute(self, invocation, context):
        if self.started_session:
            context.set_interrupt_target(self.started_session, "turn-1")
        raise RuntimeError("Claude Code turn interrupted")

    def interrupt(self, **_kwargs) -> bool:
        return False


def _fail_first_turn(tmp_path, *, started_session: str):
    from management_portal.execution.agent_data_projection import AgentDataProjector
    from management_portal.execution.contracts import ExecutionRequest, SessionOptions
    from management_portal.execution.harness import AgentHarness
    from management_portal.execution.runtime import ExecutionRuntime, RuntimeContext
    from management_portal.execution.session_manager import SessionManager

    store = AgentDataStore()
    sessions = SessionManager(agent_data=store)
    runtime = ExecutionRuntime(
        agent_providers={"claude": _FailingClaude(started_session=started_session)},
        agent_harness=AgentHarness(), session_manager=sessions,
        agent_data_projector=AgentDataProjector(store), state_root=tmp_path,
    )
    binding_id = "resident:resident_failed_first_turn"
    request = ExecutionRequest.new(
        service_type="agent_session", provider="claude", operation="turn", actor="sample_admin",
        agent_id="resident_failed_first_turn", cwd=str(tmp_path), prompt="inherited context and task",
        session=SessionOptions(mode="persistent", binding_id=binding_id, backend_route_id="claude_one"),
        runtime_options={"provider_profile": "claude_one"},
    ).validate()
    with pytest.raises(RuntimeError, match="interrupted"):
        runtime.execute(request, RuntimeContext(accepted_at="2026-09-30T13:33:35Z"))
    return sessions.provider_binding(binding_id)


def test_a_failed_first_turn_keeps_the_session_it_started(tmp_path) -> None:
    """首轮失败或被中断时保留 Provider 已开启的会话"""
    binding = _fail_first_turn(tmp_path, started_session="claude-session-started")
    assert binding["backend_session_id"] == "claude-session-started"


def test_a_turn_that_failed_before_the_provider_opened_a_session_binds_nothing(tmp_path) -> None:
    """首轮失败或被中断时保留 Provider 已开启的会话"""
    assert not _fail_first_turn(tmp_path, started_session="")


def _codex_compaction(fingerprint: str):
    from management_portal.execution.codex_rollout_history import CodexCompaction

    return CodexCompaction(
        replacement_history=({"type": "message", "role": "user",
                              "content": [{"type": "input_text", "text": f"summary {fingerprint}"}]},),
        fingerprint=fingerprint,
        created_at="2026-09-26T08:09:12.951Z",
    )


@pytest.mark.parametrize("latest,recorded", [("f-new", True), ("f-old", False)])
def test_codex_compaction_of_an_unfinished_turn_is_recorded(latest, recorded) -> None:
    """Codex 一轮未完成时的压缩照样记录，并保留压缩之后的工作"""
    backend = backends.CodexBackend.__new__(backends.CodexBackend)
    backend._latest_compaction = lambda _route, _thread: _codex_compaction(latest)  # type: ignore[method-assign]
    calls: list[dict] = []
    context = SimpleNamespace(record_compaction=lambda **kwargs: calls.append(kwargs))
    route = SimpleNamespace(account_id="three")

    with pytest.raises(InterruptedError):
        with backend._compaction_recorded_on_failure(
            context=context, route=route,  # type: ignore[arg-type]
            thread_id=lambda: "thread-cancelled",
            previous_compaction=lambda: _codex_compaction("f-old"),
        ):
            raise InterruptedError("execution cancelled during Backend call")

    assert [(call["backend_session_id"], call["created_at"]) for call in calls] == (
        [("thread-cancelled", "2026-09-26T08:09:12.951Z")] if recorded else []
    )
    if recorded:
        # What the turn did after a mid-turn compaction stays after it.
        assert calls[0]["retain_from"] == "2026-09-26T08:09:12.951Z"
        assert calls[0]["provider_compaction_ref"] == "f-new"


def test_codex_completed_turn_is_left_to_its_own_recording() -> None:
    """Codex 一轮未完成时的压缩照样记录，并保留压缩之后的工作"""
    backend = backends.CodexBackend.__new__(backends.CodexBackend)
    backend._latest_compaction = lambda *_a: pytest.fail("a completed turn records afterwards")  # type: ignore[method-assign]
    with backend._compaction_recorded_on_failure(
        context=SimpleNamespace(), route=SimpleNamespace(account_id="three"),  # type: ignore[arg-type]
        thread_id=lambda: "thread-done", previous_compaction=lambda: None,
    ):
        pass
