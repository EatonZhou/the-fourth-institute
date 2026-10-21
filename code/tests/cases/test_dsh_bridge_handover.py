"""The DSH bridge keeps a turn across Workers and never sends a turn's input twice.

The bridge (``management_portal/dsh_runtime/bridge.mjs``) runs here on a
stand-in for the DSH services (``fixtures/dsh_bridge_fake_runtime.mjs``):
its agent answers each input with ``done:<input>:<inputs taken>``.

:module: execution.service
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from management_portal.native_agent_transport import NativeAgentClient, NativeAgentDetached, ToolCallHandedOver
from management_common.local_ipc import rendezvous_path

ROOT = Path(__file__).resolve().parents[2]
BRIDGE = ROOT / "management_portal" / "dsh_runtime" / "bridge.mjs"
FAKE_RUNTIME = Path(__file__).resolve().parent / "fixtures" / "dsh_bridge_fake_runtime.mjs"
SESSION_ID = "dsh-0123456789abcdef"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None
    or not (BRIDGE.parent / "node_modules" / "@deepseek-ai" / "dsh-llm").is_dir(),
    reason="the DSH runtime packages are not installed",
)


@pytest.fixture
def bridge(tmp_path):
    processes = []

    def start(earlier: dict | None = None) -> NativeAgentClient:
        socket_path = tmp_path / f"bridge-{len(processes)}.sock"
        config = tmp_path / f"config-{len(processes)}.json"
        config.write_text(json.dumps({
            "socketPath": str(socket_path), "sessionId": SESSION_ID, "cwd": str(tmp_path), "route": "deepseek",
            "identityFingerprint": hashlib.sha256(b"{}").hexdigest(), **({"earlier": earlier} if earlier else {}),
        }))
        process = subprocess.Popen(
            ["node", str(FAKE_RUNTIME), str(BRIDGE), str(config)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        processes.append(process)
        assert process.stdout.readline().strip() == "ready", process.stderr.read()
        # The stand-in emits its setup receipt before listenLocal publishes
        # the owned endpoint. Observe publication before the first request.
        deadline = time.monotonic() + 5
        while not rendezvous_path(socket_path).exists():
            assert process.poll() is None, process.stderr.read()
            assert time.monotonic() < deadline, 'bridge endpoint was not published'
            time.sleep(0.01)
        client = NativeAgentClient(socket_path)
        client._request("health", response_timeout=5)
        return client

    yield start
    for process in processes:
        process.kill()
        process.wait(5)


def _turn(tmp_path, prompt: str, *, description: str = "Look something up", **extra) -> dict:
    return {
        "session_id": SESSION_ID, "cwd": str(tmp_path), "provider_profile": "deepseek",
        "resident_environment": {}, "is_owner": True, "permission_profile": "owner",
        "turn_id": "turn-1", "prompt": prompt, "model": "deepseek-v4-flash", "effort": "high",
        "tools": [{"name": "lookup", "description": description, "inputSchema": {"type": "object"}}], **extra,
    }


def test_a_retiring_worker_leaves_the_turn_and_its_tool_call_to_the_next_worker(bridge, tmp_path) -> None:
    """DSH 交接把轮次和工具调用留给下一个 Worker

    旧 Worker 断开时 bridge 不取消这一轮，未完成的工具调用交给按 turn_id 接回的新 Worker，输入只发送一次。
    """
    client = bridge()
    call_reached_retiring_worker = threading.Event()
    left: list[BaseException] = []

    def retiring_worker_tool(_call: dict) -> dict:
        call_reached_retiring_worker.set()
        raise ToolCallHandedOver("Worker handover: the next Worker runs this tool call")

    def retiring_worker() -> None:
        try:
            client._request("turn/run", _turn(tmp_path, "use tool"), tool_callback=retiring_worker_tool,
                            response_timeout=30, detach_requested=lambda: call_reached_retiring_worker.is_set())
        except BaseException as exc:  # noqa: BLE001 - the test inspects it
            left.append(exc)

    thread = threading.Thread(target=retiring_worker)
    thread.start()
    thread.join(20)
    assert call_reached_retiring_worker.is_set() and not thread.is_alive()
    assert len(left) == 1 and isinstance(left[0], NativeAgentDetached)

    # The next Worker's tools differ (a newer Portal); it attaches by turn id.
    events: list[dict] = []
    calls: list[dict] = []
    result = client._request(
        "turn/run", _turn(tmp_path, "use tool", description="Look something up (v2)", continue_prompt="continue"),
        event_callback=events.append, response_timeout=30,
        tool_callback=lambda call: calls.append(call) or {"content": [{"type": "text", "text": "42"}]},
    )

    # The turn was neither cancelled nor given its input again.
    assert result["response"] == "done:use tool:1"
    samples = [event["params"]["tokenUsage"] for event in events if event.get("method") == "thread/tokenUsage/updated"]
    assert samples == [{"context": {"inputTokens": 55}, "modelContextWindow": 1000000}]
    assert [call["call_id"] for call in calls] == ["call-1"]
    completed = [event["params"]["item"] for event in events if event.get("method") == "item/completed"]
    assert any(item.get("output") == [{"type": "text", "text": "42"}] for item in completed)


@pytest.mark.parametrize(("end", "continue_prompt"), [("aborted", "continue"), ("none", "continue"), ("aborted", "")])
def test_a_turn_that_stopped_is_continued_instead_of_asked_again(bridge, tmp_path, end, continue_prompt) -> None:
    """DSH 停下的轮次以 continue 续接

    中断过或进程停在中间的一轮由 bridge 发送 continue 续接并报告 turn/recovered，不重发原输入。
    """
    client = bridge(earlier={"turn_id": "turn-1", "end": end})
    events: list[dict] = []

    result = client._request(
        "turn/run", _turn(tmp_path, "the original prompt", **({"continue_prompt": continue_prompt} if continue_prompt else {})),
        event_callback=events.append, response_timeout=30,
    )

    assert result["response"] == "done:continue:1"
    assert [event["params"] for event in events if event["method"] == "turn/recovered"] == [
        {"threadId": SESSION_ID, "turnId": "turn-1", "delivery": "continued", "prompt": continue_prompt or "continue"},
    ]


def test_a_started_turn_this_process_never_took_is_delivered_for_the_first_time(bridge, tmp_path) -> None:
    """DSH 未收到的输入首次送达

    Portal 认为已开始而 DSH 从未收到的一轮，原输入第一次发送并报告 first_delivery。
    """
    client = bridge()
    events: list[dict] = []

    result = client._request(
        "turn/run", _turn(tmp_path, "the original prompt", continue_prompt="continue"),
        event_callback=events.append, response_timeout=30,
    )

    assert result["response"] == "done:the original prompt:1"
    assert [event["params"]["delivery"] for event in events if event["method"] == "turn/recovered"] == ["first_delivery"]


def test_a_turn_that_completed_without_a_worker_returns_its_answer(bridge, tmp_path) -> None:
    """DSH 无 Worker 时完成的轮次返回原结果

    没有 Worker 跟随时完成的一轮，接回时直接返回原结果，不再发送输入。
    """
    client = bridge(earlier={"turn_id": "turn-1", "end": "completed"})

    result = client._request(
        "turn/run", _turn(tmp_path, "the original prompt", description="newer", continue_prompt="continue"),
        response_timeout=30,
    )

    assert result["response"] == "earlier answer"


def test_a_subagents_steps_reach_the_worker_under_its_call_and_end_in_its_card(bridge, tmp_path) -> None:
    """DSH 子 Agent 的步骤与卡片使用通用形状

    桥接插件把子会话的条目按父调用转成 subagent/event，DshSubagentCards 给出运行中卡片和完成卡片，卡片用量是子会话各次请求之和；不转发 DSH 为子 Agent 生成的系统提示。
    """
    # DSH runs a subagent in a session of its own inside the ``subagent`` call;
    # the Chat gets the same running and finished cards as from the other Providers.
    from management_portal.dsh_subagent_cards import DshSubagentCards
    from tests.cases import capability_contracts

    client = bridge()
    events: list[dict] = []
    result = client._request("turn/run", _turn(tmp_path, "use subagent"), event_callback=events.append, response_timeout=30)
    cards = DshSubagentCards(SESSION_ID)
    extra = [card for event in events for card in cards.observe(event)]

    assert result["response"] == "done:use subagent:1"
    capability_contracts.check_subagent_events([*events, *extra])
    child = [event["params"] for event in events if event["method"] == "subagent/event"]
    assert {(params["traceKey"], params["parentToolUseId"]) for params in child} == {("5f0c-child", "call-1-subagent")}
    # The child's task is its input; DSH's own rules for it are not Portal's.
    assert [params["event"]["params"]["item"]["type"] for params in child if "item" in params["event"]["params"]] == [
        "providerInput", "agentMessage", "dynamicToolCall", "dynamicToolCall", "agentMessage"]
    # Each child request gives its own token use (input with the cache reads).
    assert [params["event"]["params"]["tokenUsage"]["last"] for params in child
            if params["event"]["method"] == "thread/tokenUsage/updated"] == [
        {"inputTokens": 10, "cachedInputTokens": 0, "outputTokens": 2, "reasoningOutputTokens": 0, "totalTokens": 12},
        {"inputTokens": 50, "cachedInputTokens": 30, "outputTokens": 5, "reasoningOutputTokens": 0, "totalTokens": 55}]
    [card] = [event["params"]["item"]["run"] for event in extra if event["method"] == "item/completed"]
    assert (card["tool_use_id"], card["description"], card["agent_type"], card["model"], card["status"], card["result"]) == (
        "call-1-subagent", "Count files", "general-purpose", "deepseek-v4-flash", "completed", "There are 3 files.")
    # The card holds the task the call gave and names the child session's trace, not the call.
    assert (card["prompt"], card["trace_ref"]) == ("How many files?", "5f0c-child")
    # Its token use is the child's requests added up.
    assert card["usage"] == {"total_tokens": 67, "input_tokens": 60, "cached_input_tokens": 30, "output_tokens": 7}
    assert [(step["kind"], step.get("tool_name"), step["text"]) for step in card["steps"]] == [
        ("message", None, "Counting."), ("tool", "bash", "ls | wc -l"), ("message", None, "There are 3 files.")]


def test_one_subagent_stops_as_its_parent_would_stop_it_while_the_turn_goes_on(bridge, tmp_path, monkeypatch) -> None:
    """DSH 单独停止一个子 agent，这一轮继续

    运行中卡片点停止时，DSH 桥接以父 agent 的身份取消这次 subagent 调用的子 agent；调用以 cancelled 和已有的部分回答返回，模型继续这一轮。桥接先发 subagent/stopping，卡片以已停止结束；未知调用返回 unknown_subagent，这一轮结束后返回 no_active_turn。

    :module: execution.providers
    :importance: P1
    """
    # Stop on a running card: the bridge cancels that subagent; its call
    # returns "cancelled" with the partial answer, the model goes on.
    from management_common.provider_subagent_runs import STOPPING_METHOD
    from management_portal.dsh_client import DshClient
    from management_portal.dsh_subagent_cards import DshSubagentCards

    client = bridge()
    monkeypatch.setattr(DshClient, "_transport", lambda self, session_id: client)
    events: list[dict] = []
    child_working = threading.Event()
    outcome: dict = {}

    def follow(event: dict) -> None:
        events.append(event)
        if event["method"] == "subagent/event":
            child_working.set()

    worker = threading.Thread(target=lambda: outcome.update(client._request(
        "turn/run", _turn(tmp_path, "use slow subagent"), event_callback=follow, response_timeout=30)))
    worker.start()
    assert child_working.wait(10)
    dsh = DshClient()
    assert dsh.stop_subagent("", SESSION_ID, tool_use_id="call-9-subagent") == {"stopped": False, "reason": "unknown_subagent"}
    assert dsh.stop_subagent("", SESSION_ID, tool_use_id="call-1-subagent") == {"stopped": True}
    worker.join(30)

    assert outcome["response"] == "done:use slow subagent:1"
    assert dsh.stop_subagent("", SESSION_ID, tool_use_id="call-1-subagent") == {"stopped": False, "reason": "no_active_turn"}
    assert [event["params"]["toolUseId"] for event in events if event["method"] == STOPPING_METHOD] == ["call-1-subagent"]
    cards = DshSubagentCards(SESSION_ID)
    observed = [card for event in events for card in cards.observe(event)]
    [(end, card)] = [(index, card["params"]["item"]["run"]) for index, card in enumerate(observed)
                     if card["method"] == "item/completed"]
    assert (card["tool_use_id"], card["status"]) == ("call-1-subagent", "stopped")
    assert card["result"].startswith("subagent run was cancelled")
    # The step the child settles after its call returned gives no new running card.
    assert "Late words." in json.dumps(events)
    assert not observed[end + 1:]


def test_a_bash_result_settled_before_its_output_is_read_again_from_the_shell(bridge, tmp_path) -> None:
    """DSH bash 在读到输出前结束时，从 shell 重新读取完整输出

    DSH 的持久 bash 从 /proc 判断命令已结束、还没读到命令的最后输出时，结果没有退出码行；桥接的 tools/post-execute 钩子稍等片刻，从同一 shell 的回滚里按这次命令的起止标记取出完整输出并补上退出码。

    :module: execution.providers
    :importance: P1
    """
    # DSH's bash can take a command for done from /proc before it reads the
    # output: the result has no exit code line, and the output comes just after.
    client = bridge()
    events: list[dict] = []
    result = client._request("turn/run", _turn(tmp_path, "use bash"), event_callback=events.append, response_timeout=30)

    assert result["response"] == "done:use bash:1"
    outputs = [event["params"]["item"].get("output") for event in events if event.get("method") == "item/completed"]
    assert [{"type": "text", "text": "12:34:56\n[Command finished with exit code 0]"}] in outputs


def test_the_whole_output_is_the_last_commands_and_only_once_it_ended() -> None:
    """重新读取的 bash 输出只取最后一条已结束的命令

    finishedBashOutput 只取回滚中最后一个起始标记对应、已出现结束标记的命令；命令未结束、开头与 DSH 已返回的部分不符或没有标记时保持原结果；空输出只给退出码行，超长输出按 DSH 的上限截断。

    :module: execution.providers
    """
    script = """
