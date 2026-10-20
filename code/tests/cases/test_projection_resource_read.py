"""Projection reads use bounded, authorized ResourceService workspace bytes."""

import hashlib
import json
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from management_portal import resources
from management_portal.workbench_projections import http, resource_access
from tests.cases.test_workspace_resource_content import (
    RESIDENT, WORKSPACE, resource_http, workspace,
)


APP = "workbench_111111111111"
ADDRESS = WORKSPACE + "/file/model.json"


@pytest.fixture
def projection_reader(workspace, resource_http, monkeypatch):
    base, client = resource_http
    calls = []

    def get(app_id, actor):
        calls.append((app_id, actor))
        if app_id != APP or actor != "alice":
            raise PermissionError("workbench unavailable")
        return {}

    monkeypatch.setattr(http, "get_service", lambda: SimpleNamespace(store=SimpleNamespace(get=get)))
    monkeypatch.setattr(resources, "get_resource_service", lambda: client)

    def read(**arguments):
        request = Request(
            base + "/api/workbenches/resource",
            data=json.dumps({"id": APP, "operation": "read", "arguments": {"address": ADDRESS, **arguments}}).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urlopen(request, timeout=10) as response:
            return json.load(response)

    return SimpleNamespace(read=read, calls=calls)


def test_projection_reads_large_workspace_snapshot_and_rechecks_authorization(workspace, projection_reader):
    """投影完整读取大型 Workspace 快照并重新检查授权"""
    # Match the reported model size; non-ASCII and CRLF exercise byte hashing.
    raw = '{"label":"投影", "body":"'.encode() + b"x" * (5_442_772 - 35) + b'"}\r\n'
    raw += b" " * (5_442_772 - len(raw))
    assert len(raw) == 5_442_772
    (workspace.root / "model.json").write_bytes(raw)
    with pytest.raises(ValueError, match="content limit"):
        workspace.service.read_content(ADDRESS, actor="alice")

    result = projection_reader.read()
    assert result["encoding"] == "utf-8"
    assert result["content"].encode() == raw
    assert result["metadata"]["content_size"] == len(raw)
    assert result["metadata"]["content_sha256"] == hashlib.sha256(raw).hexdigest()
    assert projection_reader.calls == [(APP, "alice")]

    workspace.grants.pop(("alice", WORKSPACE))
    with pytest.raises(HTTPError) as denied:
        projection_reader.read()
    assert denied.value.code in {403, 404}


def test_projection_budget_is_inclusive_and_cannot_be_raised_by_page(workspace, projection_reader):
    """投影读取允许恰好达到预算且页面无法提高上限"""
    path = workspace.root / "model.json"
    raw = b" " * (resource_access.RESOURCE_READ_MAX_BYTES - 2) + b"{}"
    path.write_bytes(raw)
    result = projection_reader.read()
    assert result["content"].encode() == raw
    assert result["metadata"]["content_sha256"] == hashlib.sha256(raw).hexdigest()
    with path.open("ab") as file:
        file.write(b" ")
    with pytest.raises(HTTPError) as oversized:
        projection_reader.read(max_bytes=200 * 1024 * 1024)
    assert oversized.value.code == 400
    assert "content limit" in oversized.value.read().decode()


def test_projection_large_read_preserves_actor_and_workbench_gate(workspace, projection_reader):
    """投影大文件读取保留调用者并检查工作台权限"""
    (workspace.root / "model.json").write_bytes(b"{}")
    with pytest.raises(HTTPError) as overridden:
        projection_reader.read(actor=RESIDENT)
    assert overridden.value.code == 403
    with pytest.raises(PermissionError, match="workbench unavailable"):
        http.post("/api/workbenches/resource", {
            "id": APP, "operation": "read", "arguments": {"address": ADDRESS},
        }, "bob")
