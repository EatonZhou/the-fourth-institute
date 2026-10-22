"""Transactional updates of a single macOS desktop's shared application code.

The immutable app runs this coordinator. The old directory is renamed, never
overwritten, and remains paired with its stopped database until health succeeds.
Unlike portable engineering upgrades this does not import release seed data.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import stat
import uuid
from contextlib import ExitStack

from management_common.file_permissions import create_private_file, ensure_private_directory
from scripts.tfi_install_progress import FileProgress, copy_stream


RECEIPT = "macos-package.json"
STATE = "instance/desktop/installation.json"
TERMINAL = {"complete", "restored"}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def control_root(platform):
    return platform.parent / ("." + platform.name + "-launcher")


def pending(platform):
    path = control_root(Path(platform)) / "upgrade.json"
    return path.is_file() and read(path).get("phase") not in TERMINAL


def changed(bundle, platform):
    marker = Path(platform) / RECEIPT
    return marker.is_file() and read(marker) != read(Path(bundle) / RECEIPT)


def copy_tree(source, destination, progress, *, excluded=(), label="正在保留本地资料"):
    """Copy each file once, without following links or copying socket inodes."""
    source, destination = Path(source), Path(destination)
    directories, files, links = [], [], []
    for directory, dirs, names in os.walk(source, followlinks=False):
        dirs[:] = [n for n in dirs if (Path(directory) / n).relative_to(source).as_posix() not in excluded]
        for name in [*dirs, *names]:
            path = Path(directory) / name
            relative = path.relative_to(source)
            if relative.as_posix() in excluded:
                continue
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                links.append((relative, os.readlink(path)))
            elif stat.S_ISDIR(mode):
                directories.append(relative)
            elif stat.S_ISREG(mode):
                files.append((relative, path.stat().st_size))
            elif not stat.S_ISSOCK(mode):
                raise ValueError(f"无法安全备份此类型的文件：{relative}")
    meter = FileProgress(label, len(files) + len(links), sum(size for _, size in files), progress)
    destination.mkdir(mode=0o700)
    ensure_private_directory(destination)
    for relative in directories:
        (destination / relative).mkdir(mode=0o700)
    for relative, _ in files:
        # Restrict and verify each new file before any account/database bytes
        # are written; the inherited ACL is independent of the POSIX mode.
        with (source / relative).open("rb") as src, create_private_file(destination / relative) as dst:
            copy_stream(src, dst, meter)
        shutil.copystat(source / relative, destination / relative)
    for relative, link in links:
        (destination / relative).symlink_to(link)
        meter.advance(0)
    for relative in reversed(directories):
        shutil.copystat(source / relative, destination / relative)
    shutil.copystat(source, destination)


def compatibility(bundle, platform):
    """Only application updates with unchanged bundled runtimes are supported."""
    bundle, platform = Path(bundle).resolve(), Path(platform).absolute()
    if platform != platform.resolve() or not platform.is_dir():
        raise ValueError("升级目录必须是原有的实际目录，不能是符号链接。")
    old, new = read(platform / RECEIPT), read(bundle / RECEIPT)
    if any(r.get("schema") != "tfi.macos-package.v1" for r in (old, new)):
        raise ValueError("旧安装记录不完整，请使用独立目录试用。")
    if old.get('bundled_python_dependencies') != new.get('bundled_python_dependencies'):
        raise ValueError('Python 依赖版本不同，请使用独立目录试用，保留原有资料。')
    for key in ("target", "python", "bundled_native_dependencies"):
        if not old.get(key) or old[key] != new.get(key):
            raise ValueError("此版本的芯片或内置运行环境不同，暂不能保留数据升级；请使用独立目录。")
    if new.get("source_dirty") is not False or new.get("prepared_engineering_validation") != "passed":
        raise ValueError("新安装包缺少构建验证记录。")
    for receipt in (old, new):
        if not re.fullmatch(r"[0-9a-f]{40}", receipt.get("source_commit", "")):
            raise ValueError("安装包缺少明确的源码版本。")
    # Updating dependencies is a separate migration; retain native binaries,
    # Python environments and user-installed Provider tools in this transaction.
    def requirements(root):
        return {p.name: p.read_bytes() for p in (root / "code").glob("requirements*.txt")}
    if not requirements(platform) or requirements(platform) != requirements(bundle):
        raise ValueError("此版本的 Python 依赖已变更，暂不能保留数据升级；请使用独立目录。")
    root = platform / "instance/desktop"
    for relative in ("code", "dependencies", "instance", "instance/desktop", ".build"):
        path = platform / relative
        if path.is_symlink() or path.resolve() != path:
            raise ValueError("升级目录包含重定向，未修改已有资料。")
    if any(p.name != "desktop" for p in (platform / "instance").iterdir()):
        raise ValueError("此目录包含其他实例，不能作为单实例桌面升级。")
    state = read(root / "installation.json")
    target = state["target"]
    if (target.get("install_root") != str(root)
            or target.get("shared_code_root") != str(platform / "code")
            or target.get("python") != str(platform / "dependencies/python/bin/python3")):
        raise ValueError("实例路径与安装目录不一致，拒绝覆盖。")
    if any(not Path(p).is_relative_to(root) or Path(p).resolve() != Path(p)
           for p in target["data_roots"].values()):
        raise ValueError("实例使用外部数据目录，不能自动备份升级。")
    for name in ("config", "runtime", "home", "data", "data/database"):
        if (root / name).resolve() != root / name:
            raise ValueError("实例核心目录发生重定向，不能自动备份升级。")
    for path in (root / "data/database").rglob("*"):
        if path.is_symlink():
            raise ValueError("数据库存在外部存储链接，不能自动备份升级。")
    from scripts.tfi_release_install import start_blocker
    if start_blocker(state):
        raise ValueError("原实例尚未安装完成，请先使用原版完成初始化。")
    return old, new, state


class DesktopUpgrade:
    def __init__(self, flow, *, api=None):
        from scripts import tfi_release_install
        self.api = api or tfi_release_install
        self.flow = flow
        self.platform = flow.platform.absolute()
        self.root = self.platform / "instance/desktop"
        self.control = control_root(self.platform)
        if self.control.is_symlink() or self.control.resolve() != self.control:
            raise ValueError("升级记录目录不能是符号链接。")
        self.journal_path = self.control / "upgrade.json"

    def save(self, journal, phase):
        journal["phase"] = phase
        self.api.write_json(self.journal_path, journal)

    def paths(self, journal):
        if (journal.get("platform") != str(self.platform)
                or not re.fullmatch(r"upgrade-[0-9a-f]{32}", journal.get("id", ""))):
            raise ValueError("升级记录不属于此安装目录。")
        parent = self.control / journal["id"]
        if parent.is_symlink() or parent.resolve() != parent:
            raise ValueError("升级备份目录发生重定向。")
        paths = tuple(parent / name for name in ("staging", "previous", "failed"))
        if any(p.is_symlink() for p in paths):
            raise ValueError("升级备份不能是符号链接。")
        return paths

    def check_copy(self, path, receipt, target):
        if read(path / RECEIPT) != receipt or read(path / STATE)["target"] != target:
            raise ValueError("升级目录的版本或实例归属不匹配，停止自动恢复。")

    def assert_stopped(self, target):
        from scripts.tfi_release_provider_services import owned_services
        control = self.api.service_controller()
        names = [self.api.unit_name(target, c) for c in self.api.COMPONENTS]
        providers = owned_services(target, self.api.unit_directory())
        names.extend(providers["names"] if providers else [])
        if any((s := control.status(name))["pid"] or s["active"] not in {"inactive", "failed"}
               for name in names):
            raise RuntimeError("仍有实例服务运行，已停止升级；原资料未覆盖。")

    def recover(self):
        ensure_private_directory(self.control)
        journal = read(self.journal_path)
        staging, backup, failed = self.paths(journal)
        if journal["phase"] in TERMINAL:
            return
        if journal["phase"] not in {"quiescing", "copying", "switching", "starting", "restoring"}:
            raise ValueError("未知的升级阶段，保留现场等待检查。")
        ensure_private_directory(staging.parent)
        self.flow.progress("正在恢复上次升级前的版本和资料")
        if backup.exists():
            self.check_copy(backup, journal["old"], journal["target"])
            if self.platform.exists():
                self.check_copy(self.platform, journal["new"], journal["target"])
                # No restore while a process can still write to the new DB.
                self.api.lifecycle("stop", self.root)
                if failed.exists():
                    raise ValueError("已有失败现场，不能覆盖；请保留升级日志。")
            with ExitStack() as locks:
                if self.platform.exists():
                    locks.enter_context(self.api.installation_lock(self.root))
                    self.assert_stopped(journal["target"])
                locks.enter_context(self.api.installation_lock(backup / "instance/desktop"))
                self.save(journal, "restoring")
                if self.platform.exists():
                    self.platform.rename(failed)
                backup.rename(self.platform)
        else:
            # Either copying never switched, or the restore rename completed
            # before its journal write. Both leave the original generation.
            self.check_copy(self.platform, journal["old"], journal["target"])
        self.save(journal, "restored")
        # Failed/staging directories contain user data; never silently delete.
        self.flow.progress("已恢复旧版，原有资料和账号保留；请使用原版打开")

    def apply(self, start):
        from scripts.tfi_release_upgrade import check_quiescent
        old, new, state = compatibility(self.flow.bundle, self.platform)
        if self.api.owned_state(self.root)["target"] != state["target"]:
            raise ValueError("实例归属检查失败。")
        journal = {"id": "upgrade-" + uuid.uuid4().hex, "platform": str(self.platform),
                   "old": old, "new": new, "target": state["target"]}
        staging, backup, _ = self.paths(journal)
        # Both levels hold private backups. Check before stopping services,
        # including when a previous attempt left these directories behind.
        ensure_private_directory(self.control)
        ensure_private_directory(staging.parent)
        self.save(journal, "quiescing")
        try:
            self.flow.progress("正在检查任务并停止原版本服务")
            control = self.api.service_controller()
            # Bring up PostgreSQL only if the user previously stopped the site.
            control.start(self.api.unit_name(state["target"], "postgres"))
            self.api.wait_database(state["target"])
            for component in ("web", "society"):
                control.stop(self.api.unit_name(state["target"], component))
            check_quiescent(self.api, state["target"])
            self.api.lifecycle("stop", self.root)  # includes owned Provider jobs
            # The first check avoids interrupting active work. This second
            # check closes the race with a request accepted just before stop.
            # Only PostgreSQL runs; no application can enqueue new work now.
            try:
                control.start(self.api.unit_name(state["target"], "postgres"))
                self.api.wait_database(state["target"])
                check_quiescent(self.api, state["target"])
            finally:
                control.stop(self.api.unit_name(state["target"], "postgres"))
            self.save(journal, "copying")
            with self.api.installation_lock(self.root):
                # Also exclude direct lifecycle commands while copying. The
                # launcher lock alone only serializes app/GUI invocations.
                self.assert_stopped(state["target"])
                copy_tree(self.platform, staging, self.flow.progress, excluded={
                    "code", "branding", RECEIPT, ".build/m", ".build/example_instance-macos",
                    "instance/desktop/.install.lock", "base-engineering.zip",
                    "example-engineering.zip", "example-seed.zip", "base-seed.zip"})
                bundle = Path(self.flow.bundle)
                for name in ("code", "branding"):
                    copy_tree(bundle / name, staging / name, self.flow.progress,
                              label="正在复制新版程序")
                self.api.write_json(staging / RECEIPT, new)
                with self.api.installation_lock(staging / "instance/desktop"):
                    self.save(journal, "switching")
                    self.platform.rename(backup)
                    staging.rename(self.platform)
                    self.save(journal, "starting")
            self.flow.progress("正在启动新版并检查资料与服务")
            result = start()
            self.save(journal, "complete")
            self.flow.progress("升级完成，原有资料与账号已保留")
            return result
        except Exception as exc:
            try:
                self.recover()
            except Exception as restore_error:
                raise RuntimeError(f"升级未完成，自动恢复也未完成。请保留目录，查看记录：{self.journal_path}") from restore_error
            raise RuntimeError(f"升级未完成，已恢复原版资料。可使用原版本打开；升级记录：{self.journal_path}。原因：{exc}") from exc
