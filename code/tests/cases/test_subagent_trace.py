"""A subagent the Resident's model starts works in a window of its own (CASE-A 10-07).

The Resident's trace holds what its model saw: the Agent call, its result and
the task notification of a run in the background.  The subagent's own steps
are a generic Sequence referenced by their own Agent row (``subagent_trace``);
the Agent row records the parent when known; they are never in the Resident's context, its
forks, or the context carried to another Provider.  The Chat shows the run as
a card once the model has its result.  Claude and Kimi Residents may start
subagents unless "允许开启 sub agent" is off, which takes the tool away.

:module: execution.providers
:importance: P1
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from pathlib import Path

import pytest

from management_common.json_columns import json_value
from management_portal.execution.agent_data_projection import AgentDataProjector
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.backend_contracts import BackendContext, BackendInvocation
from management_portal.execution.claude_backend import ClaudeBackend
from management_portal.execution.contracts import ExecutionRequest, SessionOptions
from management_portal.execution.session_manager import RuntimeSession
from tests.cases import capability_contracts
from tests.cases.test_resident_execution_protocol import resident_record  # noqa: F401 - fixture
from management_portal.society_service import resident_execution_engine_domain, resident_record_domain

AGENT = "resident_subagent_trace"
SINCE = "2026-10-07T00:00:00.000Z"


def _turn(agent: str = AGENT) -> tuple[AgentDataStore, AgentDataProjector, ExecutionRequest, str]:
    store = AgentDataStore()
    request = ExecutionRequest.new(
        actor="sample_admin", prompt="look into it", service_type="agent_session", provider="claude",
        operation="turn", agent_id=agent,
        session=SessionOptions(mode="persistent", binding_id=f"resident:{agent}", backend_route_id="one"),
    )
    session = RuntimeSession(
        mode="persistent", binding_id=request.session.binding_id, backend_session_id="session-1",
        backend_route_id="one", is_new_backend_session=False,
    )
    projector = AgentDataProjector(store)
    projector.begin(request, session, created_at=SINCE)
    projector.observe_invocation(request.execution_id, BackendInvocation(
        request=request, session=session, user_prompt=request.prompt, system_context="",
        attach_system_context=False, is_new_backend_session=False,
    ), [])
    return store, projector, request, store.latest_provider_session_ref(agent)


def _jsonl(path: Path, entries: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8")
    return path


def _untimed(steps: list[dict]) -> list[dict]:
    """A card's steps without when they happened; every finished call says when it started and ended."""

    for step in steps:
        if step["kind"] == "tool" and not step.get("running"):
            assert step["started_at"] <= step["finished_at"], step
    return [{key: value for key, value in step.items() if key not in {"started_at", "finished_at", "at"}}
            for step in steps]


NOTICE = (
    "<task-notification>\n<task-id>a1</task-id>\n<tool-use-id>toolu_agent</tool-use-id>\n"
    "<status>completed</status>\n<summary>Agent \"Survey\" finished</summary>\n<result>found 3 files</result>\n"
    "</task-notification>"
)


def _session(tmp_path: Path, *, ended: bool = True) -> Path:
    """A Claude session whose model started a subagent in the background and later heard it ended."""

    transcript = _jsonl(tmp_path / "session-1.jsonl", [
        {"type": "user", "uuid": "u1", "timestamp": "2026-10-07T00:00:01.000Z", "message": {"content": "look into it"}},
        {"type": "assistant", "uuid": "r1", "timestamp": "2026-10-07T00:00:02.000Z", "message": {"content": [
            {"type": "tool_use", "id": "toolu_agent", "name": "Agent",
             "input": {"description": "Survey", "prompt": "list files", "run_in_background": True}},
        ]}},
        {"type": "user", "uuid": "r2", "timestamp": "2026-10-07T00:00:02.100Z",
         "toolUseResult": {"isAsync": True, "status": "async_launched"},
         "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_agent", "content": "Async agent launched"}]}},
        # Portal's Send now, queued by Claude Code: Portal records it itself.
        {"type": "attachment", "uuid": "q1", "timestamp": "2026-10-07T00:00:02.500Z",
         "attachment": {"type": "queued_command", "prompt": "also check tests", "commandMode": "prompt"}},
        # Stamped when it was queued, written when the model took it.
        {"type": "attachment", "uuid": "n1", "timestamp": "2026-10-07T00:00:04.000Z", "attachment": {
            "type": "queued_command", "prompt": NOTICE, "commandMode": "task-notification",
            "origin": {"kind": "task-notification"}}},
        {"type": "assistant", "uuid": "r3", "timestamp": "2026-10-07T00:00:05.000Z", "message": {"content": [
            {"type": "text", "text": "The survey found 3 files."}]}},
    ])
    steps = [
        {"type": "user", "uuid": "s1", "timestamp": "2026-10-07T00:00:02.200Z", "isSidechain": True,
         "message": {"content": "list files"}},
        {"type": "assistant", "uuid": "s2", "timestamp": "2026-10-07T00:00:03.000Z", "isSidechain": True,
         "message": {"id": "msg_1", "model": "claude-sonnet-5-5", "stop_reason": "tool_use",
                     "usage": {"input_tokens": 100, "output_tokens": 10}, "content": [
                         {"type": "tool_use", "id": "toolu_ls", "name": "Bash", "input": {"command": "ls"}}]}},
        {"type": "user", "uuid": "s3", "timestamp": "2026-10-07T00:00:03.500Z", "isSidechain": True,
         "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_ls", "content": "a b c"}]}},
    ]
    if ended:
        steps.append({"type": "assistant", "uuid": "s4", "timestamp": "2026-10-07T00:00:03.900Z", "isSidechain": True,
                      "message": {"id": "msg_2", "model": "claude-sonnet-5-5", "stop_reason": "end_turn",
                                  "usage": {"input_tokens": 120, "output_tokens": 5},
                                  "content": [{"type": "text", "text": "found 3 files"}]}})
    folder = tmp_path / "session-1" / "subagents"
    _jsonl(folder / "agent-a1.jsonl", steps)
    (folder / "agent-a1.meta.json").write_text(json.dumps({
        "agentType": "Explore", "description": "Survey", "toolUseId": "toolu_agent", "requestShape": "background",
    }), encoding="utf-8")
    return transcript


