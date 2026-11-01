"""macOS desktop setup using the shared installer and startup controller.

The immutable app copies its platform (code and dependencies) to a user-owned
directory, by default ~/Library/TFI. The initial desktop instance, ``desktop``,
is created by the shared installer at its default location outside that
directory and registered on this host. A newer app updates the platform's code
in place (the protocol's code update); the instance and its data stay.
"""
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
from scripts.tfi_windows_launcher import DefaultStartup, Launcher, launcher_lock, stop_all  # noqa: E402

RECEIPT = "macos-package.json"
# Replaced by a code update; the bundled Python runtime and native archives of a
# compatible package are identical (checked before anything changes).
CODE_ENTRIES = ("code", "branding", "dependencies/python-wheels")


def default_platform():
    # Everything is owned by the logged-in user, outside the .app/DMG.
    return (Path.home() / "Library/TFI").resolve()


def welcome_logo(root, frame, bundle):
    import tkinter as tk
    from tkinter import ttk
    logo = tk.PhotoImage(master=root, file=str(Path(bundle) / "branding/logo.png"))
    root.iconphoto(True, logo)
    root._tfi_icon = logo
    logo = logo.subsample(max(1, (logo.width() + 127) // 128))
    label = ttk.Label(frame, image=logo)
    label.image = logo  # Tk stores only the image name; retain the Python object.
    label.pack(pady=(0, 10))
    return label


def read_receipt(root):
    return json.loads((Path(root) / RECEIPT).read_text(encoding="utf-8"))


def changed(bundle, platform):
    marker = Path(platform) / RECEIPT
    return marker.is_file() and read_receipt(platform) != read_receipt(bundle)


def _collect(source, *, skip=()):
    directories, files, links = [], [], []
    for directory, dirs, names in os.walk(source, followlinks=False):
        for name in [*dirs, *names]:
            path = Path(directory) / name
            relative = path.relative_to(source)
            if relative.as_posix() in skip:
                continue
            details = path.lstat()
            if stat.S_ISLNK(details.st_mode):
                link = os.readlink(path)
                if Path(link).is_absolute() or not path.resolve().is_relative_to(source):
                    raise ValueError("安装包包含越界链接")
                links.append((relative, link))
            elif stat.S_ISDIR(details.st_mode):
                directories.append(relative)
            elif stat.S_ISREG(details.st_mode):
                files.append((relative, details.st_size))
            else:
                raise ValueError("安装包包含非普通文件")
    return directories, files, links


def _copy(source, staging, entries, meter):
    directories, files, links = entries
    for relative in directories:
        (staging / relative).mkdir()
    from scripts.tfi_install_progress import copy_stream
    for relative, _ in files:
        with (source / relative).open('rb') as stream, (staging / relative).open('wb') as output:
            copy_stream(stream, output, meter)
        shutil.copystat(source / relative, staging / relative)
    for relative, link in links:
        (staging / relative).symlink_to(link)
        meter.advance(0)
    for relative in reversed(directories):
        shutil.copystat(source / relative, staging / relative)


def materialize(bundle, destination, *, progress=None):
    """An immutable app supplies one user-owned platform copy."""
    bundle, destination = Path(bundle).resolve(), Path(destination).absolute()
    if destination != destination.resolve():
        raise ValueError("安装目录不能是符号链接")
    marker = destination / RECEIPT
    if marker.exists():
        if json.loads(marker.read_text()) != read_receipt(bundle):
            raise ValueError("此目录已有其他版本，请使用桌面启动器的“更新程序”入口。")
        if progress:
            progress("复用已准备的本地运行环境")
        return
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("安装目录包含其他文件；请使用空目录。")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Case archives remain in the read-only app and are consumed directly.
    entries = _collect(bundle, skip={"base-engineering.zip", "example-engineering.zip"})
    from scripts.tfi_install_progress import FileProgress
    meter = FileProgress("正在复制本地运行环境", len(entries[1]) + len(entries[2]),
                         sum(size for _, size in entries[1]), progress)
    with tempfile.TemporaryDirectory(prefix=".tfi-prepare-", dir=destination.parent) as tmp:
        staging = Path(tmp) / "platform"
        staging.mkdir()
        _copy(bundle, staging, entries, meter)
        if destination.exists():
            destination.rmdir()
        staging.rename(destination)


def replace_code(bundle, platform, *, progress=lambda message: None):
    """Code update: stop this platform's instances, then swap in the new code.

    Nothing is snapshotted. The receipt is written last, so an interrupted
    replacement is simply repeated by the next start; the instance is untouched.
    Services restart through the installer's update-code on the next start.
    """
    bundle, platform = Path(bundle).resolve(), Path(platform).absolute()
    if platform != platform.resolve() or not platform.is_dir():
        raise ValueError("更新目录必须是原有的实际目录，不能是符号链接。")
    old, new = read_receipt(platform), read_receipt(bundle)
    if any(r.get("schema") != "tfi.macos-package.v1" for r in (old, new)):
        raise ValueError("安装记录不完整，请使用新的平台目录。")
    for key in ("target", "python", "bundled_native_dependencies"):
        if not old.get(key) or old[key] != new.get(key):
            raise ValueError("此版本的芯片或内置运行环境不同，不能在原目录更新程序；请使用新的平台目录。")
    if new.get("source_dirty") is not False or new.get("prepared_engineering_validation") != "passed":
        raise ValueError("新安装包缺少构建验证记录。")
    progress("正在停止 Portal 以更新程序")
    stop_all(platform, python=platform / "dependencies/python/bin/python3")
    from scripts.tfi_install_progress import FileProgress
    for name in CODE_ENTRIES:
        source, target = bundle / name, platform / name
        staging, retired = target.with_name("." + target.name + ".new"), target.with_name("." + target.name + ".old")
        for path in (staging, retired):
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.exists():
                shutil.rmtree(path)
        if not source.exists():
            continue
        entries = _collect(source)
        staging.mkdir()
        _copy(source, staging, entries, FileProgress("正在复制新版程序", len(entries[1]) + len(entries[2]),
                                                     sum(size for _, size in entries[1]), progress))
        if target.exists():
            target.rename(retired)
        staging.rename(target)
        if retired.exists():
            shutil.rmtree(retired)
    (platform / RECEIPT).write_text(json.dumps(new, indent=2) + "\n", encoding="utf-8")


class MacStartup(DefaultStartup):
    name = "desktop"
    engineering_parent = ".build/m"

    def __init__(self, platform, progress=lambda message: None, *, included=True, bundle=None,
                 update=False, instance_root=None, instance=None):
        super().__init__(platform, progress, instance=instance)
        self.include = included
        self.bundle = bundle
        self.update = update
        self.instance_root = instance_root
        self.log_path = self.platform / ".build/macos/logs/desktop.log"
        if bundle is not None:
            # A failed copy must not create log files in the still-empty target:
            # materialize deliberately refuses to overwrite foreign contents.
            self.log_path = self.platform.parent / ("." + self.platform.name + "-launcher") / "startup.log"

    def run(self):
        self.validate_selection()
        if self.bundle is not None:
            if changed(self.bundle, self.platform):
                if not self.update:
                    raise ValueError("此目录已有旧版本。请在欢迎页选择“更新程序”；命令行启动请加 --update。")
                replace_code(self.bundle, self.platform, progress=self.progress)
            else:
                self.progress("正在准备本地运行环境，首次复制可能需要一些时间")
                materialize(self.bundle, self.platform, progress=self.progress)
            self.log_path = self.platform / ".build/macos/logs/desktop.log"
        return super().run()

    def command(self, *arguments):
        from scripts.tfi_windows_launcher import command
        return command(self.platform, *arguments, python=self.platform / "dependencies/python/bin/python3")

    def included(self):
        options = self.platform / ".build/macos/options.json"
        if options.exists():
            return json.loads(options.read_text())["include_examples"]
        from management_common.configuration_files import atomic_json
        atomic_json(options, {"include_examples": self.include})  # Never a partial choice.
        return self.include

    def offline(self):
        return True  # The app bundles Python packages and native programs.

    def engineering(self):
        # Case archives stay in the read-only app; a copied platform has none.
        from scripts.tfi_windows_launcher import selected_engineering, unpack_engineering
        included = self.included()
        archive = selected_engineering(Path(self.bundle or self.platform), included)
        if not archive.is_file():
            raise ValueError("缺少安装工程压缩包，请从下载的 macOS 应用重新打开。")
        return unpack_engineering(self.platform, included=included, progress=self.progress,
                                  archive=archive, parent=self.platform / self.engineering_parent)

    def create_arguments(self):
        return ["--root", self.instance_root] if self.instance_root else []


def stop(platform, *, unregister=False):
    """Stop this platform's instances; unregistering also removes their launch agents."""
    stop_all(platform, unregister=unregister, python=Path(platform) / "dependencies/python/bin/python3")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--platform", type=Path, default=default_platform())
    parser.add_argument("--instance", help="Open an existing instance registered to this program")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--without-examples", action="store_true")
    parser.add_argument("--update", action="store_true", help="Replace the platform's code with this app's, keeping the instance")
    parser.add_argument("--instance-root", type=Path, help="Create the desktop instance here instead of the default location")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--unregister", action="store_true", help="Stop services and remove launch agents; keep data")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("This launcher requires macOS")
    # The lock is outside the payload directory, including first-copy time.
    lock_root = args.platform.parent / ("." + args.platform.name + "-launcher")
    if args.stop or args.unregister:
        with launcher_lock(lock_root, system="macos"):
            if (args.platform / RECEIPT).is_file():
                stop(args.platform, unregister=args.unregister)
        return
    with launcher_lock(lock_root, system="macos"):
        if args.headless:
            flow = MacStartup(args.platform, print, included=not args.without_examples,
                              bundle=args.bundle, update=args.update, instance_root=args.instance_root, instance=args.instance)
            flow.run()  # Never print the first-user invitation secret.
            print(json.dumps({"status": "ready", "instance": flow.name}))
            return
        import tkinter as tk
        from tkinter import ttk, messagebox
        root = tk.Tk()
        root.title("The Fourth Institute · macOS 试用版")
        frame = ttk.Frame(root, padding=30)
        frame.pack(fill="both", expand=True)
        welcome_logo(root, frame, args.bundle)
        ttk.Label(frame, text="欢迎安装 The Fourth Institute", font=("Helvetica", 24, "bold")).pack(pady=10)
        ttk.Label(frame, text="与 AI 一起探索、协作、创造知识。", wraplength=450).pack(pady=10)
        ttk.Label(frame, text="macOS 15+ 试用版，基础安装无需联网。", wraplength=450).pack(pady=10)
        included = tk.BooleanVar(value=not args.without_examples)
        updating = changed(args.bundle, args.platform)
        choice = ttk.Checkbutton(frame, text="安装 Lenia 预置案例", variable=included)
        choice.pack(pady=15)
        if (args.platform / ".build/macos/options.json").exists():
            choice.pack_forget()
        if updating:
            choice.pack_forget()
            ttk.Label(frame, text="发现已有版本。更新只替换程序，账号和资料保留在原实例中。",
                      wraplength=450).pack(pady=10)

        def begin():
            try:
                # Launcher runs flow.run in its worker thread; never copy a
                # multi-hundred-MB payload on Tk's event loop.
                Launcher(root, flow=MacStartup(args.platform, included=included.get(),
                                              bundle=args.bundle, update=updating or args.update,
                                              instance_root=args.instance_root, instance=args.instance))
                frame.destroy()
            except Exception as exc:
                messagebox.showerror("启动未完成", str(exc), parent=root)
        ttk.Button(frame, text="更新程序" if updating else "打开研究空间", command=begin).pack(pady=10)
        root.mainloop()


if __name__ == "__main__":
    main()
