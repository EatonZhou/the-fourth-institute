from __future__ import annotations

import io
from email.message import Message
from types import SimpleNamespace

import pytest

from management_portal import portal_deployment_router as router
from management_portal.resource_backup_backend import ResourceBackupBackend
from scripts import portal_services














def test_router_connection_failure_attempts_only_the_selected_backend(monkeypatch):
    """Router 连接所选后端失败时返回 502 backend_unavailable，不尝试其他后端"""
    attempts = []
    replies = []

    class Connection:
        def __init__(self, host, port, **kwargs):
            attempts.append((host, port))

        def connect(self):
            raise ConnectionRefusedError("isolated transport failure")

        def close(self):
            pass

    monkeypatch.setattr(router.http.client, "HTTPConnection", Connection)
    handler = object.__new__(router.PortalRouterHandler)
    handler.path = "/isolated-write"
    handler.command = "POST"
    handler.headers = Message()
    handler.headers["Content-Length"] = "0"
    handler.wfile = io.BytesIO()
    handler.server = SimpleNamespace(
        current_backends=lambda: {"dev": router.Backend("dev", "local-dev", 1), "release": router.Backend("release", "local-release", 2)},
        policy=SimpleNamespace(select=lambda *args: "dev"), backend_timeout=1, restart_wait=0,
    )
    handler._json = lambda status, payload: replies.append((status, payload))
    handler.log_error = lambda *args: None
    handler._proxy()
    assert attempts == [("local-dev", 1)]
    assert replies[0][0] == 502 and replies[0][1]["code"] == "backend_unavailable"


def test_backup_acceptance_has_no_completion_or_run_record_side_effect(tmp_path, monkeypatch):
    """请求备份只返回已排队回执并触发备份 Lineage 事件，不写任何完成状态或运行记录"""
    from management_portal import backup_lineage
    lineage_id = "lineage_111111111111"
    monkeypatch.setattr(backup_lineage, "read_site_config", lambda: {"deployment": {"backup": {"lineage_id": lineage_id}}})
    from tests.cases.test_resource_backup_backend import FakeWorkflow
    workflow = FakeWorkflow()
    backend = ResourceBackupBackend(tmp_path, workflow=workflow)
    before = list(tmp_path.rglob("*"))
    reply = backend.request_backup("critical", "fixture-operator")
    assert reply == {
        "accepted": True, "kind": "backup", "profile": "critical", "status": "queued",
        "lineage_id": lineage_id,
        "event_source_id": "critical", "event_id": "event1", "trigger_path_id": "path1",
    }
    assert workflow.started == ["critical"]
    assert list(tmp_path.rglob("*")) == before
