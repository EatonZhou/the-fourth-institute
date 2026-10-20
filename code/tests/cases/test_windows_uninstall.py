"""Windows 卸载时的实例选择与删除边界。

:module: operations.deployment
:design: decision_d88306d0bd17
:importance: P0
:profiles: local.fixture
:risk: no-external-side-effects
"""
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts import tfi_release_install as installer
from scripts import tfi_windows_launcher as launcher
from scripts import tfi_windows_uninstall as uninstall


@pytest.fixture
def platform(tmp_path):
    (tmp_path / "code").mkdir()
    return tmp_path


def instance(platform, name="example_instance"):
    root = platform / "instance" / name
    (root / "seed").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "data/keep.txt").write_text("private instance data")
    (root / "installation.json").write_text(json.dumps({"target": {
        "install_root": str(root), "shared_code_root": str(platform / "code"),
        "data_roots": {"share_root": str(root / "data")},
    }}))
    return root


@pytest.fixture
def owned_state():
    # Ownership/service validation itself remains in the formal installer.
    with patch.object(installer, "owned_state", side_effect=lambda root, **_: json.loads(
            (root / "installation.json").read_text())):
        yield


def test_default_uninstall_retains_instance_data(platform, monkeypatch):
    """默认卸载只注销服务，保留实例数据。"""
    root = instance(platform)
    monkeypatch.setattr(launcher, "PLATFORM", platform)
    monkeypatch.setattr(launcher, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(launcher.sys, "argv", ["launcher", "--uninstall-platform"])
    with patch.object(launcher, "stop_all") as stop, patch.object(uninstall, "remove_instances") as remove:
        launcher.main()
        stop.assert_called_once_with(platform, unregister=True)
        remove.assert_not_called()
    assert (root / "data/keep.txt").read_text() == "private instance data"


def test_remove_requires_uninstall_mode(monkeypatch):
    """单独传入删除参数不执行实例删除。"""
    monkeypatch.setattr(launcher.sys, "argv", ["launcher", "--remove-instances"])
    with patch.object(uninstall, "remove_instances") as remove, pytest.raises(SystemExit):
        launcher.main()
    remove.assert_not_called()


def test_explicit_removal_stops_all_then_uses_owned_cleanup(platform, owned_state):
    """明确删除时先停止全部实例，再沿正式清理入口删除。"""
    roots = [instance(platform, "first"), instance(platform, "second")]
    calls = []

    def run(command, **kwargs):
        action, root = command[2], Path(command[-1])
        calls.append((action, root))
        assert kwargs["check"]
        if action == "cleanup":
            # Model lifecycle's documented preservation of the public seed.
            shutil.rmtree(root / "data")
            (root / "installation.json").unlink()

    with patch.object(uninstall.subprocess, "run", side_effect=run):
        uninstall.remove_instances(platform)
    assert calls == [("stop", r) for r in roots] + [("cleanup", r) for r in roots]
    assert not (platform / "instance").exists()
    assert (platform / "code").is_dir()


def test_failed_stop_preserves_all_data(platform, owned_state):
    """任一实例停止失败时不开始删除其他实例。"""
    roots = [instance(platform, "first"), instance(platform, "second")]
    with patch.object(uninstall.subprocess, "run", side_effect=[
        None, subprocess.CalledProcessError(1, ["stop"])
    ]), pytest.raises(subprocess.CalledProcessError):
        uninstall.remove_instances(platform)
    assert all((r / "data/keep.txt").is_file() for r in roots)


def test_invalid_second_instance_prevents_any_cleanup(platform, owned_state):
    """先核对所有实例；外部数据根阻止整次自动删除。"""
    first = instance(platform, "first")
    second = instance(platform, "second")
    state = json.loads((second / "installation.json").read_text())
    state["target"]["data_roots"]["share_root"] = str(platform / "external")
    (second / "installation.json").write_text(json.dumps(state))
    with patch.object(uninstall.subprocess, "run") as run, pytest.raises(ValueError, match="external data"):
        uninstall.remove_instances(platform)
    run.assert_not_called()
    assert (first / "data/keep.txt").is_file()


def test_symlink_and_foreign_platform_refuse_removal(platform, owned_state):
    """链接与其他平台所属实例不会被自动删除。"""
    root = instance(platform)
    (root / "data/link").symlink_to(platform / "code", target_is_directory=True)
    with pytest.raises(ValueError, match="link or reparse"):
        uninstall.removal_plan(platform)
    (root / "data/link").unlink()
    state = json.loads((root / "installation.json").read_text())
    state["target"]["shared_code_root"] = str(platform / "other-code")
    (root / "installation.json").write_text(json.dumps(state))
    with pytest.raises(ValueError, match="another platform"):
        uninstall.removal_plan(platform)


def test_windows_junction_is_rejected(platform):
    """Windows junction 的 reparse 标志阻止递归删除。"""
    with patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=0o40755, st_file_attributes=0x400)):
        with pytest.raises(ValueError, match="reparse"):
            uninstall.plain_tree(platform)


def test_seed_only_install_can_be_removed_but_unknown_files_cannot(platform):
    """尚未初始化的示例可清理；无归属记录的其他数据保留。"""
    seed = platform / "instance/example_instance/seed"
    seed.mkdir(parents=True)
    (seed / "manifest.json").write_text("{}")
    with patch.object(uninstall.subprocess, "run") as run:
        uninstall.remove_instances(platform)
    run.assert_not_called()
    unknown = platform / "instance/custom"
    unknown.mkdir(parents=True)
    (unknown / "keep").write_text("unknown")
    with pytest.raises(ValueError, match="no ownership record"):
        uninstall.remove_instances(platform)
    assert (unknown / "keep").is_file()


def test_failed_cleanup_does_not_remove_remaining_files(platform, owned_state):
    """正式清理入口失败时保留实例剩余文件和平台代码。"""
    root = instance(platform)
    with patch.object(uninstall.subprocess, "run", side_effect=[
        None, subprocess.CalledProcessError(1, ["cleanup"])
    ]), pytest.raises(subprocess.CalledProcessError):
        uninstall.remove_instances(platform)
    assert (root / "data/keep.txt").is_file()
    assert (platform / "code").is_dir()
