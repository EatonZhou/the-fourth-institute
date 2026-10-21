from __future__ import annotations

from tests.cases.execution_test_support import run_worker_once

import io
import json
import socket
import ssl
import urllib.error
import urllib.request
from copy import deepcopy
from dataclasses import replace
from unittest.mock import Mock

import pytest

from management_portal.execution.backends import ChatCompletionService
from management_portal.execution.runtime import ExecutionRuntime
from management_portal.execution.scheduler import SchedulerClient
from management_portal.execution.worker import ExecutionWorker
from management_portal.provider_transport_diagnostics import sanitize_transport_observation
from tests.cases.test_execution_observation_budget import control_plane
from types import SimpleNamespace
from management_portal import model_api_store
from management_portal.ai_provider_registry import AIProviderRegistry
from management_portal.execution.backends import BackendInvocation
from management_portal.execution.requests import build_chat_completion_request


@pytest.fixture
def configured(tmp_path):
    secret = tmp_path / "credential"
    secret.write_text("test-only-key")
    entry = {"id": "approved", "enabled": True, "provider": "openai", "kind": "openai_compatible_chat",
             "base_url": "https://approved.invalid/v1", "api_key_file": str(secret)}
    assert model_api_store.create_api(entry, "test")
    return SimpleNamespace(registry=AIProviderRegistry(), sent=[])


def make_request(configured, *, prompt):
    return build_chat_completion_request(actor="alice", prompt=prompt, provider="approved",
        runtime_options={"model": "approved-model", "system_prompt": "approved shared rules",
                         "parameters": {"thinking": {"type": "disabled"}}})


def execute(configured, request):
    context = Mock()
    context.is_cancelled.return_value = False
    return ChatCompletionService(provider_registry=configured.registry).execute(
        BackendInvocation(request=request, session=SimpleNamespace(), user_prompt=request.prompt,
                          system_context="approved common rules", attach_system_context=True,
                          is_new_backend_session=True), context)



SECRET = "PRIVATE-PROMPT-KEY-RESPONSE-CANARY"


@pytest.fixture(autouse=True)
def local_only(monkeypatch):
    original = socket.socket.connect

    def connect(sock, address):
        if sock.family != socket.AF_UNIX:
            raise AssertionError("real Provider network is forbidden")
        return original(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(urllib.request, "urlopen", Mock(side_effect=AssertionError("unapproved opener")))


def install(configured, monkeypatch, *, opening_error=None, body_error=None, body=None):
    payload = body if body is not None else json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": "ranked"}}],
                                                      "usage": {"prompt_tokens": 30, "completion_tokens": 3}}).encode()

    class Response(io.BytesIO):
        status = 200

        def read(self):
            if body_error:
                raise body_error
            return super().read()

    response = Response(payload)

    def open_request(request, **kwargs):
        configured.sent.append(request)
        if opening_error:
            raise opening_error
        return response

    opener = Mock()
    opener.open.side_effect = open_request
    monkeypatch.setattr(urllib.request, "urlopen", opener.open)
    return opener, response


def request_for(configured):
    return replace(make_request(configured, prompt=SECRET + " \u4e2d\u6587"),
                   metadata={"diagnostics_test": "unit"})


def test_dispatch_times_reach_the_projector_event(configured, monkeypatch):
    """Chat Completion 请求的发出与完成时间写入 provider_output 事件"""
    install(configured, monkeypatch)
    request = request_for(configured)
    context = Mock()
    context.is_cancelled.return_value = False
    result = ChatCompletionService(provider_registry=configured.registry).execute(
        BackendInvocation(request=request, session=SimpleNamespace(), user_prompt=request.prompt,
                          system_context='', attach_system_context=False, is_new_backend_session=True), context)
    emitted = [call.args[1] for call in context.emit.call_args_list if call.args[0] == 'provider_output']
    timing = result.output['provider_transport']
    assert emitted[0]['request_timing'] == {key: timing[key] for key in ('started_at', 'finished_at')}
    assert timing['started_at'] and timing['finished_at'] >= timing['started_at']


def test_serialized_transport_statistics_preserve_payload(configured, monkeypatch):
    """传输统计按实际发出的请求体计字节与字符，请求体保持原样且统计不含提示原文"""
    opener, response = install(configured, monkeypatch)
    request = request_for(configured)
    result = execute(configured, request)
    wire = configured.sent[0]
    payload = json.loads(wire.data)
    assert set(payload) == {"model", "messages", "thinking"}
    assert payload["thinking"] == {"type": "disabled"} and payload["model"] == "approved-model"
    assert [row["role"] for row in payload["messages"]] == ["system", "system", "user"]
    assert payload["messages"][-1]["content"] == request.prompt
    assert wire.full_url == "https://approved.invalid/v1/chat/completions"
    opener.open.assert_called_once()
    assert opener.open.call_args.kwargs == {"timeout": 360.0}
    transport = sanitize_transport_observation(result.output["provider_transport"])
    assert transport["attempt_number"] == 1 and transport["outcome"] == "completed"
    assert transport["response"]["headers_received"] is True and transport["response"]["body_complete"] is True
    assert response.closed and transport["response"]["http_status"] == 200
    assert transport["request"]["characters"] == len(wire.data.decode())
    assert transport["request"]["utf8_bytes"] == len(wire.data)
    assert SECRET not in json.dumps(transport)


