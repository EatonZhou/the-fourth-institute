"""CompleteChat calls a registered model API and can hold its answer to a JSON Schema (decision_4527448ad07f)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from management_portal.execution import isa as execution_isa
from management_portal.hatg.catalog import declaration
from management_portal.hatg.contracts import InstructionError


MEMBERS = ["resident_aaaaaaaaaaaa", "resident_bbbbbbbbbbbb"]
ROUTE_SCHEMA = {
    "type": "object",
    "required": ["target", "reason", "confidence"],
    "properties": {
        "target": {"enum": MEMBERS},
        "reason": {"type": "string"},
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
    },
}


class _Registry:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.requests: list[dict] = []

    def get(self, api_id: str) -> dict:
        return {"id": api_id, "default_model": "deepseek-v4-flash", "timeout_s": 30}

    def request(self, api_id: str, endpoint: str, payload: dict, *, timeout_seconds: float) -> dict:
        self.requests.append({"api_id": api_id, "endpoint": endpoint, "payload": payload})
        return {
            "choices": [{"message": {"role": "assistant", "content": self.answer}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 8},
        }


def _complete(monkeypatch, answer: str, arguments: dict) -> tuple[dict, _Registry]:
    registry = _Registry(answer)
    monkeypatch.setattr(execution_isa, "_registry", lambda: registry)
    invocation = SimpleNamespace(caller="alice", argument=lambda name, default=None: arguments.get(name, default))
    return execution_isa.complete_chat(invocation), registry


def _arguments(**extra) -> dict:
    return {"api_id": "deepseek_primary", "messages": [{"role": "user", "content": "这条消息交给谁？"}], **extra}


def test_plain_call_returns_the_answer_without_structured_output(monkeypatch):
    """不带 response_schema 的 CompleteChat 用默认模型并原样返回回答和用量"""
    result, registry = _complete(monkeypatch, "交给权限内核成员。", _arguments())

    assert result == {
        "message": {"role": "assistant", "content": "交给权限内核成员。"},
        "usage": {"prompt_tokens": 12, "completion_tokens": 8},
    }
    sent = registry.requests[0]["payload"]
    assert sent["model"] == "deepseek-v4-flash"
    assert sent["messages"] == [{"role": "user", "content": "这条消息交给谁？"}]


@pytest.mark.parametrize("answer", ["", "  ", None])
def test_empty_answer_is_rejected(monkeypatch, answer):
    """CompleteChat 收到空回答时报“没有返回消息”"""
    with pytest.raises(InstructionError, match="没有返回消息"):
        _complete(monkeypatch, answer, _arguments())


def test_schema_answer_is_parsed_and_validated(monkeypatch):
    """CompleteChat 把 schema 作为系统消息发送，从代码块中解析出 JSON 并校验"""
    answer = '好的：\n```json\n{"target": "resident_bbbbbbbbbbbb", "reason": "负责权限内核", "confidence": 82}\n```'
    result, registry = _complete(monkeypatch, answer, _arguments(response_schema=ROUTE_SCHEMA))

    assert result["structured"] == {"target": "resident_bbbbbbbbbbbb", "reason": "负责权限内核", "confidence": 82}
    sent = registry.requests[0]["payload"]["messages"]
    assert sent[0]["role"] == "system" and '"enum": ["resident_aaaaaaaaaaaa", "resident_bbbbbbbbbbbb"]' in sent[0]["content"]
    assert sent[1:] == [{"role": "user", "content": "这条消息交给谁？"}]


def test_unfenced_json_after_prose_is_accepted(monkeypatch):
    """夹在说明文字中、未加代码块的 JSON 回答也能被解析"""
    answer = '选择 {"target": "resident_aaaaaaaaaaaa", "reason": "页面", "confidence": 40} 即可'
    result, _ = _complete(monkeypatch, answer, _arguments(response_schema=ROUTE_SCHEMA))

    assert result["structured"]["target"] == "resident_aaaaaaaaaaaa"


def test_answer_outside_the_schema_fails_and_can_be_retried(monkeypatch):
    """CompleteChat 的回答不符合 response_schema 时以可重试错误失败"""
    answer = '{"target": "resident_cccccccccccc", "reason": "猜的", "confidence": 90}'
    with pytest.raises(InstructionError) as caught:
        _complete(monkeypatch, answer, _arguments(response_schema=ROUTE_SCHEMA))

    assert caught.value.code == "instruction.failed"
    assert caught.value.retryable is True
    assert "target" in str(caught.value)


def test_answer_without_json_fails_and_can_be_retried(monkeypatch):
    """要求 response_schema 时回答里没有 JSON 则以可重试错误失败"""
    with pytest.raises(InstructionError) as caught:
        _complete(monkeypatch, "我觉得交给第二个成员。", _arguments(response_schema=ROUTE_SCHEMA))

    assert caught.value.code == "instruction.failed"
    assert caught.value.retryable is True


def test_malformed_schema_is_rejected_before_the_model_is_called(monkeypatch):
    """非法的 response_schema 以不可重试的 invalid_arguments 被拒"""
    with pytest.raises(InstructionError) as caught:
        _complete(monkeypatch, "{}", _arguments(response_schema={"type": "no-such-type"}))

    assert caught.value.code == "instruction.invalid_arguments"
    assert caught.value.retryable is False


def test_malformed_schema_makes_no_model_request(monkeypatch):
    """response_schema 非法时 CompleteChat 不向模型发请求"""
    registry = _Registry("{}")
    monkeypatch.setattr(execution_isa, "_registry", lambda: registry)
    arguments = _arguments(response_schema={"type": "no-such-type"})
    invocation = SimpleNamespace(caller="alice", argument=lambda name, default=None: arguments.get(name, default))
    with pytest.raises(InstructionError):
        execution_isa.complete_chat(invocation)

    assert registry.requests == []


def test_catalog_declares_the_schema_parameter_and_structured_result():
    """CompleteChat 声明可选的 response_schema 参数和可选的 structured 结果"""
    item = declaration("CompleteChat")

    assert item.parameter_map["response_schema"].type == "object"
    assert not item.parameter_map["response_schema"].required
    structured = next(field for field in item.result if field.name == "structured")
    assert structured.type == "any" and not structured.required