class _Client:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run_turn(self, **kwargs):
        self.calls.append(kwargs)
        raise RuntimeError("not needed")


def test_the_card_is_an_artifact_of_the_trace_shown_in_the_chat(tmp_path: Path, resident_record) -> None:
    """子 agent 卡片是轨迹中的 artifact 并显示在 Chat

    子 agent 卡片记为 Resident 轨迹中的 artifact 段（重复观察只记一次），不属于会话 context；经执行事件进入 Chat 的消息引用这一段，内容与记录一致。

    :module: resident.conversation
    :design: decision_b1ab45c4b3ec
    """
    from management_common.provider_subagent_runs import run_item
    from management_portal.execution import event_projection
    from management_portal.society_service import resident_execution_work

    from management_portal.society_service import resident_workspace_domain
    resident_record["backend"] = "claude"
    resident_record_domain.save_resident_record(resident_record, commit_configuration=True)
    resident_id = resident_record["resident_id"]
    store, projector, request, session_ref = _turn(resident_id)
    run = {"tool_use_id": "toolu_agent", "agent_id": "child", "agent_type": "explore",
           "description": "inspect", "status": "completed", "result": "found 3 files", "items": []}
    event = {"method": "item/completed", "timestamp": "2026-10-07T00:00:06.000Z", "params": {
        "threadId": "session-1", "turnId": "turn-1", "item": run_item(run, turn_id="turn-1")}}
    for _ in range(2):
        projector.observe(provider="claude", backend_route_id="one", backend_session_id="session-1",
                          execution_id=request.execution_id, event=event)
    with store.database.read() as connection:
        rows = connection.execute("SELECT data_json FROM agent_operations WHERE kind='artifact' AND agent_id=%s", (resident_id,)).fetchall()
    cards = [json_value(row["data_json"]) for row in rows if json_value(row["data_json"]).get("metadata", {}).get("kind") == "subagent_run"]
    assert len(cards) == 1 and cards[0]["segment_type"] == "artifact"
    assert cards[0]["segment_id"] not in store.session_context_ids(session_ref)

    resident_execution_work.write(resident_record, {
         "execution_id": request.execution_id, "status": "running", "actor": request.actor, "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"})
    outputs = event_projection.execution_outputs_from_events([{
        "type": "provider_event", "execution_id": request.execution_id, "sequence": 1, "details": event,
    }])
    message, = resident_execution_engine_domain.persist_execution_display_messages(
        resident_id,  request.execution_id, outputs,
    )
    part, = message["content_parts"]
    assert part == cards[0]["content"] and part["type"] == "subagent_run" and part["result"] == "found 3 files"


def test_kimi_subagent_card_comes_with_the_runs_end() -> None:
    """Kimi 子 agent 结束时 Chat 得到它的卡片

    主 agent 启动的 Kimi 子 agent 在 subagent.completed/failed 时给出卡片：类型、任务、前台或后台、状态、结果与步骤；子 agent 再启动的子 agent 不出卡片。

    :design: decision_b1ab45c4b3ec
    """
    from management_portal.kimi_live_events import KimiLiveItems
    from tests.cases.test_live_provider_ingress import frame

    events: list[dict] = []
    live = KimiLiveItems("s", events.append, prompt_id="p")
    for typ, agent, payload in [
        ("tool.call.started", "main", {"toolCallId": "Agent_0", "name": "Agent",
                                       "args": {"description": "count", "prompt": "count the files"}}),
        ("subagent.spawned", "main", {"subagentId": "agent-0", "subagentName": "explore", "parentToolCallId": "Agent_0",
                                      "parentAgentId": "main", "description": "count", "runInBackground": False, "model": "kimi-k3"}),
        ("turn.started", "agent-0", {"prompt": "count files"}),
        ("tool.call.started", "agent-0", {"toolCallId": "Bash_0", "name": "Bash", "args": {"command": "ls | wc -l"}}),
        ("tool.result", "agent-0", {"toolCallId": "Bash_0", "output": "94"}),
        # A subagent of the subagent is not the Resident's own: no card.
        ("subagent.spawned", "agent-0", {"subagentId": "agent-1", "parentToolCallId": "Agent_1", "parentAgentId": "agent-0"}),
        ("subagent.completed", "agent-0", {"subagentId": "agent-1", "resultSummary": "nested"}),
        ("assistant.delta", "agent-0", {"delta": "94"}),
        ("turn.ended", "agent-0", {"reason": "completed"}),
        ("subagent.completed", "main", {"subagentId": "agent-0", "resultSummary": "94"}),
        ("subagent.spawned", "main", {"subagentId": "agent-2", "subagentName": "coder", "parentToolCallId": "Agent_2",
                                      "parentAgentId": "main", "description": "read", "runInBackground": True}),
        ("subagent.failed", "main", {"subagentId": "agent-2", "error": "boom"}),
    ]:
        live.observe(frame(typ, agent=agent, **payload))
    capability_contracts.check_subagent_events(events)
    cards = [event["params"] for event in events
             if event["method"] == "item/completed" and event["params"]["item"]["type"] == "subagentRun"]
    assert [card["turnId"] for card in cards] == ["p", "p"]
    first, second = (card["item"]["run"] for card in cards)
    assert (first["tool_use_id"], first["agent_type"], first["status"], first["result"], first["background"]) == (
        "Agent_0", "explore", "completed", "94", False)
    # The card holds the task the main agent's call gave and names the run's own trace.
    assert (first["prompt"], first["trace_ref"]) == ("count the files", "s:agent-0")
    assert _untimed(first["steps"]) == [{"kind": "tool", "tool_name": "Bash", "text": "ls | wc -l", "error": False},
                                        {"kind": "message", "text": "94"}]
    assert (second["tool_use_id"], second["status"], second["result"], second["background"]) == ("Agent_2", "failed", "boom", True)
    # While the main agent waits, each change of a run gives its running card.
    progress = [event["params"] for event in events if event["method"] == "subagent/progress"]
    assert {card["turnId"] for card in progress} == {"p"}
    assert [(card["item"]["run"]["tool_use_id"], [(step["kind"], step.get("running", False)) for step in card["item"]["run"]["steps"]])
            for card in progress] == [
        ("Agent_0", []), ("Agent_0", [("tool", True)]), ("Agent_0", [("tool", False)]),
        ("Agent_0", [("tool", False), ("message", False)]), ("Agent_2", []),
    ]


