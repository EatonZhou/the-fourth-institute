"""The Claude daemon gives a running turn more input (``turn/steer``).

Claude Code takes a user message that arrives during a turn after the turn's
current step.  When the turn has no further step, the CLI answers the message
in a turn of its own right after; the daemon reads that turn as part of the
Portal turn, so the answer to the steered input is the turn's answer.

:module: resident.fork-handoff
:importance: P1
"""
from __future__ import annotations

import asyncio
from unittest import mock

import pytest

from tests.cases.claude_sdk_support import load_claude_module

daemon_module = load_claude_module("claude_code_daemon")



def _result(text: str, **usage):
    return daemon_module.ResultMessage(
        subtype="success", duration_ms=10, duration_api_ms=10, is_error=False, num_turns=1,
        session_id="native", result=text, errors=None, usage=usage or None,
    )


def _echo(message_uuid: str, text: str = "prompt"):
    return daemon_module.UserMessage(content=text, uuid=message_uuid)


class _Cli:
    """A Claude Code CLI whose turns the test scripts.

    ``script`` gets the list of UUIDs written so far and an event set on each
    write; it yields the messages of one ``receive_response`` call.
    """

    def __init__(self, *scripts):
        self.scripts = list(scripts)
        self.written: list[str] = []
        self.texts: list[object] = []
        self.wrote = asyncio.Event()

    async def query(self, prompt):
        async for message in prompt:
            self.written.append(str(message.get("uuid") or ""))
            self.texts.append(message["message"]["content"])
        self.wrote.set()

    def receive_response(self):
        return self.scripts.pop(0)(self)


async def _wait_written(cli: _Cli, count: int) -> None:
    while len(cli.written) < count:
        cli.wrote.clear()
        await asyncio.wait_for(cli.wrote.wait(), 5)


async def _run(cli: _Cli, steer_text: str = "The fruit is BANANA."):
    daemon = daemon_module.ClaudeCodeDaemon()
    handle = daemon_module.SessionHandle(key="resident:test", signature=(), client=cli, session_id="native")
    daemon._sessions["resident:test"] = handle
    events: list[dict] = []

    async def emit(event):
        events.append(event)

    params = {"session_key": "resident:test", "turn_id": "turn-1", "prompt": "run sleep, then name the fruit"}
    with mock.patch.object(daemon, "_handle_for", mock.AsyncMock(return_value=handle)):
        turn = asyncio.create_task(daemon.run_turn(params, emit))
        await _wait_written(cli, 1)
        steered = await daemon.steer({
            "session_key": "resident:test", "session_id": "native", "turn_id": "turn-1",
            "text": steer_text, "control_id": "control-1",
        })
        again = await daemon.steer({
            "session_key": "resident:test", "turn_id": "turn-1", "text": steer_text, "control_id": "control-1",
        })
        result = await asyncio.wait_for(turn, 5)
    after = await daemon.steer({"session_key": "resident:test", "turn_id": "turn-1", "text": "late"})
    return daemon, handle, steered, again, result, after, events


def test_input_taken_after_the_current_step_is_part_of_the_turn() -> None:
    """当前步骤之后被接收的插话属于本轮

    CLI 在正在运行的工具结果之后接收排队消息，这条插话计入当前轮。
    """
    async def turn(cli):
        yield _echo(cli.written[0])
        await _wait_written(cli, 2)
        # The CLI takes the queued message after the running tool's result.
        yield _echo(cli.written[1], "The fruit is BANANA.")
        yield _result("FRUIT=BANANA", input_tokens=7)

    cli = _Cli(turn)
    _daemon, handle, steered, again, result, after, _events = asyncio.run(_run(cli))

    assert steered["steered"] is True and again == {"steered": True, "duplicate": True}
    assert cli.texts[1] == "The fruit is BANANA." and len(cli.written) == 2
    assert result["response"] == "FRUIT=BANANA"
    assert after == {"steered": False, "reason": "no_active_turn"}
    assert not handle.pending_steers and not handle.steering_open


