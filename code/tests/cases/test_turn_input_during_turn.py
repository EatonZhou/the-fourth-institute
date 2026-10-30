"""Input that arrives while a Claude Code, DSH or Kimi Code turn runs.

Send now and the loop governor's reminders reach a running turn wherever the
Provider takes input between the steps of a turn.  Codex does it through its
app server; the other Adapters run their turn as one blocking call, and a
``TurnInputPump`` beside that call hands the input to the Provider.

:module: resident.fork-handoff
:importance: P1
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from management_portal import kimi_account_runtime, kimi_code_client
from management_portal.execution import claude_backend, dsh_backend, kimi_backend
from management_portal.execution.contracts import (
    EXECUTION_INPUT_CONTROL_SCHEMA,
    EXECUTION_TURN_SETTINGS_CONTROL_SCHEMA,
)
from management_portal.execution.harness_capabilities import ACTIVE_TURN_INPUT
from management_portal.execution.harness_policy_table import policy_parameters
from management_portal.execution.provider_catalog import AGENT_PROVIDERS
from management_portal.execution.turn_input_pump import TurnInputPump
from tests.cases import capability_contracts


def _send_now(control_id: str = "control-1", text: str = "also check the tests", turn: str = "") -> dict:
    return {
        "schema": EXECUTION_INPUT_CONTROL_SCHEMA,
        "control_id": control_id,
        "execution_id": "execution-1",
        "actor": "sample_admin",
        "input": [{"type": "text", "text": text}],
        "expected_turn_id": turn,
    }


class _Context:
    """The parts of a BackendContext the pump uses."""

    def __init__(self, *controls: dict) -> None:
        self.controls = list(controls)
        self.results: list[tuple[str, bool, str]] = []
        self.polled = 0

    def has_turn_control_provider(self) -> bool:
        return True

    def next_turn_control(self):
        self.polled += 1
        return self.controls.pop(0) if self.controls else None

    def record_turn_control_result(self, control, delivered, error):
        self.results.append((control.get("control_id"), delivered, error))


class _Governor:
    def __init__(self, *controls: dict) -> None:
        self.controls = list(controls)
        self.results: list[tuple[str, bool, str]] = []

    def next_control(self):
        return self.controls.pop(0) if self.controls else None

    def record_control_result(self, control, applied, error):
        self.results.append((control.get("control_id"), applied, error))


def test_send_now_reaches_the_running_turn_and_is_receipted() -> None:
    """Send now 送入运行中的轮次并留下回执

    用户 Send now 的输入进入运行中的轮次，并记录送达回执。
    """
    delivered: list[tuple] = []
    context = _Context(_send_now(turn="turn-1"))
    pump = TurnInputPump(context=context, event_processor=None,
                         deliver=lambda *args: (delivered.append(args), (True, ""))[1])

    # Nothing is taken before the Provider runs the turn: it waits in the queue.
    assert pump.pump_once() is False and context.polled == 0
    pump.turn_started("turn-1", "execution-1")
    assert pump.pump_once() is True

    assert delivered == [("turn-1", [{"type": "text", "text": "also check the tests"}], "control-1")]
    assert context.results == [("control-1", True, "")]


def test_input_for_another_turn_or_after_the_turn_is_not_delivered() -> None:
    """给其他轮次或轮次结束后的输入不送达

    轮内输入只送入目标轮次，轮次已结束或属于其他轮次时不送达。
    """
    deliver = mock.Mock(return_value=(True, ""))
    context = _Context(_send_now("control-1", turn="turn-0"), _send_now("control-2"))
    pump = TurnInputPump(context=context, event_processor=None, deliver=deliver)
    pump.turn_started("turn-1", "execution-1")

    assert pump.pump_once() is True
    assert context.results == [("control-1", False, "active provider turn changed")]
    pump.turn_ended()
    # After the turn the input stays queued; Send now then runs it as the next turn.
    assert pump.pump_once() is False
    deliver.assert_not_called()
    assert context.controls == [_send_now("control-2")]


def test_what_the_provider_cannot_take_fails_back_to_its_source() -> None:
    """Provider 无法接收的输入退回来源

    Provider 不能接收的轮内输入被标记失败并退回其来源队列。
    """
    settings = {"schema": EXECUTION_TURN_SETTINGS_CONTROL_SCHEMA, "control_id": "settings-1",
                "settings": {"service_tier": "fast"}}
    reply = {**_send_now("reply-1"), "input": [{"type": "user_input_response", "answers": {}}]}
    refused = _send_now("refused-1")
    context = _Context(settings, reply, refused)
    deliver = mock.Mock(return_value=(False, "turn_closing"))
    pump = TurnInputPump(context=context, event_processor=None, deliver=deliver)
    pump.turn_started("turn-1")
    while pump.pump_once():
        pass

    assert context.results == [
        ("settings-1", False, "turn_settings_unsupported"),
        ("reply-1", False, "user_input_response_unsupported"),
        ("refused-1", False, "turn_closing"),
    ]
    assert deliver.call_count == 1


def test_a_governor_reminder_is_delivered_after_send_now_and_receipted_to_the_governor() -> None:
    """守卫提醒在 Send now 之后送达并回执给守卫

    Loop governor 的提醒排在用户 Send now 之后送入运行中的轮次，并向守卫回执。
    """
    reminder = {"control_id": "native-checkpoint-0", "reason": "failure_loop_pressure",
                "input": [{"type": "text", "text": "<checkpoint/>"}], "metadata": {"kind": "safety"}}
    context = _Context(_send_now())
    governor = _Governor(reminder)
    order: list[str] = []
    pump = TurnInputPump(context=context, event_processor=governor,
                         deliver=lambda _turn, _items, control_id: (order.append(control_id), (True, ""))[1])
    pump.turn_started("turn-1")
    while pump.pump_once():
        pass

    assert order == ["control-1", "native-checkpoint-0"]
    assert context.results == [("control-1", True, "")]
    assert governor.results == [("native-checkpoint-0", True, "")]


def test_a_failing_provider_call_counts_as_not_delivered() -> None:
    """Provider 调用失败视为未送达

    轮内输入提交给 Provider 失败时，该输入记为未送达。
    """
    context = _Context(_send_now())

    def deliver(*_args):
        raise TimeoutError("daemon busy")

    pump = TurnInputPump(context=context, event_processor=None, deliver=deliver)
    pump.turn_started("turn-1")
    pump.pump_once()
    assert context.results == [("control-1", False, "TimeoutError")]


@pytest.mark.parametrize("provider", sorted(AGENT_PROVIDERS))
def test_every_backend_takes_input_during_a_turn(provider: str) -> None:
    """每个 Backend 都能在轮内接收输入

    所有 Backend 都声明并实现了轮内输入，Design 说明不声明的除外（capability_contracts.WITHHELD）。
    """
    declared = ACTIVE_TURN_INPUT in AGENT_PROVIDERS[provider].harness_capabilities()
    assert declared != capability_contracts.withheld(provider, ACTIVE_TURN_INPUT)


@pytest.mark.parametrize("provider", ["claude", "dsh", "kimi"])
def test_read_batch_advice_stays_the_measured_prompt_policy(provider: str) -> None:
    """工具粒度提醒只在 Codex 轮内发送

    按实测的提示策略，工具调用粒度提醒只在 Codex 轮内发送，其他 Backend 的轮内不发送。
    """
    assert policy_parameters(provider).granularity_reminders_during_turn is False
    assert policy_parameters("codex").granularity_reminders_during_turn is True


def _native_invocation():
    return SimpleNamespace(
        request=SimpleNamespace(
            runtime_options={"provider_profile": "deepseek_primary", "tools": [], "session_key": "resident:resident_x"},
            operation="turn", input_media=(), execution_id="execution-1", cwd="/work", permission_profile="",
            session=SimpleNamespace(binding_id="resident:resident_x"),
        ),
        session=SimpleNamespace(backend_route_id="deepseek_primary", backend_session_id="dsh-session-1"),
        user_prompt="audit the module", system_context="", attach_system_context=False,
        is_new_backend_session=False, recovery_checkpoint={}, provider_error_recovery=None,
        provider_event_processor=None, harness_tools=(), tool_batch_processor=None,
    )


def _native_context(*controls: dict) -> mock.Mock:
    queued = list(controls)
    context = mock.Mock()
    context.is_cancelled.return_value = False
    context.execution_started_at = ""
    context.has_turn_control_provider.return_value = True
    context.next_turn_control.side_effect = lambda: queued.pop(0) if queued else None
    return context


@pytest.mark.parametrize("backend_class", [dsh_backend.DshBackend, claude_backend.ClaudeBackend])
def test_a_native_turn_takes_send_now_while_it_runs(backend_class) -> None:
    """原生轮次运行中接收 Send now

    Claude Code 等原生 Backend 的轮次运行时可以接收 Send now 输入。
    """
    steered = threading.Event()
    steers: list[dict] = []

    def run_turn(**kwargs):
        kwargs["event_callback"]({"method": "turn/started",
                                  "params": {"threadId": "dsh-session-1", "turn": {"id": kwargs["turn_id"]}}})
        assert steered.wait(5), "the pump did not deliver the input while the turn ran"
        kwargs["event_callback"]({"method": "turn/completed",
                                  "params": {"threadId": "dsh-session-1", "turn": {"id": kwargs["turn_id"]}}})
        return SimpleNamespace(response="done, tests checked", session_id="dsh-session-1",
                               turn_id=kwargs["turn_id"], usage={})

    def steer(session_key, session_id, **kwargs):
        steers.append({"session_key": session_key, "session_id": session_id, **kwargs})
        steered.set()
        return {"steered": True}

    backend = backend_class()
    backend.client_factory = lambda: SimpleNamespace(run_turn=run_turn, steer=steer, close_session=lambda *a: {})  # type: ignore[method-assign]
    context = _native_context(_send_now(turn="execution-1"))
    with mock.patch("management_portal.execution.native_harness_tools.prepare_tools", return_value=([], None)), \
            mock.patch.object(backend_class, "record_provider_compaction"), \
            mock.patch.object(backend_class, "observe_turn_usage", return_value=None), \
            mock.patch.object(backend_class, "prepare_native_session"):
        result = backend.execute(_native_invocation(), context)

    assert result.output["message"] == "done, tests checked"
    assert steers == [{
        "session_key": "resident:resident_x", "session_id": "dsh-session-1", "turn_id": "execution-1",
        "text": "also check the tests", "control_id": "control-1",
    }]
    context.record_turn_control_result.assert_called_once()
    control, delivered, error = context.record_turn_control_result.call_args.args
    assert (control["control_id"], delivered, error) == ("control-1", True, "")


class _KimiServer:
    """Kimi Code's prompt routes, as far as steering uses them."""

    def __init__(self, *, active: str = "prompt-1", submitted_status: str = "queued", steer_fails: bool = False):
        self.active = active
        self.submitted_status = submitted_status
        self.steer_fails = steer_fails
        self.calls: list[tuple[str, str, dict | None]] = []

    def __call__(self, method, path, payload=None, **_kwargs):
        self.calls.append((method, path, payload))
        if method == "GET" and path.endswith("/prompts"):
            return {"active": {"prompt_id": self.active} if self.active else None, "queued": []}
        if method == "POST" and path.endswith("/prompts"):
            return {"prompt_id": payload["prompt_id"], "user_message_id": "user-msg-2",
                    "status": self.submitted_status, "content": payload["content"]}
        if path.endswith(":steer"):
            if self.steer_fails:
                raise kimi_code_client.KimiCodeError("Kimi Code error 40400: no active prompt to steer into")
            return {"steered": True}
        if path.endswith(":abort"):
            return {"aborted": True}
        raise AssertionError(path)


