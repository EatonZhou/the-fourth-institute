from __future__ import annotations

import pytest

from management_portal.execution.contracts import ExecutionRequest


def test_execution_request_preserves_input_larger_than_former_character_ceiling():
    """执行请求完整保留超过旧字符上限的输入"""
    prompt = '中文🧠\n' * 500_001
    request = ExecutionRequest.new(actor='test-actor', prompt=prompt,
                                   service_type='agent_session', provider='codex', operation='turn')
    assert request.prompt == prompt
    assert request.validate().prompt == prompt


@pytest.mark.parametrize('prompt', ['', '  \n\t'])
def test_execution_request_still_rejects_empty_input(prompt):
    """执行请求仍拒绝为空或只有空白的输入"""
    with pytest.raises(ValueError, match='prompt must not be empty'):
        ExecutionRequest.new(actor='test-actor', prompt=prompt,
                             service_type='agent_session', provider='codex', operation='turn')
