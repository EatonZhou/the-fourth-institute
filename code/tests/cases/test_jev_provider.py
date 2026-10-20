from __future__ import annotations

from types import SimpleNamespace

import pytest

from management_portal.execution import provider_balance_adapters
from management_portal.execution.backends import BackendInvocation, ChatCompletionService
from management_portal.execution.harness_jev import (
    JEV_DECIDE_TOOL_NAME,
    JevDecisionToolFeature,
)
from management_portal.jev_provider import JevProvider, JevProviderError


class _Registry:
    def __init__(self, secret_path):
        self.secret_path_value = str(secret_path)
        self.calls = []

    def get(self, provider_id):
        assert provider_id == "jev_primary"
        return {
            "id": provider_id,
            "provider": "typesafe",
            "kind": "typesafe_system_one",
            "api_key_file": self.secret_path_value,
            "default_model": "jev-latest",
            "timeout_s": 30,
            "models": [
                {"id": "jev-latest", "enabled": True},
                {"id": "jev-preview", "enabled": True},
            ],
        }

    def secret_path(self, item):
        return item["api_key_file"]

    def request(self, provider_id, endpoint, payload, *, timeout_seconds, configuration=None):
        self.calls.append((provider_id, endpoint, payload, timeout_seconds))
        answers = {}
        for question_id, question in payload["questions"].items():
            if question["type"] == "choice":
                labels = list(question["criteria"])
                answers[question_id] = {
                    "type": "choice",
                    "choice": labels[0],
                    "probabilities": {
                        label: 1.0 if index == 0 else 0.0
                        for index, label in enumerate(labels)
                    },
                    "confidence": 1.0,
                }
            elif question["type"] == "score":
                answers[question_id] = {
                    "type": "score",
                    "score": 1.0,
                    "legend": {"0": "low", "1": "high"},
                    "probabilities": {"0": 0.0, "1": 1.0},
                    "confidence": 1.0,
                }
            else:
                answers[question_id] = {"type": "noul", "noul": 0.9}
        return {
            "model": "jev-1.13.0",
            "answers": answers,
            "usage": {"input_tokens": 12, "output_tokens": 3},
        }


def _questions():
    return {
        "route": {
            "type": "choice",
            "instructions": "Choose a route",
            "criteria": {"billing": None, "technical": "Software problems"},
        },
        "urgency": {
            "type": "score",
            "instructions": "How urgent?",
            "criteria": ["low", "high"],
        },
        "refund": {
            "type": "noul",
            "instructions": "Is a refund requested?",
        },
    }


def _direct_jev_invocation(parameters):
    return SimpleNamespace(
        request=SimpleNamespace(provider="jev_primary", runtime_options={
            "parameters": parameters, "max_retries": 0,
            "messages": [{"role": "user", "content": "preset"}],
            "system_prompt": "Business context",
        }),
        attach_system_context=True, system_context="Portal identity",
        user_prompt="current input",
    )


def test_direct_model_api_jev_preserves_context_answers_and_usage(tmp_path):
    """直接 Model API Jev 调用保留上下文、答案与用量"""
    registry = _Registry(tmp_path / "secret")
    original = registry.request
    configuration_reads = []
    original_get = registry.get
    registry.get = lambda key: (configuration_reads.append(key), original_get(key))[1]
    def request(*args, observe_transport=None, **kwargs):
        observe_transport({"schema": "tfi.provider-transport.v1", "outcome": "ok"})
        return original(*args, **kwargs)
    registry.request = request
    questions = {
        "route": {"type": "choice", "instructions": "Choose", "criteria": {"yes": "Ready", "no": "Blocked"}},
        "quality": {"type": "score", "instructions": "Score", "criteria": ["Low", "High"]},
        "ready": {"type": "noul", "instructions": "Ready?"},
    }
    events = []
    context = SimpleNamespace(is_cancelled=lambda: False, emit=lambda *event: events.append(event))
    result = ChatCompletionService(registry).execute(_direct_jev_invocation({"questions": questions}), context)
    assert configuration_reads == ["jev_primary"]
    assert len(registry.calls) == 1
    _, endpoint, payload, _ = registry.calls[0]
    assert endpoint == "systemone"
    assert set(payload) == {"model", "state", "questions"}
    assert payload["state"]["messages"] == [
        {"role": "system", "content": "Portal identity"},
        {"role": "system", "content": "Business context"},
        {"role": "user", "content": "current input"},
    ]
    assert result.output["structured"] == result.output["response"]["answers"]
    assert '"quality"' in result.output["message"]
    assert result.usage == {"input_tokens": 12, "output_tokens": 3, "api_id": "jev_primary", "model": "jev-latest"}
    assert result.output["provider_transport"]["attempt_number"] == 1
    assert events == [("provider_output", {"text": result.output["message"], "final": True,
        "request_timing": {"started_at": None, "finished_at": None}})]


