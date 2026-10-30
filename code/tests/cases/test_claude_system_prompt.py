"""Claude Code starts with its own system prompt and Portal's rules every time.

The CLI's system prompt is process configuration: the session transcript does
not keep it, and a resumed process given none ran with an empty one (the SDK
turns a missing system prompt into ``--system-prompt ""``).  Until 10-07 only
a session's first turn had either.  Portal now sends its rules on every Claude
turn, and the daemon always starts Claude Code with the ``claude_code`` preset.

:module: spec.design-governance
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock

from management_portal.execution import claude_backend, dsh_backend
from management_portal.execution.contracts import ExecutionRequest, SessionOptions
from management_portal.execution.provider_catalog import AGENT_PROVIDERS
from tests.cases.test_wall08_gap_helper05 import claude_owner  # noqa: F401 - pytest fixture

RULES = '<portal-system-prompt role="resident">the core rules</portal-system-prompt>'


def _invocation(provider: str, *, attach: bool):
    request = ExecutionRequest.new(
        service_type="agent_session", provider=provider, operation="turn", actor="test",
        agent_id="resident:test", cwd="/workspace", prompt="the next task",
        session=SessionOptions(mode="persistent", binding_id="resident:test", backend_route_id="deepseek_primary"),
        runtime_options={"turn_id": "turn-2", "session_key": "resident:test", "is_owner": True},
    )
    return SimpleNamespace(
        request=request, session=SimpleNamespace(backend_route_id="deepseek_primary", backend_session_id="native"),
        user_prompt="the next task", system_context=RULES, attach_system_context=attach, recovery_checkpoint={},
    )


def _system_prompt_sent(provider: str, backend, *, attach: bool) -> str:
    client = mock.Mock()
    client.run_turn.return_value = SimpleNamespace(response="done", session_id="native", turn_id="turn-2", usage={})
    context = mock.Mock(is_cancelled=mock.Mock(return_value=False), is_handover=mock.Mock(return_value=False))
    with mock.patch.object(backend, "client_factory", staticmethod(lambda: client)), \
            mock.patch.object(backend, "prepare_native_session", lambda *_args, **_kwargs: None), \
            mock.patch.object(backend, "observe_turn_usage", lambda *_args, **_kwargs: None), \
            mock.patch.object(backend, "record_provider_compaction", lambda *_args, **_kwargs: None), \
            mock.patch("management_portal.execution.native_harness_tools.prepare_tools", return_value=([], None)):
        backend().execute(_invocation(provider, attach=attach), context)
    return client.run_turn.call_args.kwargs["system_prompt_append"]


def test_every_claude_turn_carries_the_portal_rules() -> None:
    """Claude 每一轮的系统提示都带上 Portal 规则，包括会话的后续轮次"""
    assert _system_prompt_sent("claude", claude_backend.ClaudeBackend, attach=True) == RULES
    # A later turn of the session: Portal attached the rules at its start, and
    # Claude Code needs them again in this turn's system prompt.
    assert _system_prompt_sent("claude", claude_backend.ClaudeBackend, attach=False) == RULES


def test_dsh_replacement_system_prompt_keeps_the_full_rules_each_turn() -> None:
    """dsh 每一轮都以完整的 Portal 规则作为替换系统提示

    :module: platform.core"""
    assert AGENT_PROVIDERS["dsh"].system_context_every_turn
    assert _system_prompt_sent("dsh", dsh_backend.DshBackend, attach=True) == RULES
    assert _system_prompt_sent("dsh", dsh_backend.DshBackend, attach=False) == RULES


def test_claude_code_always_starts_with_its_own_system_prompt(claude_owner, monkeypatch, tmp_path) -> None:  # noqa: F811
    """Claude Code 会话总以自带的 claude_code 系统提示启动，Portal 规则追加在其后"""
    owner = claude_owner
    profile = {"id": "fixture", "default_model": "fixture", "models": [{"id": "fixture", "enabled": True}]}
    monkeypatch.setattr(owner, "_provider_profile", lambda *_args: profile)
    monkeypatch.setattr(owner, "_provider_environment", lambda *_args: ({}, profile))
    monkeypatch.setattr(owner, "ClaudeAgentOptions", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(owner, "ClaudeSDKClient", lambda options: SimpleNamespace(
        options=options, connect=AsyncMock(), disconnect=AsyncMock(),
    ))

    async def check() -> None:
        daemon = owner.ClaudeCodeDaemon()
        base = {"session_key": "fixture", "cwd": str(tmp_path / "cwd"), "config_dir": str(tmp_path / "config")}
        first = await daemon._handle_for({**base, "system_prompt_append": RULES})
        assert first.client.options.system_prompt == {"type": "preset", "preset": "claude_code", "append": RULES}
        # The next turn brings the same rules: the same process goes on, and
        # its prompt cache with it.
        assert await daemon._handle_for({**base, "system_prompt_append": RULES}) is first
        await daemon.close_session({"session_key": "fixture"})
        # Without Portal rules Claude Code still has its own system prompt,
        # never an empty one.
        bare = await daemon._handle_for(base)
        assert bare.client.options.system_prompt == {"type": "preset", "preset": "claude_code"}
        await daemon.close_session({"session_key": "fixture"})

    asyncio.run(check())
