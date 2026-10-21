"""macOS desktop setup using the shared installer and startup controller."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.tfi_windows_launcher import (
    DefaultStartup, Launcher, choose_ports, launcher_lock, make_target, unpack_seed,
)


def default_platform():
    # Keep the database and application Unix socket paths under sockaddr_un's
    # limit. Everything is owned by the logged-in user, outside the .app/DMG.
    return (Path.home() / "Library/TFI").resolve()


def materialize(bundle, destination):
    """An immutable app supplies one recoverable, user-owned platform copy."""
    bundle, destination = Path(bundle).resolve(), Path(destination).absolute()
    if destination != destination.resolve():
        raise ValueError("安装目录不能是符号链接")
    receipt = json.loads((bundle / "macos-package.json").read_text())
    marker = destination / "macos-package.json"
    if marker.exists():
        if json.loads(marker.read_text()) != receipt:
            raise ValueError("此目录已有其他版本。试用版暂不支持覆盖升级，请使用原版本打开已有资料。")
        return
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("安装目录包含其他文件；请使用空目录。")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".tfi-prepare-", dir=destination.parent) as tmp:
        staging = Path(tmp) / "platform"
        shutil.copytree(bundle, staging, symlinks=True)
        # The bundle contains deliberate relative Python library links. Verify
        # they stay inside the payload after relocation before publishing it.
        for path in staging.rglob("*"):
            if path.is_symlink() and not path.resolve().is_relative_to(staging.resolve()):
                raise ValueError("安装包包含越界链接")
        if destination.exists():
            destination.rmdir()
        staging.rename(destination)


class MacStartup(DefaultStartup):
    def __init__(self, platform, progress=lambda message: None, *, included=True):
        super().__init__(platform, progress)
        self.included = included
        self.root = self.platform / "instance/desktop"
        self.log_path = self.platform / ".build/macos/logs/desktop.log"

    def command(self, *arguments):
        return [str(self.platform / "dependencies/python/bin/python3"),
                str(self.platform / "code/scripts/tfi_release_install.py"), *map(str, arguments)]

    def initialize(self):
        bundle = self.platform / ".build/example_instance-macos/engineering/manifest.json"
        target_path = self.platform / ".build/macos/target.json"
        options = self.platform / ".build/macos/options.json"
        state_exists = (self.root / "installation.json").exists()
        if options.exists():
            included = json.loads(options.read_text())["include_examples"]
        else:
            if state_exists:
                raise ValueError("已有实例缺少安装选项，不能重新导入资料。")
            included = self.included
            options.parent.mkdir(parents=True, exist_ok=True)
            options.write_text(json.dumps({"include_examples": included}))
        if not state_exists:
            self.progress("正在准备 Lenia 预置案例" if included else "正在准备基础平台（不含 Lenia 案例）")
            unpack_seed(self.platform, included=included, receipt_name="macos-package.json")
            self.execute([self.command()[0], str(self.platform / "prepare_install.py"),
                          "--platform", "macos", *([] if included else ["--without-examples"])])
        if not target_path.exists():
            if state_exists:
                raise ValueError("已有实例缺少原安装参数，不能覆盖资料。")
            manifest = json.loads(bundle.read_text())
            target = make_target(self.platform, "desktop", manifest, ports=choose_ports())
            target.pop("windows_service_mode")
            target["python"] = self.command()[0]
            target["offline_dependencies"] = True
            target_path.write_text(json.dumps(target, ensure_ascii=False, indent=2))
        self.progress("正在初始化 Portal；使用安装包内置的运行环境")
        self.execute(self.command("install", "--manifest", bundle, "--target", target_path))


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
            materialize(args.bundle, args.platform)
            flow = MacStartup(args.platform, print, included=not args.without_examples)
            flow.run()  # Never print the first-user invitation secret.
            print(json.dumps({"status": "ready", "root": str(flow.root)}))
            return
        import tkinter as tk
        from tkinter import ttk, messagebox
        root = tk.Tk()
        root.title("The Fourth Institute · macOS 试用版")
        frame = ttk.Frame(root, padding=30)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="The Fourth Institute", font=("Helvetica", 24, "bold")).pack(pady=10)
        ttk.Label(frame, text="本地运行环境已内置。AI 账号和可选工具可在进入后配置。",
                  wraplength=450).pack(pady=10)
        included = tk.BooleanVar(value=True)
        choice = ttk.Checkbutton(frame, text="安装 Lenia 预置案例", variable=included)
        choice.pack(pady=15)
        if (args.platform / ".build/macos/options.json").exists():
            choice.pack_forget()
        def begin():
            try:
                materialize(args.bundle, args.platform)
                frame.destroy()
                Launcher(root, flow=MacStartup(args.platform, included=included.get()))
            except Exception as exc:
                messagebox.showerror("启动未完成", str(exc), parent=root)
        ttk.Button(frame, text="打开研究空间", command=begin).pack(pady=10)
        root.mainloop()


if __name__ == "__main__":
    main()
