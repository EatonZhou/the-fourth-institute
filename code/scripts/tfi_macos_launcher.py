"""macOS desktop setup using the shared installer and startup controller."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.tfi_windows_launcher import (
    DefaultStartup, Launcher, choose_ports, launcher_lock, make_target, unpack_seed,
    selected_engineering, unpack_engineering,
)


def default_platform():
    # Keep the database and application Unix socket paths under sockaddr_un's
    # limit. Everything is owned by the logged-in user, outside the .app/DMG.
    return (Path.home() / "Library/TFI").resolve()


def materialize(bundle, destination, *, progress=None):
    """An immutable app supplies one recoverable, user-owned platform copy."""
    bundle, destination = Path(bundle).resolve(), Path(destination).absolute()
    if destination != destination.resolve():
        raise ValueError("安装目录不能是符号链接")
    receipt = json.loads((bundle / "macos-package.json").read_text())
    marker = destination / "macos-package.json"
    if marker.exists():
        if json.loads(marker.read_text()) != receipt:
            raise ValueError("此目录已有其他版本。试用版暂不支持覆盖升级，请使用原版本打开已有资料。")
        if progress:
            progress("复用已准备的本地运行环境")
        return
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("安装目录包含其他文件；请使用空目录。")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Traverse metadata once. Case archives remain in the read-only app and are
    # consumed directly; neither the unselected case nor its archive is copied.
    directories, files, links = [], [], []
    for directory, dirs, names in os.walk(bundle, followlinks=False):
        for name in [*dirs, *names]:
            source = Path(directory) / name
            relative = source.relative_to(bundle)
            if relative.as_posix() in {"base-engineering.zip", "example-engineering.zip"}:
                continue
            details = source.lstat()
            mode = details.st_mode
            if stat.S_ISLNK(mode):
                link = os.readlink(source)
                if Path(link).is_absolute() or not source.resolve().is_relative_to(bundle):
                    raise ValueError("安装包包含越界链接")
                links.append((relative, link))
            elif stat.S_ISDIR(mode):
                directories.append(relative)
            elif stat.S_ISREG(mode):
                files.append((relative, details.st_size))
            else:
                raise ValueError("安装包包含非普通文件")
    from scripts.tfi_install_progress import FileProgress, copy_stream
    meter = FileProgress("正在复制本地运行环境", len(files) + len(links), sum(size for _, size in files), progress)
    with tempfile.TemporaryDirectory(prefix=".tfi-prepare-", dir=destination.parent) as tmp:
        staging = Path(tmp) / "platform"
        staging.mkdir()
        for relative in directories:
            (staging / relative).mkdir()
        for relative, _ in files:
            with (bundle / relative).open('rb') as source, (staging / relative).open('wb') as output:
                copy_stream(source, output, meter)
            shutil.copystat(bundle / relative, staging / relative)
        for relative, link in links:
            (staging / relative).symlink_to(link)
            meter.advance(0)
        for relative in reversed(directories):
            shutil.copystat(bundle / relative, staging / relative)
        if destination.exists():
            destination.rmdir()
        staging.rename(destination)


class MacStartup(DefaultStartup):
    def __init__(self, platform, progress=lambda message: None, *, included=True, bundle=None):
        super().__init__(platform, progress)
        self.included = included
        self.bundle = bundle
        self.root = self.platform / "instance/desktop"
        self.log_path = self.platform / ".build/macos/logs/desktop.log"
        if bundle is not None:
            # A failed copy must not create log files in the still-empty target:
            # materialize deliberately refuses to overwrite foreign contents.
            self.log_path = self.platform.parent / ("." + self.platform.name + "-launcher") / "startup.log"

    def run(self):
        if self.bundle is not None:
            self.progress("正在准备本地运行环境，首次复制可能需要一些时间")
            materialize(self.bundle, self.platform, progress=self.progress)
            self.log_path = self.platform / ".build/macos/logs/desktop.log"
        return super().run()

    def command(self, *arguments):
        return [str(self.platform / "dependencies/python/bin/python3"),
                str(self.platform / "code/scripts/tfi_release_install.py"), *map(str, arguments)]

    def initialize(self):
        from scripts.tfi_release_install import write_json
        bundle = self.platform / ".build/m/engineering/manifest.json"
        legacy_bundle = self.platform / ".build/example_instance-macos/engineering/manifest.json"
        target_path = self.platform / ".build/macos/target.json"
        options = self.platform / ".build/macos/options.json"
        state_exists = (self.root / "installation.json").exists()
        if not bundle.exists() and legacy_bundle.exists():
            bundle = legacy_bundle
        if options.exists():
            included = json.loads(options.read_text())["include_examples"]
        else:
            if state_exists:
                raise ValueError("已有实例缺少安装选项，不能重新导入资料。")
            included = self.included
            write_json(options, {"include_examples": included})
        if not state_exists:
            archive = selected_engineering(Path(self.bundle or self.platform), included)
            if archive.is_file():
                bundle = unpack_engineering(self.platform, included=included, progress=self.progress,
                                            archive=archive, parent=self.platform / '.build/m')
            elif bundle == legacy_bundle or (self.platform / 'example-seed.zip').is_file():
                # Original preview packages retain their existing seed contract.
                bundle = legacy_bundle
                unpack_seed(self.platform, included=included, receipt_name="macos-package.json")
                self.execute([self.command()[0], str(self.platform / "prepare_install.py"),
                              "--platform", "macos", *([] if included else ["--without-examples"])])
            elif not bundle.is_file():
                raise ValueError("缺少安装工程压缩包，请从下载的 macOS 应用重新打开。")
        if not target_path.exists():
            if state_exists:
                raise ValueError("已有实例缺少原安装参数，不能覆盖资料。")
            manifest = json.loads(bundle.read_text())
            target = make_target(self.platform, "desktop", manifest, ports=choose_ports())
            target.pop("windows_service_mode")
            target["python"] = self.command()[0]
            target["offline_dependencies"] = True
            write_json(target_path, target)
        self.progress("正在初始化 Portal；使用安装包内置的运行环境")
        self.execute(self.command("install", "--manifest", bundle, "--target", target_path,
                                  "--skip-file-verification"))


def stop(platform, *, unregister=False):
    from scripts import tfi_release_install as api
    flow = MacStartup(platform)
    if not (flow.root / "installation.json").is_file():
        return
    flow.execute(flow.command("stop", "--root", flow.root))
    if unregister:
        from scripts import tfi_release_platform_macos as adapter
        target = flow.state()["target"]
        names = [adapter.unit_name(target, c) for c in api.COMPONENTS]
        for component, name in zip(api.COMPONENTS, names):
            path = adapter.unit_path(adapter.unit_directory(), name)
            if path.exists() and path.read_text() != adapter.unit_text(target, component, api.COMPONENTS):
                raise ValueError("拒绝删除不属于此实例的启动项")
        from scripts.tfi_release_provider_services import remove_template
        remove_template(target, adapter.unit_directory())
        adapter.remove_services(adapter.unit_directory(), names)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--platform", type=Path, default=default_platform())
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--without-examples", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--unregister", action="store_true", help="Stop services and remove launch agents; keep data")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("This launcher requires macOS")
    if args.stop or args.unregister:
        lock_root = args.platform.parent / ("." + args.platform.name + "-launcher")
        with launcher_lock(lock_root, system="macos"):
            stop(args.platform, unregister=args.unregister)
        return
    # The lock is outside the payload directory, including first-copy time.
    lock_root = args.platform.parent / ("." + args.platform.name + "-launcher")
    with launcher_lock(lock_root, system="macos"):
        if args.headless:
            flow = MacStartup(args.platform, print, included=not args.without_examples, bundle=args.bundle)
            flow.run()  # Never print the first-user invitation secret.
            print(json.dumps({"status": "ready", "root": str(flow.root)}))
            return
        import tkinter as tk
        from tkinter import ttk, messagebox
        root = tk.Tk()
        root.title("The Fourth Institute · macOS 试用版")
        frame = ttk.Frame(root, padding=30)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="欢迎安装 The Fourth Institute", font=("Helvetica", 24, "bold")).pack(pady=10)
        ttk.Label(frame, text="与 AI 一起探索、协作、创造知识。", wraplength=450).pack(pady=10)
        ttk.Label(frame, text="macOS 15+ 试用版，基础安装无需联网。", wraplength=450).pack(pady=10)
        included = tk.BooleanVar(value=not args.without_examples)
        choice = ttk.Checkbutton(frame, text="安装 Lenia 预置案例", variable=included)
        choice.pack(pady=15)
        if (args.platform / ".build/macos/options.json").exists():
            choice.pack_forget()
        def begin():
            try:
                # Launcher runs flow.run in its worker thread; never copy a
                # multi-hundred-MB payload on Tk's event loop.
                Launcher(root, flow=MacStartup(args.platform, included=included.get(), bundle=args.bundle))
                frame.destroy()
            except Exception as exc:
                messagebox.showerror("启动未完成", str(exc), parent=root)
        ttk.Button(frame, text="打开研究空间", command=begin).pack(pady=10)
        root.mainloop()


if __name__ == "__main__":
    main()
