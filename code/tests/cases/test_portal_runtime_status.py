from __future__ import annotations

from management_portal import runtime_status


def test_runtime_status_treats_schema_marker_mismatch_as_non_terminal_contracts(monkeypatch, subtests, isolated_portal_case):
    """运行状态把 AgentData schema 标记不符和读取失败报为可重试的非终止状态"""
    with subtests.test(contract='test_runtime_status_treats_schema_marker_mismatch_as_non_terminal'), isolated_portal_case() as case_path:
        monkeypatch.setattr(runtime_status, '_actual_agent_data_schema', lambda: 'invalid-schema')
        payload = runtime_status.portal_runtime_status('tfi.agent-data.v7')
        assert payload['code'] == 'agent_data_schema_marker_mismatch'
        assert payload['ready'] is True
        assert payload['terminal'] is False
        assert payload['retryable'] is True
        assert payload['message'] == '数据库标记为 invalid-schema，当前运行版本为 v7。请核对数据库版本与部署版本。'
        assert payload['agent_data'] == {'compatible': False, 'expected_schema': 'tfi.agent-data.v7', 'actual_schema': 'invalid-schema', 'expected_version': 'v7', 'actual_version': 'invalid-schema'}
    with subtests.test(contract='test_runtime_status_reports_compatible_schema'), isolated_portal_case() as case_path:
        monkeypatch.setattr(runtime_status, '_actual_agent_data_schema', lambda: 'tfi.agent-data.v7')
        payload = runtime_status.portal_runtime_status('tfi.agent-data.v7')
        assert payload['ok'] is True
        assert payload['ready'] is True
        assert payload['terminal'] is False
        assert payload['agent_data']['compatible'] is True
    with subtests.test(contract='test_runtime_status_expects_the_marker_its_store_creates'), isolated_portal_case() as case_path:
        from management_common.agent_data_schema import AGENT_DATA_SCHEMA
        monkeypatch.setenv('PORTAL_EXPECTED_AGENT_DATA_SCHEMA', 'tfi.agent-data.v7')
        monkeypatch.setattr(runtime_status, '_actual_agent_data_schema', lambda: AGENT_DATA_SCHEMA)
        payload = runtime_status.portal_runtime_status()
        assert payload['code'] == ''
        assert payload['agent_data']['expected_schema'] == AGENT_DATA_SCHEMA
    with subtests.test(contract='test_runtime_status_keeps_schema_read_failures_retryable'), isolated_portal_case() as case_path:
        def unavailable():
            raise RuntimeError('database unavailable')
        monkeypatch.setattr(runtime_status, '_actual_agent_data_schema', unavailable)
        payload = runtime_status.portal_runtime_status('tfi.agent-data.v7')
        assert payload['code'] == 'agent_data_schema_unreadable'
        assert payload['ready'] is False
        assert payload['terminal'] is False
        assert payload['retryable'] is True
        assert '继续重试' in payload['message']