def test_codex_subagent_cards_follow_the_child_threads(tmp_path: Path) -> None:
    """Codex 子 agent 的实时卡片与结果卡片

    Codex 父线程的 subAgentActivity 与子线程自己的条目给出实时卡片和结果卡片：后台运行、昵称、任务名、步骤、结束或中断状态；孙级子 agent 没有卡片；已结束的子 agent 被 followup_task 派新任务（interacted）时重新加入它的线程，以只含这次步骤和结果的新卡片运行；事件符合统一的子 agent 事件形状。

    Codex 0.162: the parent thread says a subagent started and ended; its steps come on its own thread.

    :importance: P2
    :design: decision_b1ab45c4b3ec
    """
    from management_portal.codex_app_server_client import CodexAppServerClient
    from management_portal.codex_app_server_protocol import TurnWaiter
    from management_portal.codex_subagent_cards import CodexSubagentCards

    client = CodexAppServerClient(tmp_path / "unused.sock")
    output: list[dict] = []
    client.work.turns["root"] = TurnWaiter(thread_id="root", turn_id="t", event_callback=output.append)
    followed: list[str] = []
    client.subagents.follow, client.subagents.release = followed.append, lambda child: None

    def child_started(child: str, parent: str = "root") -> None:
        client.events.dispatch_notification("thread/started", {"thread": {
            "id": child, "agentNickname": "Boyle", "agentRole": None, "model": "gpt-6.1-sol",
            "source": {"subAgent": {"thread_spawn": {"parent_thread_id": parent, "depth": 1}}}}})

    def item(thread: str, method: str, body: dict) -> None:
        client.events.dispatch_notification(method, {"threadId": thread, "turnId": "t" if thread == "root" else "c", "item": body})

    def activity(child: str, kind: str, call: str) -> None:
        item("root", "item/completed", {"type": "subAgentActivity", "id": call, "kind": kind, "agentThreadId": child,
                                        "agentPath": "/root/count_files", "model": "gpt-6.1-sol" if kind == "started" else None})

    command = {"type": "commandExecution", "id": "exec-1", "command": "/bin/bash -lc 'sleep 6 && ls | wc -l'"}
    client.events.dispatch_notification("turn/started", {"threadId": "root", "turn": {"id": "t", "status": "inProgress"}})
    child_started("child-a")
    activity("child-a", "started", "call_spawn_a")
    item("child-a", "item/started", {**command, "status": "inProgress"})
    item("child-a", "item/completed", {**command, "status": "completed", "exitCode": 0})
    child_started("grandchild", parent="child-a")
    item("grandchild", "item/completed", {"type": "agentMessage", "id": "g", "text": "nested"})
    item("child-a", "item/completed", {"type": "agentMessage", "id": "m", "text": "94 files", "phase": "final_answer"})
    activity("child-a", "completed", "subagent-completed-a")
    child_started("child-b")
    activity("child-b", "started", "call_spawn_b")
    item("child-b", "item/completed", {**command, "id": "exec-2", "status": "failed", "exitCode": 2})
    activity("child-b", "interrupted", "subagent-interrupted-b")
    # The finished child-a gets more work (``followup_task``): it is joined again, which says who it is.
    activity("child-a", "interacted", "call_followup_a")
    child_started("child-a")
    # The thread's total counts its first run too; the follow-up's card counts only its own requests.
    client.events.dispatch_notification("thread/tokenUsage/updated", {"threadId": "child-a", "turnId": "c2", "tokenUsage": {
        "total": {"totalTokens": 1700, "inputTokens": 1600, "cachedInputTokens": 900, "outputTokens": 100, "reasoningOutputTokens": 0},
        "last": {"totalTokens": 700, "inputTokens": 650, "cachedInputTokens": 300, "outputTokens": 50, "reasoningOutputTokens": 0}}})
    item("child-a", "item/completed", {"type": "agentMessage", "id": "m2", "text": "32 css files", "phase": "final_answer"})
    activity("child-a", "completed", "subagent-completed-a2")
    deadline = time.monotonic() + 5
    while len(followed) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert followed == ["child-a", "child-b", "child-a"]

    cards = CodexSubagentCards("root")
    events = [extra for event in output for extra in cards.observe(event)]
    capability_contracts.check_subagent_events([*output, *events])
    progress = [event["params"]["item"]["run"] for event in events if event["method"] == "subagent/progress"]
    assert [(run["tool_use_id"], run["agent_type"], run["description"], [(step["kind"], step.get("running", False)) for step in run["steps"]])
            for run in progress] == [
        ("child-a", "Boyle", "", []),
        ("child-a", "Boyle", "count_files", []),
        ("child-a", "Boyle", "count_files", [("tool", True)]),
        ("child-a", "Boyle", "count_files", [("tool", False)]),
        ("child-a", "Boyle", "count_files", [("tool", False), ("message", False)]),
        ("child-b", "Boyle", "", []),
        ("child-b", "Boyle", "count_files", []),
        ("child-b", "Boyle", "count_files", [("tool", False)]),
        ("child-a", "", "count_files", []),
        ("child-a", "Boyle", "count_files", []),
        ("child-a", "Boyle", "count_files", []),
        ("child-a", "Boyle", "count_files", [("message", False)]),
    ]
    first, second, again = (event["params"] for event in events if event["method"] == "item/completed")
    assert (first["threadId"], first["turnId"]) == ("root", "t")
    run = first["item"]["run"]
    assert (run["tool_use_id"], run["end_id"], run["status"], run["result"], run["background"], run["model"]) == (
        "child-a", "subagent-completed-a", "completed", "94 files", True, "gpt-6.1-sol")
    assert _untimed(run["steps"]) == [{"kind": "tool", "tool_name": "Shell", "text": "sleep 6 && ls | wc -l", "error": False},
                                      {"kind": "message", "text": "94 files"}]
    run = second["item"]["run"]
    assert (run["status"], run["steps"][0]["error"]) == ("stopped", True)
    # The follow-up is a run of its own: only its steps, its result, from when it started.
    run = again["item"]["run"]
    assert (run["tool_use_id"], run["end_id"], run["result"], run["agent_type"]) == (
        "child-a", "subagent-completed-a2", "32 css files", "Boyle")
    assert _untimed(run["steps"]) == [{"kind": "message", "text": "32 css files"}]
    assert run["started_at"] >= first["item"]["run"]["finished_at"]
    assert (run["usage"]["total_tokens"], run["usage"]["input_tokens"], run["usage"]["output_tokens"]) == (700, 650, 50)