def _kimi_steer(server: _KimiServer, prompt_id: str = "prompt-1") -> dict:
    kimi_code_client._STEERED.clear()
    client = kimi_code_client.KimiCodeClient()
    with mock.patch.object(client, "_request", side_effect=server):
        return client.steer("session-1", prompt_id=prompt_id, text="also check the tests", control_id="control-9")


def test_kimi_steers_a_queued_prompt_into_the_running_one() -> None:
    """Kimi 把排队提示插入运行中的提示

    Kimi Code 把排队中的提示作为插话送入正在运行的提示。
    """
    server = _KimiServer()
    assert _kimi_steer(server)["steered"] is True
    actions = [path.rsplit("/", 1)[-1] for _method, path, _payload in server.calls]
    assert actions == ["prompts", "prompts", "portal-steer-prompt-1-control9:steer"]
    assert server.calls[1][2]["content"] == [{"type": "text", "text": "also check the tests"}]


@pytest.mark.parametrize("server, reason", [
    (_KimiServer(active="prompt-0"), "no_active_turn"),
    (_KimiServer(submitted_status="running"), "turn_closing"),
    (_KimiServer(steer_fails=True), "turn_closing"),
])
def test_kimi_input_that_cannot_join_the_running_turn_does_not_run_on_its_own(server, reason) -> None:
    """不能并入当前轮的 Kimi 输入不单独运行

    Kimi 的插话无法并入运行中的轮次时，不会被当作新的轮次单独执行。
    """
    result = _kimi_steer(server)
    assert result == {"steered": False, "reason": reason}
    submitted = any(method == "POST" and path.endswith("/prompts") for method, path, _ in server.calls)
    aborted = any(path.endswith(":abort") for _method, path, _ in server.calls)
    # A submitted prompt that did not join the turn is aborted, or Kimi Code
    # would run it as a turn of its own after this one.
    assert aborted == submitted


