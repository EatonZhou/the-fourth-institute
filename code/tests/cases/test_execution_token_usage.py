from __future__ import annotations

from pathlib import Path

from tests.cases.database_test_support import connect

from management_portal.execution.contracts import (
    ExecutionRequest,
    ExecutionResult,
    SessionOptions,
)
from management_portal.execution.scheduler import Scheduler, normalize_token_usage


WORKER_ID = "dev:usage-worker"


def test_anthropic_cache_usage_is_complete_and_normalization_is_idempotent():
    """Anthropic 缓存写入和读取计入输入 token，用量归一化可重复执行"""
    native = {
        "input_tokens": 10, "output_tokens": 5,
        "cache_creation_input_tokens": 40, "cache_read_input_tokens": 60,
    }
    normalized = normalize_token_usage(native)
    assert normalized == {
        "input_tokens": 110, "output_tokens": 5, "cached_input_tokens": 60, "total_tokens": 115,
    }
    assert normalize_token_usage(normalized) == normalized
    assert Scheduler._remaining_normalized_usage(native, normalized) == {}
    assert Scheduler._remaining_normalized_usage(native, {
        "input_tokens": 100, "cached_input_tokens": 60, "total_tokens": 100,
    }) == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


def _scheduler() -> Scheduler:
    scheduler = Scheduler(
        dispatch_interval_min_seconds=0,
        dispatch_interval_max_seconds=0,
    )
    scheduler.register_worker(
        worker_id=WORKER_ID,
        build_id="test",
        capabilities=["agent_session:codex", "chat_completion"],
    )
    return scheduler


def _complete(
    scheduler: Scheduler,
    request: ExecutionRequest,
    usage: dict,
) -> None:
    scheduler.submit(request)
    claim = scheduler.claim(
        worker_id=WORKER_ID,
        capabilities=["agent_session:codex", "chat_completion"],
    )
    assert claim["claimed"] is True, claim
    scheduler.start(
        request.execution_id,
        worker_id=WORKER_ID,
        claim_token=claim["claim_token"],
    )
    scheduler.finish(
        ExecutionResult(
            execution_id=request.execution_id,
            status="completed",
            output={"message": "done"},
            usage=usage,
        ),
        worker_id=WORKER_ID,
        claim_token=claim["claim_token"],
    )


