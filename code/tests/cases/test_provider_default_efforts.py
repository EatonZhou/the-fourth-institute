"""Resident Settings names the effort a Provider uses for "default".

A Resident whose Effort is default sends no effort, and the Provider picks
one.  Claude Code records the effort of each request in its transcript; the
turn's usage carries it, the ExecutionService keeps the latest default per
account route and model, and the runtime catalog offers it as the model's
default effort, so the settings dialog shows "medium (default)".

:module: execution.providers
:importance: P2
"""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from management_portal.execution import claude_backend, provider_default_efforts
from management_portal.execution.provider_turn_usage import claude_turn_usage, read_claude_turn_usage

SESSION = "0d1c2b3a-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
USAGE = {"input_tokens": 2, "cache_creation_input_tokens": 50, "cache_read_input_tokens": 2100, "output_tokens": 80}


def _assistant(at: str, message_id: str, effort: str, *, sidechain: bool = False) -> dict:
    return {"type": "assistant", "timestamp": at, "isSidechain": sidechain, "effort": effort,
            "message": {"id": message_id, "model": "claude-opus-5-5", "usage": USAGE}}


def _transcript(config_dir: Path) -> Path:
    path = config_dir / "projects" / "-work" / f"{SESSION}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        _assistant("2026-10-01T10:00:02.000Z", "msg_1", "high"),
        _assistant("2026-10-01T10:00:03.000Z", "msg_2", "medium"),
        # A subagent's request is not the turn's own.
        _assistant("2026-10-01T10:00:04.000Z", "msg_sub", "low", sidechain=True),
    ]
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return path


def test_claude_usage_names_the_effort_of_the_last_main_request(tmp_path) -> None:
    """claude 用量记录最后一次主上下文请求实际使用的推理强度"""
    observed = read_claude_turn_usage(_transcript(tmp_path), since="2026-10-01T10:00:00.000Z")

    assert claude_turn_usage(USAGE, observed)["reasoningEffort"] == "medium"
    # Without the transcript the effort is unknown, not guessed.
    assert "reasoningEffort" not in claude_turn_usage(USAGE, None)


def _claude_turn(config_dir: Path, options: dict) -> object:
    context = mock.Mock(execution_started_at="2026-10-01T10:00:00.000Z")
    invocation = SimpleNamespace(
        request=SimpleNamespace(runtime_options={"provider_profile": "claude_one", "config_dir": str(config_dir), **options},
                                operation="turn", input_media=(), execution_id="execution-claude", cwd="/work",
                                session=SimpleNamespace(binding_id="resident:resident_claude"), permission_profile=""),
        session=SimpleNamespace(backend_route_id="claude_one", backend_session_id=SESSION),
        user_prompt="build it", system_context="", attach_system_context=False,
    )
    backend = claude_backend.ClaudeBackend()
    backend.client_factory = lambda: SimpleNamespace(  # type: ignore[method-assign]
        run_turn=lambda **_kwargs: SimpleNamespace(response="done", session_id=SESSION, turn_id="turn-1",
                                                   usage=USAGE, model_usage={}),
        session_status=lambda *_args: {}, close_session=lambda *_args: {},
    )
    with mock.patch("management_portal.execution.native_harness_tools.prepare_tools", return_value=([], None)), \
            mock.patch.object(claude_backend.ClaudeBackend, "record_provider_compaction"), \
            contextlib.suppress(RuntimeError):
        return backend.execute(invocation, context)  # type: ignore[arg-type]


def test_a_turn_without_requested_effort_reports_the_providers_default(tmp_path) -> None:
    """未指定强度的轮次把 Provider 实际选用的强度标为该模型的默认强度"""
    _transcript(tmp_path)

    default_turn = _claude_turn(tmp_path, {"model": "claude-opus-5-5"})
    requested_turn = _claude_turn(tmp_path, {"model": "claude-opus-5-5", "effort": "medium"})

    assert default_turn.usage["providerDefaultEffort"] == {"model": "claude-opus-5-5", "effort": "medium"}  # type: ignore[attr-defined]
    # A requested effort says nothing about the Provider's default.
    assert "providerDefaultEffort" not in requested_turn.usage  # type: ignore[attr-defined]


class _Meta:
    """execution_store_meta as the module uses it."""

    def __init__(self) -> None:
        self.rows: dict[str, str] = {}
        self.writes = 0

    @contextlib.contextmanager
    def connect(self):
        meta = self

        class Connection:
            def execute(self, sql, params):
                if sql.startswith("SELECT value"):
                    value = meta.rows.get(params[0])
                    return SimpleNamespace(fetchone=lambda: {"value": value} if value is not None else None)
                if sql.startswith("SELECT key"):
                    rows = [{"key": key, "value": meta.rows[key]} for key in params[0] if key in meta.rows]
                    return SimpleNamespace(fetchall=lambda: rows)
                meta.rows[params[0]] = params[1]
                meta.writes += 1
                return None

            def commit(self):
                return None

        yield Connection()


def _result(effort: str, route: str = "claude_one") -> dict:
    return {"backend_route_id": route,
            "usage": {"reasoningEffort": effort, "providerDefaultEffort": {"model": "claude-opus-5-5", "effort": effort}}}


def test_the_catalog_offers_the_last_observed_default_of_its_route() -> None:
    """运行时目录按账号路由和模型提供最近观测到的默认强度"""
    meta = _Meta()
    assert provider_default_efforts.record(meta, _result("medium"))
    # The same default again is not rewritten: a write wakes the Scheduler.
    assert not provider_default_efforts.record(meta, _result("medium"))
    assert meta.writes == 1
    # A turn that requested its effort leaves the default alone.
    assert not provider_default_efforts.record(meta, {"backend_route_id": "claude_one", "usage": {"reasoningEffort": "max"}})

    catalog = {"backend_route_id": "claude_one", "models": ["claude-opus-5-5", "claude-haiku-4-5"],
               "efforts_by_model": {"claude-opus-5-5": ["low", "medium", "high"], "claude-haiku-4-5": []}}
    assert provider_default_efforts.apply(meta, catalog)["default_efforts_by_model"] == {"claude-opus-5-5": "medium"}

    # The Provider changed its default: the next such turn shows it.
    provider_default_efforts.record(meta, _result("high"))
    nested = {"backends": [{"routes": [{"backend_route_id": "claude_one",
                                        "efforts_by_model": {"claude-opus-5-5": ["low", "medium", "high"]}},
                                       {"backend_route_id": "claude_two",
                                        "efforts_by_model": {"claude-opus-5-5": ["low", "medium", "high"]}}]}]}
    routes = provider_default_efforts.apply(meta, nested)["backends"][0]["routes"]
    assert routes[0]["default_efforts_by_model"] == {"claude-opus-5-5": "high"}
    assert "default_efforts_by_model" not in routes[1]


def test_a_declared_default_or_an_unoffered_effort_is_not_replaced() -> None:
    """Provider 声明的默认强度优先；模型未提供的强度不作为默认显示"""
    meta = _Meta()
    provider_default_efforts.record(meta, _result("xhigh"))
    declared = {"backend_route_id": "claude_one", "efforts_by_model": {"claude-opus-5-5": ["low", "xhigh"]},
                "default_efforts_by_model": {"claude-opus-5-5": "low"}}
    unoffered = {"backend_route_id": "claude_one", "efforts_by_model": {"claude-opus-5-5": ["low", "high"]}}

    assert provider_default_efforts.apply(meta, declared)["default_efforts_by_model"] == {"claude-opus-5-5": "low"}
    assert "default_efforts_by_model" not in provider_default_efforts.apply(meta, unoffered)