def test_codex_subagent_cards_carry_the_task_the_token_use_and_when_each_step_ran(tmp_path: Path) -> None:
    """Codex 子 agent 卡片带任务全文、token 用量和每步时间

    Codex 子 agent 卡片的 prompt 来自子线程 preview，用量来自子线程 tokenUsage，工具步骤带 startedAtMs/completedAtMs 换算的时间。

    The joined child thread previews its task; its token use and its items' times come on its own thread.

    :importance: P2
    """
    from management_portal.codex_app_server_client import CodexAppServerClient
    from management_portal.codex_app_server_protocol import TurnWaiter
    from management_portal.codex_subagent_cards import CodexSubagentCards

    client = CodexAppServerClient(tmp_path / "unused.sock")
    output: list[dict] = []
    client.work.turns["root"] = TurnWaiter(thread_id="root", turn_id="t", event_callback=output.append)
    client.subagents.follow = client.subagents.release = lambda child: None
    client.rpc.request = lambda method, params, **options: {"thread": {
        "id": "child-a", "preview": "Count the files under src/", "agentNickname": "Boyle", "model": "gpt-6.1-sol",
    }} if method == "thread/resume" else {}

    def child(method: str, **params) -> None:
        client.events.dispatch_notification(method, {"threadId": "child-a", "turnId": "child-turn", **params})

    def activity(kind: str, call: str) -> None:
        client.events.dispatch_notification("item/completed", {"threadId": "root", "turnId": "t", "item": {
            "type": "subAgentActivity", "id": call, "kind": kind, "agentThreadId": "child-a", "agentPath": "/root/count"}})

    command = {"type": "commandExecution", "id": "exec-1", "command": "ls src | wc -l"}
    client.events.dispatch_notification("turn/started", {"threadId": "root", "turn": {"id": "t", "status": "inProgress"}})
    activity("started", "call_spawn_a")
    # The client joins the running child thread (``thread/resume``), which says who it is and its task.
    type(client.subagents).follow(client.subagents, "child-a")
    child("item/started", startedAtMs=1791732242000, item={**command, "status": "inProgress"})
    child("item/completed", completedAtMs=1791732243500, item={**command, "status": "completed", "exitCode": 0})
    child("thread/tokenUsage/updated", tokenUsage={
        "total": {"totalTokens": 1000, "inputTokens": 950, "cachedInputTokens": 600, "outputTokens": 50,
                  "reasoningOutputTokens": 20},
        "last": {"totalTokens": 1000, "inputTokens": 950, "cachedInputTokens": 600, "outputTokens": 50,
                 "reasoningOutputTokens": 20}})
    child("thread/tokenUsage/updated", tokenUsage={
        "total": {"totalTokens": 1300, "inputTokens": 1200, "cachedInputTokens": 800, "outputTokens": 100,
                  "reasoningOutputTokens": 40},
        "last": {"totalTokens": 300, "inputTokens": 250, "cachedInputTokens": 200, "outputTokens": 50, "reasoningOutputTokens": 20}})
    child("item/completed", completedAtMs=1791732244000, item={"type": "agentMessage", "id": "m", "text": "12 files"})
    activity("completed", "subagent-completed-a")

    cards = CodexSubagentCards("root")
    events = [extra for event in output for extra in cards.observe(event)]
    capability_contracts.check_subagent_events([*output, *events])
    (done,) = [event["params"]["item"]["run"] for event in events if event["method"] == "item/completed"]
    assert (done["prompt"], done["trace_ref"], done["tool_use_id"], done["agent_type"]) == (
        "Count the files under src/", "child-a", "child-a", "Boyle")
    assert done["usage"] == {"total_tokens": 1300, "input_tokens": 1200, "cached_input_tokens": 800,
                             "output_tokens": 100, "reasoning_tokens": 40}
    assert done["steps"] == [
        {"kind": "tool", "tool_name": "Shell", "text": "ls src | wc -l", "error": False,
         "started_at": "2026-10-11T15:24:02.000Z", "finished_at": "2026-10-11T15:24:03.500Z"},
        {"kind": "message", "text": "12 files", "at": "2026-10-11T15:24:04.000Z"},
    ]
    # The running card showed the token use as it came.
    progress = [event["params"]["item"]["run"] for event in events if event["method"] == "subagent/progress"]
    assert progress[-1]["usage"]["total_tokens"] == 1300 and progress[-1]["prompt"] == "Count the files under src/"


