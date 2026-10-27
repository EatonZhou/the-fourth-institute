from management_portal.society_service import resident_execution_work
import copy
from pathlib import Path

import pytest

from management_portal.society_service import lineage_domain, resident_domain
from management_portal.society_service import resident_execution_domain
from management_portal.society_service import resident_runtime_domain
from tests.cases.test_resident_folder_creation_contract import (
    _add_created_resident_to_lineage,
    _append_conversation_turn,
    _new_session_payload,
    future_creation_store,
)


from management_portal.society_service import resident_workspace_domain

@pytest.mark.parametrize("damage", [
    "missing_owned_id", "missing_owned_object", "wrong_owned_path",
    "missing_directory", "wrong_workspace_id",
])
def test_invalid_owned_binding_blocks_execution_preparation_and_is_not_repaired(
    future_creation_store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    damage: str,
) -> None:
    """执行准备拒绝失效的自有Workspace，保留原身份和损坏状态，不创建替代目录；消息准入按当前异步协议执行。

    Admission queues the input; execution preparation validates its Workspace."""
    record = resident_domain.register_resident(_new_session_payload("Strict owned fixture"), "sample_admin")
    reference = tmp_path / "reference"
    reference.mkdir()
    owned = record["owned_workspace"]
    owned_path = Path(owned["path"])
    if damage == "missing_owned_id":
        record.pop("owned_workspace_id")
    elif damage == "missing_owned_object":
        record.pop("owned_workspace")
    elif damage == "wrong_owned_path":
        owned["path"] = str(reference)
        record["default_cwd"] = str(reference)
    elif damage == "missing_directory":
        owned_path.rename(owned_path.with_name("workspace-preserved"))
    else:
        record["owned_workspace_id"] = "workspace_000000000001"
    before = copy.deepcopy(record)
    with pytest.raises(ValueError, match="Workspace"):
        resident_runtime_domain.resident_working_directory(record)
    with pytest.raises(ValueError, match="Workspace"):
        resident_execution_domain._build_resident_execution_request(record, {
            "actor": "sample_admin", "prompt_cwd": str(reference), "message": "must not run",
        })
    assert record == before
    assert not resident_execution_work.read(record)
    if damage == "missing_directory":
        assert not owned_path.exists()


def test_working_directory_uses_owned_workspace_not_stale_projection(future_creation_store: Path) -> None:
    """Resident 工作目录取自有 Workspace 的路径，忽略过期的 default_cwd"""
    record = resident_domain.register_resident(_new_session_payload("Owned cwd"), "sample_admin")
    expected = Path(record["owned_workspace"]["path"])
    record["default_cwd"] = str(expected.parent)
    assert resident_runtime_domain.resident_working_directory(record) == expected


def test_fork_reference_keeps_shared_identity_and_purpose(
    future_creation_store: Path, tmp_path: Path,
) -> None:
    """fork 出的 Resident 保留来源的引用 Workspace 及其用途，自有 Workspace 另行创建"""
    source = resident_domain.register_resident(_new_session_payload("Binding origin source"), "sample_admin")
    reference = tmp_path / "shared-reference"
    reference.mkdir()
    resident_domain.add_resident_reference_workspace(
        source["resident_id"], str(reference), "sample_admin", purpose="Shared task files",
    )
    source = resident_workspace_domain.find_resident(source["resident_id"])
    _append_conversation_turn(source["resident_id"], 0)
    created = resident_domain.create_resident_full_context_fork(
        source["resident_id"], "sample_admin", "lineage:owner-decision:origin", defer_backend_start=True,
    )
    _add_created_resident_to_lineage(source, created["resident_id"])
    child = resident_workspace_domain.find_resident(created["resident_id"])
    binding = next(item for item in child["workspace_bindings"] if item["path"] == str(reference))
    assert binding == next(item for item in source["workspace_bindings"] if item["path"] == str(reference))
    assert binding["purpose"] == "Shared task files"
    assert child["owned_workspace_id"] != source["owned_workspace_id"]
    assert child["default_cwd"] != source["default_cwd"]
    assert not resident_execution_work.read(child)




def test_ready_handoff_remains_ready_until_invoked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, future_creation_store: Path,
) -> None:
    """执行 ready 状态的 Handoff 时状态保持 ready，不派发执行"""
    record = resident_domain.register_resident(_new_session_payload("Handoff ready only"), "sample_admin")
    handoff = {"id": "handoff_fixture", "status": "ready", "execution": {"enabled": True, "status": "not_requested"}}
    lineage = {"lineage_id": record["lineage_id"], "nodes": [], "revision": 1}
    monkeypatch.setattr(lineage_domain, "_read_lineage", lambda _, **_kwargs: lineage)
    monkeypatch.setattr(lineage_domain, "_ensure_manager", lambda *_args: None)
    monkeypatch.setattr(lineage_domain, "_handoff_edge", lambda *_args: handoff)
    monkeypatch.setattr(lineage_domain, "_verified_lineage_view", lambda value, _actor: value)

    result = lineage_domain.execute_handoff(record["lineage_id"], "handoff_fixture", "sample_admin")
    assert result["handoff"]["status"] == "ready"
    assert "dispatch" not in result