def test_a_kimi_turn_answer_includes_what_followed_steered_input() -> None:
    """Kimi 轮次的回答包含插话之后的内容

    插话进入 Kimi 运行中的提示后，本轮回答包含插话之后产生的输出。
    """
    from management_portal.kimi_live_events import KimiLiveItems
    events = []
    live = KimiLiveItems("s", events.append, prompt_id="prompt-7")
    for kind, payload in [
        ("turn.started", {}), ("assistant.delta", {"delta": "Reading the module."}),
        ("turn.step.completed", {}), ("turn.step.started", {"step": 2}),
        ("assistant.delta", {"delta": "Module and tests audited."}),
        ("turn.ended", {"reason": "completed"}),
    ]:
        live.observe({"type": kind, "session_id": "s", "epoch": "e", "payload": {"turnId": 7, **payload}})
    assert live.response == "Module and tests audited."
    assert all(event["params"]["turnId"] == "prompt-7" for event in events)

KIMI_HOME = Path("/nonexistent/kimi-account")


def test_kimi_backend_hands_send_now_to_the_running_prompt() -> None:
    """Kimi Backend 把 Send now 交给运行中的提示

    Kimi Code 后端把 Send now 输入并入正在运行的提示。
    """
    steered = threading.Event()
    steers: list[dict] = []

    def run_turn(**kwargs):
        kwargs["event_callback"]({"method": "turn/started",
                                  "params": {"thread": {"id": "session-1"}, "turn": {"id": "prompt-1"}}})
        assert steered.wait(5)
        return SimpleNamespace(response="done", session_id="session-1", turn_id="prompt-1", usage={})

    def steer(session_key, session_id, **kwargs):
        steers.append({"session_id": session_id, **kwargs})
        steered.set()
        return {"steered": True}

    client = SimpleNamespace(home=KIMI_HOME, run_turn=run_turn, steer=steer)
    backend = kimi_backend.KimiBackend()
    invocation = _native_invocation()
    invocation.request.runtime_options = {"tools": [], "model": "kimi-k2.7-code"}
    invocation.session.backend_session_id = "session-1"
    invocation.session.backend_route_id = "kimi_primary"
    context = _native_context(_send_now(turn="prompt-1"))
    # The session's route names a Kimi account; its client is the fake.
    with mock.patch.object(kimi_account_runtime, "client_for_route", return_value=client), \
            mock.patch.object(kimi_account_runtime, "remember_session"), \
            mock.patch.object(kimi_backend.KimiBackend, "offer_portal_tools", return_value=([], None)), \
            mock.patch.object(kimi_backend.KimiBackend, "settle_portal_tools", return_value=[]), \
            mock.patch.object(kimi_backend.KimiBackend, "record_provider_compaction"), \
            mock.patch.object(kimi_backend.KimiBackend, "observe_turn_usage", return_value=None), \
            mock.patch.object(kimi_backend, "KimiToolGateway"):
        result = backend.execute(invocation, context)

    assert result.output["message"] == "done"
    assert steers == [{"session_id": "session-1", "turn_id": "prompt-1",
                       "text": "also check the tests", "control_id": "control-1"}]
    control, delivered, _error = context.record_turn_control_result.call_args.args
    assert control["control_id"] == "control-1" and delivered is True


