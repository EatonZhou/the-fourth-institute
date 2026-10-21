from __future__ import annotations

from management_portal import serve_management_portal as portal


class _HealthyResourceService:
    def status(self, *, timeout: float | None = None) -> dict[str, object]:
        assert timeout == 2.25
        return {"ok": True, "build_id": "test-build"}


class _UnavailableResourceService:
    def status(self, *, timeout: float | None = None) -> dict[str, object]:
        assert timeout == 2.25
        raise RuntimeError("socket unavailable")


def test_resource_service_status_reports_ready_contracts(monkeypatch, subtests, isolated_portal_case):
    """ResourceService 可连通时状态为就绪，连不上且已宣布重启时显示正在重启"""
    with subtests.test(contract='test_resource_service_status_reports_ready'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            monkeypatch.setattr(portal, 'get_resource_service', lambda: _HealthyResourceService())
            monkeypatch.setattr(portal, 'read_component_state', lambda *_args: {'state': 'restarting', 'operation_id': 'op-1'})
            payload = portal._resource_service_status_payload()
            assert payload['ready'] is True
            assert payload['state'] == 'ready'
            assert payload['health']['build_id'] == 'test-build'
    with subtests.test(contract='test_resource_service_status_preserves_announced_restart'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            monkeypatch.setattr(portal, 'get_resource_service', lambda: _UnavailableResourceService())
            monkeypatch.setattr(portal, 'read_component_state', lambda *_args: {'state': 'restarting', 'operation_id': 'op-2', 'updated_at': 12.5})
            payload = portal._resource_service_status_payload()
            assert payload['ready'] is False
            assert payload['state'] == 'restarting'
            assert payload['message'] == 'ResourceService 正在重启'
            assert payload['operation_id'] == 'op-2'
