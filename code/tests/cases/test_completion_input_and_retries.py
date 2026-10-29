from types import SimpleNamespace

import pytest

from urllib.error import URLError
from management_portal.ai_provider_registry import AIProviderHTTPError
from management_portal.execution.backends import ChatCompletionService


def invocation():
    return SimpleNamespace(
        # No Resident in the metadata: capability assembly leaves the call as it is.
        request=SimpleNamespace(provider="api", operation="turn", metadata={}, runtime_options={
            "max_retries": 2,
            "messages": [{"role": "user", "content": "previous"}, {"role": "assistant", "content": "answer"},
                         {"role": "user", "content": "current"}],
        }),
        attach_system_context=False, user_prompt="IDENTITY\ncurrent\nAUTHORIZATION", system_context="",
    )


def test_completion_sends_materialized_current_prompt_and_preserves_history():
    """Chat Completion 发送物化后的当前提示并保留历史"""
    sent = []
    def request(_provider, _endpoint, payload, **_kwargs):
        sent.append(payload)
        return {"choices": [{"message": {"content": "done"}}]}
    service = ChatCompletionService(SimpleNamespace(get=lambda _: {"default_model": "model"}, request=request))
    service.execute(invocation(), SimpleNamespace(is_cancelled=lambda: False, emit=lambda *_: None))
    assert sent[0]["messages"] == [
        {"role": "user", "content": "previous"}, {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "IDENTITY\ncurrent\nAUTHORIZATION"},
    ]


@pytest.mark.parametrize("error,retry", [
    (AIProviderHTTPError(429, "busy"), True), (AIProviderHTTPError(503, "busy"), True),
    (URLError("network"), True), (AIProviderHTTPError(401, "auth"), False),
    (AIProviderHTTPError(400, "bad request"), False), (ValueError("configuration"), False),
    (KeyboardInterrupt(), False),
])
def test_completion_retries_only_transient_provider_failures(monkeypatch, error, retry):
    """Chat Completion 仅重试临时 Provider 故障"""
    monkeypatch.setattr("management_portal.execution.backends.time.sleep", lambda _: None)
    calls = []
    def request(*_args, **_kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise error
        return {"choices": [{"message": {"content": "done"}}]}
    service = ChatCompletionService(SimpleNamespace(get=lambda _: {"default_model": "model", "max_retries": 2}, request=request))
    if retry:
        service.execute(invocation(), SimpleNamespace(is_cancelled=lambda: False, emit=lambda *_: None))
        assert len(calls) == 2
    else:
        with pytest.raises(type(error)):
            service.execute(invocation(), SimpleNamespace(is_cancelled=lambda: False, emit=lambda *_: None))
        assert len(calls) == 1


def test_a_stop_ends_the_wait_for_a_model_api_answer_at_once():
    """停止 Model API Resident 的轮次立即结束等待

    Chat Completion 请求阻塞期间收到停止：Resident 立即以中断结束，不再等 Provider 返回；请求在自己的线程里结束，之后返回的回答和传输记录都被丢弃。

    :module: execution.providers
    :importance: P1
    """
    import threading
    import time

    release, asked = threading.Event(), threading.Event()
    def request(*_args, observe_transport, **_kwargs):
        asked.set()
        release.wait(10)
        observe_transport({"schema": "late"})
        return {"choices": [{"message": {"content": "too late"}}]}
    stopped = threading.Event()
    transport_seen = []
    service = ChatCompletionService(SimpleNamespace(get=lambda _: {"default_model": "model"}, request=request))
    threading.Timer(0.3, stopped.set).start()
    began = time.monotonic()
    with pytest.raises(InterruptedError) as raised:
        service.execute(invocation(), SimpleNamespace(is_cancelled=stopped.is_set, emit=lambda *_: transport_seen.append(_)))
    # The Resident stops while the Provider still answers; the answer that comes later is dropped.
    assert asked.is_set() and time.monotonic() - began < 2
    release.set()
    time.sleep(0.1)
    assert not transport_seen and not getattr(raised.value, "error_details", {})
