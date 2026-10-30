"""Codex thread and turn options: subagents, capability disables, sandbox and permission profiles,
account routes and fallback.

:module: execution.service
:importance: P2
"""
from __future__ import annotations

from tests.cases.execution_test_support import run_worker_once

from pathlib import Path
from unittest import mock

import pytest

pytestmark = pytest.mark.usefixtures("registered_execution_defaults")

from management_portal.agent_prompt_context import (
    portal_system_instructions,
)
from management_portal.execution import backend_contracts, codex_backend, provider_activity_events
from management_portal.execution.backend_contracts import BackendInvocation, BackendResult
from management_portal.execution.contracts import ExecutionRequest, SessionOptions
from management_portal.execution.runtime import ExecutionRuntime
from management_portal.execution.scheduler import SchedulerClient
from management_portal.execution.session_manager import RuntimeSession
from management_portal.execution.worker import ExecutionWorker
from tests.cases.execution_layer_test_support import _stack, _close


def test_codex_backend_defaults_subagents_off_in_thread_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """codex 轮次配置默认关闭子 agent，并用本次执行的上下文文件替换旧的执行 ID 环境变量"""
    client = mock.Mock()

    def run_turn(*_args, **kwargs):
        kwargs["turn_started_callback"](
            "019f0000-0000-7000-8000-000000000402"
        )
        return mock.Mock(
            thread_id="019f0000-0000-7000-8000-000000000401",
            turn_id="019f0000-0000-7000-8000-000000000402",
            response="done",
            token_usage={},
        )

    client.run_turn.side_effect = run_turn
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        runtime_options={
            "client_user_message_id": "msg_0123456789abcdef",
            "config_overrides": {"shell_environment_policy.set": {
                "PORTAL_EXECUTION_ID": "stale-execution", "PORTAL_RESIDENT_ID": "resident_0123456789ab",
            }},
        },
        session=SessionOptions(
            mode="persistent",
            binding_id="provider:test-session",
            backend_route_id="one",
        ),
        system_instructions=portal_system_instructions("resident"),
    )
    events: list[tuple[str, dict]] = []
    context = backend_contracts.BackendContext(
        emit_event=lambda name, details: events.append((name, details)),
        cancellation_requested=lambda: False,
        set_interrupt_target=lambda *_args: None,
    )

    session = RuntimeSession(
        mode="persistent",
        binding_id="",
        backend_session_id="019f0000-0000-7000-8000-000000000401",
        backend_route_id=request.session.backend_route_id,
        is_new_backend_session=False,
    )
    codex_backend.CodexBackend(
        client_factory=lambda _socket_path: client,
    ).execute(
        BackendInvocation(
            request=request,
            session=session,
            user_prompt=request.prompt,
            system_context="",
            attach_system_context=False,
            is_new_backend_session=False,
        ),
        context,
    )

    assert client.run_turn.call_args.kwargs["config"] == {
        "show_raw_agent_reasoning": True,
        "model_reasoning_summary": "auto",
        "agents": {"enabled": False, "interrupt_message": False},
        "compact_prompt": codex_backend._CODEX_COMPACTION_PROMPT,
        "project_doc_max_bytes": 0,
        "tools": {"update_plan": {"enabled": True}},
        "shell_environment_policy.set": {
            "PORTAL_EXECUTION_CONTEXT_FILE": str(codex_backend.CodexBackend._execution_context_path(request)), "PORTAL_RESIDENT_ID": "resident_0123456789ab",
        },
    }
    assert request.runtime_options["config_overrides"]["shell_environment_policy.set"]["PORTAL_EXECUTION_ID"] == "stale-execution"
    assert events == [
        (
            "provider_input_bound",
            {
                "provider": "codex",
                "backend_session_id": (
                    "019f0000-0000-7000-8000-000000000401"
                ),
                "backend_route_id": "one",
                "turn_id": "019f0000-0000-7000-8000-000000000402",
                "provider_message_id": "",
            },
        )
    ]


