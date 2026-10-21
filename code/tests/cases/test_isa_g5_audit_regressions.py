"""G5 audit: instance boundaries, Workspace receipts and Handoff state.

:module: spec.design-governance
"""
import copy
from types import SimpleNamespace

import pytest

from tests.cases.isa_test_support import ACTOR, ok, run
from tests.cases.test_isa_lineage_lifecycle import (
    future_creation_store, graph, lineage, resident,
)
































def test_workspace_binding_receipt_and_projection_pending(lineage, tmp_path, monkeypatch):
    """Workspace 绑定返回稳定标识且解绑报告文档待更新"""
    from management_portal.society_service.resident_lifecycle import ResidentLifecycleOperations
    rid = resident(lineage, "Worker")
    path = tmp_path / "reference"
    path.mkdir()
    receipt = ok("BindWorkspace", {"resident_id": rid, "path": str(path)}, key="bind")
    assert receipt["binding"]["path"] == str(path.resolve())
    assert receipt["binding"]["workspace_id"].startswith("workspace_")
    again = ok("BindWorkspace", {"resident_id": rid, "path": str(path)}, key="bind-again")
    assert again["binding"]["workspace_id"] == receipt["binding"]["workspace_id"]
    monkeypatch.setattr(ResidentLifecycleOperations, "run", lambda *_: False)
    removed = ok("UnbindWorkspace", {"resident_id": rid, "workspace_id": receipt["binding"]["workspace_id"]}, key="unbind")
    assert removed["workspace_document_pending"] is True and removed["operation_id"]


def test_handoff_get_reports_draft_and_creation_rejects_25_items(lineage):
    """Workspace 交接超过 24 项时创建被拒绝，新建交接为草稿且重复创建返回同一交接"""
    source, target = resident(lineage, "Source"), resident(lineage, "Target")
    item = {"source": {"root_id": "resident_workspace"}}
    overflow = run("CreateWorkspaceHandoff", {"lineage_id": lineage, "source_resident_id": source, "target_resident_id": target,
                                               "items": [item] * 25}, key="overflow")
    assert overflow.status == "failed" and "24" in overflow.error["message"]
    created = ok("CreateWorkspaceHandoff", {"lineage_id": lineage, "source_resident_id": source, "target_resident_id": target,
                                             "items": [item]}, key="handoff")
    assert ok("GetWorkspaceHandoff", {"handoff_id": created["handoff_id"]})["status"] == "draft"
    again = ok("CreateWorkspaceHandoff", {"lineage_id": lineage, "source_resident_id": source,
                                          "target_resident_id": target, "items": [item]}, key="handoff-again")
    assert again["handoff_id"] == created["handoff_id"] and not again["created"]






def test_bind_receipt_preserves_pending_projection(lineage, tmp_path, monkeypatch):
    """Workspace 文档尚未更新时 BindWorkspace 回执标明待更新并给出操作 ID"""
    from management_portal.society_service.resident_lifecycle import ResidentLifecycleOperations
    rid = resident(lineage, "Worker")
    reference = tmp_path / "reference"
    reference.mkdir()
    monkeypatch.setattr(ResidentLifecycleOperations, "run", lambda *_: False)
    result = ok("BindWorkspace", {"resident_id": rid, "path": str(reference)}, key="pending")
    assert result["binding"]["path"] == str(reference)
    assert result["workspace_document_pending"] is True and result["operation_id"]


def test_handoff_draft_items_rejects_overflow_before_resolving(tmp_path):
    """交接草稿超过 24 项时在解析路径前就被拒绝"""
    from management_portal.society_service import lineage_handoff_preparation as prep
    with pytest.raises(ValueError, match="24"):
        prep.draft_items(lineage_id="lineage", handoff_id="handoff", source_id="source", target_title="Target",
                         payload={"items": [{}] * 25}, actor=ACTOR, lineage_folder=tmp_path,
                         source_workspace=lambda *_: {"path": str(tmp_path)}, root_path=lambda _: "")
