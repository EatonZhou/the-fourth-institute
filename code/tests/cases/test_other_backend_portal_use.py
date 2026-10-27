"""Portal use with the Kimi Code and DSH backends.

Found by driving Kimi and DSH Residents through the ISA:

* the account list names the Kimi account ``kimi_default``, and creating a
  Resident with that account must work;
* DSH does not mark its agent messages, so the adapter marks the last one of a
  completed turn as the answer and the others as commentary; otherwise the
  answer shows up twice in Chat.

:module: resident.fork-handoff
:importance: P1
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest import mock

import pytest

from management_portal.execution import backends
from management_portal.execution.accounts import resolve_account_configuration


def test_kimi_account_is_found_by_the_id_the_account_list_shows() -> None:
    """Kimi Code 与 DSH Resident 的 Portal 使用"""
    assert resolve_account_configuration("kimi", "kimi_default")["account_id"] == "kimi_default"
    assert resolve_account_configuration("kimi", "")["account_id"] == "kimi_default"
    with pytest.raises(ValueError, match="Unknown Kimi account"):
        resolve_account_configuration("kimi", "kimi_primary")


def _agent_message(item_id: str, text: str, **extra) -> dict:
    return {"method": "item/completed",
            "params": {"threadId": "dsh-session", "item": {"id": item_id, "type": "agentMessage", "text": text, **extra}}}


def _run(backend_class, events: list[dict], *, fail: bool = False) -> list[dict]:
    observed: list[dict] = []
    context = mock.Mock()
    context.observe_provider_event.side_effect = lambda event, **_kwargs: observed.append(event)

    def run_turn(**kwargs):
        for event in events:
            kwargs["event_callback"](event)
        if fail:
            raise RuntimeError("DSH turn failed")
        return SimpleNamespace(response="final text", session_id="dsh-session", turn_id="turn-1", usage={})

    invocation = SimpleNamespace(
        request=SimpleNamespace(runtime_options={"provider_profile": "deepseek_primary"}, operation="turn",
                                input_media=(), execution_id="execution-dsh", cwd="/work",
                                session=SimpleNamespace(binding_id="resident:resident_dsh"), permission_profile=""),
        session=SimpleNamespace(backend_route_id="deepseek_primary", backend_session_id="dsh-session"),
        user_prompt="hello", system_context="", attach_system_context=False,
    )
    client = SimpleNamespace(run_turn=run_turn, session_status=lambda *a: {}, close_session=lambda *a: {})
    backend = backend_class()
    backend.client_factory = lambda: client  # type: ignore[method-assign]
    with mock.patch("management_portal.execution.native_harness_tools.prepare_tools", return_value=([], None)), \
            mock.patch.object(backend_class, "record_provider_compaction"):
        try:
            backend.execute(invocation, context)
        except RuntimeError:
            pass
    return [
        (event["params"]["item"].get("text", ""), event["params"]["item"].get("phase"))
        for event in observed if event.get("method") == "item/completed"
    ]


TURN = [
    _agent_message("m1", "Looking at the files."),
    {"method": "item/started", "params": {"threadId": "dsh-session", "item": {"id": "t1", "type": "commandExecution"}}},
    {"method": "item/completed", "params": {"threadId": "dsh-session", "item": {"id": "t1", "type": "commandExecution"}}},
    _agent_message("m2", "final text"),
    {"method": "turn/completed", "params": {"threadId": "dsh-session", "turn": {"id": "turn-1", "status": "completed"}}},
]


def test_dsh_last_agent_message_of_a_completed_turn_is_the_answer() -> None:
    """Kimi Code 与 DSH Resident 的 Portal 使用"""
    items = _run(backends.DshBackend, TURN)
    assert [item for item in items if item[0] != ""] == [
        ("Looking at the files.", "commentary"),
        ("final text", "final_answer"),
    ]


def test_dsh_message_of_a_failed_turn_is_not_an_answer() -> None:
    """Kimi Code 与 DSH Resident 的 Portal 使用"""
    items = _run(backends.DshBackend, TURN[:4], fail=True)
    assert ("final text", "commentary") in items


def test_providers_that_mark_phases_pass_through_unchanged() -> None:
    """Kimi Code 与 DSH Resident 的 Portal 使用"""
    items = _run(backends.ClaudeBackend, [_agent_message("m1", "hello", phase="commentary"),
                                          _agent_message("m2", "final text", phase="final_answer")])
    assert items == [("hello", "commentary"), ("final text", "final_answer")]


def _kimi_turn(snapshots: list[tuple[str, str]], *, response: str = "", fail: bool = False) -> list[tuple[str, bool]]:
    """Run a Kimi turn whose client reports step text as replacing snapshots."""

    observed: list[dict] = []
    context = mock.Mock()
    context.is_cancelled.return_value = False
    context.observe_provider_event.side_effect = lambda event, **_kwargs: observed.append(event)

    def run_turn(**kwargs):
        for update_id, text in snapshots:
            kwargs["event_callback"]({"type": "event_msg", "timestamp": "2026-09-30T19:17:23Z", "payload": {
                "type": "agent_message", "message": text, "phase": "commentary",
                "update_id": update_id, "replace_previous": True}})
        if fail:
            raise RuntimeError("Kimi turn failed")
        return SimpleNamespace(response=response, turn_id="p1", session_id="session_kimi", usage={})

    invocation = SimpleNamespace(
        request=SimpleNamespace(session=SimpleNamespace(binding_id="resident:test"), runtime_options={}, permission_profile="", cwd="/work", operation="turn", execution_id="execution-kimi"),
        session=SimpleNamespace(backend_route_id="kimi_primary", backend_session_id="session_kimi"), recovery_checkpoint={},
        system_context="", user_prompt="run pwd", attach_system_context=False, is_new_backend_session=False,
    )
    with mock.patch.object(backends, "shared_kimi_client", return_value=SimpleNamespace(run_turn=run_turn)), \
            mock.patch.object(backends.KimiBackend, "record_provider_compaction"):
        try:
            backends.KimiBackend().execute(invocation, context)  # type: ignore[arg-type]
        except RuntimeError:
            pass
    return [(event["payload"]["message"], event["payload"]["replace_previous"])
            for event in observed if event.get("type") == "event_msg"]


def test_streamed_snapshots_are_replacements_not_completed_messages() -> None:
    """kimi 流式文本快照以替换上一版的方式记录，不当作已完成的消息

    :module: platform.core
    :importance: P2
    """
    answer = "pwd 输出：/work\n\n文件创建成功：`probe`（7 字节）。"
    assert _kimi_turn([("kimi:p1:assistant:0", "pwd 输出：/work\n\n文件创建成功：`probe`（"),
                       ("kimi:p1:assistant:0", answer)], response=answer) == [("pwd 输出：/work\n\n文件创建成功：`probe`（", True), (answer, True)]


def test_the_text_of_an_earlier_step_stays_an_update() -> None:
    """Kimi Code 与 DSH Resident 的 Portal 使用"""
    assert _kimi_turn([("kimi:p1:assistant:0", "先看一下目录。"), ("kimi:p1:assistant:1", "目录是 /work")],
                      response="目录是 /work") == [("先看一下目录。", True), ("目录是 /work", True)]


def test_the_last_step_text_of_a_failed_turn_is_kept() -> None:
    """Kimi Code 与 DSH Resident 的 Portal 使用

    用 Kimi Code 与 DSH Resident 实际使用 Portal 时发现的问题：Kimi 流式回复只在一步完成后出现一次，回答本身不再重复。
    """
    assert _kimi_turn([("kimi:p1:assistant:0", "正在检查")], fail=True) == [("正在检查", True)]


@pytest.mark.parametrize("model,effort,closed", [
    ("deepseek-v4-flash", "", True),    # a changed model needs a new DSH agent
    ("deepseek-v4-pro", "", False),     # the logged header already matches
    ("deepseek-v4-pro", "max", True),   # so does a changed reasoning effort
])
def test_dsh_applies_a_changed_model_by_resuming_with_a_new_agent(tmp_path, monkeypatch, model, effort, closed) -> None:
    """Kimi Code 与 DSH Resident 的 Portal 使用"""
    import json as _json

    from management_portal import dsh_client

    session_id = "dsh-a74bf6395a07cf3bcc0ab3e032665462"
    log = tmp_path / session_id / "sessions" / "--work--" / session_id / "session.v4.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(_json.dumps({"type": "request/header", "seq": 6, "time": 1, "data": {"header": {
        "config": {"provider": "deepseek-official", "model": "deepseek-v4-pro", "reasoningEffort": "high"},
        "adapterDefaults": {"maxTokens": True}}}}) + "\n", encoding="utf-8")
    # The process was started with the subagent tool the Resident allows.
    (tmp_path / session_id / "launch.json").write_text('{"subagents": true}', encoding="utf-8")
    monkeypatch.setattr(dsh_client, "STATE", tmp_path)
    calls: list[tuple] = []
    backend = backends.DshBackend()
    backend.client_factory = lambda: SimpleNamespace(close_session=lambda *args: calls.append(args))  # type: ignore[method-assign]

    backend.prepare_native_session(session_key="resident:resident_dsh", backend_session_id=session_id,
                                   options={"model": model, "effort": effort})

    assert calls == ([("resident:resident_dsh", session_id)] if closed else [])


@pytest.mark.parametrize("backend", ["codex", "claude", "kimi", "dsh"])
def test_every_agent_backend_can_be_forked_from_its_effective_context(backend) -> None:
    """Kimi Code 与 DSH Resident 的 Portal 使用"""
    from management_portal.society_service import resident_execution_provider

    provider = resident_execution_provider.provider_for({"backend": backend, "mode": "new_session"})
    assert provider.capabilities.supports_fork is True


@pytest.mark.parametrize("cancelled", [False, True])
def test_a_kimi_turn_aborted_by_a_service_restart_is_continued_not_asked_again(cancelled) -> None:
    """服务重启中止的 Kimi 轮次以 continue 续接

    Kimi 跟随的 prompt 因服务重启结束且没有回答时，恢复的一次发送 continue 并记录 recovery_input_sent，不重发原 prompt；停止后不再接回。

    Kimi has the prompt already; the recovered attempt sends "continue", never the prompt again."""
    from management_portal.kimi_code_client import KimiCodeTurnFailed

    invocation = SimpleNamespace(
        request=SimpleNamespace(session=SimpleNamespace(binding_id="resident:test"), runtime_options={}, permission_profile="", cwd="/work", operation="turn",
                                execution_id="execution-1"),
        session=SimpleNamespace(backend_route_id="kimi_primary", backend_session_id="session_kimi"),
        recovery_checkpoint={"turn_id": "prompt-7"}, system_context="", user_prompt="sleep 90 then reply done",
        attach_system_context=False, is_new_backend_session=False,
    )
    client = SimpleNamespace(
        resume_turn=mock.Mock(side_effect=KimiCodeTurnFailed("Kimi Code resumed turn completed without a final response")),
        run_turn=mock.Mock(return_value=SimpleNamespace(response="done", turn_id="prompt-8", session_id="session_kimi", usage={})),
    )
    context = mock.Mock()
    context.is_cancelled.return_value = cancelled
    with mock.patch.object(backends, "shared_kimi_client", return_value=client), \
            mock.patch.object(backends.KimiBackend, "record_provider_compaction"), \
            mock.patch.object(backends.KimiBackend, "settle_portal_tools", return_value=[]):
        if cancelled:
            # A stop before the attach starts nothing.
            with pytest.raises(InterruptedError):
                backends.KimiBackend().execute(invocation, context)  # type: ignore[arg-type]
            client.resume_turn.assert_not_called()
            client.run_turn.assert_not_called()
        else:
            result = backends.KimiBackend().execute(invocation, context)  # type: ignore[arg-type]
            assert result.output["message"] == "done"
            client.resume_turn.assert_not_called()
            request = client.run_turn.call_args.kwargs
            assert request["turn_id"] == "prompt-7"
            assert request["prompt"] == invocation.user_prompt
            assert request["continue_prompt"] == "continue"


def test_restarted_session_is_created_where_the_residents_turns_look_for_it(monkeypatch) -> None:
    """API 路由上的 claude 重启会话时，新会话建在 Resident 后续轮次读取的运行目录中

    Claude Code on an API route keeps sessions in a per-Resident directory.

    A session restart prepares the new session under a temporary binding; it
    must use the Resident's runtime root, or the next turn cannot resume it.
    """
    from pathlib import Path

    from management_portal.society_service import resident_execution_domain, resident_restart_domain

    record = {"resident_id": "resident_0123456789ab", "title": "Probe", "lineage_id": ""}
    monkeypatch.setattr(
        "management_portal.society_service.resident_record_domain.resident_folder_path",
        lambda _record: Path("/srv/residents/resident_0123456789ab"),
    )
    restart = resident_restart_domain._session_restart_runtime_options(
        record, "claude", "sample_admin", runtime_settings={"model": "x"}, target_route="deepseek_primary",
    )
    assert restart["runtime_root"] == "/srv/residents/resident_0123456789ab/runtime/backend"
    source = inspect.getsource(resident_execution_domain.resident_execution_runtime_options)
    assert '"runtime_root": str(resident_record_domain.sessions_path(record) / "backend")' in source  # the same root for turns
