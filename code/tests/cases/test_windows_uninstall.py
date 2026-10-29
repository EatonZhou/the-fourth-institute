"""Windows 卸载时的实例选择与删除边界。

:module: operations.deployment
:design: decision_d88306d0bd17
:importance: P0
:profiles: local.fixture
:risk: no-external-side-effects
"""
import json
from pathlib import Path, PureWindowsPath
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
    """默认卸载只注销服务，保留实例与缓存数据。"""
    root = instance(platform)
    cache = platform / "dependencies/download"
    cache.mkdir(parents=True)
    (cache / "keep").write_bytes(b"cached dependency")
    prepared = platform / ".build/example_instance-windows/engineering/keep"
    prepared.parent.mkdir(parents=True)
    prepared.write_bytes(b"prepared seed")
    monkeypatch.setattr(launcher, "PLATFORM", platform)
    monkeypatch.setattr(launcher, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(launcher.sys, "argv", ["launcher", "--uninstall-platform"])
    with patch.object(launcher, "stop_all") as stop, patch.object(uninstall, "remove_instances") as remove:
        launcher.main()
        stop.assert_called_once_with(platform, unregister=True)
        remove.assert_not_called()
    assert (root / "data/keep.txt").read_text() == "private instance data"
    assert (cache / "keep").read_bytes() == b"cached dependency"
    assert prepared.read_bytes() == b"prepared seed"


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


@pytest.mark.parametrize("name", [".build", "dependencies"])
@pytest.mark.parametrize("nested", [False, True])
def test_redirected_cache_prevents_any_instance_removal(platform, owned_state, tmp_path_factory, name, nested):
    """缓存根或子目录指向外部时，删除实例前即拒绝卸载。"""
    root = instance(platform)
    external = tmp_path_factory.mktemp("external-cache-target")
    (external / "keep").write_text("not platform data")
    link = platform / name
    if nested:
        link.mkdir()
        link = link / "redirect"
    link.symlink_to(external, target_is_directory=True)
    with patch.object(uninstall.subprocess, "run") as run, pytest.raises(ValueError, match="link or reparse"):
        uninstall.remove_instances(platform)
    run.assert_not_called()
    assert (root / "data/keep.txt").is_file()
    assert (external / "keep").read_text() == "not platform data"


@pytest.mark.parametrize("name", [".build", "dependencies"])
def test_non_directory_cache_prevents_any_instance_removal(platform, owned_state, name):
    """未知类型的缓存入口阻止删除，不留下部分清理的实例。"""
    root = instance(platform)
    (platform / name).write_text("unexpected file")
    with patch.object(uninstall.subprocess, "run") as run, pytest.raises(ValueError, match="cache directory"):
        uninstall.remove_instances(platform)
    run.assert_not_called()
    assert (root / "data/keep.txt").is_file()


def test_valid_caches_wait_for_native_uninstaller_even_without_instances(platform):
    """Python 返回前保留运行时和日志，由原生卸载器在进程退出后删除。"""
    files = [platform / "dependencies/python/python.exe", platform / ".build/windows/launcher.lock"]
    for path in files:
        path.parent.mkdir(parents=True)
        path.write_bytes(b"in use by uninstall helper")
    with patch.object(uninstall.subprocess, "run") as run:
        uninstall.remove_instances(platform)
    run.assert_not_called()
    assert all(path.is_file() for path in files)
    # A cached junction still blocks deletion when no instance directory exists.
    with patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=0o40755, st_file_attributes=0x400)):
        with pytest.raises(ValueError, match="reparse"):
            uninstall.validate_platform_caches(platform)


