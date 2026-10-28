"""A Codex thread's requests reach every client subscribed to it.

App-server sends a server request (a dynamic tool call, a user input or
approval request) to every client that has the thread loaded and takes the
first answer.  A Worker that ran an earlier turn keeps the thread loaded for a
while; it must stay silent so that the Worker running the current turn
answers (observed 2026-10-07: its "no dispatcher" error failed every Harness
tool call of the running turn).

:module: execution.providers
:importance: P1
:design: decision_b1ab45c4b3ec
"""
from __future__ import annotations

from management_portal.codex_app_server_client import CodexAppServerClient
from management_portal.codex_app_server_protocol import TurnWaiter


def _client(tmp_path, *, runs_turn: bool):
    client = CodexAppServerClient(tmp_path / "absent.sock")
    sent: list[dict] = []
    client.rpc.send = sent.append
    client.user_inputs._send = sent.append
    if runs_turn:
        client.work.turns["thread-shared"] = TurnWaiter(thread_id="thread-shared", turn_id="turn-1", event_callback=lambda _event: None)
    return client, sent


def test_a_client_without_the_running_turn_leaves_its_requests_to_the_worker_that_runs_it(tmp_path) -> None:
    """不运行这一轮的客户端不回答线程请求

    Codex app-server 把线程的请求发给所有订阅它的客户端并取第一个回答；只是还保留着线程、不运行当前这一轮的客户端不回答动态工具调用和用户输入请求，以免它的“没有分发器”错误让正在运行的这一轮工具调用失败。
    """
    idle, sent = _client(tmp_path, runs_turn=False)
    idle.events.answer_server_request(7, "item/tool/call", {"threadId": "thread-shared", "tool": "hatg_instruction"})
    idle.events.answer_server_request(8, "item/tool/requestUserInput", {"threadId": "thread-shared", "questions": []})
    assert sent == []


def test_the_worker_running_the_turn_answers_even_without_a_dispatcher(tmp_path) -> None:
    """运行这一轮的 Worker 负责回答线程请求

    运行当前这一轮的客户端没有动态工具分发器时立即返回错误，有分发器时返回工具结果。
    """
    running, sent = _client(tmp_path, runs_turn=True)
    running.events.answer_server_request(7, "item/tool/call", {"threadId": "thread-shared", "tool": "hatg_instruction"})
    assert sent and sent[0]["id"] == 7 and sent[0]["error"]["code"] == -32601

    answered: list[dict] = []
    running.work.dynamic_tool_handlers["thread-shared"] = lambda params: answered.append(params) or {
        "success": True, "contentItems": [{"type": "inputText", "text": "ok"}],
    }
    running.events.run_dynamic_tool_request(9, running.work.dynamic_tool_handlers["thread-shared"], {"threadId": "thread-shared"})
    assert sent[-1] == {"id": 9, "result": {"success": True, "contentItems": [{"type": "inputText", "text": "ok"}]}}