@pytest.mark.parametrize("parameters", [{}, {"temperature": 1}, {"questions": {}}])
def test_direct_jev_rejects_invalid_inputs_without_http(tmp_path, parameters):
    """直接调用 Jev 在无效输入时不发送 HTTP 请求"""
    registry = _Registry(tmp_path / "secret")
    context = SimpleNamespace(is_cancelled=lambda: False, emit=lambda *args: pytest.fail("unexpected output"))
    with pytest.raises(ValueError):
        ChatCompletionService(registry).execute(_direct_jev_invocation(parameters), context)
    assert not registry.calls


def test_direct_jev_cancellation_discards_late_response(tmp_path):
    """直接调用 Jev 取消后丢弃迟到响应"""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    registry = _Registry(tmp_path / "secret")
    started, release, cancelled, finished = (threading.Event() for _ in range(4))
    original = registry.request
    events = []
    def delayed(*args, observe_transport=None, **kwargs):
        started.set()
        assert release.wait(3)
        response = original(*args, **kwargs)
        observe_transport({"schema": "tfi.provider-transport.v1"})
        finished.set()
        return response
    registry.request = delayed
    context = SimpleNamespace(is_cancelled=cancelled.is_set, emit=lambda *args: events.append(args))
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(ChatCompletionService(registry).execute,
            _direct_jev_invocation({"questions": {"ok": {"type": "noul", "instructions": "Ready?"}}}), context)
        try:
            assert started.wait(1)
            cancelled.set()
            with pytest.raises(InterruptedError):
                future.result(timeout=1)
            assert not events
        finally:
            release.set()
        assert finished.wait(1)
    assert not events


def test_jev_explicit_zero_retries_and_cancelled_retry(tmp_path, monkeypatch):
    """Jev 显式零重试仅调用一次且取消后停止重试"""
    from management_portal import jev_provider
    from management_portal.ai_provider_registry import AIProviderHTTPError
    registry = _Registry(tmp_path / "secret")
    configuration = {**registry.get("jev_primary"), "max_retries": 3}
    calls = []
    def fail(*args, **kwargs):
        calls.append(args)
        raise AIProviderHTTPError(503, "temporary")
    registry.request = fail
    provider = JevProvider(registry)
    arguments = dict(state="test", questions={"ok": {"type": "noul", "instructions": "Ready?"}}, configuration=configuration)
    with pytest.raises(AIProviderHTTPError):
        provider.decide(**arguments, max_retries=0)
    assert len(calls) == 1
    calls.clear()
    monkeypatch.setattr(jev_provider.time, "sleep", lambda _: None)
    with pytest.raises(InterruptedError):
        provider.decide(**arguments, is_cancelled=lambda: bool(calls))
    assert len(calls) == 1


def test_jev_provider_validates_and_sends_typed_batch(tmp_path):
    """Jev 校验并发送带类型的问题批次"""
    secret = tmp_path / "jev.key"
    secret.write_text("test-key")
    registry = _Registry(secret)
    provider = JevProvider(registry=registry)

    result = provider.decide(
        state={"ticket": "charged twice"},
        questions=_questions(),
    )

    assert result["answers"]["route"]["choice"] == "billing"
    assert registry.calls[0][0:2] == ("jev_primary", "systemone")
    assert registry.calls[0][2]["model"] == "jev-latest"


@pytest.mark.parametrize(
    "questions,match",
    [
        ({}, "non-empty"),
        ({"bad": {"type": "chat", "instructions": "x"}}, "choice, score, or noul"),
        ({"bad": {"type": "score", "instructions": "x", "criteria": ["one"]}}, "2 to 10"),
        ({"bad": {"type": "choice", "instructions": "x", "criteria": {}}}, "1 to 255"),
    ],
)
def test_jev_provider_rejects_invalid_question_contract(tmp_path, questions, match):
    """Jev 拒绝不符合问题契约的输入"""
    registry = _Registry(tmp_path / "jev.key")
    with pytest.raises(ValueError, match=match):
        JevProvider(registry=registry).decide(state="x", questions=questions)


