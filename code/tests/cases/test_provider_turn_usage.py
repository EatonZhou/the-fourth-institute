"""Every Provider reports a turn's token usage to Portal the same way.

Input counts every token sent to the model, including the cached context that
each request of an agent loop sends again; output includes reasoning.  Claude
Code and Kimi Code report input without its cache and only for their last
query, so a turn interrupted by a Worker restart lost its earlier queries.
Their adapters sum the per-request records of the Provider session file since
the Execution began, and report the last request as the context size.

:module: resident.fork-handoff
:importance: P1
"""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from management_portal.execution import backends
from management_portal.codex_app_server_protocol import merge_turn_token_usage
from management_portal.codex_app_server_client import codex_token_usage_delta
from management_portal.execution.provider_turn_usage import (
    claude_turn_usage,
    kimi_reported_usage,
    kimi_turn_usage,
    observed_turn_usage,
    read_claude_turn_usage,
    read_dsh_turn_usage,
    read_kimi_turn_usage,
)
from management_portal.execution.scheduler import normalize_token_usage
from management_portal.society_service.resident_domain_common import (
    execution_token_usage_contract as _execution_token_usage_contract,
)

SESSION = "0d1c2b3a-4e5f-4a6b-8c7d-9e0f1a2b3c4d"


def _anthropic(uncached: int, written: int, read: int, output: int, thinking: int = 0) -> dict:
    return {
        "input_tokens": uncached, "cache_creation_input_tokens": written, "cache_read_input_tokens": read,
        "output_tokens": output, "output_tokens_details": {"thinking_tokens": thinking},
    }


def _assistant(at: str, message_id: str, usage: dict, *, sidechain: bool = False) -> dict:
    return {"type": "assistant", "timestamp": at, "isSidechain": sidechain, "requestId": "req_" + message_id,
            "message": {"id": message_id, "model": "claude-opus-5-5", "usage": usage}}


def _transcript(config_dir: Path, records: list[dict]) -> Path:
    path = config_dir / "projects" / "-work" / f"{SESSION}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return path


CLAUDE_TURN = [
    # A request of the previous Execution.
    _assistant("2026-10-01T09:59:59.000Z", "msg_old", _anthropic(5, 0, 900, 50)),
    {"type": "user", "timestamp": "2026-10-01T10:00:01.000Z", "message": {"role": "user", "content": "build it"}},
    # One response is written as one entry per content block.
    _assistant("2026-10-01T10:00:02.000Z", "msg_1", _anthropic(10, 2000, 0, 300, 120)),
    _assistant("2026-10-01T10:00:03.000Z", "msg_1", _anthropic(10, 2000, 0, 300, 120)),
    # A Worker restart interrupted the query; the recovered turn asked again.
    {"type": "user", "timestamp": "2026-10-01T10:05:00.000Z", "message": {"role": "user", "content": "build it"}},
    _assistant("2026-10-01T10:05:01.000Z", "msg_2", _anthropic(4, 100, 2000, 200, 50)),
    _assistant("2026-10-01T10:05:02.000Z", "msg_sub", _anthropic(3, 0, 500, 40), sidechain=True),
    _assistant("2026-10-01T10:05:03.000Z", "msg_3", _anthropic(2, 50, 2100, 80, 10)),
]


def test_claude_turn_counts_cached_input_thinking_and_every_query(tmp_path) -> None:
    """claude 轮次用量含缓存输入、思考与执行内每次查询，上下文取最后一次主请求"""
    path = _transcript(tmp_path, CLAUDE_TURN)
    observed = read_claude_turn_usage(path, since="2026-10-01T10:00:00.000Z")
    # Claude Code's result covers only its last query (msg_2 and msg_3).
    last_query = {
        "input_tokens": 6, "cache_creation_input_tokens": 150, "cache_read_input_tokens": 4100,
        "output_tokens": 280, "output_tokens_details": {"thinking_tokens": 60},
    }

    usage = claude_turn_usage(last_query, observed, {
        "claude-haiku-4-5": {"inputTokens": 10, "contextWindow": 200000},
        "claude-opus-5-5": {"inputTokens": 4000, "contextWindow": 1000000},
    })

    assert usage == {
        "last": {"inputTokens": 6769, "cachedInputTokens": 4600, "outputTokens": 620,
                 "reasoningOutputTokens": 180, "totalTokens": 7389},
        # The last main request shows how full the context is.
        "context": {"inputTokens": 2152, "cachedInputTokens": 2100, "outputTokens": 80,
                    "reasoningOutputTokens": 10, "totalTokens": 2232},
        "modelContextWindow": 1000000,
    }


