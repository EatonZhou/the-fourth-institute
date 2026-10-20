from __future__ import annotations

import errno
from pathlib import Path

import pytest

from management_portal.society_service import invariant_locks, resident_workspace_transfer as transfer


@pytest.fixture
def directories(tmp_path):
    source = tmp_path / "source" / "residents" / "resident_111111111111"
    target = tmp_path / "target" / "residents" / source.name
    source.mkdir(parents=True)
    (source / "content.txt").write_text("original", encoding="utf-8")
    return source, target


@pytest.mark.parametrize("cross_device", [False, True])
def test_transfer_replay_preserves_files_and_removes_marker_only_after_commit(directories, monkeypatch, cross_device):
    """Resident 文件夹迁移在同盘或跨盘时重放都保留文件，提交后才删除迁移标记"""
    source, target = directories
    rename = Path.rename

    def simulated_device_boundary(path, destination):
        if cross_device and path == source:
            raise OSError(errno.EXDEV, "cross-device")
        return rename(path, destination)

    monkeypatch.setattr(Path, "rename", simulated_device_boundary)
    transfer.relocate("operation-one", str(source), str(target))
    assert not source.exists()
    assert (target / "content.txt").read_text() == "original"
    transfer.relocate("operation-one", str(source), str(target))
    assert (target / transfer._MARKER).exists()
    transfer.finish("operation-one", str(source), str(target))
    transfer.finish("operation-one", str(source), str(target))
    assert not (target / transfer._MARKER).exists()


def test_copy_recovery_does_not_overwrite_complete_target_from_partial_source(directories, monkeypatch):
    """跨盘复制后清理源目录中断时，恢复不用残缺的源目录覆盖完整的目标目录"""
    source, target = directories
    rename, remove = Path.rename, transfer.remove_tree

    def crossing(path, destination):
        if path == source:
            raise OSError(errno.EXDEV, "cross-device")
        return rename(path, destination)

    def interrupted_removal(path):
        assert Path(path) == source
        (source / "content.txt").unlink()
        raise OSError("cleanup interrupted")

    monkeypatch.setattr(Path, "rename", crossing)
    monkeypatch.setattr(transfer, "remove_tree", interrupted_removal)
    with pytest.raises(OSError, match="cleanup interrupted"):
        transfer.relocate("operation-one", str(source), str(target))
    assert (target / "content.txt").read_text() == "original"
    monkeypatch.setattr(transfer, "remove_tree", remove)
    transfer.relocate("operation-one", str(source), str(target))
    assert not source.exists()
    assert (target / "content.txt").read_text() == "original"


def test_transfer_refuses_unowned_destination_and_state_lock(directories, monkeypatch, tmp_path):
    """目标目录已存在或持有 Resident 状态锁时拒绝迁移，原有文件不变"""
    source, target = directories
    target.mkdir(parents=True)
    (target / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        transfer.relocate("operation-one", str(source), str(target))
    assert (target / "keep.txt").read_text() == "keep"
    assert (source / "content.txt").read_text() == "original"
    monkeypatch.setattr(invariant_locks, "_ROOT_PROVIDER", lambda: tmp_path)
    with invariant_locks.resident_lock(source.name):
        with pytest.raises(RuntimeError, match="outside state scopes"):
            transfer.relocate("operation-one", str(source), str(target))
