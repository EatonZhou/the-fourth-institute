"""Owned move cleanup preserves entries replaced at the deletion boundary.

:module: resident.workspace-memory
:design: decision_4aa13bc21e8e
"""
from pathlib import Path
import pytest
from tests.cases.test_workspace_unregistration import workspace
from tests.cases.test_workspace_resource_rename import workspace as resource_workspace, WID
from tests.cases.test_directory_move_safety import cross_device

@pytest.mark.parametrize("entry", ["stage", "target"])
def test_move_recovery_preserves_replacement_at_delete_boundary(workspace, monkeypatch, entry):
    from management_common import directory_move_store as module
    from management_common.directory_paths import cleanup_path
    cross_device(monkeypatch, workspace.root)
    mover = module.DirectoryMoveStore()
    target = workspace.root.with_name("move-target")
    if entry == "stage":
        with monkeypatch.context() as patch:
            patch.setattr(module.shutil, "copytree", lambda *a, **k: (_ for _ in ()).throw(SystemExit("crash")))
            patch.setattr(mover, "abort", lambda *_: None)
            with pytest.raises(SystemExit):
                mover.prepare(workspace.root, target, owner_id=WID, actor="alice", terminate=lambda _: None, operation_id="boundary")
        path = cleanup_path(mover.get("boundary")["staging"])
    else:
        mover.prepare(workspace.root, target, owner_id=WID, actor="alice", terminate=lambda _: None, operation_id="boundary")
        path = target
    original = module.remove_path
    injected = []
    def replaced(value, **kwargs):
        if value == path and not injected:
            value.rename(value.with_name("saved-original-" + entry))
            value.mkdir()
            (value / "FOREIGN.txt").write_text("foreign host data")
            injected.append(True)
        return original(value, **kwargs)
    monkeypatch.setattr(module, "remove_path", replaced)
    result = module.DirectoryMoveStore().recover(dependencies=lambda *_: None)
    assert injected
    assert (path / "FOREIGN.txt").exists(), result