const { finishedBashOutput } = await import(process.argv[1]);
const mark = (n, end) => `__DSH_PERSISTENT_BASH_START_${n}__\\n` + (end === undefined ? 'running\\n' : `out ${n}\\n__DSH_PERSISTENT_BASH_END_${n}:${end}\\n`);
const a = '11111111-1111-1111-1111-111111111111', b = '22222222-2222-2222-2222-222222222222';
const long = 'x'.repeat(16010);
console.log(JSON.stringify([
  finishedBashOutput(mark(a, 0) + 'dsh> ' + mark(b, 3) + 'dsh> ', ''),
  finishedBashOutput(mark(a, 0) + mark(b), 'running'),
  finishedBashOutput(mark(a, 0), 'other'),
  finishedBashOutput(`__DSH_PERSISTENT_BASH_START_${a}__\\n__DSH_PERSISTENT_BASH_END_${a}:1\\n`, ''),
  finishedBashOutput(`__DSH_PERSISTENT_BASH_START_${a}__\\n${long}\\n__DSH_PERSISTENT_BASH_END_${a}:0\\n`, '').length,
  finishedBashOutput('no marks here', ''),
]));
"""
    out = subprocess.run(["node", "--input-type=module", "-e", script, str(BRIDGE)],
                         capture_output=True, text=True, timeout=30, check=True).stdout
    last, running, other, empty, clipped, none = json.loads(out)
    assert last == "out 22222222-2222-2222-2222-222222222222\n[Command finished with exit code 3]"
    # Not yet ended, or not the command DSH already returned the start of: left as it was.
    assert running is None and other is None and none is None
    assert empty == "[Command finished with exit code 1]"
    assert 16000 < clipped < 16300
