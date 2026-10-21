from __future__ import annotations

import json
import sys

from scripts import restart_portal_with_notice


def test_default_restart_reloads_only_web_contracts(monkeypatch, tmp_path, subtests, isolated_portal_case):
    """带通知的默认重启只重启 Web 服务，请求中的其他服务被剔除，通知失败不影响重启"""
    with subtests.test(contract='test_default_restart_reloads_only_web'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            request_path = tmp_path / 'restart-request.json'
            calls: list[str] = []
            monkeypatch.setattr(restart_portal_with_notice, 'REQUEST_PATH', request_path)
            monkeypatch.setattr(restart_portal_with_notice.ServiceControl, 'restart',
                                lambda _self, service: calls.append(service))
            monkeypatch.setattr(sys, 'argv', ['restart_portal_with_notice.py'])
            assert restart_portal_with_notice.main() == 0
            request = json.loads(request_path.read_text(encoding='utf-8'))
            assert request['services'] == ['management-portal.service']
            assert calls == ['management-portal.service']
    with subtests.test(contract='test_exec_stop_notice_rejects_domain_service_restart'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            request_path = tmp_path / 'restart-request.json'
            monkeypatch.setattr(restart_portal_with_notice, 'REQUEST_PATH', request_path)
            monkeypatch.setattr(restart_portal_with_notice.time, 'time', lambda: 1000.0)
            request_path.write_text(json.dumps({'seconds': 8, 'message': 'Reload Dev', 'services': ['management-portal.service', 'portal-dev-lineage-runtime.service', 'not-a-portal-service.service'], 'created_at': 999.0}), encoding='utf-8')
            seconds, message, services = restart_portal_with_notice._pop_restart_request('Default', ['management-portal.service'])
            assert seconds == 5
            assert message == 'Reload Dev'
            assert services == ['management-portal.service']
    with subtests.test(contract='test_exec_stop_projects_notice_before_waiting_and_draining'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            request_path = tmp_path / 'restart-request.json'
            request_path.write_text(json.dumps({'seconds': 5, 'message': 'Reload Dev', 'services': ['management-portal.service'], 'created_at': 999.0}), encoding='utf-8')
            calls: list[tuple] = []
            monkeypatch.setattr(restart_portal_with_notice, 'REQUEST_PATH', request_path)
            monkeypatch.setattr(restart_portal_with_notice.time, 'time', lambda: 1000.0)
            monkeypatch.setattr(restart_portal_with_notice, 'publish_restart_notice', lambda url, **payload: calls.append(('publish', url, payload)) or {'active': True})
            monkeypatch.setattr(restart_portal_with_notice.time, 'sleep', lambda seconds: calls.append(('sleep', seconds)))
            monkeypatch.setattr(restart_portal_with_notice, '_drain_realtime', lambda: calls.append(('drain',)))
            monkeypatch.setattr(sys, 'argv', ['restart_portal_with_notice.py', '--no-restart', '--sleep'])
            assert restart_portal_with_notice.main() == 0
            assert calls == [('publish', restart_portal_with_notice.RESTART_NOTICE_URL, {'seconds': 5, 'message': 'Reload Dev', 'services': ['management-portal.service']}), ('sleep', 5), ('drain',)]
    with subtests.test(contract='test_exec_stop_api_failure_cannot_fail_service_restart'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            request_path = case_path / 'restart-request.json'
            request_path.write_text(json.dumps({'seconds': 5, 'message': 'Reload Dev', 'services': ['management-portal.service'], 'created_at': 999.0}), encoding='utf-8')
            calls: list[tuple] = []
            monkeypatch.setattr(restart_portal_with_notice, 'REQUEST_PATH', request_path)
            monkeypatch.setattr(restart_portal_with_notice.time, 'time', lambda: 1000.0)
            monkeypatch.setattr(restart_portal_with_notice, 'publish_restart_notice', lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError('HTTP 503')))
            monkeypatch.setattr(restart_portal_with_notice.time, 'sleep', lambda seconds: calls.append(('sleep', seconds)))
            monkeypatch.setattr(restart_portal_with_notice, '_drain_realtime', lambda: calls.append(('drain',)) or (_ for _ in ()).throw(RuntimeError('connection refused')))
            monkeypatch.setattr(sys, 'argv', ['restart_portal_with_notice.py', '--no-restart', '--sleep'])
            assert restart_portal_with_notice.main() == 0
            assert calls == [('drain',)]


def test_native_restart_continues_when_notice_endpoint_is_unavailable(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(restart_portal_with_notice, 'REQUEST_PATH', tmp_path / 'restart.json')
    class NativeControl:
        platform = 'macos'

        def restart(self, service):
            calls.append(service)

    monkeypatch.setattr(restart_portal_with_notice, 'ServiceControl', NativeControl)
    monkeypatch.setattr(restart_portal_with_notice, 'publish_restart_notice',
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError('offline')))
    monkeypatch.setattr(restart_portal_with_notice, '_drain_realtime', lambda: None)
    monkeypatch.setattr(sys, 'argv', ['restart_portal_with_notice.py'])
    assert restart_portal_with_notice.main() == 0
    assert calls == [restart_portal_with_notice.SERVICE]