def test_codex_stops_one_subagent_by_interrupting_its_own_thread_while_the_turn_goes_on(tmp_path: Path) -> None:
    """Codex 单独停止一个子 agent：中断它自己的子线程，卡片以已停止结束，父轮次继续

    subagent_stop 控制到达 Codex 时，向该子线程当前轮次发送 turn/interrupt；子线程这一轮以 interrupted 结束时卡片以已停止结束，同一轮迟到的事件不再生成新卡片，再次停止返回 subagent_not_running；父轮次不受影响，未知子 agent 返回 unknown_subagent。

    A ``subagent_stop`` control interrupts the turn of the subagent's thread (its card's ``tool_use_id``).

    :importance: P2
    """
    from management_portal.codex_app_server_client import CodexAppServerClient
    from management_portal.codex_app_server_protocol import TurnWaiter
    from management_portal.codex_subagent_cards import CodexSubagentCards

    client = CodexAppServerClient(tmp_path / "unused.sock")
    output: list[dict] = []
    waiter = TurnWaiter(thread_id="root", turn_id="t", event_callback=output.append)
    client.work.turns["root"] = waiter
    client.subagents.follow = client.subagents.release = lambda child: None
    requests: list[tuple[str, dict]] = []

    def request(method: str, params: dict, **options) -> dict:
        requests.append((method, params))
        # A child whose turn this client did not see is asked for its turn in progress.
        if method == "thread/turns/list":
            return {"data": [{"id": "turn-b", "status": "inProgress"}] if params["threadId"] == "child-b" else []}
        return {}

    client.rpc.request = request

    def activity(child: str, kind: str) -> None:
        client.events.dispatch_notification("item/completed", {"threadId": "root", "turnId": "t", "item": {
            "type": "subAgentActivity", "id": f"{kind}-{child}", "kind": kind, "agentThreadId": child}})

    client.events.dispatch_notification("turn/started", {"threadId": "root", "turn": {"id": "t", "status": "inProgress"}})
    for name in ("child-a", "child-b", "child-c"):
        activity(name, "started")
    client.events.dispatch_notification("item/started", {"threadId": "child-a", "turnId": "turn-a", "item": {
        "type": "commandExecution", "id": "exec-1", "command": "sleep 60", "status": "inProgress"}})
    results: list[tuple[str, bool, str]] = []

    def stop(child: str) -> None:
        control = {"control_id": f"stop-{child}", "input": [{"type": "subagent_stop", "tool_use_id": child}]}
        client.controls.deliver_turn_control(
            thread_id="root", waiter=waiter, turn_control_provider=lambda: control,
            turn_control_result_callback=lambda taken, applied, error: results.append((taken["control_id"], applied, error)),
            cancellation_requested=None,
        )

    for name in ("child-a", "child-b", "child-c", "someone-else"):
        stop(name)
    assert results == [("stop-child-a", True, ""), ("stop-child-b", True, ""),
                       ("stop-child-c", False, "subagent_not_running"), ("stop-someone-else", False, "unknown_subagent")]
    assert [params for method, params in requests if method == "turn/interrupt"] == [
        {"threadId": "child-a", "turnId": "turn-a"}, {"threadId": "child-b", "turnId": "turn-b"}]
    # The Resident's own turn goes on.
    assert waiter.completed is None and not waiter.interrupt_requests

    # The parent hears nothing of an interrupt from outside: the child's own turn ends the card.
    # The aborted command's item comes after that turn and starts no new card.
    client.events.dispatch_notification("turn/completed", {"threadId": "child-a", "turn": {"id": "turn-a", "status": "interrupted"}})
    client.events.dispatch_notification("item/completed", {"threadId": "child-a", "turnId": "turn-a", "item": {
        "type": "commandExecution", "id": "exec-1", "command": "sleep 60", "status": "failed", "exitCode": 130}})
    cards = CodexSubagentCards("root")
    events = [extra for event in output for extra in cards.observe(event)]
    capability_contracts.check_subagent_events([*output, *events])
    (done,) = [event["params"]["item"]["run"] for event in events if event["method"] == "item/completed"]
    assert (done["tool_use_id"], done["status"], done["end_id"]) == ("child-a", "stopped", "turn-a")
    assert events[-1]["method"] == "item/completed"
    # Stopped once, it is no longer running.
    stop("child-a")
    assert results[-1] == ("stop-child-a", False, "subagent_not_running")
    # Given more work, the child runs again under a new card.
    client.events.dispatch_notification("turn/started", {"threadId": "child-a", "turn": {"id": "turn-a2", "status": "inProgress"}})
    client.events.dispatch_notification("item/started", {"threadId": "child-a", "turnId": "turn-a2", "item": {
        "type": "commandExecution", "id": "exec-2", "command": "ls", "status": "inProgress"}})
    again = [extra for event in output[-2:] for extra in cards.observe(event)]
    assert [event["params"]["item"]["run"]["status"] for event in again] == ["running"]