def test_transport_failures_keep_known_phase_without_inventing_network_root_cause(configured, monkeypatch, subtests):
    """传输失败保留已知阶段而不臆断网络根因

    传输失败保留已知阶段而不臆断网络根因；使用临时存储与禁止外网的 HTTP 夹具，不操作真实 Provider、凭据或业务 Run。

    :module: execution.providers
    :risk: no-external-side-effects
    """
    cases = [
        ({"opening_error": TimeoutError("The read operation timed out")}, "opening_response", "timeout", False),
        ({"body_error": TimeoutError("The read operation timed out")}, "reading_response_body", "read_timeout", True),
        ({"opening_error": urllib.error.URLError(TimeoutError(SECRET))}, "opening_response", "timeout", False),
        ({"opening_error": urllib.error.URLError(ssl.SSLError(SECRET))}, "opening_response", "tls_error", False),
        ({"opening_error": ConnectionResetError(SECRET)}, "opening_response", "connection_error", False),
        ({"body": b"invalid-json"}, "decoding_response_json", "invalid_json", True),
        ({"body": b"[]"}, "decoding_response_json", "unknown", True),
        ({"opening_error": urllib.error.HTTPError("https://approved.invalid", 503, SECRET, {}, io.BytesIO(SECRET.encode()))}, "opening_response", "http_error", True),
    ]
    for values, phase, kind, headers in cases:
        with subtests.test(phase=phase, kind=kind):
            opener, response = install(configured, monkeypatch, **values)
            request = request_for(configured)
            with pytest.raises(Exception) as caught:
                execute(configured, request)
            transport = sanitize_transport_observation(caught.value.error_details["provider_transport"])
            assert transport["phase"] == phase and transport["error_kind"] == kind
            assert transport["response"]["headers_received"] is headers
            if phase == "reading_response_body":
                assert transport["response"]["http_status"] == 200
                assert transport["response"]["body_bytes"] is None and transport["response"]["body_complete"] is False
                assert response.closed
            assert transport["durations_ms"][phase] >= 0
            assert SECRET not in json.dumps(transport)
            assert opener.open.call_count == 1 and opener.open.call_args.kwargs["timeout"] == 360


def test_observer_failure_cannot_change_success_or_replay_request(configured, monkeypatch):
    """观测回调异常不改变成功或重复请求

    观测回调异常不改变成功或重复请求；使用临时存储与禁止外网的 HTTP 夹具，不操作真实 Provider、凭据或业务 Run。

    :module: execution.providers
    :risk: no-external-side-effects
    """
    opener, _ = install(configured, monkeypatch)
    callback = Mock(side_effect=RuntimeError(SECRET))
    result = configured.registry.request("approved", "chat/completions", {"model": "approved-model", "messages": [{"role": "user", "content": SECRET}]},
        timeout_seconds=120, observe_transport=callback)
    assert result["choices"][0]["message"]["content"] == "ranked"
    assert opener.open.call_count == callback.call_count == 1


def test_response_validation_failure_retains_successful_http_observation(configured, monkeypatch):
    """Completion 校验失败保留 HTTP 成功事实

    Completion 校验失败保留 HTTP 成功事实；使用临时存储与禁止外网的 HTTP 夹具，不操作真实 Provider、凭据或业务 Run。

    :module: execution.providers
    :risk: no-external-side-effects
    """
    install(configured, monkeypatch, body=b'{"choices":[{"message":{"content":""}}]}')
    request = request_for(configured)
    with pytest.raises(RuntimeError, match="empty completion") as caught:
        execute(configured, request)
    transport = sanitize_transport_observation(caught.value.error_details["provider_transport"])
    assert transport["outcome"] == "completed"


def test_worker_persists_failed_transport(configured, monkeypatch, control_plane, tmp_path):
    """Worker 把读取响应体超时的传输观测随失败结果持久保存"""
    opener, _ = install(configured, monkeypatch, body_error=TimeoutError("The read operation timed out"))
    runtime = ExecutionRuntime(services={"chat_completion": ChatCompletionService(configured.registry)}, state_root=tmp_path / "state")
    worker = ExecutionWorker(build_id="test", worker_id=control_plane.worker, state_root=tmp_path / "worker",
        scheduler=SchedulerClient(worker_id=control_plane.worker, build_id="test", capabilities=runtime.capabilities(), service_client=control_plane.service.client),
        runtime=runtime)
    request = request_for(configured)
    control_plane.scheduler.submit(request)
    assert run_worker_once(worker) is True
    before = control_plane.scheduler.snapshot(request.execution_id)
    assert before["status"] == "failed" and before["result"]["error_details"]["provider_transport"]["phase"] == "reading_response_body"
    transport = sanitize_transport_observation(before["result"]["error_details"]["provider_transport"])
    assert transport["phase"] == "reading_response_body"
    assert control_plane.scheduler.snapshot(request.execution_id) == before
    assert opener.open.call_count == 1 and SECRET not in json.dumps(transport)


def test_transport_metadata_is_whitelisted(configured, monkeypatch):
    """传输观测按字段白名单清洗，类型不符的值置空且不带出敏感内容"""
    request = request_for(configured)
    install(configured, monkeypatch)
    observed = deepcopy(execute(configured, request).output["provider_transport"])
    observed.update(headers={"Authorization": SECRET}, error=SECRET, duration=SECRET)
    observed["request"]["extra_body"] = SECRET
    observed["response"]["http_status"] = SECRET
    observed["response"]["body_bytes"] = float("inf")
    observed["durations_ms"]["total"] = True
    clean = sanitize_transport_observation(observed)
    assert clean["response"]["http_status"] is None and clean["response"]["body_bytes"] is None
    assert clean["durations_ms"]["total"] is None and SECRET not in json.dumps(clean, allow_nan=False)