def test_jev_provider_rejects_mismatched_response(tmp_path):
    """Jev 拒绝与请求问题集合不匹配的响应"""
    registry = _Registry(tmp_path / "jev.key")
    provider = JevProvider(registry=registry)
    with pytest.raises(JevProviderError, match="requested questions"):
        provider._validate_response(
            {"model": "jev", "answers": {}, "usage": {}},
            {"expected": {"type": "noul", "instructions": "x"}},
        )


@pytest.mark.parametrize("status,retries", [(503, 2), (429, 2), (401, 0)])
def test_jev_reuses_configuration_and_retries_transient_failures(monkeypatch, tmp_path, status, retries):
    """Jev 重试瞬时失败时复用同一配置"""
    from management_portal.ai_provider_registry import AIProviderHTTPError

    registry = _Registry(tmp_path / "jev.key")
    original_get = registry.get
    original_request = registry.request
    reads = []
    attempts = []

    def get(provider_id):
        reads.append(provider_id)
        return {**original_get(provider_id), "max_retries": 2}

    def request(*args, **kwargs):
        attempts.append(kwargs["configuration"])
        if len(attempts) <= 2:
            raise AIProviderHTTPError(status, "provider unavailable")
        return original_request(*args, **kwargs)

    monkeypatch.setattr(registry, "get", get)
    monkeypatch.setattr(registry, "request", request)
    monkeypatch.setattr("management_portal.jev_provider.time.sleep", lambda _seconds: None)
    if retries:
        assert JevProvider(registry=registry).decide(state="x", questions=_questions())["answers"]
    else:
        with pytest.raises(AIProviderHTTPError):
            JevProvider(registry=registry).decide(state="x", questions=_questions())
    assert reads == ["jev_primary"]
    assert len(attempts) == retries + 1
    assert all(item is attempts[0] for item in attempts)


def test_jev_harness_feature_exposes_native_codex_tool(tmp_path):
    """Jev 功能向 codex 暴露原生决策工具"""
    secret = tmp_path / "jev.key"
    secret.write_text("test-key")
    provider = JevProvider(registry=_Registry(secret))
    feature = JevDecisionToolFeature(provider)
    request = SimpleNamespace(provider="codex", runtime_options={})
    invocation = BackendInvocation(
        request=request,
        session=SimpleNamespace(),
        user_prompt="classify",
        system_context="",
        attach_system_context=False,
        is_new_backend_session=True,
    )
    outcome = feature.apply(
        provider=SimpleNamespace(provider_id="codex"),
        invocation=invocation,
        context=SimpleNamespace(),
        config={},
    )

    tool = next(item for item in outcome.invocation.harness_tools if item.name == JEV_DECIDE_TOOL_NAME)
    assert tool.expose_schema is True
    assert '"choice":"billing"' in tool.handler(
        {"state": "charged twice", "questions": _questions()}
    )


@pytest.mark.parametrize("provider_id", ["codex", "claude", "kimi", "dsh"])
def test_disabled_jev_is_unavailable_in_resumed_session(provider_id):
    """恢复会话关闭 Jev 时不暴露工具或访问 Provider"""
    from unittest.mock import Mock

    provider = Mock()
    feature = JevDecisionToolFeature(provider)
    invocation = BackendInvocation(
        request=SimpleNamespace(provider=provider_id, runtime_options={
            "agent_harness": {"features": {"jev_decision_tool": {"enabled": False}}}}),
        session=SimpleNamespace(), user_prompt="classify", system_context="",
        attach_system_context=False, is_new_backend_session=False,
    )
    assert feature.prepare_invocation(invocation).harness_tools == ()
    provider.configuration.assert_not_called()
    provider.decide.assert_not_called()


def test_jev_status_uses_models_endpoint_as_availability(monkeypatch, tmp_path):
    """Jev 状态通过模型列表接口判断可用性"""
    secret = tmp_path / "jev.key"
    secret.write_text("test-key")
    item = {
        "id": "jev_primary",
        "provider": "typesafe",
        "base_url": "https://api.typesafe.ai/v1",
        "api_key_file": str(secret),
    }
    observed = {}

    def read_json(url, headers, timeout):
        observed.update(url=url, headers=headers, timeout=timeout)
        return {"models": [{"name": "jev-latest"}]}

    monkeypatch.setattr(provider_balance_adapters, "_read_json_response", read_json)
    result = provider_balance_adapters._fetch_provider_balance(item)

    assert observed["url"] == "https://api.typesafe.ai/v1/models"
    assert result["status"] == "ok"
    assert result["metric_kind"] == "availability"
    assert result["is_available"] is True
    assert result["display"] == "ready"