def test_input_the_cli_answers_after_the_turn_is_still_this_turn() -> None:
    """CLI 在本轮结束后才回答的插话仍属本轮

    插话发出时回答已在路上，插话等待该回答并仍归入本轮。
    """
    async def first(cli):
        yield _echo(cli.written[0])
        await _wait_written(cli, 2)
        # The answer was already on its way: the steered message waits for it.
        yield _result("FRUIT=NONE", input_tokens=5, output_tokens=2)

    async def second(cli):
        yield _echo(cli.written[1], "The fruit is BANANA.")
        yield _result("Now: FRUIT=BANANA", input_tokens=10, output_tokens=3)

    cli = _Cli(first, second)
    _daemon, _handle, steered, _again, result, _after, events = asyncio.run(_run(cli))

    assert steered["steered"] is True
    assert result["response"] == "Now: FRUIT=BANANA"
    assert result["usage"] == {"input_tokens": 15, "output_tokens": 5}
    texts = [(e["params"]["item"].get("phase"), e["params"]["item"]["text"])
             for e in events if e.get("method") == "item/completed" and e["params"]["item"].get("type") == "agentMessage"]
    assert texts == [("commentary", "FRUIT=NONE"), ("final_answer", "Now: FRUIT=BANANA")]


def test_steering_closes_when_the_answer_arrives() -> None:
    """回答到达时关闭插话通道

    本轮最终回答到达后，不再接收新的插话进入该轮。
    """
    gate = {}

    async def turn(cli):
        yield _echo(cli.written[0])
        yield _result("FRUIT=NONE")
        gate["answered"] = True

    async def run():
        daemon = daemon_module.ClaudeCodeDaemon()
        cli = _Cli(turn)
        handle = daemon_module.SessionHandle(key="resident:test", signature=(), client=cli, session_id="native")
        daemon._sessions["resident:test"] = handle
        seen = []

        async def emit(event):
            # Steering that arrives while the finished turn is being reported
            # would start a turn no Portal turn reads: it is refused.
            if event.get("method") == "turn/completed":
                seen.append(await daemon.steer({"session_key": "resident:test", "turn_id": "turn-1", "text": "late"}))

        with mock.patch.object(daemon, "_handle_for", mock.AsyncMock(return_value=handle)):
            result = await daemon.run_turn({"session_key": "resident:test", "turn_id": "turn-1", "prompt": "p"}, emit)
        return result, seen, cli

    result, seen, cli = asyncio.run(run())
    assert result["response"] == "FRUIT=NONE"
    assert seen == [{"steered": False, "reason": "turn_closing"}]
    assert len(cli.written) == 1


def test_a_cli_that_never_starts_on_the_steering_does_not_hold_the_turn() -> None:
    """CLI 一直不处理插话时不拖住本轮

    Claude Code CLI 没有开始处理插话消息时，本轮照常结束，不被插话挂起。
    """
    async def first(cli):
        yield _echo(cli.written[0])
        await _wait_written(cli, 2)
        yield _result("FRUIT=NONE")

    async def silent(_cli):
        await asyncio.Event().wait()
        yield  # pragma: no cover

    cli = _Cli(first, silent)
    with mock.patch.object(daemon_module, "STEER_START_SECONDS", 0.05):
        _daemon, handle, steered, _again, result, _after, _events = asyncio.run(_run(cli))
    assert steered["steered"] is True
    assert result["response"] == "FRUIT=NONE"
    assert not handle.pending_steers


def test_the_daemon_advertises_steering() -> None:
    """Claude Code daemon 声明支持插话

    daemon 的能力声明中包含轮内插话（steering），供执行层判断是否可以 Send now。
    """
    daemon = daemon_module.ClaudeCodeDaemon()
    with mock.patch.object(daemon_module, "_provider_environment", return_value=({}, {"models": []})):
        health = asyncio.run(daemon.health())
    assert health["capabilities"]["turn_steer"] is True