def test_kimi_subagent_cards_carry_the_task_the_token_use_and_when_each_step_ran() -> None:
    """Kimi 子 agent 卡片带任务全文、token 用量和每步时间

    Kimi 子 agent 卡片的 prompt 来自主 Agent 调用参数，trace_ref 为子 agent 轨迹键，用量累计子 agent 每步 usage，工具步骤带帧时间。

    :importance: P2
    """
    from management_portal.kimi_live_events import KimiLiveItems
    from tests.cases.test_live_provider_ingress import frame

    events: list[dict] = []
    live = KimiLiveItems("s", events.append, prompt_id="p")

    def at(second: int, typ: str, agent: str, **payload) -> None:
        live.observe({**frame(typ, agent=agent, **payload), "timestamp": f"2026-10-11T15:24:{second:02d}.000Z"})

    step_usage = {"inputOther": 100, "output": 20, "inputCacheRead": 400, "inputCacheCreation": 0}
    # A subagent starts its turn before Kimi says who spawned it; no Agent call was seen (a joined turn).
    at(1, "turn.started", "agent-0", prompt="count the files")
    at(2, "subagent.spawned", "main", subagentId="agent-0", subagentName="explore", parentToolCallId="Agent_0",
       parentAgentId="main", description="count", runInBackground=True, taskId="agent-task-1")
    at(3, "tool.call.started", "agent-0", toolCallId="Bash_0", name="Bash", args={"command": "ls | wc -l"})
    at(5, "tool.result", "agent-0", toolCallId="Bash_0", output="94")
    at(6, "turn.step.completed", "agent-0", step=1, usage=step_usage)
    at(7, "turn.step.completed", "agent-0", step=2, usage=step_usage)
    progress = [event["params"]["item"]["run"] for event in events if event["method"] == "subagent/progress"]
    assert progress[-1]["usage"] == {"input_tokens": 1000, "cached_input_tokens": 800, "output_tokens": 40, "total_tokens": 1040}
    at(8, "assistant.delta", "agent-0", delta="94")
    at(9, "turn.ended", "agent-0", reason="completed")
    # Kimi Code's own total of the run, when it gives one, is the card's.
    at(10, "subagent.completed", "main", subagentId="agent-0", resultSummary="94",
       usage={"inputOther": 300, "output": 50, "inputCacheRead": 900, "inputCacheCreation": 100})
    # A run Portal cancelled (``stop_subagent``) ends stopped, as on the other Providers.
    at(11, "subagent.spawned", "main", subagentId="agent-1", subagentName="coder", parentToolCallId="Agent_1",
       parentAgentId="main", description="edit", runInBackground=True)
    at(12, "subagent.cancelled", "main", subagentId="agent-1")
    capability_contracts.check_subagent_events(events)
    card, stopped = [event["params"]["item"]["run"] for event in events
                     if event["method"] == "item/completed" and event["params"]["item"]["type"] == "subagentRun"]
    assert (stopped["tool_use_id"], stopped["status"], stopped["trace_ref"]) == ("Agent_1", "stopped", "s:agent-1")
    assert (card["prompt"], card["trace_ref"], card["tool_use_id"]) == ("count the files", "s:agent-0", "Agent_0")
    assert card["usage"] == {"input_tokens": 1300, "cached_input_tokens": 900, "output_tokens": 50, "total_tokens": 1350}
    tool = card["steps"][0]
    assert (tool["tool_name"], tool["started_at"], tool["finished_at"]) == (
        "Bash", "2026-10-11T15:24:03.000Z", "2026-10-11T15:24:05.000Z")
    # Every subagent event names its run's trace (``trace_ref``).
    assert {event["params"]["traceKey"] for event in events if event["method"] == "subagent/event"} == {"s:agent-0"}


def test_kimi_stops_one_subagent_by_cancelling_its_task_while_the_turn_goes_on() -> None:
    """Kimi 单独停止一个子 agent：取消它的任务，父轮次继续

    subagent_stop 控制经 KimiBackend 读取会话任务、按 parent_tool_call_id 找到子 agent 任务并调用 cancel；找不到时返回原因，父轮次继续。

    Kimi Code runs each subagent as a task named by the Agent call; Portal cancels that task alone.

    :importance: P2
    """
    from unittest import mock

    from management_portal import kimi_code_client
    from management_portal.execution import kimi_backend

    tasks = [
        {"id": "bash-1", "kind": "bash", "status": "running", "parent_tool_call_id": "Bash_9"},
        {"id": "agent-0", "kind": "subagent", "status": "completed", "agent_id": "agent-0", "parent_tool_call_id": "Agent_0"},
        {"id": "agent-1", "kind": "subagent", "status": "running", "agent_id": "agent-1", "parent_tool_call_id": "Agent_1"},
        {"id": "agent-2", "kind": "subagent", "status": "running", "agent_id": "agent-2", "parent_tool_call_id": "Agent_2"},
    ]
    calls: list[tuple[str, str, object]] = []

    class Response:
        def __init__(self, envelope: dict) -> None:
            self.body = json.dumps(envelope).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> None:
            return None

        def read(self) -> bytes:
            return self.body

    def urlopen(request, timeout=None):
        body = json.loads(request.data) if request.data else None
        calls.append((request.get_method(), request.full_url, body))
        if request.get_method() == "GET":
            return Response({"code": 0, "data": {"items": tasks}})
        if request.full_url.endswith("/agent-2:cancel"):
            # It ended in between: Kimi Code says the task already finished.
            return Response({"code": 40904, "msg": "task agent-2 already finished", "data": {"cancelled": False}})
        return Response({"code": 0, "data": {"cancelled": True}})

    client = kimi_code_client.KimiCodeClient("http://kimi.test/api/v1")
    with mock.patch.object(kimi_code_client, "urlopen", urlopen), \
            mock.patch.object(kimi_backend, "shared_kimi_client", return_value=client):
        def stop(call: str) -> tuple[bool, str]:
            return kimi_backend.KimiBackend().deliver_turn_input(
                session_key="resident:kimi", session_id="session_1", turn_id="prompt-1",
                items=[{"type": "subagent_stop", "tool_use_id": call}], control_id=f"stop-{call}")

        outcomes = [stop(call) for call in ("Agent_1", "Agent_0", "Agent_2", "Agent_7", "Bash_9")]
    assert outcomes == [(True, ""), (False, "subagent_finished"), (False, "subagent_finished"),
                        (False, "unknown_subagent"), (False, "unknown_subagent")]
    base = "http://kimi.test/api/v1/sessions/session_1/tasks"
    assert [(method, url) for method, url, _body in calls if method == "POST"] == [
        ("POST", f"{base}/agent-1:cancel"), ("POST", f"{base}/agent-2:cancel")]
    # Neither the prompt nor the session is aborted: the turn goes on.
    assert all(url.startswith(base) for _method, url, _body in calls)