def test_claude_usage_without_a_transcript_still_counts_cache_and_thinking() -> None:
    """没有会话文件时 claude 用量仍把缓存计入输入并包含思考"""
    usage = claude_turn_usage(_anthropic(12, 6541, 5588814, 4140, 841), None)
    assert usage == {"last": {"inputTokens": 5595367, "cachedInputTokens": 5588814, "outputTokens": 4140,
                              "reasoningOutputTokens": 841, "totalTokens": 5599507}}


def _claude_turn(config_dir: Path, *, fail: bool) -> tuple[mock.Mock, object]:
    context = mock.Mock(execution_started_at="2026-10-01T10:00:00.000Z")

    def run_turn(**_kwargs):
        if fail:
            raise RuntimeError("Claude Code turn failed")
        return SimpleNamespace(response="done", session_id=SESSION, turn_id="turn-1",
                               usage=_anthropic(2, 50, 2100, 80, 10), model_usage={})

    invocation = SimpleNamespace(
        request=SimpleNamespace(runtime_options={"provider_profile": "claude_one", "config_dir": str(config_dir)},
                                operation="turn", input_media=(), execution_id="execution-claude", cwd="/work",
                                session=SimpleNamespace(binding_id="resident:resident_claude"), permission_profile=""),
        session=SimpleNamespace(backend_route_id="claude_one", backend_session_id=SESSION),
        user_prompt="build it", system_context="", attach_system_context=False,
    )
    backend = backends.ClaudeBackend()
    backend.client_factory = lambda: SimpleNamespace(  # type: ignore[method-assign]
        run_turn=run_turn, session_status=lambda *_args: {}, close_session=lambda *_args: {},
    )
    result: object = None
    with mock.patch("management_portal.execution.native_harness_tools.prepare_tools", return_value=([], None)), \
            mock.patch.object(backends.ClaudeBackend, "record_provider_compaction"), \
            contextlib.suppress(RuntimeError):
        result = backend.execute(invocation, context)  # type: ignore[arg-type]
    return context, result


def _reported(context: mock.Mock) -> list[tuple[str, dict, str]]:
    return [
        (call.args[1]["event_id"], call.args[1]["usage"], call.args[1]["occurred_at"])
        for call in context.emit.call_args_list
        if call.args[0] == "token_usage_delta"
    ]


def test_claude_backend_reports_the_whole_execution_and_each_request(tmp_path) -> None:
    """claude 后端汇报整次执行的用量，并按发出时间逐条上报每个请求"""
    _transcript(tmp_path, CLAUDE_TURN)

    context, result = _claude_turn(tmp_path, fail=False)

    assert result.usage["last"]["inputTokens"] == 6769  # type: ignore[attr-defined]
    assert result.usage["last"]["outputTokens"] == 620  # type: ignore[attr-defined]
    reported = _reported(context)
    assert [event_id for event_id, _, _ in reported] == ["claude:msg_1", "claude:msg_2", "claude:msg_sub", "claude:msg_3"]
    assert reported[0][1] == {"input_tokens": 2010, "output_tokens": 300, "reasoning_output_tokens": 120,
                              "total_tokens": 2310}
    # Each request counts in the hour it was made, not when the turn ended.
    assert reported[0][2] == "2026-10-01T10:00:02.000Z"


def test_a_failed_claude_turn_still_records_the_requests_it_made(tmp_path) -> None:
    """失败的 claude 轮次仍按会话文件上报已发出请求的用量"""
    _transcript(tmp_path, CLAUDE_TURN)

    context, result = _claude_turn(tmp_path, fail=True)

    assert result is None
    assert sum(usage["total_tokens"] for _, usage, _ in _reported(context)) == 7389


def test_execution_usage_is_recorded_and_shown_as_input_including_cache() -> None:
    """执行用量按含缓存的输入记录和显示，原始 Anthropic 用量同样折算"""
    payload = claude_turn_usage(_anthropic(12, 6541, 5588814, 4140, 841), None)
    turn = {"input_tokens": 5595367, "cached_input_tokens": 5588814, "output_tokens": 4140,
            "reasoning_output_tokens": 841, "total_tokens": 5599507}

    assert normalize_token_usage(payload) == turn
    assert _execution_token_usage_contract(payload)["turn_usage"] == turn
    # Raw Anthropic usage is accounted the same way, thinking included.
    assert normalize_token_usage(_anthropic(12, 6541, 5588814, 4140, 841)) == turn