def test_codex_backend_forwards_per_session_capability_disables() -> None:
    """codex 后端把会话级关闭 web 搜索、插件和 MCP 等配置原样传给轮次"""
    client = mock.Mock()

    def run_turn(*_args, **kwargs):
        kwargs["turn_started_callback"](
            "019f0000-0000-7000-8000-000000000405"
        )
        return mock.Mock(
            thread_id="019f0000-0000-7000-8000-000000000404",
            turn_id="019f0000-0000-7000-8000-000000000405",
            response="done",
            token_usage={},
        )

    client.run_turn.side_effect = run_turn
    config_overrides = {
        "web_search": "disabled",
        "features": {
            "apps": False,
            "memories": False,
            "multi_agent": False,
            "plugins": False,
        },
        "mcp_servers": {
            "openaiDeveloperDocs": {"enabled": False},
        },
    }
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="offline turn",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        runtime_options={"config_overrides": config_overrides},
        session=SessionOptions(
            mode="persistent",
            binding_id="provider:test-session",
            backend_route_id="one",
        ),
        system_instructions=portal_system_instructions("general"),
    )
    session = RuntimeSession(
        mode="persistent",
        binding_id="",
        backend_session_id="019f0000-0000-7000-8000-000000000404",
        backend_route_id=request.session.backend_route_id,
        is_new_backend_session=False,
    )

    codex_backend.CodexBackend(
        client_factory=lambda _socket_path: client,
    ).execute(
        BackendInvocation(
            request=request,
            session=session,
            user_prompt=request.prompt,
            system_context="",
            attach_system_context=False,
            is_new_backend_session=False,
        ),
        backend_contracts.BackendContext(
            emit_event=lambda *_args: None,
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
        ),
    )

    assert client.run_turn.call_args.kwargs["config"] == {
        **config_overrides,
        "shell_environment_policy.set": {"PORTAL_EXECUTION_CONTEXT_FILE": str(codex_backend.CodexBackend._execution_context_path(request))},
        "show_raw_agent_reasoning": True,
        "model_reasoning_summary": "auto",
        "agents": {"enabled": False, "interrupt_message": False},
        "compact_prompt": codex_backend._CODEX_COMPACTION_PROMPT,
        "project_doc_max_bytes": 0,
        "tools": {"update_plan": {"enabled": True}},
    }