def test_a_running_subagent_card_leaves_no_record_in_the_trace() -> None:
    """运行中的子 agent 卡片不写入 AgentData

    subagent/progress 事件不在 AgentData 留下操作记录，只有子 agent 结束时的结果卡片成为 artifact 记录；Execution 活动日志保留运行中卡片供 Chat 显示。

    :design: decision_b1ab45c4b3ec
    """
    from management_common.provider_subagent_runs import SubagentRuns, content_part

    store, projector, request, _session = _turn()
    runs = SubagentRuns()
    runs.start("toolu_a", tool_use_id="toolu_a", agent_type="Explore", description="Survey")
    running = runs.live("toolu_a", turn_id="t")
    done = runs.finish("toolu_a", result="3 files", end_id="r1", turn_id="t")
    for method, item in (("subagent/progress", running), ("item/completed", done)):
        projector.observe(provider="claude", backend_route_id="one", backend_session_id="session-1",
                          execution_id=request.execution_id, event={"method": method, "params": {"turnId": "t", "item": item}})
    with pytest.raises(KeyError):
        store.operation_record(content_part(running, request.execution_id)["segment_id"])
    # The Execution's activity log keeps the running card for the Chat.
    from management_portal.execution.provider_activity_events import _compact_provider_activity_event

    kept = _compact_provider_activity_event({"method": "subagent/progress", "params": {"turnId": "t", "item": running}})
    assert kept == {"method": "subagent/progress", "params": {"turnId": "t", "item": running}}
    assert store.operation_record(content_part(done, request.execution_id)["segment_id"])["content"]["result"] == "3 files"


def test_claude_turn_usage_counts_the_subagents_requests_outside_the_context(tmp_path: Path) -> None:
    """子 agent 的请求计入本轮用量但不计入 context

    Claude 本轮用量包含会话目录 subagents 记录中本轮的请求；context 用量只看主会话最后一次请求。

    :design: decision_b1ab45c4b3ec
    """
    from management_portal.execution.provider_turn_usage import read_claude_turn_usage

    _session(tmp_path)
    transcript = _jsonl(tmp_path / "session-1.jsonl", [
        {"type": "assistant", "uuid": "r1", "timestamp": "2026-10-07T00:00:05.000Z", "message": {
            "id": "msg_main", "model": "claude-opus-5-5", "usage": {"input_tokens": 1000, "output_tokens": 20},
            "content": [{"type": "text", "text": "done"}]}},
    ])
    usage = read_claude_turn_usage(transcript, since=SINCE)
    assert usage.turn["input_tokens"] == 1220 and usage.context["input_tokens"] == 1000


def test_kimi_turn_usage_counts_the_subagents_requests_outside_the_context(tmp_path: Path) -> None:
    """Kimi 子 agent 的请求计入本轮用量但不计入 context

    Kimi 本轮用量包含同一会话各子 agent wire 中本轮的请求；context 用量只看主 agent 最后一次请求。

    :design: decision_b1ab45c4b3ec
    """
    from management_portal.execution.provider_turn_usage import kimi_turn_usage, read_kimi_turn_usage

    def record(agent: str, time: int, other: int, read: int, output: int) -> dict:
        return {"type": "usage.record", "agentId": agent, "usageScope": "turn", "time": time,
                "usage": {"inputOther": other, "output": output, "inputCacheRead": read, "inputCacheCreation": 0}}

    # Kimi Code keeps one wire per agent of the session.
    agents = tmp_path / "session_1" / "agents"
    main = _jsonl(agents / "main" / "wire.jsonl", [
        record("main", 999, 500, 0, 5), record("main", 1001, 1000, 0, 10), record("main", 1004, 100, 1000, 20)])
    _jsonl(agents / "agent-0" / "wire.jsonl", [record("agent-0", 1002, 200, 0, 4), record("agent-0", 1003, 50, 200, 6)])
    usage = kimi_turn_usage({}, read_kimi_turn_usage(main, since_ms=1000))
    assert usage["last"]["inputTokens"] == 1000 + 1100 + 200 + 250 and usage["last"]["outputTokens"] == 40
    assert usage["context"]["inputTokens"] == 1100