@pytest.mark.parametrize(("original", "expected"), [
    (r"C:\Users\测试 User\TFI\.build", "\\\\?\\C:\\Users\\测试 User\\TFI\\.build"),
    (r"\\server\share\TFI\.build", "\\\\?\\UNC\\server\\share\\TFI\\.build"),
    (r"\\?\C:\TFI\.build", "\\\\?\\C:\\TFI\\.build"),
])
def test_windows_removal_uses_extended_paths(original, expected):
    """盘符、中文空格及 UNC 删除路径均保持含义，不重复添加前缀。"""
    with patch.object(uninstall, "os", SimpleNamespace(name="nt")):
        assert uninstall._native_path(PureWindowsPath(original)) == expected


def test_deep_preparation_cache_is_removed_while_launcher_is_locked(platform):
    """清理用户报告的超长准备路径，同时保留正在使用的启动锁和运行时。"""
    workspace = (
        ".build/example_instance-windows/engineering/data/share_root/Agents/"
        "lineage_75e3399f4877/residents/resident_2461a452e3f7/workspace/"
    )
    project = "lenia-core/code/src/local_learning/method_renewal_local_plasticity/anticipatory_io.py"
    # The reported path passed 260 characters under the user's install root;
    # pad it so a short temporary root still crosses that limit.
    padding = "p" * max(1, 262 - len(str(platform / workspace / project)))
    target = Path(uninstall._native_path(platform / workspace / padding / project))
    target.parent.mkdir(parents=True)
    target.write_bytes(b"example code")
    assert len(str(target)) > 260
    python = platform / "dependencies/python/python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"runtime in use")
    with launcher.launcher_lock(platform), patch.object(uninstall.subprocess, "run") as run:
        uninstall.remove_instances(platform)
        assert (platform / ".build/windows/launcher.lock").is_file()
    run.assert_not_called()
    assert not target.exists()
    assert not (platform / ".build/example_instance-windows").exists()
    assert python.is_file()
    assert (platform / "code").is_dir()


def test_preparation_cache_failure_preserves_instance_and_platform(platform, owned_state):
    """准备缓存无法删除时明确失败，保留实例及卸载所需程序供重试。"""
    root = instance(platform)
    cached = platform / ".build/example_instance-windows"
    cached.mkdir(parents=True)
    (cached / "locked").write_bytes(b"locked cache")
    with patch.object(uninstall.subprocess, "run") as run, \
            patch.object(uninstall.shutil, "rmtree", side_effect=PermissionError("cache in use")), \
            pytest.raises(PermissionError, match="cache in use"):
        uninstall.remove_instances(platform)
    assert len(run.call_args_list) == 1
    assert run.call_args.args[0][2] == "stop"
    assert (root / "data/keep.txt").is_file()
    assert (cached / "locked").is_file()
    assert (platform / "code").is_dir()


def test_native_uninstall_cleanup_is_gated_and_checks_for_residuals():
    """原生卸载在等待 Python 退出后清理缓存，并检查根目录是否仍有残留。"""
    script = (Path(__file__).resolve().parents[2] / "scripts/windows/setup.nsi").read_text(encoding="utf-8")
    assert "ManifestLongPathAware true" in script
    section = script.split('Section "Uninstall"', 1)[1]
    consent = "${If} $RemoveInstances == ${BST_CHECKED}"
    assert section.index('SetOutPath "$TEMP"') < section.index("nsExec::ExecToLog")
    after_stop = section.split("stopped:", 1)[1]
    deletion = after_stop.split(consent, 1)[1].split("${Else}", 1)[0]
    assert 'RMDir /r "$INSTDIR\\.build"' in deletion
    assert 'RMDir /r "$INSTDIR\\dependencies"' in deletion
    assert 'RMDir /r "$INSTDIR"' not in section
    assert 'RMDir /r "$INSTDIR\\instance"' not in section
    assert 'ClearErrors\n    RMDir "$INSTDIR"\n    ${If} ${Errors}' in section
    assert section.index("SetErrorLevel 1") < section.index("Platform, instances, dependencies, caches and logs removed.")
    assert "Call un.ListResiduals" in section
    assert "Remaining:$ResidualItems" in section
