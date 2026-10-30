from __future__ import annotations

from management_portal import serve_management_portal as portal
from management_portal import http_overview, web_clients
from management_portal.society_service import SocietyServiceBusy


class _HealthySocietyService:
    def health(self) -> dict[str, object]:
        return {"ok": True, "build_id": "test-build"}


class _UnavailableSocietyService:
    def health(self) -> dict[str, object]:
        raise RuntimeError("socket unavailable")


class _BusySocietyService:
    def health(self) -> dict[str, object]:
        raise SocietyServiceBusy("socket timed out")


def test_society_service_status_reports_ready_contracts(monkeypatch, subtests, isolated_portal_case):
    """SocietyService 状态接口区分就绪、断开和繁忙"""
    with subtests.test(contract='test_society_service_status_reports_ready'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            monkeypatch.setattr(web_clients, "_SOCIETY_SERVICE", _HealthySocietyService())

            payload = http_overview._society_service_status_payload()

            assert payload["ready"] is True
            assert payload["state"] == "ready"
            assert payload["health"]["build_id"] == "test-build"
    with subtests.test(contract='test_society_service_status_recovers_unannounced_outage'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            monkeypatch.setattr(web_clients, "_SOCIETY_SERVICE", _UnavailableSocietyService())

            payload = http_overview._society_service_status_payload()

            assert payload["ready"] is False
            assert payload["state"] == "unavailable"
            assert payload["retry_after_ms"] > 0
    with subtests.test(contract='test_society_service_status_distinguishes_busy_from_disconnected'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            monkeypatch.setattr(web_clients, "_SOCIETY_SERVICE", _BusySocietyService())

            payload = http_overview._society_service_status_payload()

            assert payload["ready"] is False
            assert payload["state"] == "busy"
            assert "正忙" in payload["message"]