def test_scheduler_aggregates_provider_usage_by_hour_and_resident(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """调度器按小时和 Resident 汇总 Provider 用量"""
    scheduler = _scheduler()
    monkeypatch.setattr(
        "management_portal.execution.scheduler.execution_timestamp",
        lambda: "2026-08-03T12:34:56.000Z",
    )
    resident_id = "resident_0123456789ab"
    for index, usage in enumerate(
        (
            {
                "last": {
                    "inputTokens": 100,
                    "cachedInputTokens": 40,
                    "outputTokens": 20,
                    "reasoningOutputTokens": 5,
                    "totalTokens": 120,
                }
            },
            {
                "last": {
                    "inputTokens": 30,
                    "outputTokens": 10,
                    "totalTokens": 40,
                }
            },
        )
    ):
        _complete(
            scheduler,
            ExecutionRequest.new(
                actor="sample_admin",
                prompt=f"turn {index}",
                service_type="agent_session",
                provider="codex",
                operation="turn",
                session=SessionOptions(
                    mode="persistent",
                    binding_id=f"resident:{resident_id}:{index}",
                    backend_route_id="one",
                ),
                metadata={"business_type": "resident", "resident_id": resident_id},
            ),
            usage,
        )

    payload = scheduler.token_usage(
        start_hour="2026-08-03T12:00:00Z",
        end_hour="2026-08-03T13:00:00Z",
        provider="codex",
    )

    assert payload["schema"] == "tfi.execution-token-usage.v1"
    assert payload["rows"] == [
        {
            "bucket_hour": "2026-08-03T12:00:00Z",
            "resident_id": resident_id,
            "usage": {
                "input_tokens": 130,
                "cached_input_tokens": 40,
                "output_tokens": 30,
                "reasoning_output_tokens": 5,
                "total_tokens": 160,
            },
        }
    ]


def test_scheduler_classifies_non_resident_usage_as_others(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """不属于 Resident 的执行用量归入 others"""
    scheduler = _scheduler()
    monkeypatch.setattr(
        "management_portal.execution.scheduler.execution_timestamp",
        lambda: "2026-08-03T13:01:00.000Z",
    )
    _complete(
        scheduler,
        ExecutionRequest.new(
            actor="review",
            prompt="summarize",
            service_type="chat_completion",
            provider="openai",
            operation="generate",
            session=SessionOptions(mode="stateless"),
            metadata={"business_type": "review"},
        ),
        {
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "prompt_tokens_details": {"cached_tokens": 3},
            "completion_tokens_details": {"reasoning_tokens": 2},
            "total_tokens": 18,
        },
    )

    payload = scheduler.token_usage(
        start_hour="2026-08-03T13:00:00Z",
        end_hour="2026-08-03T14:00:00Z",
        provider="openai",
    )

    assert payload["rows"][0]["resident_id"] == "others"
    assert payload["rows"][0]["usage"] == {
        "input_tokens": 11,
        "cached_input_tokens": 3,
        "output_tokens": 7,
        "reasoning_output_tokens": 2,
        "total_tokens": 18,
    }


def test_all_provider_usage_includes_api_calls_and_deepseek_cache(tmp_path, monkeypatch):
    """Provider 用量汇总包含 API 调用和 deepseek 缓存"""
    scheduler = _scheduler()
    monkeypatch.setattr(
        "management_portal.execution.scheduler.execution_timestamp",
        lambda: "2026-09-23T08:10:00Z",
    )
    resident_id = "resident_0123456789ab"
    for provider in ("codex", "kimi", "deepseek", "openrouter"):
        is_codex = provider == "codex"
        _complete(scheduler, ExecutionRequest.new(
            actor="usage-test", prompt="test usage", provider=provider,
            service_type="agent_session" if is_codex else "chat_completion",
            operation="turn" if is_codex else "generate",
            session=SessionOptions(mode="stateless", backend_route_id="one" if is_codex else ""),
            metadata={"resident_id": resident_id} if provider != "openrouter" else {},
        ), {
            "prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
            **({"prompt_cache_hit_tokens": 60} if provider == "deepseek" else
               {"prompt_tokens_details": {"cached_tokens": 40}}),
        })
    all_usage = scheduler.token_usage(
        start_hour="2026-09-23T08:00:00Z", end_hour="2026-09-23T09:00:00Z", provider="*",
    )
    rows = {row["provider"]: row for row in all_usage["rows"]}
    assert set(rows) == {"codex", "kimi", "deepseek", "openrouter"}
    assert sum(row["usage"]["total_tokens"] for row in rows.values()) == 480
    assert rows["deepseek"]["usage"]["cached_input_tokens"] == 60
    assert rows["deepseek"]["resident_id"] == resident_id
    assert rows["openrouter"]["resident_id"] == "others"
    codex = scheduler.token_usage(
        start_hour="2026-09-23T08:00:00Z", end_hour="2026-09-23T09:00:00Z",
    )
    assert len(codex["rows"]) == 1
    assert codex["rows"][0]["usage"]["total_tokens"] == 120


def test_scheduler_records_live_usage_in_observation_hour_without_double_counting(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """实时用量按观察到的小时记账，重复事件不重计，结束时只补记未上报的差额

    :design: decision_b1ab45c4b3ec
    :design-point:
    """
    current_time = ["2026-08-03T12:10:00.000Z"]
    monkeypatch.setattr(
        "management_portal.execution.scheduler.execution_timestamp",
        lambda: current_time[0],
    )
    scheduler = _scheduler()
    resident_id = "resident_0123456789ab"
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="long turn",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        session=SessionOptions(
            mode="persistent",
            binding_id=f"resident:{resident_id}",
            backend_route_id="one",
        ),
        metadata={"business_type": "resident", "resident_id": resident_id},
    )
    scheduler.submit(request)
    claim = scheduler.claim(
        worker_id=WORKER_ID,
        capabilities=["agent_session:codex", "chat_completion"],
    )
    scheduler.start(
        request.execution_id,
        worker_id=WORKER_ID,
        claim_token=claim["claim_token"],
    )

    first = scheduler.record_token_usage_delta(
        request.execution_id,
        worker_id=WORKER_ID,
        claim_token=claim["claim_token"],
        event_id="usage-one",
        usage={
            "last": {
                "inputTokens": 100,
                "cachedInputTokens": 40,
                "outputTokens": 20,
                "totalTokens": 120,
            }
        },
    )
    duplicate = scheduler.record_token_usage_delta(
        request.execution_id,
        worker_id=WORKER_ID,
        claim_token=claim["claim_token"],
        event_id="usage-one",
        usage={"last": {"inputTokens": 100, "outputTokens": 20}},
    )
    current_time[0] = "2026-08-03T13:05:00.000Z"
    scheduler.record_token_usage_delta(
        request.execution_id,
        worker_id=WORKER_ID,
        claim_token=claim["claim_token"],
        event_id="usage-two",
        usage={
            "last": {
                "inputTokens": 30,
                "outputTokens": 10,
                "totalTokens": 40,
            }
        },
    )

    assert first == {"recorded": True, "duplicate": False}
    assert duplicate == {"recorded": False, "duplicate": True}
    live = scheduler.token_usage(
        start_hour="2026-08-03T12:00:00Z",
        end_hour="2026-08-03T14:00:00Z",
        provider="codex",
    )
    assert [row["usage"] for row in live["rows"]] == [
        {
            "input_tokens": 100,
            "cached_input_tokens": 40,
            "output_tokens": 20,
            "total_tokens": 120,
        },
        {
            "input_tokens": 30,
            "output_tokens": 10,
            "total_tokens": 40,
        },
    ]

    current_time[0] = "2026-08-03T14:01:00.000Z"
    scheduler.finish(
        ExecutionResult(
            execution_id=request.execution_id,
            status="completed",
            output={"message": "done"},
            usage={
                "last": {
                    "inputTokens": 150,
                    "cachedInputTokens": 40,
                    "outputTokens": 40,
                    "totalTokens": 190,
                }
            },
        ),
        worker_id=WORKER_ID,
        claim_token=claim["claim_token"],
    )
    final = scheduler.token_usage(
        start_hour="2026-08-03T12:00:00Z",
        end_hour="2026-08-03T15:00:00Z",
        provider="codex",
    )
    assert [row["usage"]["total_tokens"] for row in final["rows"]] == [
        120,
        40,
        30,
    ]


def test_a_finished_execution_reports_the_usage_it_counted_for_all_its_requests(monkeypatch) -> None:
    """执行结束时报告本次计入的全部请求用量

    Codex 结束时只报告最后一次请求和线程累计；执行结果的 accounted_usage 等于本次执行计入账本的全部请求用量之和，作为本轮用量。

    Codex ends with its last request and its thread total; the turn is the sum of its requests.

    :importance: P1
    :design: decision_f1331d57be89
    """
    monkeypatch.setattr("management_portal.execution.scheduler.execution_timestamp", lambda: "2026-08-03T12:10:00.000Z")
    scheduler = _scheduler()
    request = ExecutionRequest.new(
        actor="sample_admin", prompt="long turn", service_type="agent_session", provider="codex",
        operation="turn", session=SessionOptions(mode="stateless", backend_route_id="one"),
    )
    scheduler.submit(request)
    claim = scheduler.claim(worker_id=WORKER_ID, capabilities=["agent_session:codex"])
    owner = {"worker_id": WORKER_ID, "claim_token": claim["claim_token"]}
    scheduler.start(request.execution_id, **owner)
    requests = [
        {"inputTokens": 200_000, "cachedInputTokens": 190_000, "outputTokens": 500,
         "reasoningOutputTokens": 100, "totalTokens": 200_500},
        {"inputTokens": 230_000, "cachedInputTokens": 220_000, "outputTokens": 700,
         "reasoningOutputTokens": 200, "totalTokens": 230_700},
    ]
    for index, last in enumerate(requests):
        scheduler.record_token_usage_delta(
            request.execution_id, event_id=f"request-{index}", usage={"last": last}, **owner,
        )
    scheduler.finish(
        ExecutionResult(
            execution_id=request.execution_id, status="completed", output={"message": "done"},
            usage={"last": requests[-1], "total": {"totalTokens": 90_000_000}, "modelContextWindow": 258_400},
        ),
        **owner,
    )

    turn = {
        "input_tokens": 430_000, "cached_input_tokens": 410_000, "output_tokens": 1_200,
        "reasoning_output_tokens": 300, "total_tokens": 431_200,
    }
    assert scheduler.snapshot(request.execution_id)["result"]["accounted_usage"] == turn
    ledger = scheduler.token_usage(start_hour="2026-08-03T12:00:00Z", end_hour="2026-08-03T13:00:00Z")
    assert sum(row["usage"]["total_tokens"] for row in ledger["rows"]) == turn["total_tokens"]


def test_a_cancellation_the_scheduler_finalizes_still_reports_the_counted_usage(monkeypatch) -> None:
    """调度器代为结束的停止执行仍报告已计入用量

    Worker 已不在时由 Scheduler 结束的取消执行，结果中的 accounted_usage 仍是本次执行已计入账本的全部请求用量。

    A stopped execution whose Worker is gone keeps the usage its requests were counted for.

    :design: decision_f1331d57be89
    """
    monkeypatch.setattr("management_portal.execution.scheduler.execution_timestamp", lambda: "2026-08-03T12:10:00.000Z")
    scheduler = _scheduler()
    request = ExecutionRequest.new(
        actor="sample_admin", prompt="long turn", service_type="agent_session", provider="codex",
        operation="turn", session=SessionOptions(mode="stateless", backend_route_id="one"),
    )
    scheduler.submit(request)
    claim = scheduler.claim(worker_id=WORKER_ID, capabilities=["agent_session:codex"])
    owner = {"worker_id": WORKER_ID, "claim_token": claim["claim_token"]}
    scheduler.start(request.execution_id, **owner)
    scheduler.record_token_usage_delta(request.execution_id, event_id="request-0", usage={"last": {
        "inputTokens": 200_000, "cachedInputTokens": 190_000, "outputTokens": 500, "totalTokens": 200_500,
    }}, **owner)
    scheduler.cancel(request.execution_id, actor="sample_admin", reason="stopped")
    with scheduler._lock, connect(scheduler) as connection:
        row = connection.execute(
            "SELECT * FROM executions WHERE execution_id = %s", (request.execution_id,),
        ).fetchone()
        scheduler._finalize_released_cancellation(connection, row, error="worker gone", details={})
        connection.commit()

    result = scheduler.snapshot(request.execution_id)["result"]
    assert result["status"] == "cancelled"
    assert result["accounted_usage"] == {
        "input_tokens": 200_000, "cached_input_tokens": 190_000, "output_tokens": 500, "total_tokens": 200_500,
    }


def test_the_service_gives_the_ended_execution_record_its_counted_usage(tmp_path: Path, monkeypatch) -> None:
    """执行结束后执行记录带上调度器计入的用量

    ExecutionService 在 scheduler.finish 之后把 Scheduler 计入的全部请求用量写入 AgentData 执行记录的 accounted，Worker 写入的 Provider 报告保持不变。

    The Chat footer reads the Execution record; it gets every counted request, not the last Provider turn.

    :importance: P1
    :design: decision_f1331d57be89
    """
    from unittest import mock
    from management_portal.execution.agent_data_store import AgentDataStore
    from management_portal.execution.service import ExecutionServiceServer

    monkeypatch.setattr("management_portal.execution.scheduler.execution_timestamp", lambda: "2026-08-03T12:10:00.000Z")
    scheduler, store = _scheduler(), AgentDataStore()
    request = ExecutionRequest.new(
        actor="sample_admin", prompt="long turn", service_type="agent_session", provider="codex",
        operation="turn", session=SessionOptions(mode="stateless", backend_route_id="one"),
    )
    scheduler.submit(request)
    claim = scheduler.claim(worker_id=WORKER_ID, capabilities=["agent_session:codex"])
    owner = {"worker_id": WORKER_ID, "claim_token": claim["claim_token"]}
    scheduler.start(request.execution_id, **owner)
    store.begin_execution(
        execution_id=request.execution_id, agent_id=request.agent_id, actor_ref="sample_admin",
        provider="codex", started_at="2026-08-03T12:00:00.000Z",
    )
    for index, tokens in enumerate((200_000, 230_000)):
        scheduler.record_token_usage_delta(request.execution_id, event_id=f"request-{index}", usage={
            "last": {"inputTokens": tokens, "outputTokens": 500, "totalTokens": tokens + 500},
        }, **owner)
    last_provider_turn = {"last": {"inputTokens": 230_000, "outputTokens": 500, "totalTokens": 230_500}}
    store.finish_execution(request.execution_id, status="completed", usage=last_provider_turn)
    server = ExecutionServiceServer(tmp_path / "usage.sock", scheduler, instance="usage", agent_data=store)
    try:
        with mock.patch.object(server.notification_publisher, "publish"):
            server.dispatch("scheduler.finish", {**owner, "result": {
                "execution_id": request.execution_id, "status": "completed",
                "output": {"message": "done"}, "usage": last_provider_turn,
            }})
    finally:
        server.server_close()

    usage = store.execution(request.execution_id)["usage"]
    assert usage["last"] == last_provider_turn["last"]
    assert usage["accounted"] == {"input_tokens": 430_000, "output_tokens": 1_000, "total_tokens": 431_000}


def test_scheduler_keeps_old_hourly_token_buckets() -> None:
    """小时用量聚合长期保留

    重启后的 Scheduler 不删除任何小时 token 用量聚合，即使它早于以前的 400 天保留期。

    Hourly usage aggregates are kept; a restarted Scheduler deletes none of them."""
    scheduler = _scheduler()
    with connect(scheduler) as connection:
        connection.execute(
            """
            INSERT INTO execution_account_usage_hourly (
                bucket_hour, provider, account_id, resident_id, input_tokens,
                cached_input_tokens, output_tokens,
                reasoning_output_tokens, total_tokens, updated_at
            ) VALUES ('2020-01-01T00:00:00Z', 'codex', '', 'others', 1, 0, 0, 0, 1, '2020-01-01T00:00:00Z')
            """
        )

    Scheduler()

    with connect(scheduler) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM execution_account_usage_hourly"
        ).fetchone()[0] == 1


def test_api_usage_uses_registry_account_and_backend_without_provider_branches(monkeypatch):
    """API 调用用量按注册表的厂商和账号记账，旧的无账号记录保留原身份"""
    from management_portal.ai_provider_registry import shared_ai_provider_registry
    monkeypatch.setattr("management_portal.execution.scheduler.execution_timestamp", lambda: "2026-09-27T08:00:00Z")
    scheduler = _scheduler()
    monkeypatch.setattr(shared_ai_provider_registry, "get", lambda name: {"id": name, "provider": "future_backend"})
    monkeypatch.setattr(shared_ai_provider_registry, "entries", lambda: [{"id": name, "provider": "future_backend", "enabled": True} for name in ["future_primary", "future_secondary"]])
    with scheduler._connect() as connection:
        # A row recorded before the ledger kept accounts: the account is empty.
        connection.execute("INSERT INTO execution_account_usage_hourly VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                           ("2026-09-27T08:00:00Z", "future_primary", "", "others", 20, 5, 0, 0, 20, "2026-09-27T08:00:00Z"))
        connection.commit()
    for account in ["future_primary", "future_secondary"]:
        _complete(scheduler, ExecutionRequest.new(actor="usage-test", prompt="usage", provider=account,
            service_type="chat_completion", operation="generate", session=SessionOptions(mode="stateless")),
            {"input_tokens": 30, "cached_input_tokens": 10, "total_tokens": 30})
    rows = scheduler.token_usage(start_hour="2026-09-27T08:00:00Z", end_hour="2026-09-27T09:00:00Z", provider="*", include_accounts=True)["rows"]
    assert {(r["provider"], r["account_id"]): r["usage"]["total_tokens"] for r in rows} == {
        ("future_backend", "future_primary"): 30,
        ("future_backend", "future_secondary"): 30,
        # Unattributed historical rows keep their recorded identity.
        ("future_primary", ""): 20,
    }
    assert sum(r["usage"]["total_tokens"] for r in rows) == 80
    for include_accounts in (True, False):
        for provider, expected in (("future_backend", 60), ("future_primary", 20)):
            filtered = scheduler.token_usage(start_hour="2026-09-27T08:00:00Z", end_hour="2026-09-27T09:00:00Z",
                provider=provider, include_accounts=include_accounts)
            assert sum(row["usage"]["total_tokens"] for row in filtered["rows"]) == expected


def test_account_ledger_rpc_requests_the_additive_account_dimension():
    """用量查询 RPC 按 include_accounts 请求并返回账号维度"""
    from unittest.mock import Mock
    from management_portal.execution.service import ExecutionService
    client = Mock()
    client.request.return_value = {"rows": [{"account_id": "future_primary"}]}
    response = ExecutionService(client=client).token_usage(start_hour="2026-09-27T08:00:00Z", end_hour="2026-09-27T09:00:00Z", provider="*", include_accounts=True)
    assert response["rows"][0]["account_id"] == "future_primary"
    assert client.request.call_args.args[1]["include_accounts"] is True


def test_usage_catalog_preserves_same_account_name_under_distinct_backends(monkeypatch):
    """同名账号在不同后端下分别列入用量目录，历史记录按 Provider 和路线归并"""
    from management_portal.ai_provider_registry import shared_ai_provider_registry
    from management_portal.execution.accounts import ExecutionAccount, ExecutionAccounts
    from unittest.mock import Mock
    adapter = Mock(provider="agent_backend")
    adapter.accounts.return_value = (ExecutionAccount("shared_primary", "agent_backend", "route", "capacity"),)
    accounts = ExecutionAccounts((adapter,))
    monkeypatch.setattr(shared_ai_provider_registry, "entries", lambda: [{"id": "shared_primary", "provider": "api_backend", "enabled": True}])
    assert accounts.usage_catalog() == [{"account_id": "shared_primary", "provider": "agent_backend"}, {"account_id": "shared_primary", "provider": "api_backend"}]

    from management_portal.execution.account_adapters import ClaudeAccounts
    # Native Claude identities come from the test site's registrations, with
    # no same-named subscription Model API after migration.
    entries = [
        {"id": "api_one", "provider": "anthropic", "kind": "openai_compatible_chat", "enabled": True},
        {"id": "deepseek_primary", "provider": "deepseek", "kind": "openai_compatible_chat", "enabled": True},
    ]
    monkeypatch.setattr(shared_ai_provider_registry, "entries", lambda: entries)
    accounts = ExecutionAccounts((ClaudeAccounts(),))
    assert accounts.usage_catalog() == [
        {"provider": "anthropic", "account_id": "api_one"},
        {"provider": "claude", "account_id": "claude_one"},
        {"provider": "claude", "account_id": "deepseek_primary"},
        {"provider": "deepseek", "account_id": "deepseek_primary"},
    ]
    assert accounts.usage_identity({"service_type": "agent_session", "provider": "claude",
        "session": {"backend_route_id": "claude_one"}}) == ("claude", "claude_one")
    assert accounts.usage_identity({"service_type": "agent_session", "provider": "kimi"}) == ("kimi", "")

    # Provider names, route ids and registry vendor/account pairs reconcile
    # through the real database without guessing missing accounts.
    scheduler = Scheduler(accounts=accounts)
    hour = "2026-09-27T08:00:00Z"
    with scheduler._connect() as connection:
        for provider, account, tokens in [
            ("claude", "claude_one", 10), ("anthropic", "claude_one", 30),
            ("anthropic", "api_one", 40), ("kimi", "kimi_default", 50),
        ]:
            connection.execute("INSERT INTO execution_account_usage_hourly VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                               (hour, provider, account, "others", tokens, 0, 0, 0, tokens, hour))
        for provider, tokens in [("claude", 70), ("claude_one", 90)]:
            connection.execute("INSERT INTO execution_account_usage_hourly VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                               (hour, provider, "", "others", tokens, 0, 0, 0, tokens, hour))
        connection.commit()
    arguments = dict(start_hour=hour, end_hour="2026-09-27T09:00:00Z")
    rows = scheduler.token_usage(**arguments, provider="*", include_accounts=True)["rows"]
    assert {(row["provider"], row["account_id"]): row["usage"]["total_tokens"] for row in rows} == {
        ("claude", "claude_one"): 40, ("claude", ""): 70, ("claude_one", ""): 90,
        ("anthropic", "api_one"): 40, ("kimi", "kimi_default"): 50,
    }
    for include_accounts in (True, False):
        all_rows = scheduler.token_usage(**arguments, provider="*", include_accounts=include_accounts)["rows"]
        assert sum(row["usage"]["total_tokens"] for row in all_rows) == 290
        for provider, expected in [("claude", 110), ("claude_one", 90), ("anthropic", 40), ("kimi", 50)]:
            filtered = scheduler.token_usage(**arguments, provider=provider, include_accounts=include_accounts)["rows"]
            assert sum(row["usage"]["total_tokens"] for row in filtered) == expected


def test_turns_claude_code_takes_on_its_own_count_for_the_resident_once(tmp_path: Path, monkeypatch) -> None:
    """Claude Code 自己开始的轮次计入 Resident 账本且只记一次

    会话记录中不在任何执行时间段内、也未被执行上报的请求按发生小时记入该 Resident 的账本；执行内的请求、已上报的请求和最近两分钟内的请求不记，重复运行不重复计。

    A request outside every Execution goes to the Resident's hourly usage once, to no turn.

    :importance: P1
    :design: decision_f1331d57be89
    """
    import json
    from datetime import datetime, timezone

    from management_portal.execution.provider_catalog import AGENT_PROVIDERS
    from management_portal.execution.unattributed_usage import record_self_started_claude_usage

    monkeypatch.setenv("PORTAL_AGENT_CONFIG_ROOT", str(tmp_path / "agent-config"))
    resident_id, session_id, binding_id = "resident_0123456789ab", "5d0c7a52-0000-4000-8000-000000000001", "resident:binding"
    # The Resident's requests name its runtime directory, which holds the transcripts.
    runtime_root = tmp_path / "runtime" / "backend"
    projects = runtime_root / "claude" / "config" / "projects" / "-w"
    projects.mkdir(parents=True)

    def request(message_id: str, at: str, tokens: int) -> str:
        return json.dumps({"type": "assistant", "timestamp": at, "message": {
            "id": message_id, "model": "m", "usage": {"input_tokens": tokens, "output_tokens": 10},
        }}) + "\n"

    (projects / f"{session_id}.jsonl").write_text(
        request("m0", "2026-08-03T08:30:00.000Z", 50)  # copied from the session it was forked from
        + request("m1", "2026-08-03T09:00:00.000Z", 100)  # a background task's notice: counted
        + request("m2", "2026-08-03T10:00:00.000Z", 200)  # inside a Portal turn
        + request("m3", "2026-08-03T11:00:00.000Z", 300)  # an Execution reported it
        + request("m4", "2026-08-03T11:59:30.000Z", 400)  # not settled yet
    )
    monkeypatch.setattr("management_portal.execution.scheduler.execution_timestamp", lambda: "2026-08-03T11:00:05.000Z")
    scheduler = _scheduler()
    reported = ExecutionRequest.new(
        actor="sample_admin", prompt="turn", service_type="agent_session", provider="codex", operation="turn",
        session=SessionOptions(mode="persistent", binding_id=binding_id, backend_route_id="one"),
        runtime_options={"runtime_root": str(runtime_root)},
        metadata={"resident_id": "resident_ba9876543210"},
    )
    scheduler.submit(reported)
    claim = scheduler.claim(worker_id=WORKER_ID, capabilities=["agent_session:codex"])
    scheduler.start(reported.execution_id, worker_id=WORKER_ID, claim_token=claim["claim_token"])
    scheduler.record_token_usage_delta(
        reported.execution_id, worker_id=WORKER_ID, claim_token=claim["claim_token"],
        event_id="claude:m3", usage={"input_tokens": 300, "output_tokens": 10},
    )
    scheduler.finish(
        ExecutionResult(execution_id=reported.execution_id, status="completed", output={"message": "done"}),
        worker_id=WORKER_ID, claim_token=claim["claim_token"],
    )

    class AgentData:
        @staticmethod
        def provider_session_executions(provider: str, *, active_since: str) -> list[dict]:
            assert provider == "claude"
            return [{
                "backend_session_id": session_id, "backend_route_id": AGENT_PROVIDERS["claude"].default_route,
                "agent_id": f"resident:{resident_id}", "created_at": "2026-08-03T08:45:00.000Z",
                "binding_ids": [binding_id], "executions": [("2026-08-03T09:50:00.000Z", "2026-08-03T10:10:00.000Z")],
            }]

    noon = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
    assert record_self_started_claude_usage(AgentData(), scheduler, now=noon) == 1
    assert record_self_started_claude_usage(AgentData(), scheduler, now=noon) == 0
    assert record_self_started_claude_usage(AgentData(), scheduler, now=noon.replace(minute=5)) == 1

    rows = scheduler.token_usage(start_hour="2026-08-03T09:00:00Z", end_hour="2026-08-03T13:00:00Z", provider="claude")["rows"]
    assert sorted((row["bucket_hour"], row["resident_id"], row["usage"]["input_tokens"]) for row in rows) == [
        ("2026-08-03T09:00:00Z", resident_id, 100), ("2026-08-03T11:00:00Z", resident_id, 400),
    ]
