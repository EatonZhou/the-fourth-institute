"""Windows desktop entry point for the shared platform's instance lifecycle."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import re
import socket
import subprocess
import sys
import threading
import webbrowser
import zipfile

PLATFORM = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLATFORM / "code"))


def instance_name(value):
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,31}", value):
        raise ValueError("实例名称使用 3–32 位小写字母、数字、下划线，以字母开头。")
    return value


def choose_ports(existing=()):
    reserved = set(existing)
    result = {}
    for key, first in (("web", 28199), ("postgres", 56432), ("nats", 14223)):
        for port in range(first, first + 1000):
            if port in reserved:
                continue
            with socket.socket() as probe:
                try:
                    probe.bind(("0.0.0.0", port))
                except OSError:
                    continue
            result[key] = port
            reserved.add(port)
            break
        else:
            raise RuntimeError("没有找到空闲端口：" + key)
    return result


def make_target(platform, name, manifest, *, lan=False, ports=None):
    name = instance_name(name)
    root = platform / "instance" / name
    token = __import__("hashlib").sha256(str(root).casefold().encode()).hexdigest()[:12]
    return {
        "install_root": str(root), "shared_code_root": str(platform / "code"),
        "run_id": manifest["run_id"], "supervisor_resident_id": manifest.get("supervisor_resident_id", ""),
        "service_prefix": "tfi-" + token, "database_name": "tfi_" + token,
        "python": str(platform / "dependencies/python/python.exe"),
        "windows_service_mode": "user", "bind": "0.0.0.0" if lan else "127.0.0.1",
        "ports": ports or choose_ports(),
        "data_roots": {key: str(root / "data" / folder) for key, folder in (
            ("share_root", "share"), ("staging_root", "staging"),
            ("instances_root", "instances"), ("accounts_root", "accounts"), ("secrets_dir", "secrets"))},
    }


def instances(platform):
    base = platform / "instance"
    return sorted(path.name for path in base.iterdir()
                  if path.is_dir() and not path.is_symlink() and (path / "installation.json").is_file()) if base.exists() else []


def command(platform, *arguments):
    return [str(platform / "dependencies/python/python.exe"),
            str(platform / "code/scripts/tfi_release_install.py"), *map(str, arguments)]


def stop_all(platform, *, unregister=False):
    for name in instances(platform):
        root = platform / "instance" / name
        subprocess.run(command(platform, "stop", "--root", root), check=True)
        if unregister:
            from scripts import tfi_release_platform_windows as adapter
            from scripts.tfi_release_install import COMPONENTS
            target = json.loads((root / "installation.json").read_text(encoding="utf-8"))["target"]
            for component in COMPONENTS:
                service = adapter.unit_name(target, component)
                marker = adapter.unit_path(adapter.unit_directory(), service)
                if marker.exists() and marker.read_text(encoding="utf-8") != adapter.unit_text(target, component, COMPONENTS):
                    raise ValueError("Service ownership changed: " + service)
            adapter.remove_services(adapter.unit_directory(), [adapter.unit_name(target, c) for c in COMPONENTS])


def long_paths_enabled():
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
        try:
            return winreg.QueryValueEx(key, "LongPathsEnabled")[0] == 1
        except FileNotFoundError:
            return False


def unpack_seed(platform):
    """Only extract the public seed; never overwrite an initialized instance."""
    import hashlib
    archive = platform / "example-seed.zip"
    receipt = json.loads((platform / "windows-package.json").read_text(encoding="utf-8"))
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != receipt["seed_sha256"]:
            raise ValueError("示例资料压缩包校验失败，请重新下载安装包。")
    destination = platform / "instance/example_instance/seed"
    marker = destination / "manifest.json"
    if marker.exists():
        return
    with zipfile.ZipFile(archive) as zipped:
        # Write the completion marker last, so interrupted extraction is resumable.
        for entry in sorted(zipped.infolist(), key=lambda e: e.filename == "manifest.json"):
            relative = Path(entry.filename)
            if relative.is_absolute() or ".." in relative.parts or "\\" in entry.filename or ":" in entry.filename:
                raise ValueError("Invalid seed archive entry")
            target = destination / relative
            if target.is_symlink() or any(p.is_symlink() for p in target.parents):
                raise ValueError("Redirected seed destination")
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(entry) as source, target.open("wb") as output:
                    __import__("shutil").copyfileobj(source, output)


def invitation_url(text):
    links = re.findall(r"https?://[^\s]+/join#invite=[^\s]+", text)
    if len(links) != 1:
        raise ValueError("初始化邀请格式不正确，请查看实例 secrets 目录中的邀请文件。")
    return links[0]


class Launcher:
    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk, scrolledtext
        self.root = root
        self.events = queue.Queue()
        self.busy = False
        root.title("The Fourth Institute — 实例管理")
        root.geometry("820x580")
        self.name = tk.StringVar(value="example_instance")
        self.username = tk.StringVar(value="admin")
        self.lan = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="安装平台后，在这里创建或启动实例。首次初始化需要联网，可能耗时较长。")
        frame = ttk.Frame(root, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="The Fourth Institute", font=("", 20, "bold")).pack(anchor="w")
        ttk.Label(frame, text="共享平台 · 独立实例 / Shared platform · Separate instances").pack(anchor="w", pady=(0, 14))
        row = ttk.Frame(frame)
        row.pack(fill="x")
        ttk.Label(row, text="实例 / Instance").pack(side="left")
        self.picker = ttk.Combobox(row, textvariable=self.name, values=instances(PLATFORM), width=32)
        self.picker.pack(side="left", padx=8)
        ttk.Checkbutton(row, text="新实例允许局域网访问", variable=self.lan).pack(side="left")
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=12)
        self.buttons = []
        for title, action in (("创建 / 继续初始化", self.install), ("启动", lambda: self.lifecycle("start")),
                              ("停止", lambda: self.lifecycle("stop")), ("状态", lambda: self.lifecycle("status")),
                              ("打开 Portal", self.open_portal), ("打开日志", self.open_logs)):
            button = ttk.Button(actions, text=title, command=lambda f=action: self.run(f))
            button.pack(side="left", padx=(0, 6))
            self.buttons.append(button)
        row = ttk.Frame(frame)
        row.pack(fill="x")
        ttk.Label(row, text="首次管理员名称").pack(side="left")
        ttk.Entry(row, textvariable=self.username, width=20).pack(side="left", padx=8)
        button = ttk.Button(row, text="注册首个管理员", command=lambda: self.run(self.first_admin))
        button.pack(side="left")
        self.buttons.append(button)
        ttk.Label(frame, textvariable=self.status, wraplength=780).pack(anchor="w", pady=12)
        self.log = scrolledtext.ScrolledText(frame, height=18, state="disabled")
        self.log.pack(fill="both", expand=True)
        ttk.Label(frame, text="配置 AI 账号请在 Portal 页面完成。关闭此窗口不会停止已启动的实例。").pack(anchor="w", pady=8)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.poll)

    def close(self):
        from tkinter import messagebox
        if self.busy:
            messagebox.showinfo("任务进行中", "请等待当前操作完成；不要在数据库初始化期间关闭程序。")
        else:
            self.root.destroy()

    def run(self, action):
        if self.busy:
            return
        try:
            self.selected = instance_name(self.name.get().strip())
            self.selected_username = self.username.get().strip()
            self.selected_lan = self.lan.get()
        except ValueError as exc:
            from tkinter import messagebox
            messagebox.showerror("输入有误", str(exc))
            return
        self.busy = True
        for button in self.buttons:
            button.configure(state="disabled")
        self.status.set("正在处理。初始化会自动下载依赖；详细进度见日志。")
        def work():
            try:
                action()
            except Exception as exc:
                self.events.put(("error", str(exc)))
            finally:
                self.events.put(("done", ""))
        threading.Thread(target=work, daemon=False).start()

    def poll(self):
        from tkinter import messagebox
        try:
            while True:
                kind, text = self.events.get_nowait()
                if kind == "done":
                    self.busy = False
                    self.picker.configure(values=instances(PLATFORM))
                    for button in self.buttons:
                        button.configure(state="normal")
                    self.status.set("操作已结束。若发生错误，查看下方提示和实例日志后重试。")
                elif kind == "error":
                    messagebox.showerror("操作未完成", text)
                    self.append(text + "\n")
                else:
                    self.append(text)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def append(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def execute(self, argv):
        directory = PLATFORM / ".build/windows/logs"
        directory.mkdir(parents=True, exist_ok=True)
        log = directory / (self.selected + ".log")
        env = os.environ.copy()
        env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
        # Source-instance environment must never select the desktop instance.
        for key in list(env):
            if key.startswith("PORTAL_") or key.startswith("TFI_WINDOWS_SERVICE_"):
                env.pop(key)
        self.events.put(("log", "日志：" + str(log) + "\n"))
        with log.open("a", encoding="utf-8") as stream:
            process = subprocess.Popen(argv, cwd=PLATFORM, env=env, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, encoding="utf-8", errors="replace",
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            for line in process.stdout:
                stream.write(line)
                stream.flush()
                self.events.put(("log", line))
            if process.wait():
                raise RuntimeError(f"操作失败，请查看：{log}\n依赖安装日志位于实例 runtime/dependency-install.log。")

    def root_path(self):
        root = PLATFORM / "instance" / self.selected
        if root.is_symlink():
            raise ValueError("实例目录不能是符号链接")
        return root

    def install(self):
        bundle = PLATFORM / ".build/example_instance-windows/engineering/manifest.json"
        target_path = PLATFORM / ".build/windows/targets" / (self.selected + ".json")
        state_path = self.root_path() / "installation.json"
        if not target_path.exists():
            if state_path.exists():
                raise ValueError("实例已有安装记录，请使用启动或状态按钮。")
            if not bundle.is_file():
                if (PLATFORM / "example-seed.zip").is_file():
                    unpack_seed(PLATFORM)
                self.execute([str(PLATFORM / "dependencies/python/python.exe"),
                              str(PLATFORM / "prepare_install.py"), "--platform", "windows"])
            manifest = json.loads(bundle.read_text(encoding="utf-8"))
            ports = []
            for name in instances(PLATFORM):
                state = json.loads((PLATFORM / "instance" / name / "installation.json").read_text(encoding="utf-8"))
                ports.extend(state["target"]["ports"].values())
            target = make_target(PLATFORM, self.selected, manifest, lan=self.selected_lan,
                                 ports=choose_ports(ports))
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(json.dumps(target, ensure_ascii=False, indent=2), encoding="utf-8")
        self.execute(command(PLATFORM, "install", "--manifest", bundle, "--target", target_path))
        self.open_portal()

    def lifecycle(self, action):
        self.execute(command(PLATFORM, action, "--root", self.root_path()))
        if action == "start":
            self.open_portal()

    def open_portal(self):
        state = json.loads((self.root_path() / "installation.json").read_text(encoding="utf-8"))
        webbrowser.open(f"http://127.0.0.1:{state['target']['ports']['web']}/")

    def open_logs(self):
        directory = self.root_path() / "runtime"
        if not directory.exists():
            directory = PLATFORM / ".build/windows/logs"
        directory.mkdir(parents=True, exist_ok=True)
        os.startfile(directory)

    def first_admin(self):
        if not self.selected_username:
            raise ValueError("请输入管理员名称")
        self.execute(command(PLATFORM, "first-admin", "--root", self.root_path(), "--username", self.selected_username))
        state = json.loads((self.root_path() / "installation.json").read_text(encoding="utf-8"))
        path = Path(state["target"]["data_roots"]["secrets_dir"]) / "first-admin-link.txt"
        # Keep the invitation token out of logs and command lines.
        webbrowser.open(invitation_url(path.read_text(encoding="utf-8")))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stop-all", action="store_true")
    parser.add_argument("--uninstall-platform", action="store_true")
    parser.add_argument("--enable-long-paths", action="store_true")
    args = parser.parse_args()
    if os.name != "nt":
        raise SystemExit("This desktop launcher requires Windows.")
    if args.enable_long_paths:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem",
                           0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "LongPathsEnabled", 0, winreg.REG_DWORD, 1)
        return
    if args.stop_all or args.uninstall_platform:
        stop_all(PLATFORM, unregister=args.uninstall_platform)
        return
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    if not long_paths_enabled():
        root.withdraw()
        if messagebox.askyesno("启用 Windows 长路径",
                              "示例资源包含长文件路径，需要启用 Windows 长路径支持。\n"
                              "是否允许系统显示管理员确认窗口？完成后请重新打开启动器。\n"
                              "这不会修改系统 PATH 或安装系统服务。"):
            import ctypes
            arguments = subprocess.list2cmdline([str(Path(__file__)), "--enable-long-paths"])
            api = ctypes.WinDLL("shell32", use_last_error=True)
            api.ShellExecuteW.argtypes = [ctypes.c_void_p, *([ctypes.c_wchar_p] * 4), ctypes.c_int]
            api.ShellExecuteW.restype = ctypes.c_void_p
            result = api.ShellExecuteW(None, "runas", sys.executable, arguments, str(PLATFORM), 1)
            if not result or result <= 32:
                messagebox.showerror("未启用", "未获得修改权限。请让管理员启用 LongPathsEnabled 后再试。")
        root.destroy()
        return
    Launcher(root)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        failure = PLATFORM / ".build/windows/launcher-error.log"
        failure.parent.mkdir(parents=True, exist_ok=True)
        failure.write_text(traceback.format_exc(), encoding="utf-8")
        if not {"--stop-all", "--uninstall-platform"} & set(sys.argv):
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, "启动器未能完成操作。错误日志：\n" + str(failure),
                                             "The Fourth Institute", 0x10)
        raise SystemExit(1)