def test_the_pump_stops_with_the_turn() -> None:
    """输入泵随轮次结束而停止

    负责轮内输入的泵在轮次结束时停止，不再转发输入。
    """
    context = _Context()
    pump = TurnInputPump(context=context, event_processor=None, deliver=lambda *a: (True, ""), poll_seconds=0.01)
    started = time.monotonic()
    with pump:
        pump.turn_started("turn-1")
        time.sleep(0.05)
    assert time.monotonic() - started < 5
    assert pump._thread is not None and not pump._thread.is_alive()


def _latency_runtime(now: list[float]):
    from management_portal.execution.harness_native_loop import NativeToolLoopRuntime

    return NativeToolLoopRuntime(
        checkpoint_interval=500, max_checkpoints=5, duplicate_read_threshold=100,
        large_output_bytes=16 * 1024 * 1024, granularity_strategy="off", fragmented_read_batch_threshold=4,
        discovery_target_threshold=4, confirm_singleton_reads=2, max_granularity_steers=1,
        provider_latency_threshold_seconds=600, monotonic=lambda: now[0], emit=lambda _details: None,
    )


def test_a_provider_that_writes_without_tools_is_not_silent() -> None:
    """只写文本不调用工具的 Provider 不算沉默

    Now that reminders reach Claude Code, DSH and Kimi Code during a turn,
    output other than tool events must count as progress.

    :importance: P2
    """

    from management_portal.execution.native_tool_events import provider_activity

    now = [0.0]
    runtime = _latency_runtime(now)
    for _ in range(4):
        now[0] += 300
        runtime.process_event(provider_activity())
        assert runtime.next_control() is None
    now[0] += 601
    control = runtime.next_control()
    assert control is not None and control["reason"] == "provider_latency_pressure"