def test_codex_backend_enables_subagents_only_when_explicitly_allowed_contracts(subtests, isolated_portal_case):
    """codex 仅在显式允许时开启子 agent，Provider 活动去掉增量和原始命令输出

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_codex_backend_enables_subagents_only_when_explicitly_allowed'), isolated_portal_case() as case_path:
        assert codex_backend.CodexBackend._thread_config(
            {"allow_subagents": True}
        ) == {
            "show_raw_agent_reasoning": True,
            "model_reasoning_summary": "auto",
            "agents": {"enabled": True, "interrupt_message": False},
            "compact_prompt": codex_backend._CODEX_COMPACTION_PROMPT,
            "project_doc_max_bytes": 0,
            "tools": {"update_plan": {"enabled": True}},
        }
    with subtests.test(contract='test_backend_context_emits_uniform_provider_input_binding'), isolated_portal_case() as case_path:
        events: list[tuple[str, dict]] = []
        interrupt_targets: list[tuple[str, str, str]] = []
        context = backend_contracts.BackendContext(
            emit_event=lambda name, details: events.append((name, details)),
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *values: interrupt_targets.append(values),
        )

        context.bind_input(
            provider="codex",
            backend_session_id="thread-1",
            backend_route_id="zero",
            turn_id="turn-1",
            provider_message_id="provider-message-1",
        )

        assert interrupt_targets == [("thread-1", "turn-1", "zero")]
        assert events == [
            (
                "provider_input_bound",
                {
                    "provider": "codex",
                    "backend_session_id": "thread-1",
                    "backend_route_id": "zero",
                    "turn_id": "turn-1",
                    "provider_message_id": "provider-message-1",
                },
            )
        ]
    with subtests.test(contract='test_provider_activity_drops_deltas_and_raw_command_output'), isolated_portal_case() as case_path:
        assert provider_activity_events._compact_provider_activity_event({
            "method": "item/commandExecution/outputDelta",
            "params": {"delta": "unbounded output"},
        }) is None
        assert provider_activity_events._compact_provider_activity_event({
            "method": "item/agentMessage/delta",
            "params": {"delta": "token delta"},
        }) is None

        compact = provider_activity_events._compact_provider_activity_event({
            "method": "item/completed",
            "params": {
                "turnId": "turn-1",
                "item": {
                    "id": "command-1",
                    "type": "commandExecution",
                    "command": "pytest -q",
                    "status": "completed",
                    "exitCode": 0,
                    "aggregatedOutput": "private raw output" * 1000,
                },
            },
        })

        assert compact == {
            "method": "item/completed",
            "params": {
                "turnId": "turn-1",
                "item": {
                    "id": "command-1",
                    "type": "commandExecution",
                    "status": "completed",
                    "exitCode": 0,
                    "command": "pytest -q",
                },
            },
        }
    with subtests.test(contract='test_contract_requires_explicit_fork_source_and_new_destination'), isolated_portal_case() as case_path:
        with pytest.raises(ValueError, match="requires one context source"):
            ExecutionRequest.new(
                actor="sample_admin",
                prompt="fork",
                service_type="agent_session",
                provider="codex",
                operation="fork",
                session=SessionOptions(mode="persistent", binding_id="fork:target"),
            )

        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="fork",
            service_type="agent_session",
            provider="codex",
            operation="fork",
            session=SessionOptions(
                mode="persistent",
                binding_id="fork:target",
                context_source_binding_id="resident:source",
            ),
        )
        assert request.session.context_source_binding_id == "resident:source"
    with subtests.test(contract='test_codex_backend_rejects_goal_on_ephemeral_thread_before_creation'), isolated_portal_case() as case_path:
        registry = mock.Mock()
        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="/goal finish the portal goal integration",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            cwd="/tmp",
            runtime_options={
                "ephemeral": True,
                "goal": {
                    "action": "set",
                    "objective": "finish the portal goal integration",
                },
            },
        )

        with pytest.raises(
            ValueError,
            match="native goals require a persistent thread",
        ):
            codex_backend.CodexBackend(
                account_registry=registry,
                client_factory=mock.Mock(),
            ).execute(
                BackendInvocation(
                    request=request,
                    session=RuntimeSession(
                        mode="persistent",
                        binding_id="",
                        backend_session_id="",
                        backend_route_id="",
                        is_new_backend_session=True,
                    ),
                    user_prompt=request.prompt,
                    system_context="",
                    attach_system_context=False,
                    is_new_backend_session=True,
                ),
                backend_contracts.BackendContext(
                    emit_event=lambda *_args: None,
                    cancellation_requested=lambda: False,
                    set_interrupt_target=lambda *_args: None,
                ),
            )

        registry.candidates.assert_not_called()
    with subtests.test(contract='test_execution_worker_never_self_reactivates_after_scheduler_fence'), isolated_portal_case() as case_path:
        scheduler_client = mock.Mock()
        scheduler_client.claim.return_value = {
            "claimed": False,
            "reason": "worker_not_active",
        }
        worker = ExecutionWorker(
            build_id="current-build",
            worker_id="dev:recovering-worker",
            scheduler=scheduler_client,
            runtime=mock.Mock(),
        )

        assert worker.claim(wait_seconds=0.1) == scheduler_client.claim.return_value
        scheduler_client.register.assert_not_called()
        scheduler_client.claim.assert_called_once_with(270, wait_seconds=0.1)


def test_codex_backend_owns_interrupt_attribution(subtests, isolated_portal_case):
    """codex 线程配置固定关闭中断提示并使用 Portal 压缩提示词，不受覆盖配置影响"""
    for allow_subagents in [False, True]:
        for interrupt_message in [False, True]:
            with subtests.test(allow_subagents=allow_subagents, interrupt_message=interrupt_message), isolated_portal_case() as case_path:
                overrides = {
                    "agents": {"interrupt_message": interrupt_message, "max_threads": 2},
                    "compact_prompt": "Create a handoff for the next LLM.",
                }
                config = codex_backend.CodexBackend._thread_config({
                    "allow_subagents": allow_subagents,
                    "config_overrides": overrides,
                })

                assert config["agents"] == {
                    "enabled": allow_subagents,
                    "interrupt_message": False,
                    "max_threads": 2,
                }
                assert config["compact_prompt"] == codex_backend._CODEX_COMPACTION_PROMPT
                assert overrides == {
                    "agents": {"interrupt_message": interrupt_message, "max_threads": 2},
                    "compact_prompt": "Create a handoff for the next LLM.",
                }


def test_codex_backend_forwards_structured_sandbox_through_unified_exit(
    tmp_path: Path,
) -> None:
    """codex 新线程按 sandbox 模式启动，轮次传入结构化 sandbox_policy 和审批策略"""
    client = mock.Mock()
    client.start_thread.return_value = {
        "thread": {"id": "019f0000-0000-7000-8000-000000000411"}
    }

    def run_turn(*_args, **kwargs):
        kwargs["turn_started_callback"](
            "019f0000-0000-7000-8000-000000000412"
        )
        return mock.Mock(
            thread_id="019f0000-0000-7000-8000-000000000411",
            turn_id="019f0000-0000-7000-8000-000000000412",
            response="done",
            token_usage={},
        )

    client.run_turn.side_effect = run_turn
    sandbox_policy = {
        "type": "workspaceWrite",
        "writableRoots": [str(tmp_path)],
        "networkAccess": False,
    }
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="author graph",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd=str(tmp_path),
        runtime_options={
            "sandbox_policy": sandbox_policy,
            "approval_policy": "never",
        },
        session=SessionOptions(mode="persistent", binding_id="sandbox:test"),
        system_instructions=portal_system_instructions("general"),
    )
    session = RuntimeSession(
        mode="persistent",
        binding_id=request.session.binding_id,
        backend_session_id="",
        backend_route_id="",
        is_new_backend_session=True,
    )

    codex_backend.CodexBackend(
        client_factory=lambda _socket_path: client,
    ).execute(
        BackendInvocation(
            request=request,
            session=session,
            user_prompt=request.prompt,
            system_context="rules",
            attach_system_context=True,
            is_new_backend_session=True,
        ),
        backend_contracts.BackendContext(
            emit_event=lambda *_args: None,
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
        ),
    )

    assert client.start_thread.call_args.args[0]["sandbox"] == "workspace-write"
    assert client.start_thread.call_args.args[0]["approvalPolicy"] == "never"
    assert client.start_thread.call_args.args[0]["developerInstructions"] == (
        "rules"
    )
    assert client.run_turn.call_args.args[1] == "author graph"
    assert client.run_turn.call_args.kwargs["sandbox_policy"] == sandbox_policy
    assert client.run_turn.call_args.kwargs["approval_policy"] == "never"


def test_codex_backend_selects_native_permission_profile_and_approval(
    tmp_path: Path,
) -> None:
    """指定 permissions 时 codex 线程和轮次使用原生权限配置，不再传 sandbox"""
    client = mock.Mock()
    client.start_thread.return_value = {
        "thread": {"id": "019f0000-0000-7000-8000-000000000421"}
    }
    client.run_turn.return_value = mock.Mock(
        thread_id="019f0000-0000-7000-8000-000000000421",
        turn_id="019f0000-0000-7000-8000-000000000422",
        response="done",
        token_usage={},
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="author graph",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd=str(tmp_path),
        runtime_options={
            "permissions": "portal-closed-cwd",
            "sandbox": "danger-full-access",
            "approval_policy": "never",
        },
        session=SessionOptions(
            mode="persistent",
            binding_id="permissions:test",
        ),
        system_instructions=portal_system_instructions("general"),
    )
    session = RuntimeSession(
        mode="persistent",
        binding_id=request.session.binding_id,
        backend_session_id="",
        backend_route_id="",
        is_new_backend_session=True,
    )

    codex_backend.CodexBackend(
        client_factory=lambda _socket_path: client,
    ).execute(
        BackendInvocation(
            request=request,
            session=session,
            user_prompt=request.prompt,
            system_context="rules",
            attach_system_context=True,
            is_new_backend_session=True,
        ),
        backend_contracts.BackendContext(
            emit_event=lambda *_args: None,
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
        ),
    )

    start_params = client.start_thread.call_args.args[0]
    assert start_params["permissions"] == "portal-closed-cwd"
    assert start_params["approvalPolicy"] == "never"
    assert "sandbox" not in start_params
    assert (
        client.run_turn.call_args.kwargs["permissions"]
        == "portal-closed-cwd"
    )
    assert client.run_turn.call_args.kwargs["approval_policy"] == "never"
    assert "sandbox_policy" not in client.run_turn.call_args.kwargs


def test_codex_permission_profile_owns_sandbox_policy(subtests, isolated_portal_case):
    """codex 指定 permissions 时忽略 sandbox 和 sandbox_policy"""
    for lower_level_policy in (
        {"sandbox": "danger-full-access"},
        {"sandbox_policy": {"type": "dangerFullAccess"}},
    ):
        with subtests.test(lower_level_policy=lower_level_policy), isolated_portal_case() as case_path:
            sandbox, sandbox_policy = codex_backend.CodexBackend._sandbox_options({
                "permissions": ":danger-full-access",
                **lower_level_policy,
            })

            assert sandbox == ""
            assert sandbox_policy is None


def test_codex_account_route_uses_one_then_falls_back_before_thread_start(
    tmp_path: Path,
) -> None:
    """codex 新线程启动前 one 账号不可用时回退到 zero，并记录回退事件"""
    one_client = mock.Mock()
    one_client.start_thread.side_effect = (
        codex_backend.CodexAppServerUnavailable("socket unavailable")
    )
    zero_client = mock.Mock()
    zero_client.start_thread.return_value = {
        "thread": {"id": "019f0000-0000-7000-8000-000000000501"}
    }
    zero_client.run_turn.return_value = mock.Mock(
        thread_id="019f0000-0000-7000-8000-000000000501",
        turn_id="019f0000-0000-7000-8000-000000000502",
        response="done",
        token_usage={},
    )

    def client_factory(socket_path):
        return (
            one_client
            if str(socket_path).endswith("codex-app-server-one.sock")
            else zero_client
        )

    request = ExecutionRequest.new(
        actor="sample_reader",
        prompt="new turn",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd=str(tmp_path),
        session=SessionOptions(mode="persistent", binding_id="resident:test-sample_reader"),
        system_instructions=portal_system_instructions("resident"),
    )
    session = RuntimeSession(
        mode="persistent",
        binding_id=request.session.binding_id,
        backend_session_id="",
        is_new_backend_session=True,
    )
    events: list[tuple[str, dict]] = []
    result = codex_backend.CodexBackend(
        client_factory=client_factory,
    ).execute(
        BackendInvocation(
            request=request,
            session=session,
            user_prompt=request.prompt,
            system_context="rules",
            attach_system_context=True,
            is_new_backend_session=True,
        ),
        backend_contracts.BackendContext(
            emit_event=lambda name, details: events.append((name, details)),
            cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
        ),
    )

    assert result.backend_route_id == "zero"
    assert one_client.start_thread.call_count == 1
    assert zero_client.start_thread.call_count == 1
    assert events[0] == (
        "backend_route_fallback",
        {
            "provider": "codex",
            "from_route_id": "one",
            "to_route_id": "zero",
            "reason": "CodexAppServerUnavailable",
        },
    )


@pytest.mark.parametrize("blocked", [{"zero"}, {"zero", "two"}])
def test_codex_fallback_skips_accounts_that_reject_capacity(tmp_path, blocked):
    """codex 账号回退跳过拒绝转移容量的账号，全部拒绝时报原错误

    :design: decision_3dd52c0692c1
    :design-point:
    """
    request = ExecutionRequest.new(
        actor="test", prompt="hello", service_type="agent_session", provider="codex", operation="turn",
        cwd=str(tmp_path), session=SessionOptions(mode="persistent", binding_id="resident:fallback", backend_route_id="one"),
    )
    invocation = BackendInvocation(
        request=request, session=RuntimeSession(mode="persistent", binding_id=request.session.binding_id,
                                               backend_session_id="", backend_route_id="one", is_new_backend_session=True),
        user_prompt=request.prompt, system_context="", attach_system_context=False, is_new_backend_session=True,
    )
    attempts, events, started = [], [], []

    def reassign(route):
        attempts.append(route)
        return {"accepted": route not in blocked}

    registry = mock.Mock()
    registry.candidates.return_value = [
        mock.Mock(account_id=route, socket_path=tmp_path / f"codex-app-server-{route}.sock")
        for route in ("one", "zero", "two")
    ]
    backend = codex_backend.CodexBackend(account_registry=registry, client_factory=lambda socket: socket)

    def start(client, *_args):
        started.append(str(client))
        if str(client).endswith("codex-app-server-one.sock"):
            raise codex_backend.CodexAppServerUnavailable("primary unavailable")
        return "new-thread"

    context = backend_contracts.BackendContext(
        emit_event=lambda *event: events.append(event), cancellation_requested=lambda: False,
        set_interrupt_target=lambda *_args: None, reassign_backend_route=reassign,
    )
    with mock.patch.object(backend, "_start_thread", side_effect=start):
        if "two" in blocked:
            with pytest.raises(codex_backend.CodexAppServerUnavailable, match="primary unavailable"):
                backend._resolve_client(invocation, context)
            assert events == []
            assert len(started) == 1
        else:
            route, _client, thread_id, _attached = backend._resolve_client(invocation, context)
            assert (route.account_id, thread_id) == ("two", "new-thread")
            assert events[0][1]["from_route_id"] == "one"
            assert events[0][1]["to_route_id"] == "two"
            assert not any(path.endswith("codex-app-server-zero.sock") for path in started)
    assert attempts == ["zero", "two"]


def test_explicit_codex_account_route_does_not_fall_back(
    tmp_path: Path,
) -> None:
    """禁止回退的显式 codex 账号路线不可用时直接报错，不尝试其他账号"""
    one_client = mock.Mock()
    one_client.start_thread.side_effect = (
        codex_backend.CodexAppServerUnavailable("socket unavailable")
    )
    zero_client = mock.Mock()

    def client_factory(socket_path):
        return (
            one_client
            if str(socket_path).endswith("codex-app-server-one.sock")
            else zero_client
        )

    request = ExecutionRequest.new(
        actor="sample_reader",
        prompt="new turn",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd=str(tmp_path),
        runtime_options={"allow_backend_route_fallback": False},
        session=SessionOptions(
            mode="persistent",
            binding_id="resident:test-explicit-one",
            backend_route_id="one",
        ),
        system_instructions=portal_system_instructions("resident"),
    )
    session = RuntimeSession(
        mode="persistent",
        binding_id=request.session.binding_id,
        backend_session_id="",
        backend_route_id="one",
        is_new_backend_session=True,
    )

    with pytest.raises(
        codex_backend.CodexAppServerUnavailable,
        match="socket unavailable",
    ):
        codex_backend.CodexBackend(
            client_factory=client_factory,
        ).execute(
            BackendInvocation(
                request=request,
                session=session,
                user_prompt=request.prompt,
                system_context="rules",
                attach_system_context=True,
                is_new_backend_session=True,
            ),
            backend_contracts.BackendContext(
                emit_event=lambda *_args: None,
                cancellation_requested=lambda: False,
                set_interrupt_target=lambda *_args: None,
            ),
        )

    assert one_client.start_thread.call_count == 1
    assert zero_client.mock_calls == []


def test_codex_account_route_never_moves_an_existing_thread() -> None:
    """已有线程的 codex 轮次在账号不可用时报错，不迁移到其他账号"""
    one_client = mock.Mock()
    one_client.run_turn.side_effect = (
        codex_backend.CodexAppServerUnavailable("socket unavailable")
    )
    zero_client = mock.Mock()

    def client_factory(socket_path):
        return (
            one_client
            if str(socket_path).endswith("codex-app-server-one.sock")
            else zero_client
        )

    request = ExecutionRequest.new(
        actor="sample_reader",
        prompt="continue",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(
            mode="persistent",
            binding_id="provider:test-session",
            backend_route_id="one",
        ),
        system_instructions=portal_system_instructions("resident"),
    )
    session = RuntimeSession(
        mode="persistent",
        binding_id="",
        backend_session_id="019f0000-0000-7000-8000-000000000601",
        backend_route_id="one",
        is_new_backend_session=False,
    )

    with pytest.raises(
        codex_backend.CodexAppServerUnavailable,
        match="socket unavailable",
    ):
        codex_backend.CodexBackend(
            client_factory=client_factory,
        ).execute(
            BackendInvocation(
                request=request,
                session=session,
                user_prompt=request.prompt,
                system_context="",
                attach_system_context=False,
                is_new_backend_session=False,
            ),
            backend_contracts.BackendContext(
                emit_event=lambda *_args: None,
                cancellation_requested=lambda: False,
                set_interrupt_target=lambda *_args: None,
            ),
        )
    assert zero_client.mock_calls == []


def test_codex_account_route_does_not_treat_auth_failure_as_failover() -> None:
    """codex 认证失败不触发账号回退"""
    one_client = mock.Mock()
    one_client.start_thread.side_effect = RuntimeError(
        "authentication failed"
    )
    zero_client = mock.Mock()

    def client_factory(socket_path):
        return (
            one_client
            if str(socket_path).endswith("codex-app-server-one.sock")
            else zero_client
        )

    request = ExecutionRequest.new(
        actor="sample_reader",
        prompt="new turn",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        session=SessionOptions(mode="persistent", binding_id="resident:test-auth"),
        system_instructions=portal_system_instructions("resident"),
    )
    with pytest.raises(RuntimeError, match="authentication failed"):
        codex_backend.CodexBackend(
            client_factory=client_factory,
        ).execute(
            BackendInvocation(
                request=request,
                session=RuntimeSession(
                    mode="persistent",
                    binding_id=request.session.binding_id,
                    backend_session_id="",
                    is_new_backend_session=True,
                ),
                user_prompt=request.prompt,
                system_context="rules",
                attach_system_context=True,
                is_new_backend_session=True,
            ),
            backend_contracts.BackendContext(
                emit_event=lambda *_args: None,
                cancellation_requested=lambda: False,
                set_interrupt_target=lambda *_args: None,
            ),
        )
    assert zero_client.mock_calls == []


def test_new_codex_session_moves_capacity_when_backend_falls_back(
    tmp_path: Path,
) -> None:
    """新 codex 会话回退到其他账号时执行的容量分区和绑定路线随之迁移"""
    scheduler, server, thread, client, service = _stack(tmp_path)

    class FallbackBackend:
        provider_id = "codex"

        def execute(self, invocation, context):
            context.reassign_backend_route("zero")
            return BackendResult(
                output={"response": "fallback completed"},
                backend_session_id=(
                    "019f0000-0000-7000-8000-000000000802"
                ),
                backend_route_id="zero",
            )

        def interrupt(self, **_values):
            return True

    runtime = ExecutionRuntime(
        agent_providers={"codex": FallbackBackend()},
        state_root=tmp_path / "execution-state",
    )
    worker = ExecutionWorker(
        build_id="test-build",
        worker_id="explore-1:fallback-worker",
        scheduler=SchedulerClient(
            worker_id="explore-1:fallback-worker",
            build_id="test-build",
            capabilities=["agent_session:codex"],
            service_client=client,
        ),
        runtime=runtime,
        state_root=tmp_path / "execution-state",
    )
    try:
        worker.register()
        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="use the available account",
            service_type="agent_session",
            provider="codex",
            operation="turn",
            session=SessionOptions(
                mode="persistent",
                binding_id="resident:test-fallback",
            ),
            system_instructions=portal_system_instructions("resident"),
        )
        service.submit(request)

        assert run_worker_once(worker) is True

        snapshot = service.status(request.execution_id)
        assert snapshot["status"] == "completed"
        assert snapshot["request"]["session"]["backend_route_id"] == "zero"
        assert snapshot["result"]["backend_route_id"] == "zero"
        assert any(
            event["type"] == "capacity_reassigned"
            and event["details"] == {
                "from_capacity_key": "codex:one",
                "to_capacity_key": "codex:zero",
            }
            for event in snapshot["events"]
        )
        binding = runtime.sessions.provider_binding(
            "resident:test-fallback"
        )
        assert binding["backend_route_id"] == "zero"
        assert scheduler.health()["active_count"] == 0
    finally:
        _close(server, thread)


from tests.cases.compatibility_test_support import setUpModule, tearDownModule