def _kimi_wire(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return path


def test_kimi_turn_counts_cached_input_of_every_main_agent_request(tmp_path) -> None:
    """kimi 轮次累计执行开始后每个主 agent 请求的缓存输入，不含子 agent 记录"""
    def record(time: int, other: int, read: int, output: int, **extra) -> dict:
        return {"type": "usage.record", "agentId": "main", "model": "kimi-k2.7-code", "usageScope": "turn",
                "usage": {"inputOther": other, "output": output, "inputCacheRead": read, "inputCacheCreation": 0},
                "time": time, **extra}

    path = _kimi_wire(tmp_path / "wire.jsonl", [
        record(999, 100, 1000, 10),                     # before the Execution
        record(1001, 1481, 17920, 175),
        record(1002, 50, 900, 5, agentId="subagent-1"),  # a subagent's own wire record
        record(1003, 200, 19200, 90),
    ])

    usage = kimi_turn_usage({}, read_kimi_turn_usage(path, since_ms=1000))

    assert usage == {
        "last": {"inputTokens": 38801, "cachedInputTokens": 37120, "outputTokens": 265, "totalTokens": 39066},
        "context": {"inputTokens": 19400, "cachedInputTokens": 19200, "outputTokens": 90, "totalTokens": 19490},
    }


def test_kimi_reported_usage_counts_cache_in_input() -> None:
    """kimi 上报用量把缓存读写计入输入，轮次事件与状态计数口径一致"""
    # From the turn events: cached_input_tokens includes the cache writes.
    assert kimi_reported_usage({"input_tokens": 1437, "cached_input_tokens": 47872 + 300, "cache_creation_tokens": 300,
                                "output_tokens": 167, "total_tokens": 1437 + 47872 + 300 + 167}) == {
        "input_tokens": 49609, "cached_input_tokens": 47872, "output_tokens": 167, "total_tokens": 49776,
    }
    # From the status counters: cached_input_tokens holds only the cache reads.
    assert kimi_reported_usage({"input_tokens": 1437, "cached_input_tokens": 47872, "cache_creation_tokens": 300,
                                "output_tokens": 167, "total_tokens": 1437 + 300 + 167}) == {
        "input_tokens": 49609, "cached_input_tokens": 47872, "output_tokens": 167, "total_tokens": 49776,
    }


def test_dsh_turn_counts_cache_and_reasoning_of_each_request(tmp_path) -> None:
    """dsh 轮次按本轮每个请求计入缓存读写与推理 token"""
    path = _kimi_wire(tmp_path / "session.v4.jsonl", [
        {"type": "assistant/message", "seq": 4, "time": 999,
         "data": {"usage": {"inputTokens": 50, "outputTokens": 5, "cacheReadTokens": 0, "totalTokens": 55}}},
        {"type": "user/message", "seq": 5, "time": 1001, "data": {"content": "hello"}},
        {"type": "assistant/message", "seq": 6, "time": 1002,
         "data": {"usage": {"inputTokens": 7609, "outputTokens": 30, "cacheReadTokens": 1000,
                            "cacheWriteTokens": 20, "reasoningTokens": 12, "totalTokens": 8659}}},
    ])

    observed = read_dsh_turn_usage(path, session_id="dsh-1", since_ms=1000)

    assert observed is not None
    assert [request.request_id for request in observed.requests] == ["dsh:dsh-1:6"]
    assert observed_turn_usage({}, observed)["last"] == {
        "inputTokens": 8629, "cachedInputTokens": 1000, "outputTokens": 30,
        "reasoningOutputTokens": 12, "totalTokens": 8659,
    }


def test_dsh_turn_counts_its_subagents_requests_outside_its_context(tmp_path) -> None:
    """DSH 一轮的用量包含子 Agent 的请求

    父会话日志中本轮 subagent/catalog 列出的子会话请求计入这一轮，上下文大小只取父会话最后一次请求。

    :importance: P2
    """
    # A DSH subagent keeps a log of its own beside the session's; the parent
    # names it in a ``subagent/catalog`` event.  Found live 10-11: the turn
    # counted only the parent's requests.
    sessions = tmp_path / "sessions" / "--work--"
    usage = lambda tokens: {"usage": {"inputTokens": tokens, "outputTokens": 1, "totalTokens": tokens + 1}}
    path = _kimi_wire(sessions / "dsh-1" / "session.v4.jsonl", [
        {"type": "subagent/catalog", "seq": 2, "time": 900, "data": {"childId": "earlier-child", "mode": "one-shot"}},
        {"type": "assistant/message", "seq": 4, "time": 1001, "data": usage(100)},
        {"type": "subagent/catalog", "seq": 6, "time": 1002, "data": {"childId": "child-a", "mode": "one-shot"}},
        {"type": "subagent/catalog", "seq": 7, "time": 1002, "data": {"childId": "../escape", "mode": "one-shot"}},
        {"type": "assistant/message", "seq": 9, "time": 1010, "data": usage(300)},
    ])
    _kimi_wire(sessions / "child-a" / "session.v4.jsonl", [
        {"type": "assistant/message", "seq": 9, "time": 1003, "data": usage(40)},
        {"type": "assistant/message", "seq": 16, "time": 1004, "data": usage(50)},
    ])
    _kimi_wire(sessions / "earlier-child" / "session.v4.jsonl", [
        {"type": "assistant/message", "seq": 9, "time": 901, "data": usage(7000)},
    ])

    observed = read_dsh_turn_usage(path, session_id="dsh-1", since_ms=1000)

    assert observed is not None
    assert [request.request_id for request in observed.requests] == [
        "dsh:dsh-1:4", "dsh:child-a:9", "dsh:child-a:16", "dsh:dsh-1:9"]
    assert observed.turn["total_tokens"] == 101 + 41 + 51 + 301
    # The session's context is its own last request, never a subagent's.
    assert observed.context["input_tokens"] == 300


def test_codex_compaction_estimate_is_a_context_size_not_usage() -> None:
    """codex 压缩后的估算只作为上下文大小，不计入用量"""
    request = {"last": {"inputTokens": 176000, "cachedInputTokens": 172000, "outputTokens": 1670, "totalTokens": 177670},
               "total": {"totalTokens": 5322379}}
    # After a compaction Codex reports the estimated size of the compacted
    # history; its cumulative total does not change.
    estimate = {"last": {"totalTokens": 14970}, "total": {"totalTokens": 5322379}}

    assert codex_token_usage_delta(estimate) == ("", {})
    assert codex_token_usage_delta(request)[1] == {"last": request["last"]}
    merged = merge_turn_token_usage(merge_turn_token_usage({}, request), estimate)
    assert merged["last"] == request["last"]
    assert merged["context"] == {"totalTokens": 14970}


def test_a_request_read_after_the_turn_is_recorded_in_the_hour_it_was_made(monkeypatch) -> None:
    """轮次结束后才读到的请求用量计入请求发出的小时，重复上报只计一次"""
    from management_portal.execution.contracts import ExecutionRequest, SessionOptions
    from management_portal.execution.service import ExecutionServiceServer
    from tests.cases.test_execution_token_usage import WORKER_ID, _scheduler

    monkeypatch.setattr("management_portal.execution.scheduler.execution_timestamp", lambda: "2026-10-01T12:30:00.000Z")
    scheduler = _scheduler()
    request = ExecutionRequest.new(
        actor="sample_admin", prompt="long turn", service_type="agent_session", provider="codex", operation="turn",
        session=SessionOptions(mode="persistent", binding_id="resident:resident_0123456789ab", backend_route_id="one"),
        metadata={"business_type": "resident", "resident_id": "resident_0123456789ab"},
    )
    scheduler.submit(request)
    claim = scheduler.claim(worker_id=WORKER_ID, capabilities=["agent_session:codex", "chat_completion"])
    scheduler.start(request.execution_id, worker_id=WORKER_ID, claim_token=claim["claim_token"])
    server = ExecutionServiceServer.__new__(ExecutionServiceServer)
    server.scheduler = scheduler

    for _attempt in range(2):  # a recovered attempt reports the same request again
        server.dispatch("scheduler.event", {
            "execution_id": request.execution_id, "worker_id": WORKER_ID, "claim_token": claim["claim_token"],
            "event_type": "token_usage_delta",
            "details": {"event_id": "claude:msg_1", "occurred_at": "2026-10-01T10:05:02.000Z",
                        "usage": {"input_tokens": 2010, "cached_input_tokens": 2000, "output_tokens": 300,
                                  "total_tokens": 2310}},
        })

    usage = scheduler.token_usage(start_hour="2026-10-01T09:00:00Z", end_hour="2026-10-01T13:00:00Z")
    assert [(row["bucket_hour"], row["usage"]["total_tokens"]) for row in usage["rows"]] == [
        ("2026-10-01T10:00:00Z", 2310),
    ]