def test_a_running_tool_holds_the_silence_watch_until_its_result() -> None:
    """运行中的工具使静默检测等待其结果

    工具开始后到结果返回前，即使其间只有文字活动，也不触发 Provider 静默提醒；结果返回后静默检测恢复计时。

    :importance: P2
    :design: decision_5c62deb8253a
    """
    from management_portal.execution.native_tool_events import provider_activity, tool_completed, tool_started

    now = [0.0]
    runtime = _latency_runtime(now)
    runtime.process_event(tool_started("t1", "Bash", {"command": "sleep 1800"}))
    now[0] += 900
    runtime.process_event(provider_activity())  # text says nothing about the tool
    now[0] += 900
    assert runtime.next_control() is None
    runtime.process_event(tool_completed("t1", "Bash", {"command": "sleep 1800"}, "done"))
    now[0] += 601
    assert runtime.next_control()["reason"] == "provider_latency_pressure"


class _Observed:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def process_event(self, event):
        self.events.append(event)

    def next_control(self):
        return None

    def record_control_result(self, *_args):
        pass


@pytest.mark.parametrize("backend_class", [dsh_backend.DshBackend, claude_backend.ClaudeBackend])
def test_native_text_and_reasoning_reach_the_governor_as_activity(backend_class) -> None:
    """原生文本和推理作为活动报告给守卫

    :importance: P2
    """
    def run_turn(**kwargs):
        emit = kwargs["event_callback"]
        emit({"method": "turn/started", "params": {"threadId": "dsh-session-1", "turn": {"id": kwargs["turn_id"]}}})
        emit({"method": "item/agentMessage/delta", "params": {"delta": "thinking aloud"}})
        emit({"method": "item/started", "params": {"item": {"type": "commandExecution", "id": "c1", "command": "ls"}}})
        emit({"method": "item/completed", "params": {"item": {
            "type": "commandExecution", "id": "c1", "command": "ls", "status": "completed", "exitCode": 0}}})
        return SimpleNamespace(response="done", session_id="dsh-session-1", turn_id=kwargs["turn_id"], usage={})

    observed = _Observed()
    invocation = _native_invocation()
    invocation.provider_event_processor = observed
    backend = backend_class()
    backend.client_factory = lambda: SimpleNamespace(run_turn=run_turn, steer=None, close_session=lambda *a: {})  # type: ignore[method-assign]
    with mock.patch("management_portal.execution.native_harness_tools.prepare_tools", return_value=([], None)), \
            mock.patch.object(backend_class, "record_provider_compaction"), \
            mock.patch.object(backend_class, "observe_turn_usage", return_value=None), \
            mock.patch.object(backend_class, "prepare_native_session"):
        backend.execute(invocation, _native_context())

    methods = [event["method"] for event in observed.events]
    assert methods[:3] == ["provider/activity", "provider/activity", "item/started"]
    capability_contracts.check_tool_events(observed.events)


