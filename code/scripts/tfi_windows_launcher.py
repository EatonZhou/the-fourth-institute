"""Windows desktop entry point for the shared platform's instance lifecycle."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import queue
import re
import socket
import subprocess
import sys
import tempfile
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


def enable_long_paths_elevated():
    """Wait for the narrow privileged helper; the application stays unelevated."""
    import ctypes
    from ctypes import wintypes as w

    class ExecuteInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", w.DWORD), ("fMask", w.ULONG), ("hwnd", w.HWND),
            ("lpVerb", w.LPCWSTR), ("lpFile", w.LPCWSTR),
            ("lpParameters", w.LPCWSTR), ("lpDirectory", w.LPCWSTR),
            ("nShow", ctypes.c_int), ("hInstApp", w.HINSTANCE),
            ("lpIDList", ctypes.c_void_p), ("lpClass", w.LPCWSTR),
            ("hkeyClass", w.HKEY), ("dwHotKey", w.DWORD),
            ("hIcon", w.HANDLE), ("hProcess", w.HANDLE),
        ]

    shell = ctypes.WinDLL("shell32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    ole = ctypes.WinDLL("ole32", use_last_error=True)
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(ExecuteInfo)]
    shell.ShellExecuteExW.restype = w.BOOL
    kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    kernel.WaitForSingleObject.restype = w.DWORD
    kernel.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
    kernel.GetExitCodeProcess.restype = w.BOOL
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, w.DWORD]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoUninitialize.argtypes = []
    initialized = ole.CoInitializeEx(None, 0x6)  # STA, disable OLE1 DDE
    if initialized < 0:
        raise RuntimeError("无法准备 Windows 授权窗口")
    info = ExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x40 | 0x100 | 0x400  # keep process handle, no async, own error UI
    info.lpVerb = "runas"
    info.lpFile = sys.executable
    info.lpParameters = subprocess.list2cmdline([str(Path(__file__)), "--enable-long-paths"])
    info.lpDirectory = str(PLATFORM)
    info.nShow = 0
    try:
        if not shell.ShellExecuteExW(ctypes.byref(info)):
            error = ctypes.get_last_error()
            if error == 1223:
                raise RuntimeError("已取消 Windows 管理员授权，长路径未启用，Portal 尚未启动。")
            raise ctypes.WinError(error)
        if not info.hProcess:
            raise RuntimeError("未取得设置程序的运行状态，请重新打开启动器检查。")
        if kernel.WaitForSingleObject(info.hProcess, 30000) != 0:
            raise RuntimeError("设置程序未在预期时间内完成，请查看 launcher-error.log 后重试。")
        status = w.DWORD()
        if not kernel.GetExitCodeProcess(info.hProcess, ctypes.byref(status)):
            raise ctypes.WinError(ctypes.get_last_error())
        if status.value or not long_paths_enabled():
            raise RuntimeError("长路径设置未生效。请查看 .build/windows/launcher-error.log，或联系管理员检查系统策略。")
    finally:
        if info.hProcess:
            kernel.CloseHandle(info.hProcess)
        ole.CoUninitialize()


def include_examples(platform):
    import configparser
    options = configparser.ConfigParser()
    options.read(platform / 'installation-options.ini', encoding='utf-8-sig')
    return options.getboolean('installation', 'include_examples', fallback=True)


def selected_seed(platform, included):
    return platform / ('example-seed.zip' if included else 'base-seed.zip')


def seed_destination_paths(platform, spec):
    for entry in spec['inventory']:
        relative = entry['path']
        for prefix in ('instance/example_instance/seed', '.build/w/engineering.pending'):
            yield platform / prefix / relative
        for key, folder in spec['engineering']['files'].items():
            if relative.startswith(folder + '/'):
                installed = {'share_root': 'share', 'staging_root': 'staging',
                             'instances_root': 'instances', 'accounts_root': 'accounts',
                             'secrets_dir': 'secrets'}[key]
                yield platform / 'instance/example_instance/data' / installed / relative[len(folder) + 1:]


def required_long_path(platform):
    """Check the selected data and transient copies, in Windows UTF-16 units."""
    if (platform / 'instance/example_instance/installation.json').exists():
        return None  # Already prepared; a service retry extracts no new seed.
    archive = selected_seed(platform, include_examples(platform))
    candidates = []
    if archive.is_file():
        with zipfile.ZipFile(archive) as zipped:
            spec = json.loads(zipped.read('manifest.json'))
            candidates.extend(seed_destination_paths(platform, spec))
    candidates.extend(platform / '.build/w/engineering.pending/code' / p.relative_to(platform / 'code')
                      for p in (platform / 'code').rglob('*') if p.is_file())
    def units(path):
        return len(str(path).encode('utf-16-le')) // 2
    return next((str(p) for p in sorted(candidates, key=units, reverse=True)
                 if units(p) >= 260 or units(p.parent) >= 248), None)


def unpack_seed(platform, *, included=True):
    """Only extract the public seed; never overwrite an initialized instance."""
    import hashlib
    archive = selected_seed(platform, included)
    receipt = json.loads((platform / "windows-package.json").read_text(encoding="utf-8"))
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != receipt["seed_sha256" if included else "base_seed_sha256"]:
            raise ValueError("示例资料压缩包校验失败，请重新下载安装包。")
    destination = platform / "instance/example_instance/seed"
    marker = destination / "manifest.json"
    with zipfile.ZipFile(archive) as zipped:
        if marker.is_symlink():
            raise ValueError("Redirected seed destination")
        # A crash while writing the old completion marker must not prevent a
        # retry. Data integrity remains checked by prepare_install afterwards.
        if marker.is_file() and marker.read_bytes() == zipped.read("manifest.json"):
            return
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
                descriptor, temporary = tempfile.mkstemp(prefix=".seed-", dir=target.parent)
                try:
                    with os.fdopen(descriptor, "wb") as output, zipped.open(entry) as source:
                        __import__("shutil").copyfileobj(source, output)
                    os.replace(temporary, target)
                finally:
                    Path(temporary).unlink(missing_ok=True)


def invitation_url(text):
    links = re.findall(r"https?://[^\s]+/join#invite=[^\s]+", text)
    if len(links) != 1:
        raise ValueError("初始化邀请格式不正确，请查看实例 secrets 目录中的邀请文件。")
    return links[0]


@contextmanager
def launcher_lock(platform):
    """One desktop owner; uninstall must wait until that owner closes."""
    from management_common.file_lock import try_lock_descriptor, unlock_descriptor
    path = platform / ".build/windows/launcher.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if not try_lock_descriptor(stream.fileno()):
            raise RuntimeError("Portal 启动窗口已经打开，请等待准备完成。卸载前请先关闭启动窗口。")
        try:
            yield
        finally:
            unlock_descriptor(stream.fileno())


class DefaultStartup:
    """One desktop action: prepare the default instance, then return its URL."""
    def __init__(self, platform, progress=lambda message: None):
        self.platform = Path(platform)
        self.root = self.platform / "instance/example_instance"
        self.progress = progress
        self.log_path = self.platform / ".build/windows/logs/example_instance.log"

    def state(self):
        path = self.root / "installation.json"
        if not path.is_file():
            raise RuntimeError("默认实例尚未完成初始化，请重试准备过程。")
        return json.loads(path.read_text(encoding="utf-8"))

    def execute(self, argv):
        from management_common.installation_progress import progress_message
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
        for key in list(env):
            if key.startswith("PORTAL_") or key.startswith("TFI_WINDOWS_SERVICE_"):
                env.pop(key)
        output = []
        with self.log_path.open("a", encoding="utf-8") as stream:
            process = subprocess.Popen(argv, cwd=self.platform, env=env, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, encoding="utf-8", errors="replace",
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            for line in process.stdout:
                stream.write(line)
                stream.flush()
                output.append(line)
                message = progress_message(line)
                if message is not None:
                    self.progress(message)
                # Diagnostic output is on disk; keep a bounded tail in memory.
                if len(output) > 200:
                    del output[:100]
            if process.wait():
                detail = "请打开日志查看完整原因。"
                for line in reversed(output):
                    try:
                        record = json.loads(line)
                        if isinstance(record, dict) and record.get("status") == "failed" and isinstance(record.get("error"), str):
                            detail = record["error"]
                            break
                    except ValueError:
                        continue
                raise RuntimeError(f"准备未完成。\n{detail}\n\n可打开日志查看原因，处理后点击重试。")
        return "".join(output)

    def initialize(self):
        bundle = self.platform / ".build/w/engineering/manifest.json"
        target_path = self.platform / ".build/windows/targets/example_instance.json"
        state_exists = (self.root / "installation.json").exists()
        if state_exists and not bundle.exists():
            bundle = self.platform / '.build/example_instance-windows/engineering/manifest.json'
        if not state_exists:
            # Before installation has begun, this is only a generated cache.
            # Rebuild it on retry so an invalid earlier manifest is not reused.
            included = include_examples(self.platform)
            self.progress("正在准备 Lenia 预置案例" if included else "正在准备基础平台（不含 Lenia 案例）")
            if selected_seed(self.platform, included).is_file():
                unpack_seed(self.platform, included=included)
            self.execute([str(self.platform / "dependencies/python/python.exe"),
                          str(self.platform / "prepare_install.py"), "--platform", "windows",
                          *([] if included else ["--without-examples"])])
        if not target_path.exists():
            if state_exists:
                raise ValueError("已有未完成的初始化记录，但缺少原安装参数。请查看日志，勿覆盖实例资料。")
            manifest = json.loads(bundle.read_text(encoding="utf-8"))
            ports = []
            for name in instances(self.platform):
                state = json.loads((self.platform / "instance" / name / "installation.json").read_text(encoding="utf-8"))
                ports.extend(state["target"]["ports"].values())
            target = make_target(self.platform, "example_instance", manifest, ports=choose_ports(ports))
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(json.dumps(target, ensure_ascii=False, indent=2), encoding="utf-8")
        self.progress("正在安装环境并初始化 Portal\n首次使用需要联网，可能需要几分钟")
        self.execute(command(self.platform, "install", "--manifest", bundle, "--target", target_path))

    def wait_ready(self, state, timeout=120):
        from scripts.tfi_release_install import wait_for_ready, health_failure
        result = wait_for_ready(state, timeout=timeout, progress=self.progress)
        if result['status'] != 'installed':
            raise RuntimeError(health_failure(state, result))
        return result['url']

    def run(self):
        from scripts.tfi_release_install import start_blocker
        if self.root.is_symlink():
            raise ValueError("实例目录不能是符号链接")
        initialized = not (self.root / "installation.json").is_file() or bool(start_blocker(self.state()))
        if initialized:
            self.initialize()
        state = self.state()
        if start_blocker(state):
            raise RuntimeError("初始化没有完成，尚不能启动 Portal。请查看日志后重试。")
        if initialized:
            # install already started and checked every component in this run.
            # Never reuse a persisted health result from an earlier launch.
            url = f"http://127.0.0.1:{state['target']['ports']['web']}"
        else:
            self.progress("正在启动 Portal")
            self.execute(command(self.platform, "start", "--root", self.root))
            url = self.wait_ready(state)
        self.progress("正在打开你的研究空间")
        # This idempotent setup command uses the instance's own account service.
        # Invitation secrets stay in its private file, never in log output.
        result = json.loads(self.execute(command(self.platform, "first-admin", "--root", self.root,
                                                "--username", "admin", "--if-needed")))
        if result.get("status") == "already_configured":
            return url + "/"
        if result.get("status") != "setup_link_created":
            raise RuntimeError("未能准备首次注册页面，请查看日志。")
        path = Path(state["target"]["data_roots"]["secrets_dir"]) / "first-admin-link.txt"
        return invitation_url(path.read_text(encoding="utf-8"))


def brand_window(root):
    icon = PLATFORM / "branding/tfi.ico"
    if icon.is_file():
        root.iconbitmap(str(icon))


class Launcher:
    """Transient progress only; the application UI lives in the browser."""
    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk
        self.root = root
        self.events = queue.Queue()
        self.busy = False
        self.ready_url = None
        self.flow = DefaultStartup(PLATFORM, lambda text: self.events.put(("progress", text)))
        brand_window(root)
        root.title("The Fourth Institute")
        root.minsize(540, 365)
        root.resizable(False, False)
        root.configure(bg="#f5f8f7")
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFI.Horizontal.TProgressbar", troughcolor="#dce8e4", background="#08616a",
                        bordercolor="#dce8e4", lightcolor="#08616a", darkcolor="#08616a")
        frame = tk.Frame(root, bg="#f5f8f7", padx=32, pady=24)
        frame.pack(fill="both", expand=True)
        mark = PLATFORM / "branding/mark.png"
        if mark.is_file():
            self.mark = tk.PhotoImage(file=str(mark))
            tk.Label(frame, image=self.mark, bg="#f5f8f7").pack()
        tk.Label(frame, text="The Fourth Institute", font=("Segoe UI", 21, "bold"),
                 fg="#083f48", bg="#f5f8f7").pack(pady=(8, 4))
        tk.Label(frame, text="与 AI 一起探索、协作、创造知识", font=("Microsoft YaHei UI", 10),
                 fg="#637573", bg="#f5f8f7").pack()
        self.status = tk.StringVar(value="正在准备你的研究空间")
        tk.Label(frame, textvariable=self.status, wraplength=470, justify="center",
                 font=("Microsoft YaHei UI", 10), fg="#284b4a", bg="#f5f8f7").pack(pady=(18, 12))
        self.progress = ttk.Progressbar(frame, style="TFI.Horizontal.TProgressbar", mode="indeterminate")
        self.progress.pack(fill="x")
        self.failure = tk.Frame(frame, bg="#f5f8f7")
        ttk.Button(self.failure, text="重试", command=self.begin).pack(side="left", padx=5)
        ttk.Button(self.failure, text="查看日志", command=self.open_log).pack(side="left", padx=5)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.poll)
        root.after(200, self.begin)

    def begin(self):
        if self.busy:
            return
        self.busy = True
        self.failure.pack_forget()
        self.status.set("正在准备你的研究空间")
        self.progress.start()
        def work():
            try:
                url = self.ready_url or self.flow.run()
                self.events.put(("ready", url))
            except Exception as exc:
                try:
                    self.flow.log_path.parent.mkdir(parents=True, exist_ok=True)
                    with self.flow.log_path.open("a", encoding="utf-8") as stream:
                        stream.write("\n启动未完成：" + str(exc) + "\n")
                except OSError:
                    pass
                self.events.put(("error", str(exc)))
        threading.Thread(target=work, daemon=False).start()

    def open_log(self):
        self.flow.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.flow.log_path.touch(exist_ok=True)
        os.startfile(self.flow.log_path)

    def close(self):
        if self.busy:
            from tkinter import messagebox
            messagebox.showinfo("正在准备", "首次准备仍在进行，请等待完成。", parent=self.root)
        else:
            self.root.destroy()

    def fail(self, text):
        self.busy = False
        self.progress.stop()
        # Let Tk size the window from its contents, including Windows font/DPI
        # scaling. A long error must not push recovery buttons out of view.
        detail = text if len(text) <= 220 else text[:220] + "…\n完整原因请查看日志。"
        self.status.set(detail)
        self.failure.pack(pady=16)

    def poll(self):
        try:
            while True:
                kind, text = self.events.get_nowait()
                if kind == "progress":
                    self.status.set(text)
                elif kind == "error":
                    self.fail(text)
                elif kind == "ready":
                    self.busy = False
                    self.ready_url = text
                    try:
                        if not webbrowser.open(text):
                            self.fail("未能打开默认浏览器。请设置默认浏览器后重试。")
                        else:
                            self.root.destroy()
                            return
                    except OSError:
                        self.fail("未能打开默认浏览器。请设置默认浏览器后重试。")
        except queue.Empty:
            pass
        self.root.after(100, self.poll)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stop-all", action="store_true")
    parser.add_argument("--uninstall-platform", action="store_true")
    parser.add_argument("--remove-instances", action="store_true")
    parser.add_argument("--enable-long-paths", action="store_true")
    args = parser.parse_args()
    if args.remove_instances and not args.uninstall_platform:
        parser.error("--remove-instances requires --uninstall-platform")
    if os.name != "nt":
        raise SystemExit("This desktop launcher requires Windows.")
    if args.enable_long_paths:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem",
                           0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "LongPathsEnabled", 0, winreg.REG_DWORD, 1)
        return
    if args.uninstall_platform:
        with launcher_lock(PLATFORM):
            if args.remove_instances:
                from scripts.tfi_windows_uninstall import remove_instances
                remove_instances(PLATFORM)
            else:
                stop_all(PLATFORM, unregister=True)
        return
    if args.stop_all:
        stop_all(PLATFORM)
        return
    with launcher_lock(PLATFORM):
        restart = launch_desktop()
    if restart:
        # Windows caches long-path support per process. Start a fresh ordinary
        # user process, only after releasing this launcher's exclusive lock.
        subprocess.Popen([sys.executable, str(Path(__file__))], cwd=PLATFORM,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True,
                         creationflags=subprocess.CREATE_NO_WINDOW)


def launch_desktop():
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    brand_window(root)
    long_path = required_long_path(PLATFORM) if not long_paths_enabled() else None
    if long_path:
        root.withdraw()
        if not messagebox.askyesno("启用 Windows 长路径",
                                  "所选安装内容在当前目录下超过 Windows 默认路径长度。\n"
                                  "可以取消并选择更短的安装目录，或启用 Windows 长路径支持。\n"
                                  "是否允许系统显示管理员确认窗口？成功后会自动继续准备 Portal。\n"
                                  "这不会修改系统 PATH 或安装系统服务。", parent=root):
            messagebox.showinfo("尚未初始化", "已取消。Portal 尚未启动；稍后可重新打开桌面启动器。", parent=root)
            root.destroy()
            return False
        return wait_for_long_path_setup(root)
    Launcher(root)
    root.mainloop()
    return False


def wait_for_long_path_setup(root):
    """Keep visible feedback while the Windows authorization/helper completes."""
    from tkinter import ttk, messagebox
    brand_window(root)
    root.title("The Fourth Institute — 正在准备")
    root.geometry("490x150")
    ttk.Label(root, padding=16, wraplength=450,
              text="正在等待 Windows 管理员授权并启用长路径。\n"
                   "成功后自动继续准备 Portal，完成后打开浏览器。").pack()
    progress = ttk.Progressbar(root, mode="indeterminate")
    progress.pack(fill="x", padx=16)
    progress.start()
    root.protocol("WM_DELETE_WINDOW", lambda: messagebox.showinfo(
        "正在等待授权", "请在 Windows 授权窗口确认或取消，完成后会显示结果。", parent=root))
    root.deiconify()
    results = queue.Queue()
    restart = False

    def work():
        try:
            enable_long_paths_elevated()
        except Exception as exc:
            results.put(str(exc))
        else:
            results.put(None)

    def poll():
        nonlocal restart
        try:
            error = results.get_nowait()
        except queue.Empty:
            root.after(100, poll)
            return
        if error is not None:
            messagebox.showerror("未完成设置", error, parent=root)
        else:
            restart = True
        root.destroy()

    threading.Thread(target=work, daemon=False).start()
    root.after(100, poll)
    root.mainloop()
    return restart


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