def test_providers_that_can_start_subagents_declare_it_and_follow_the_setting() -> None:
    """能启动子 agent 的 Provider 声明能力并遵从开关

    Claude、Kimi、Codex 声明 subagents 与 subagent_events，DSH 都不声明；Claude、Kimi 的新 Resident 默认允许开启 sub agent，Codex 与 DSH 默认关闭；DSH 请求打开被拒，没有能力的 Provider 不能默认打开；Claude 后端把设置传给守护进程，未给出时视为允许。

    :design: decision_3948ce9b9443, decision_5c62deb8253a
    """
    from dataclasses import replace as replace_provider

    from management_portal.execution.harness_capabilities import SUBAGENT_EVENTS, SUBAGENTS
    from management_portal.execution.provider_catalog import AGENT_PROVIDERS

    # Claude, Kimi and DeepSeek Harness Residents start with subagents
    # allowed (sample_admin 10-11 for DSH), Codex Residents without.
    known = ("claude", "codex", "kimi", "dsh")
    assert all(SUBAGENTS in AGENT_PROVIDERS[name].harness and SUBAGENT_EVENTS in AGENT_PROVIDERS[name].harness for name in known)
    assert {name: AGENT_PROVIDERS[name].runtime_adapter().defaults(AGENT_PROVIDERS[name], {})["allow_subagents"]
            for name in known} == {"claude": True, "codex": False, "kimi": True, "dsh": True}
    # A Provider's default follows its declaration, whatever its name.
    for p in AGENT_PROVIDERS.values():
        assert p.runtime_adapter().defaults(p, {}).get("allow_subagents", False) is (SUBAGENTS in p.harness and p.subagents_by_default)
    # A Provider whose model cannot start subagents has no switch.
    without = replace_provider(AGENT_PROVIDERS["dsh"], harness=AGENT_PROVIDERS["dsh"].harness - {SUBAGENTS, SUBAGENT_EVENTS},
                               subagents_by_default=False)
    assert not without.allows_subagents({"allow_subagents": True})
    with pytest.raises(ValueError, match="does not let its model start subagents"):
        without.runtime_adapter().validate_requested_fields(without, {"allow_subagents": True})
    without.runtime_adapter().validate_requested_fields(without, {"allow_subagents": False})
    with pytest.raises(ValueError, match="without the subagents capability"):
        replace_provider(without, subagents_by_default=True)

    backend = ClaudeBackend()
    assert backend.turn_options({"allow_subagents": False}) == {"allow_subagents": False}
    assert backend.turn_options({}) == {"allow_subagents": True}


def test_kimi_without_subagents_has_no_agent_tools() -> None:
    """关闭 sub agent 的 Kimi 没有 Agent 工具

    Kimi Resident 关闭“允许开启 sub agent”时，传给 Kimi Code 的工具列表不含 Agent 与 AgentSwarm；打开时含有。

    :design: decision_3948ce9b9443
    """
    from management_portal.execution.native_options import KimiOptions

    request = ExecutionRequest.new(
        actor="sample_admin", prompt="hi", service_type="agent_session", provider="kimi", operation="turn", agent_id=AGENT,
        session=SessionOptions(mode="persistent", binding_id=f"resident:{AGENT}"),
    )

    session = RuntimeSession(mode="persistent", binding_id=request.session.binding_id, backend_session_id="",
                             backend_route_id="", is_new_backend_session=True)

    def tools(allow: bool) -> list[str]:
        invocation = BackendInvocation(
            request=replace(request, runtime_options={
                "runtime_settings": {"model": "kimi-k3"}, "is_owner": True, "allow_subagents": allow,
            }),
            session=session, user_prompt="hi", system_context="", attach_system_context=False,
            is_new_backend_session=True,
        )
        return KimiOptions().prepare(invocation).request.runtime_options["tools"]

    assert {"Agent", "AgentSwarm"} <= set(tools(True))
    assert not {"Agent", "AgentSwarm"} & set(tools(False))

    # Kimi Code keeps its built-in tools whatever the profile lists (found
    # live 10-09): each prompt switches the subagent tools off.
    from unittest import mock
    from types import SimpleNamespace
    from management_portal import kimi_account_runtime
    from management_portal.execution import kimi_backend

    def disabled(allow: bool) -> list[str]:
        seen: dict = {}
        client = SimpleNamespace(home=Path("/nonexistent/kimi-account"), run_turn=lambda **kwargs: seen.update(kwargs) or SimpleNamespace(
            response="done", session_id="session-a", turn_id="prompt-1", usage={}))
        invocation = SimpleNamespace(
            request=SimpleNamespace(session=SimpleNamespace(binding_id="resident:test"), runtime_options={"tools": ["Read"], "allow_subagents": allow}, permission_profile="yolo",
                                    cwd="/work", operation="turn", execution_id="execution-1"),
            session=SimpleNamespace(backend_route_id="kimi_primary", backend_session_id="session-a"), recovery_checkpoint={}, system_context="",
            user_prompt="hi", attach_system_context=False, is_new_backend_session=False, harness_tools=(),
            tool_batch_processor=None, provider_event_processor=None, provider_error_recovery=None,
        )
        context = mock.Mock()
        context.is_cancelled.return_value = False
        # The session's route names a Kimi account; its client is the fake.
        with mock.patch.object(kimi_account_runtime, "client_for_route", return_value=client), \
                mock.patch.object(kimi_account_runtime, "remember_session"), \
                mock.patch.object(kimi_backend.KimiBackend, "offer_portal_tools", return_value=([], {})), \
                mock.patch.object(kimi_backend.KimiBackend, "settle_portal_tools", return_value=["mcp__portal__x"]), \
                mock.patch.object(kimi_backend.KimiBackend, "record_provider_compaction"), \
                mock.patch.object(kimi_backend.KimiBackend, "observe_turn_usage", return_value=None):
            kimi_backend.KimiBackend().execute(invocation, context)  # type: ignore[arg-type]
        return seen["disabled_tools"]

    assert disabled(True) == ["mcp__portal__x"]
    assert disabled(False) == ["mcp__portal__x", "Agent", "AgentSwarm"]
