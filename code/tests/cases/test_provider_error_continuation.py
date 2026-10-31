"""After a Provider error an Adapter continues the turn in the same session.

The Harness feature ``provider_error_continue`` attaches the recovery policy;
every Adapter that declares ``error_continuation`` consumes it.

:module: resident.fork-handoff
:importance: P1
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from management_portal.execution import backend_contracts, dsh_backend, kimi_backend, provider_continuation
from management_portal.execution.contracts import ExecutionRecoveryDeferred
from management_portal.execution.harness import ProviderErrorRecoveryPolicy
from management_portal.execution.harness_capabilities import ERROR_CONTINUATION
from management_portal.execution.provider_catalog import AGENT_PROVIDERS
from management_portal.kimi_code_client import KimiCodeTurnFailed, KimiCodeUnavailable
from management_portal.native_agent_transport import NativeAgentUnavailable
from tests.cases import capability_contracts

ANSWER = SimpleNamespace(response="done", session_id="session-1", turn_id="turn-1:continue1", usage={})


def _context(*, cancelled: bool = False) -> mock.Mock:
    context = mock.Mock()
    context.is_cancelled.return_value = cancelled
    context.execution_started_at = ""
    return context


def _invocation(*, policy: ProviderErrorRecoveryPolicy | None, used: int = 0, provider_profile: str = "deepseek_primary"):
    return SimpleNamespace(
        request=SimpleNamespace(
            runtime_options={"provider_profile": provider_profile, "tools": []}, operation="turn", input_media=(),
            execution_id="execution-1", cwd="/work", permission_profile="",
            session=SimpleNamespace(binding_id="resident:resident_x"),
        ),
        session=SimpleNamespace(backend_route_id=provider_profile, backend_session_id="session-1"),
        user_prompt="audit the module", system_context="", attach_system_context=False,
        is_new_backend_session=False,
        recovery_checkpoint=(
            {"provider_recovery_state": {"provider_error_continue_count": used}} if used else {}
        ),
        provider_error_recovery=policy,
        provider_event_processor=None,
        harness_tools=(),
        tool_batch_processor=None,
    )


def _run_native(
    calls: list[dict], outcomes: list, invocation, context, *,
    started: bool = True, sleep=None, provider_id: str = "dsh",
):
    """Run a native-socket turn (DSH unless ``provider_id`` says otherwise) whose client yields ``outcomes`` one call after another."""

    def run_turn(**kwargs):
        calls.append({key: kwargs[key] for key in ("prompt", "turn_id", "session_id")})
        if started:
            kwargs["event_callback"]({"method": "turn/started", "params": {
                "threadId": "session-1", "turn": {"id": kwargs["turn_id"]}}})
            kwargs["event_callback"]({"method": "thread/tokenUsage/updated", "params": {
                "threadId": "session-1", "tokenUsage": {}}})
            context.set_interrupt_target.assert_called_with("session-1", kwargs["turn_id"])
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    backend = dsh_backend.DshBackend()
    backend.provider_id = provider_id
    backend.client_factory = lambda: SimpleNamespace(run_turn=run_turn, close_session=lambda *a: {})  # type: ignore[method-assign]
    with mock.patch("management_portal.execution.native_harness_tools.prepare_tools", return_value=([], None)), \
            mock.patch.object(dsh_backend.DshBackend, "record_provider_compaction"), \
            mock.patch.object(dsh_backend.DshBackend, "observe_turn_usage", return_value=None), \
            mock.patch.object(dsh_backend.DshBackend, "prepare_native_session"), \
            mock.patch.object(provider_continuation.time, "sleep", sleep or mock.Mock()):
        return backend.execute(invocation, context)


@pytest.mark.parametrize("provider", sorted(AGENT_PROVIDERS))
def test_every_backend_declares_continuation(provider: str) -> None:
    """每个 Backend 都声明错误后继续的方式

    Codex 在自己的 Adapter 中继续，Claude Code 与 DSH 共用原生 socket Adapter，Kimi Code 有自己的实现；Design 说明不声明的除外（capability_contracts.WITHHELD）。
    """
    # Codex continues in its own Adapter; Claude Code and DSH share the native-socket
    # Adapter and Kimi Code has its own, both exercised below.
    declared = ERROR_CONTINUATION in AGENT_PROVIDERS[provider].harness_capabilities()
    assert declared != capability_contracts.withheld(provider, ERROR_CONTINUATION)


def test_a_failed_turn_is_continued_once_in_the_same_session() -> None:
    """失败的轮次在同一会话中继续一次

    Provider 错误导致轮次失败时，执行在原会话中自动继续一次。
    """
    calls: list[dict] = []
    context = _context()
    result = _run_native(
        calls, [RuntimeError("DSH operation turn-1 did not complete: stream idle timeout"), ANSWER],
        _invocation(policy=ProviderErrorRecoveryPolicy(1)), context,
    )

    assert result.output["message"] == "done"
    assert calls == [
        {"prompt": "audit the module", "turn_id": "execution-1", "session_id": "session-1"},
        {"prompt": "continue", "turn_id": "execution-1:continue1", "session_id": "session-1"},
    ]
    # The used attempt stays with the Execution, so another Worker cannot spend it again.
    context.set_provider_recovery_state.assert_called_once_with({"provider_error_continue_count": 1})
    events = {call.args[0]: call.args[1] for call in context.emit.call_args_list}
    details = events["agent_harness_provider_error_continue"]
    assert details["attempt"] == 1 and details["max_attempts"] == 1 and details["provider"] == "dsh"
    # The continuation goes into the trace and the Chat as the Harness's own input.
    recovery = events["recovery_input_sent"]
    assert (recovery["reason"], recovery["prompt"], recovery["input_delivery"]) == (
        "provider_error", "continue", "continued",
    )
    context.set_turn_start_reason.assert_called_with("provider_recovery", replace_pending=True)
    context.set_interrupt_target.assert_called_with("session-1", "execution-1:continue1")


def test_a_continuation_after_a_kept_turn_starts_as_a_recovery() -> None:
    """Worker 交接后保留的轮次出错续跑时，开始原因记为 provider_recovery

    A Worker handover whose turn kept running started no turn; its reason does not name the continuation.

    :importance: P2
    """

    activities: list[dict] = []
    context = backend_contracts.BackendContext(
        emit_event=lambda kind, details: activities.append(details) if kind == "provider_event" else None,
        cancellation_requested=lambda: False,
        set_interrupt_target=lambda *_args: None,
        turn_start_reason="backend_service_update",
    )
    with mock.patch.object(provider_continuation.time, "sleep"):
        prompt, attempt = provider_continuation.provider_error_continuation(
            RuntimeError("Claude Code daemon connection closed"),
            invocation=_invocation(policy=ProviderErrorRecoveryPolicy(1)), context=context,
            provider_id="claude", turn_started=True,
        )
    context.observe_provider_event(
        {"method": "turn/started", "params": {"threadId": "session-1", "turn": {"id": f"turn-1:continue{attempt}"}}},
    )

    assert prompt == "continue"
    assert [activity["turn_start_reason"] for activity in activities] == ["provider_recovery"]


def test_a_second_failure_ends_the_execution() -> None:
    """第二次失败结束执行

    继续后再次失败时，执行以失败结束，不再继续。
    """
    calls: list[dict] = []
    with pytest.raises(RuntimeError, match="again"):
        _run_native(
            calls, [RuntimeError("overloaded"), RuntimeError("overloaded again")],
            _invocation(policy=ProviderErrorRecoveryPolicy(1)), _context(),
        )
    assert len(calls) == 2


@pytest.mark.parametrize("case", [
    "no_policy", "attempt_used", "cancelled", "turn_not_started", "authentication", "usage_limit",
    "context_overflow", "request_error", "interrupted", "control_signal",
])
def test_a_turn_is_not_continued_when_it_cannot_or_must_not_be(case: str) -> None:
    """不能或不应继续的轮次不继续

    错误类型不允许或 Adapter 不支持时，失败的轮次不会被继续。
    """
    error: BaseException = RuntimeError("stream idle timeout")
    policy: ProviderErrorRecoveryPolicy | None = ProviderErrorRecoveryPolicy(1)
    used, cancelled, started = 0, False, True
    if case == "no_policy":
        policy = None
    elif case == "attempt_used":
        used = 1
    elif case == "cancelled":
        cancelled = True
    elif case == "turn_not_started":
        started = False
    elif case == "authentication":
        error = RuntimeError("Claude Code turn failed: failed to authenticate")
    elif case == "usage_limit":
        error = RuntimeError("You've hit your usage limit")
    elif case == "context_overflow":
        error = RuntimeError("prompt is too long: 1200000 tokens")
    elif case == "request_error":
        error = ValueError("Selected route does not support images")
    elif case == "interrupted":
        error = InterruptedError("Agent tool execution cancelled")
    elif case == "control_signal":
        error = ExecutionRecoveryDeferred("retry later", retry_after_seconds=5)

    calls: list[dict] = []
    context = _context(cancelled=cancelled)
    with pytest.raises(type(error)) as raised:
        _run_native(calls, [error, ANSWER], _invocation(policy=policy, used=used), context, started=started)

    assert raised.value is error
    assert len(calls) == 1
    context.set_provider_recovery_state.assert_not_called()


def test_the_continuation_goes_out_three_seconds_after_the_error() -> None:
    """错误后 3 秒再发出继续

    Provider 错误后先暂停共 3 秒，再在原会话中发出继续。
    """
    calls: list[dict] = []
    result = _run_native(
        calls, [RuntimeError("API Error: 529 Overloaded"), ANSWER],
        _invocation(policy=ProviderErrorRecoveryPolicy(1)), _context(),
        sleep=lambda seconds: calls.append({"sleep": seconds}),
    )

    assert result.output["message"] == "done"
    assert calls[0]["prompt"] == "audit the module" and calls[-1]["prompt"] == "continue"
    assert all("sleep" in call for call in calls[1:-1])
    assert sum(call["sleep"] for call in calls[1:-1]) == 3.0


def test_a_cancel_during_the_pause_sends_no_continuation() -> None:
    """暂停期间取消则不发出继续

    Provider 错误后的 3 秒暂停内取消执行时，不再发出继续，原错误照常抛出。
    """
    calls: list[dict] = []
    slept: list[float] = []
    context = _context()
    context.is_cancelled.side_effect = lambda: bool(slept)
    error = RuntimeError("API Error: 529 Overloaded")
    with pytest.raises(RuntimeError) as raised:
        _run_native(calls, [error, ANSWER], _invocation(policy=ProviderErrorRecoveryPolicy(1)), context,
                    sleep=slept.append)

    assert raised.value is error
    assert [call["prompt"] for call in calls] == ["audit the module"]
    context.set_provider_recovery_state.assert_not_called()


def test_the_continuation_waits_for_a_provider_process_that_is_restarting() -> None:
    """继续会等待正在重启的 Provider 进程

    Provider 进程重启期间，继续操作等待其恢复后再发出。
    """
    calls: list[dict] = []
    result = _run_native(
        calls,
        [
            NativeAgentUnavailable("Native Agent closed before returning a result"),
            NativeAgentUnavailable("Native Agent unavailable: connection refused"),
            NativeAgentUnavailable("Native Agent unavailable: connection refused"),
            ANSWER,
        ],
        _invocation(policy=ProviderErrorRecoveryPolicy(1)), _context(),
    )

    assert result.output["message"] == "done"
    assert [call["prompt"] for call in calls] == ["audit the module", "continue", "continue", "continue"]
    assert {call["turn_id"] for call in calls[1:]} == {"execution-1:continue1"}


KIMI_HOME = Path("/nonexistent/kimi-account")


def _run_kimi(client: SimpleNamespace, invocation, context):
    from management_portal import kimi_account_runtime

    # The invocation's route names a Kimi account; its client is the fake.
    with mock.patch.object(kimi_account_runtime, "client_for_route", return_value=client), \
            mock.patch.object(kimi_account_runtime, "remember_session"), \
            mock.patch.object(kimi_backend.KimiBackend, "record_provider_compaction"), \
            mock.patch.object(kimi_backend.KimiBackend, "observe_turn_usage", return_value=None), \
            mock.patch.object(kimi_backend.KimiBackend, "settle_portal_tools", return_value=[]), \
            mock.patch.object(provider_continuation.time, "sleep"):
        return kimi_backend.KimiBackend().execute(invocation, context)


def test_a_failed_kimi_turn_is_continued_in_its_session() -> None:
    """失败的 Kimi 轮次在原会话中继续

    Kimi 轮次因 Provider 错误失败时，在同一会话中继续一次。
    """
    prompts: list[str] = []
    outcomes: list = [
        KimiCodeTurnFailed("Kimi Code turn failed before returning a response: upstream 502"),
        KimiCodeUnavailable("Kimi Code backend unavailable: connection refused"),
        SimpleNamespace(response="done", session_id="session-1", turn_id="prompt-2", usage={}),
    ]

    def run_turn(**kwargs):
        prompts.append(kwargs["prompt"])
        if len(prompts) == 1:
            kwargs["event_callback"]({"method": "turn/started", "params": {"turn": {"id": "prompt-1"}}})
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    context = _context()
    result = _run_kimi(SimpleNamespace(home=KIMI_HOME, run_turn=run_turn), _invocation(policy=ProviderErrorRecoveryPolicy(1)), context)

    assert result.output["message"] == "done"
    assert prompts == ["audit the module", "continue", "continue"]
    context.set_provider_recovery_state.assert_called_once_with({"provider_error_continue_count": 1})


def test_a_kimi_turn_that_never_reached_the_provider_is_not_continued() -> None:
    """未到达 Provider 的 Kimi 轮次不继续

    Kimi 轮次的输入尚未送达 Provider 就失败时，不做继续。
    """
    client = SimpleNamespace(home=KIMI_HOME, run_turn=mock.Mock(side_effect=KimiCodeUnavailable("Kimi Code backend unavailable")))
    with pytest.raises(KimiCodeUnavailable):
        _run_kimi(client, _invocation(policy=ProviderErrorRecoveryPolicy(1)), _context())
    assert client.run_turn.call_count == 1


# What Claude Code reports while the host's VPS egress has failed over to the
# physical network and Anthropic refuses the location (10-07).
EGRESS_BLOCK = "Claude Code turn failed: Failed to authenticate. API Error: 403 Request not allowed"
# What it reports while the VPS tunnel is down and the host holds its default
# traffic closed.
NO_CONNECTION = "Claude Code turn failed: API Error: Unable to connect to API (ECONNRESET)"
# Claude Code's subscription route reaches Anthropic through the VPS egress;
# its DeepSeek route (deepseek_primary) reaches DeepSeek directly.
SUBSCRIPTION = "claude_subscription"


@pytest.fixture(autouse=True)
def claude_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    from management_portal import claude_provider_catalog

    monkeypatch.setattr(claude_provider_catalog, "catalog", lambda route: {
        "upstream_provider": "anthropic" if route == SUBSCRIPTION else "deepseek",
    })


def _tracking_context() -> mock.Mock:
    """A context that remembers what the attempt checkpointed, as BackendContext does."""

    context = _context()
    context.is_handover.return_value = False
    state: dict = {}
    context.set_provider_recovery_state.side_effect = lambda value: state.__setitem__("value", dict(value))
    context.current_provider_recovery_state.side_effect = lambda: state.get("value")
    return context


def test_a_turn_refused_for_the_hosts_location_waits_for_the_egress_and_continues() -> None:
    """因出口地区被拒的轮次等出口恢复后继续

    Claude Code 报告 403 Request not allowed（主机出口切回物理网络、Anthropic 拒绝该地区）时，不按凭证错误结束执行：每 10 秒探测一次 Anthropic 是否重新接受本机请求，恢复后在同一会话发送 continue；这次续跑另计次数，不占 Provider 出错续跑的一次。

    :design: decision_5c62deb8253a
    """
    calls: list[dict] = []
    context = _tracking_context()
    probes = mock.Mock(side_effect=["blocked", "unreachable", "allowed"])
    with mock.patch.object(provider_continuation, "egress_state", probes):
        result = _run_native(
            calls, [RuntimeError(EGRESS_BLOCK), ANSWER],
            _invocation(policy=ProviderErrorRecoveryPolicy(1), provider_profile=SUBSCRIPTION), context,
            sleep=lambda seconds: calls.append({"sleep": seconds}), provider_id="claude",
        )

    assert result.output["message"] == "done"
    prompts = [call["prompt"] for call in calls if "prompt" in call]
    assert prompts == ["audit the module", "continue"]
    # It looks every 10 seconds until Anthropic answers this host again.
    assert probes.call_count == 3
    assert sum(call["sleep"] for call in calls if "sleep" in call) == 2 * provider_continuation.EGRESS_PROBE_INTERVAL_SECONDS
    events = {call.args[0]: call.args[1] for call in context.emit.call_args_list}
    assert (events["provider_egress_unavailable"]["provider"], events["provider_egress_unavailable"]["state"]) == (
        "claude", "blocked",
    )
    assert events["provider_egress_unavailable"]["upstream"] == "anthropic"
    details = events["agent_harness_provider_error_continue"]
    assert (details["reason"], details["attempt"], details["max_attempts"]) == (
        "egress_blocked", 1, provider_continuation.EGRESS_BLOCK_CONTINUE_ATTEMPTS,
    )
    assert context.current_provider_recovery_state() == {
        "provider_error_continue_count": 1, "egress_block_continue_count": 1,
    }


def test_a_continuation_refused_again_waits_again_until_its_attempts_are_used() -> None:
    """续跑再次被地区拒绝时再等一次，最多 5 次

    出口在续跑得到回答前再次切走时，再等出口恢复、再续跑一次；地区拒绝的续跑每个执行最多 5 次，用完后以最后一次错误结束，Provider 出错续跑的次数不受影响。

    :design: decision_5c62deb8253a
    """
    calls: list[dict] = []
    context = _tracking_context()
    with mock.patch.object(provider_continuation, "egress_state", return_value="allowed"):
        result = _run_native(
            calls, [RuntimeError(EGRESS_BLOCK), RuntimeError(EGRESS_BLOCK), ANSWER],
            _invocation(policy=ProviderErrorRecoveryPolicy(1), provider_profile=SUBSCRIPTION), context, provider_id="claude",
        )
    assert result.output["message"] == "done"
    assert [call["turn_id"] for call in calls] == ["execution-1", "execution-1:continue1", "execution-1:continue2"]
    # The refusals leave the attempt for a Provider error untouched.
    assert context.current_provider_recovery_state() == {
        "provider_error_continue_count": 2, "egress_block_continue_count": 2,
    }

    calls.clear()
    refusals = [RuntimeError(EGRESS_BLOCK) for _ in range(provider_continuation.EGRESS_BLOCK_CONTINUE_ATTEMPTS + 1)]
    last = refusals[-1]
    with mock.patch.object(provider_continuation, "egress_state", return_value="allowed"), \
            pytest.raises(RuntimeError) as raised:
        _run_native(calls, [*refusals, ANSWER], _invocation(policy=ProviderErrorRecoveryPolicy(1), provider_profile=SUBSCRIPTION),
                    _tracking_context(), provider_id="claude")
    assert raised.value is last
    assert len(calls) == provider_continuation.EGRESS_BLOCK_CONTINUE_ATTEMPTS + 1


def test_a_refused_turn_fails_when_the_egress_does_not_come_back() -> None:
    """出口 10 分钟内不恢复时轮次失败

    等待出口恢复最多 10 分钟；期间探测一直被拒或无法连接时不发送续跑，以原来的错误结束执行。

    :design: decision_5c62deb8253a
    """
    calls: list[dict] = []
    clock = {"now": 0.0}

    def sleep(seconds: float) -> None:
        clock["now"] += seconds

    error = RuntimeError(EGRESS_BLOCK)
    context = _tracking_context()
    with mock.patch.object(provider_continuation, "egress_state", return_value="blocked") as probe, \
            mock.patch.object(provider_continuation.time, "monotonic", lambda: clock["now"]), \
            pytest.raises(RuntimeError) as raised:
        _run_native(calls, [error, ANSWER], _invocation(policy=ProviderErrorRecoveryPolicy(1), provider_profile=SUBSCRIPTION), context,
                    sleep=sleep, provider_id="claude")

    assert raised.value is error
    assert [call["prompt"] for call in calls] == ["audit the module"]
    assert clock["now"] == provider_continuation.EGRESS_BLOCK_WAIT_SECONDS
    assert probe.call_count == provider_continuation.EGRESS_BLOCK_WAIT_SECONDS / provider_continuation.EGRESS_PROBE_INTERVAL_SECONDS + 1
    context.set_provider_recovery_state.assert_not_called()


def test_a_worker_handing_over_leaves_the_wait_for_the_egress_to_the_next_worker() -> None:
    """Worker 交接时把等待出口交给下一个 Worker

    等待出口恢复期间 Worker 被要求交接时停止等待并交回执行（ExecutionRecoveryDeferred），由下一个 Worker 继续这一轮；当前 Worker 不发送续跑、不记次数。

    :design: decision_5c62deb8253a
    """
    calls: list[dict] = []
    context = _tracking_context()
    probe = mock.Mock(return_value="blocked")
    # The Worker is asked to hand over after its first look at the egress.
    context.is_handover.side_effect = lambda: probe.call_count > 0
    with mock.patch.object(provider_continuation, "egress_state", probe), \
            pytest.raises(ExecutionRecoveryDeferred):
        _run_native(calls, [RuntimeError(EGRESS_BLOCK), ANSWER], _invocation(policy=ProviderErrorRecoveryPolicy(1), provider_profile=SUBSCRIPTION),
                    context, provider_id="claude")
    assert [call["prompt"] for call in calls] == ["audit the module"]
    assert probe.call_count == 1
    context.set_provider_recovery_state.assert_not_called()


def test_the_egress_probe_tells_a_refused_location_from_a_served_one() -> None:
    """出口探测区分被拒地区与可用出口

    探测不带凭证访问 Anthropic API：任何 403 都表示地区被拒（不带凭证的请求不会因权限被拒），其他 HTTP 回答（如 401）表示出口可用，连接失败表示无法连接。

    :design: decision_5c62deb8253a
    """
    import io
    import urllib.error

    from management_portal.execution import provider_egress

    def answer(status: int, body: str, url: str = provider_egress.UPSTREAMS["anthropic"].probe_url):
        def urlopen(request, timeout):
            assert request.full_url == url
            raise urllib.error.HTTPError(request.full_url, status, "", {}, io.BytesIO(body.encode()))
        return urlopen

    refused = '{"type":"error","error":{"type":"forbidden","message":"Request not allowed"}}'
    served = '{"type":"error","error":{"type":"authentication_error","message":"x-api-key header is required"}}'
    with mock.patch.object(provider_egress.urllib.request, "urlopen", answer(403, refused)):
        assert provider_egress.egress_state("anthropic") == "blocked"
    with mock.patch.object(provider_egress.urllib.request, "urlopen", answer(403, "<html>Forbidden</html>")):
        assert provider_egress.egress_state("anthropic") == "blocked"
    with mock.patch.object(provider_egress.urllib.request, "urlopen", answer(401, served)):
        assert provider_egress.egress_state("anthropic") == "allowed"
    with mock.patch.object(provider_egress.urllib.request, "urlopen",
                           side_effect=urllib.error.URLError("Network is unreachable")):
        assert provider_egress.egress_state("anthropic") == "unreachable"
    # A service that serves every location is not refused for it.
    with mock.patch.object(provider_egress.urllib.request, "urlopen",
                           answer(403, "", provider_egress.UPSTREAMS["deepseek"].probe_url)):
        assert provider_egress.egress_state("deepseek") == "allowed"


def test_kimi_probe_uses_the_server_registry_and_profile(monkeypatch) -> None:
    """kimi 网络探测使用 Kimi 服务端 profile 对应的已登记模型 API 地址

    :module: platform.core
    :importance: P2
    """
    import urllib.error
    from management_portal import model_api_store
    from management_portal.execution import provider_egress

    monkeypatch.setenv("PORTAL_KIMI_CODE_PROVIDER_PROFILE", "fixture")
    # The profile the Kimi server would use is the registered model API row.
    assert model_api_store.create_api({"id": "fixture", "kind": "openai_compatible_chat", "enabled": True,
                                       "base_url": "https://fixture.example/v1/"}, "test")
    with mock.patch.object(provider_egress.urllib.request, "urlopen") as urlopen:
        urlopen.side_effect = urllib.error.HTTPError("", 401, "", {}, None)
        assert provider_egress.provider_network_state("kimi") == "allowed"
        assert urlopen.call_args.args[0].full_url == "https://fixture.example/v1/models"


def test_each_route_declares_the_service_it_reaches() -> None:
    """每条路线声明自己到达的服务

    Codex 到 OpenAI、Kimi 到 Moonshot、DSH 与 Claude 的 DeepSeek 路线到 DeepSeek、Claude 订阅路线到 Anthropic、Antigravity 到 Google；只有 Google 与 Anthropic 限制访问地点；每个 Provider 声明的服务都有探测入口。

    :module: execution.providers
    :design: decision_3948ce9b9443
    """
    from management_portal.execution.provider_egress import UPSTREAMS, route_upstream

    assert {name: route_upstream(name) for name in ("antigravity", "claude", "codex", "dsh", "kimi")} == {
        "antigravity": "google", "claude": "deepseek", "codex": "openai", "dsh": "deepseek", "kimi": "moonshot"}
    assert route_upstream("claude", SUBSCRIPTION) == "anthropic"
    # Every Provider names its service; Portal checks the egress of those it has a probe for.
    assert all(p.upstream for p in AGENT_PROVIDERS.values())
    assert sorted(name for name, upstream in UPSTREAMS.items() if upstream.location_restricted) == [
        "anthropic", "google"]


def test_only_the_refusal_for_the_location_counts_as_an_egress_block() -> None:
    """只有地区拒绝算出口被拒

    只有 403 Request not allowed 算出口被拒；401 凭证无效、其他 403 权限错误仍按凭证或权限错误处理，不续跑。

    :design: decision_5c62deb8253a
    """
    from management_portal.execution.provider_failures import is_egress_block_failure

    assert is_egress_block_failure(EGRESS_BLOCK)
    assert is_egress_block_failure(RuntimeError(EGRESS_BLOCK))
    assert not is_egress_block_failure("Claude Code turn failed: Failed to authenticate. API Error: 401 Invalid bearer token")
    assert not is_egress_block_failure("API Error: 403 permission_error: model not allowed for this organization")


def test_a_claude_turn_that_cannot_reach_anthropic_waits_for_the_tunnel_and_continues() -> None:
    """claude 轮次连不上 Anthropic 时等出口隧道恢复后继续，之后的失败按普通 Provider 错误续跑

    :importance: P2"""
    calls: list[dict] = []
    context = _tracking_context()
    # The first look tells the tunnel is down; the wait looks until it is
    # back; the second failure looks once more and finds Anthropic answering.
    probes = mock.Mock(side_effect=["unreachable", "unreachable", "allowed", "allowed"])
    with mock.patch.object(provider_continuation, "egress_state", probes):
        result = _run_native(
            calls, [RuntimeError(NO_CONNECTION), RuntimeError("API Error: Connection error."), ANSWER],
            _invocation(policy=ProviderErrorRecoveryPolicy(1), provider_profile=SUBSCRIPTION), context,
            sleep=lambda seconds: calls.append({"sleep": seconds}), provider_id="claude",
        )

    assert result.output["message"] == "done"
    assert [call["prompt"] for call in calls if "prompt" in call] == ["audit the module", "continue", "continue"]
    events = [call.args for call in context.emit.call_args_list]
    unavailable = [details for name, details in events if name == "provider_egress_unavailable"]
    assert [details["state"] for details in unavailable] == ["unreachable"]
    continued = [details for name, details in events if name == "agent_harness_provider_error_continue"]
    # The second failure came while Anthropic answered the probe again: an
    # ordinary Provider error with the policy's single attempt.
    assert [(details["reason"], details["attempt"]) for details in continued] == [
        ("egress_unreachable", 1), ("provider_error", 2),
    ]
    assert context.current_provider_recovery_state() == {
        "provider_error_continue_count": 2, "egress_block_continue_count": 1,
    }


def test_a_connection_failure_while_anthropic_answers_is_an_ordinary_provider_error() -> None:
    """Anthropic 探测可达时的连接失败按普通 Provider 错误续跑，不等待出口

    :importance: P2"""
    calls: list[dict] = []
    context = _tracking_context()
    with mock.patch.object(provider_continuation, "egress_state", return_value="allowed") as probe:
        result = _run_native(
            calls, [RuntimeError(NO_CONNECTION), ANSWER],
            _invocation(policy=ProviderErrorRecoveryPolicy(1), provider_profile=SUBSCRIPTION), context,
            sleep=lambda seconds: calls.append({"sleep": seconds}), provider_id="claude",
        )
    assert result.output["message"] == "done"
    assert probe.call_count == 1
    assert sum(call["sleep"] for call in calls if "sleep" in call) == 3.0
    assert context.current_provider_recovery_state() == {"provider_error_continue_count": 1}


@pytest.mark.parametrize("provider_id", ["dsh", "claude"])
def test_a_route_to_a_service_that_serves_every_location_does_not_wait_for_the_vps_egress(provider_id) -> None:
    """到达不限地点服务的路线不等待 VPS 出口

    DSH 与 Claude Code 的 DeepSeek 路线连接失败时按普通 Provider 错误续跑，不探测 Anthropic、不等待出口恢复。

    DSH, and Claude Code on its DeepSeek route, reach DeepSeek directly.

    :module: execution.providers
    :design: decision_3948ce9b9443
    """

    calls: list[dict] = []
    context = _tracking_context()
    with mock.patch.object(provider_continuation, "egress_state", return_value="unreachable") as probe:
        result = _run_native(
            calls, [RuntimeError("DeepSeek request failed: Connection error."), ANSWER],
            _invocation(policy=ProviderErrorRecoveryPolicy(1)), context, provider_id=provider_id,
        )
    assert result.output["message"] == "done"
    probe.assert_not_called()
    assert context.current_provider_recovery_state() == {"provider_error_continue_count": 1}


def test_only_failures_to_reach_the_api_count_as_network_failures() -> None:
    """只有连不上 API 的错误算网络失败，出口拒绝与 5xx 错误不算

    :importance: P2"""
    from management_portal.execution.provider_failures import is_network_failure

    for message in (
        NO_CONNECTION,
        "API Error: Unable to connect to API. Check your internet connection",
        "API Error: Connection error.",
        "API Error: Request timed out.",
        "connect ETIMEDOUT 160.79.104.10:443",
        "getaddrinfo EAI_AGAIN api.anthropic.com",
    ):
        assert is_network_failure(message), message
        assert is_network_failure(RuntimeError(message)), message
    for message in (EGRESS_BLOCK, "API Error: 529 Overloaded", "API Error: 500 Internal server error"):
        assert not is_network_failure(message), message
