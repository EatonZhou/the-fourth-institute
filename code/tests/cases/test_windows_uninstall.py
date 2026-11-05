"""Windows 卸载时的实例选择与删除边界。

Instances belong to the uninstalled program when their deployments run its code;
removal retires them through the shared installer.

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

from scripts import tfi_windows_launcher as launcher
from scripts import tfi_windows_uninstall as uninstall
from tests.cases.instance_test_support import write_instance


@pytest.fixture
def platform(tmp_path):
    (tmp_path / "program/code").mkdir(parents=True)
    return tmp_path / "program"


@pytest.fixture
def registry():
    entries = {}
    with patch("management_common.host_instances.read_registry", side_effect=lambda: dict(entries)):
        yield entries


def instance(platform, registry, name="example_instance", **options):
    root = write_instance(platform.parent / "instances" / name, code=platform / "code", **options)
    manifest = json.loads((root / "instance.json").read_text())
    (root / "instance.json").write_text(json.dumps({**manifest, "name": name}))  # Registered by name.
    (root / "data/projects").mkdir(parents=True)
    (root / "data/projects/keep.txt").write_text("private instance data")
    registry[name] = str(root)
    return root


def installer_run(registry, calls):
    def run(command, **kwargs):
        action, name = command[2], command[-1]
        calls.append((action, name))
        assert kwargs["check"]
        if action == "retire":
            shutil.rmtree(registry.pop(name))
    return run


def test_default_uninstall_retains_instance_data(platform, registry, monkeypatch):
    """默认卸载只注销服务，保留实例与缓存数据。"""
    root = instance(platform, registry)
    cache = platform / "dependencies/download"
    cache.mkdir(parents=True)
    (cache / "keep").write_bytes(b"cached dependency")
    prepared = platform / ".build/w/engineering/keep"
    prepared.parent.mkdir(parents=True)
    prepared.write_bytes(b"prepared bundle")
    monkeypatch.setattr(launcher, "PLATFORM", platform)
    monkeypatch.setattr(launcher, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(launcher.sys, "argv", ["launcher", "--uninstall-platform"])
    with patch.object(launcher, "stop_all") as stop, patch.object(uninstall, "remove_instances") as remove:
        launcher.main()
        stop.assert_called_once_with(platform, unregister=True)
        remove.assert_not_called()
    assert (root / "data/projects/keep.txt").read_text() == "private instance data"
    assert (cache / "keep").read_bytes() == b"cached dependency"
    assert prepared.read_bytes() == b"prepared bundle"


def test_remove_requires_uninstall_mode(monkeypatch):
    """单独传入删除参数不执行实例删除。"""
    monkeypatch.setattr(launcher.sys, "argv", ["launcher", "--remove-instances"])
    with patch.object(uninstall, "remove_instances") as remove, pytest.raises(SystemExit):
        launcher.main()
    remove.assert_not_called()


def test_explicit_removal_stops_all_then_retires_through_the_installer(platform, registry):
    """明确删除时先停止全部实例，再由安装器逐个退役。"""
    roots = [instance(platform, registry, "first"), instance(platform, registry, "second")]
    calls = []
    with patch.object(uninstall.subprocess, "run", side_effect=installer_run(registry, calls)):
        assert uninstall.remove_instances(platform) == []
    assert calls == [("stop", "first"), ("stop", "second"), ("retire", "first"), ("retire", "second")]
    assert not any(root.exists() for root in roots)
    assert (platform / "code").is_dir()


def test_areas_outside_the_instance_root_are_kept_and_reported(platform, registry, tmp_path):
    external = tmp_path / "large-disk/papers"
    external.mkdir(parents=True)
    instance(platform, registry, areas={"papers": external})
    calls = []
    with patch.object(uninstall.subprocess, "run", side_effect=installer_run(registry, calls)):
        assert uninstall.remove_instances(platform) == [str(external.resolve())]
    assert external.is_dir()


def test_failed_stop_preserves_all_data(platform, registry):
    """任一实例停止失败时不开始退役。"""
    roots = [instance(platform, registry, "first"), instance(platform, registry, "second")]
    with patch.object(uninstall.subprocess, "run", side_effect=[
        None, subprocess.CalledProcessError(1, ["stop"])
    ]), pytest.raises(subprocess.CalledProcessError):
        uninstall.remove_instances(platform)
    assert all((r / "data/projects/keep.txt").is_file() for r in roots)


def test_unreadable_registration_prevents_any_removal(platform, registry):
    """先读取全部登记；无法读取的登记可能属于本程序，阻止整次自动删除。"""
    first = instance(platform, registry, "first")
    broken = platform.parent / "instances/broken"
    broken.mkdir(parents=True)
    (broken / "instance.json").write_text("{")
    registry["broken"] = str(broken)
    with patch.object(uninstall.subprocess, "run") as run, pytest.raises(ValueError):
        uninstall.remove_instances(platform)
    run.assert_not_called()
    assert (first / "data/projects/keep.txt").is_file()


def test_symlink_refuses_removal_and_other_programs_instances_are_untouched(platform, registry, tmp_path):
    """链接阻止删除；运行其他代码的实例不在卸载范围。"""
    root = instance(platform, registry)
    (root / "data/link").symlink_to(platform / "code", target_is_directory=True)
    with pytest.raises(ValueError, match="link or reparse"):
        uninstall.removal_plan(platform)
    (root / "data/link").unlink()
    foreign = write_instance(tmp_path / "foreign-instance")
    registry["foreign"] = str(foreign)
    assert [i.root for i in uninstall.removal_plan(platform)] == [root]


def test_windows_junction_is_rejected(platform):
    """Windows junction 的 reparse 标志阻止递归删除。"""
    with patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=0o40755, st_file_attributes=0x400)):
        with pytest.raises(ValueError, match="reparse"):
            uninstall.plain_tree(platform)


def test_failed_retire_does_not_remove_remaining_files(platform, registry):
    """退役失败时保留实例剩余文件和平台代码。"""
    root = instance(platform, registry)
    with patch.object(uninstall.subprocess, "run", side_effect=[
        None, subprocess.CalledProcessError(1, ["retire"])
    ]), pytest.raises(subprocess.CalledProcessError):
        uninstall.remove_instances(platform)
    assert (root / "data/projects/keep.txt").is_file()
    assert (platform / "code").is_dir()


@pytest.mark.parametrize("name", [".build", "dependencies"])
@pytest.mark.parametrize("nested", [False, True])
def test_redirected_cache_prevents_any_instance_removal(platform, registry, tmp_path_factory, name, nested):
    """缓存根或子目录指向外部时，删除实例前即拒绝卸载。"""
    root = instance(platform, registry)
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
    assert (root / "data/projects/keep.txt").is_file()
    assert (external / "keep").read_text() == "not platform data"


@pytest.mark.parametrize("name", [".build", "dependencies"])
def test_non_directory_cache_prevents_any_instance_removal(platform, registry, name):
    """未知类型的缓存入口阻止删除，不留下部分清理的实例。"""
    root = instance(platform, registry)
    (platform / name).write_text("unexpected file")
    with patch.object(uninstall.subprocess, "run") as run, pytest.raises(ValueError, match="cache directory"):
        uninstall.remove_instances(platform)
    run.assert_not_called()
    assert (root / "data/projects/keep.txt").is_file()


def test_valid_caches_wait_for_native_uninstaller_even_without_instances(platform, registry):
    """Python 返回前保留运行时和日志，由原生卸载器在进程退出后删除。"""
    files = [platform / "dependencies/python/python.exe", platform / ".build/windows/launcher.lock"]
    for path in files:
        path.parent.mkdir(parents=True)
        path.write_bytes(b"in use by uninstall helper")
    with patch.object(uninstall.subprocess, "run") as run:
        uninstall.remove_instances(platform)
    run.assert_not_called()
    assert all(path.is_file() for path in files)
    # A cached junction still blocks deletion when no instance exists.
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


def test_deep_preparation_cache_is_removed_while_launcher_is_locked(platform, registry):
    """清理超长准备路径，同时保留正在使用的启动锁和运行时。"""
    workspace = ".build/w/engineering/data/projects/元胞自动机研究/agents/lineage_75e3399f4877/residents/resident_2461a452e3f7/workspace/"
    project = "lenia-core/code/src/local_learning/method_renewal_local_plasticity/anticipatory_io.py"
    # Pad so a short temporary root still crosses the 260-character limit.
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
    assert not (platform / ".build/w").exists()
    assert python.is_file()
    assert (platform / "code").is_dir()


def test_preparation_cache_failure_preserves_instance_and_platform(platform, registry):
    """准备缓存无法删除时明确失败，保留实例及卸载所需程序供重试。"""
    root = instance(platform, registry)
    cached = platform / ".build/w"
    cached.mkdir(parents=True)
    (cached / "locked").write_bytes(b"locked cache")
    with patch.object(uninstall.subprocess, "run") as run, \
            patch.object(uninstall.shutil, "rmtree", side_effect=PermissionError("cache in use")), \
            pytest.raises(PermissionError, match="cache in use"):
        uninstall.remove_instances(platform)
    assert len(run.call_args_list) == 1
    assert run.call_args.args[0][2] == "stop"
    assert (root / "data/projects/keep.txt").is_file()
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
    assert 'ClearErrors\n    RMDir "$INSTDIR"\n    ${If} ${Errors}' in section
    assert section.index("SetErrorLevel 1") < section.index("Platform, instances, dependencies, caches and logs removed.")
    assert "Call un.ListResiduals" in section
    assert "Remaining:$ResidualItems" in section
    # Bulk deletion must not build an unbounded per-file native UI log. Failure
    # diagnostics must resume before the final residual check.
    assert after_stop.index('SetDetailsPrint none') < after_stop.index('RMDir /r')
    assert after_stop.index('Delete "$INSTDIR\\README_CN.md"') < after_stop.index('SetDetailsPrint lastused')
    assert after_stop.index('SetDetailsPrint lastused') < after_stop.index('Call un.ListResiduals')
    assert "$LOCALAPPDATA\\TFI\\instances" in section


@pytest.mark.parametrize("name", ["codex-cli-current", "dsh-runtime-current"])
def test_managed_provider_alias_removed_without_following_target(platform, registry, name):
    from management_common.directory_links import create_directory_link, is_directory_link
    root = instance(platform, registry)
    target = platform / "dependencies/providers" / (name.removesuffix("current") + "1.2.3")
    target.mkdir(parents=True)
    sentinel = target / "keep"
    sentinel.write_bytes(b"provider runtime")
    alias = target.parent / name
    create_directory_link(alias, target)
    calls = []
    with patch.object(uninstall.subprocess, "run", side_effect=installer_run(registry, calls)):
        uninstall.remove_instances(platform)
    assert not is_directory_link(alias)
    assert not alias.exists()
    assert sentinel.read_bytes() == b"provider runtime"
    assert not root.exists()


@pytest.mark.parametrize("kind", ["external", "unknown", "nested_redirect"])
def test_provider_alias_validation_failure_preserves_everything(platform, registry, tmp_path, kind):
    from management_common.directory_links import create_directory_link, is_directory_link
    root = instance(platform, registry)
    providers = platform / "dependencies/providers"
    providers.mkdir(parents=True)
    target = tmp_path / "external" if kind == "external" else providers / "codex-cli-1"
    target.mkdir()
    sentinel = target / "keep"
    sentinel.write_text("keep")
    alias = providers / ("unknown-current" if kind == "unknown" else "codex-cli-current")
    create_directory_link(alias, target)
    if kind == "nested_redirect":
        create_directory_link(target / "redirect", platform / "code")
    with patch.object(uninstall.subprocess, "run") as run, pytest.raises(ValueError, match="link or reparse"):
        uninstall.remove_instances(platform)
    run.assert_not_called()
    assert is_directory_link(alias)
    assert sentinel.read_text() == "keep"
    assert root.exists()


def test_failed_stop_preserves_managed_alias(platform, registry):
    from management_common.directory_links import create_directory_link, is_directory_link
    root = instance(platform, registry)
    target = platform / "dependencies/providers/codex-cli-1"
    target.mkdir(parents=True)
    alias = target.parent / "codex-cli-current"
    create_directory_link(alias, target)
    with patch.object(uninstall.subprocess, "run", side_effect=subprocess.CalledProcessError(1, ["stop"])), pytest.raises(subprocess.CalledProcessError):
        uninstall.remove_instances(platform)
    assert is_directory_link(alias)
    assert root.exists()


@pytest.mark.parametrize('external', [False, True])
def test_dsh_local_package_alias_only_targets_own_code(platform, registry, tmp_path, external):
    from management_common.directory_links import create_directory_link, is_directory_link
    alias = platform / 'dependencies/providers/dsh-runtime-1/node_modules/tfi-portal'
    alias.parent.mkdir(parents=True)
    target = tmp_path / 'external-code' if external else platform / 'code'
    target.mkdir(exist_ok=True)
    sentinel = target / 'keep'
    sentinel.write_text('program source')
    create_directory_link(alias, target)
    with patch.object(uninstall.subprocess, 'run') as run:
        if external:
            with pytest.raises(ValueError, match='link or reparse'):
                uninstall.remove_instances(platform)
            assert is_directory_link(alias)
        else:
            uninstall.remove_instances(platform)
            assert not is_directory_link(alias)
        run.assert_not_called()
    assert sentinel.read_text() == 'program source'
