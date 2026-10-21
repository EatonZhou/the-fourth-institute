from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from management_portal.execution.backends import BackendInvocation, CodexBackend
from management_portal.execution.contracts import ExecutionRequest, InputMedia, SessionOptions
from management_portal.execution import codex_rollout_history, harness_inline_media
from management_portal.execution.codex_rollout_history import read_codex_context_window
from management_portal.execution.session_manager import RuntimeSession


def _record(record_type: str, **payload) -> dict:
    return {"type": record_type, "payload": payload}


def _window_record(window: int = 258_400) -> dict:
    return _record("event_msg", type="token_count", info={
        "model_context_window": window,
        "last_token_usage": {"input_tokens": 201_610},
    })


def _write_rollout(path: Path, records: list[dict]) -> Path:
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    return path


def _invocation(tmp_path: Path) -> BackendInvocation:
    request = ExecutionRequest.new(
        actor="test-user", prompt="continue", service_type="agent_session",
        provider="codex", operation="turn", cwd=str(tmp_path),
        runtime_options={"model": "gpt-test", "config_overrides": {"unrelated": "kept"}},
        session=SessionOptions(mode="persistent", binding_id="resident:test", backend_route_id="four"),
    )
    return BackendInvocation(
        request=request,
        session=RuntimeSession(
            mode="persistent", binding_id="resident:test", backend_route_id="four",
            backend_session_id="019f0000-0000-7000-8000-000000000001",
            is_new_backend_session=False,
        ),
        user_prompt=request.prompt, system_context="", attach_system_context=False,
        is_new_backend_session=False,
    )


@pytest.mark.parametrize("metadata_source", ["token_count", "failed_turn", "after_compaction"])
def test_context_window_uses_same_model_native_metadata(tmp_path, metadata_source):
    """上下文窗口取同一模型的原生元数据，包括失败轮次和压缩之后的记录"""
    if metadata_source == "failed_turn":
        records = [
            _record("event_msg", type="task_started", turn_id="turn-test", model_context_window=258_400),
            _record("turn_context", model="gpt-test", turn_id="turn-test"),
            _record("event_msg", type="error", message="Bad Request"),
        ]
    elif metadata_source == "after_compaction":
        records = [
            _record("event_msg", type="thread_settings_applied", thread_settings={"model": "gpt-test"}),
            _window_record(),
        ]
    else:
        records = [_record("turn_context", model="gpt-test", turn_id="turn-test"), _window_record()]
    path = _write_rollout(tmp_path / "rollout.jsonl", records)
    assert read_codex_context_window(path, model="gpt-test") == 258_400


@pytest.mark.parametrize("newer_record", [
    _record("turn_context", model="other-model", turn_id="new-turn"),
    _record("event_msg", type="thread_settings_applied", thread_settings={"model": "other-model"}),
    _record("event_msg", type="thread_rolled_back", num_turns=1),
])
def test_context_window_does_not_cross_model_changes_or_rollback(tmp_path, newer_record):
    """模型切换或回滚之后，不沿用之前记录的 codex 上下文窗口"""
    path = _write_rollout(tmp_path / "rollout.jsonl", [
        _record("turn_context", model="gpt-test", turn_id="old-turn"),
        _window_record(), newer_record,
    ])
    assert read_codex_context_window(path, model="gpt-test") == 0


def test_context_window_reads_only_bounded_complete_metadata(tmp_path, monkeypatch):
    """读取上下文窗口只扫描有限字节内的完整元数据行，文件缺失时返回 0"""
    monkeypatch.setattr(codex_rollout_history, "MAX_CONTEXT_METADATA_SCAN_BYTES", 2048)
    path = _write_rollout(tmp_path / "rollout.jsonl", [
        _record("turn_context", model="gpt-test", turn_id="old-turn"),
        _window_record(),
        _record("response_item", content="x" * 4096),
    ])
    assert read_codex_context_window(path, model="gpt-test") == 0
    with path.open("a") as stream:
        stream.write(json.dumps(_record("event_msg", type="thread_settings_applied", thread_settings={"model": "gpt-test"})) + "\n")
        stream.write(json.dumps(_window_record()) + "\n")
        stream.write('{"type":"event_msg","payload":')
    assert read_codex_context_window(path, model="gpt-test") == 258_400
    assert read_codex_context_window(tmp_path / "missing", model="gpt-test") == 0


def test_unknown_catalog_model_installs_native_threshold_without_model_call(tmp_path):
    """模型目录中没有的模型按 rollout 观测到的窗口设置自动压缩阈值，不调用模型"""
    rollout = _write_rollout(tmp_path / "rollout.jsonl", [
        _record("turn_context", model="gpt-test", turn_id="turn-test"), _window_record(),
    ])
    registry = mock.Mock()
    registry.routes.return_value = (SimpleNamespace(account_id="four", codex_home=tmp_path),)
    registry.find_thread_rollout.return_value = rollout
    backend = CodexBackend(account_registry=registry)
    transformed = backend.configure_auto_compact_threshold(invocation=_invocation(tmp_path), threshold_percent=70)
    assert transformed.applied
    overrides = transformed.invocation.request.runtime_options["config_overrides"]
    assert overrides["model_auto_compact_token_limit"] == 180_880
    assert overrides["model_auto_compact_token_limit_scope"] == "total"
    assert overrides["unrelated"] == "kept"
    assert transformed.metrics["context_window_source"] == "provider_thread_observation"
    registry.find_thread_rollout.assert_called_once_with(
        "four", "019f0000-0000-7000-8000-000000000001", include_archived=True,
    )
    assert not hasattr(backend, "rewrite_active_history")


@pytest.mark.parametrize("context_source", ["catalog", "override", "new_session"])
def test_context_window_fallback_preserves_existing_priority_and_session_boundary(tmp_path, context_source):
    """上下文窗口优先取模型目录或配置覆盖值，新会话不读取 rollout"""
    registry = mock.Mock()
    registry.routes.return_value = (SimpleNamespace(account_id="four", codex_home=tmp_path),)
    backend = CodexBackend(account_registry=registry)
    invocation = _invocation(tmp_path)
    if context_source == "catalog":
        (tmp_path / "models_cache.json").write_text(json.dumps({"models": [{
            "slug": "gpt-test", "context_window": 200_000, "effective_context_window_percent": 90,
        }]}))
        expected = 180_000
    elif context_source == "override":
        invocation = replace(invocation, request=replace(invocation.request, runtime_options={
            "model": "gpt-test", "config_overrides": {"model_context_window": 200_000},
        }))
        expected = 190_000
    else:
        invocation = replace(invocation, is_new_backend_session=True)
        expected = 0
    observation = backend.observe_context_window(invocation=invocation)
    assert observation.tokens == expected
    registry.find_thread_rollout.assert_not_called()