def test_kimi_reports_the_main_agents_tool_calls_live_in_the_common_shape() -> None:
    """Kimi 主 agent 的工具调用实时以通用形状报告给 Harness

    Kimi 运行中的主 agent 工具调用（toolCall 条目的 name 与 input）经通用翻译成为 commandExecution 的开始与结束事件，失败状态保留，turn 结束时报告 turn/completed；循环治理在运行中即可记账。

    The live items of the main agent (``KimiLiveItems``) reach the governor while the turn runs.

    :design: decision_5c62deb8253a
    """

    def run_turn(**kwargs):
        emit = kwargs["event_callback"]
        emit({"method": "turn/started", "params": {"thread": {"id": "session-1"}, "turn": {"id": "prompt-1"}}})
        emit({"method": "item/started", "params": {"item": {
            "id": "Bash_0", "type": "toolCall", "name": "Bash", "input": {"command": "pytest -q"}, "status": "running"}}})
        emit({"method": "item/reasoning/textDelta", "params": {"delta": "…"}})
        emit({"method": "item/completed", "params": {"item": {
            "id": "Bash_0", "type": "toolCall", "output": "1 failed", "status": "failed", "error": True}}})
        emit({"method": "turn/completed", "params": {"turn": {"id": "prompt-1", "status": "completed"}}})
        return SimpleNamespace(response="done", session_id="session-1", turn_id="prompt-1", usage={})

    observed = _Observed()
    invocation = _native_invocation()
    invocation.provider_event_processor = observed
    invocation.request.runtime_options = {"tools": [], "model": "kimi-k2.7-code"}
    invocation.session.backend_session_id = "session-1"
    invocation.session.backend_route_id = "kimi_primary"
    client = SimpleNamespace(home=KIMI_HOME, run_turn=run_turn, steer=None)
    # The session's route names a Kimi account; its client is the fake.
    with mock.patch.object(kimi_account_runtime, "client_for_route", return_value=client), \
            mock.patch.object(kimi_account_runtime, "remember_session"), \
            mock.patch.object(kimi_backend.KimiBackend, "offer_portal_tools", return_value=([], None)), \
            mock.patch.object(kimi_backend.KimiBackend, "settle_portal_tools", return_value=[]), \
            mock.patch.object(kimi_backend.KimiBackend, "record_provider_compaction"), \
            mock.patch.object(kimi_backend.KimiBackend, "observe_turn_usage", return_value=None), \
            mock.patch.object(kimi_backend, "KimiToolGateway"):
        kimi_backend.KimiBackend().execute(invocation, _native_context())

    calls = [(event["method"], event["params"]["item"]["type"], event["params"]["item"].get("command"),
              event["params"]["item"].get("status")) for event in observed.events if event["method"].startswith("item/")]
    assert calls == [("item/started", "commandExecution", "pytest -q", "inProgress"),
                     ("item/completed", "commandExecution", "pytest -q", "failed")]
    assert observed.events[-1]["method"] == "turn/completed"
    capability_contracts.check_tool_events(observed.events)
