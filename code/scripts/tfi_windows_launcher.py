"""Windows desktop entry point for the shared platform's instance lifecycle."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import webbrowser
import zipfile

PLATFORM = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLATFORM / "code"))


def is_elevated_process():
    if sys.platform != 'win32':
        return False
    import ctypes
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


INSTANCE = "example_instance"


def python_executable(platform):
    return platform / "dependencies/python/python.exe"


def command(platform, *arguments, python=None):
    """The shared installer of this platform's code, run with its bundled Python."""
    return [str(python or python_executable(platform)),
            str(platform / "code/scripts/tfi_release_install.py"), *map(str, arguments)]


def platform_instances(platform):
    """Registered instances whose deployments run this platform's code.

    An unreadable registration fails closed: it could belong to this platform.
    """
    from management_common import host_instances
    from management_common.instance_manifest import load
    code = (Path(platform) / "code").resolve()
    result = []
    for root in host_instances.read_registry().values():
        instance = load(root)
        if any(Path(row["code_root"]) == code for row in instance.deployments.values()):
            result.append(instance)
    return sorted(result, key=lambda instance: instance.name)


def stop_all(platform, *, unregister=False, python=None):
    """Stop this platform's instances; unregistering also deletes their service
    definitions. Data, accounts and registrations stay."""
    from scripts import instance_services
    for instance in platform_instances(platform):
        subprocess.run(command(platform, "stop", "--instance", instance.name, python=python), check=True)
        if unregister:
            instance_services.remove_definitions(instance)


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


def selected_engineering(platform, included):
    return platform / ('example-engineering.zip' if included else 'base-engineering.zip')


def unpack_engineering(platform, *, included=True, progress=None, archive=None, parent=None):
    """Extract a build-verified candidate once, without scanning or hashing it."""
    from scripts.tfi_install_progress import FileProgress, copy_stream
    import shutil
    import stat
    archive = Path(archive) if archive is not None else selected_engineering(platform, included)
    parent = Path(parent) if parent is not None else platform / '.build/w'
    if not parent.absolute().is_relative_to(platform.absolute() / '.build'):
        raise ValueError('Engineering destination must stay in the platform build directory')
    if parent.absolute() != parent.resolve():
        raise ValueError('Redirected engineering destination')
    destination, temporary = parent / 'engineering', parent / 'engineering.pending'
    if any(p.is_symlink() for p in (platform, platform / '.build', parent, destination, temporary)):
        raise ValueError('Redirected engineering destination')
    with zipfile.ZipFile(archive) as zipped:
        marker = zipped.read('manifest.json')
        manifest = json.loads(marker)
        if manifest.get('include_examples') is not included:
            raise ValueError('Unexpected packaged engineering selection')
        completed = destination / 'manifest.json'
        if completed.is_symlink():
            raise ValueError('Redirected engineering marker')
        if completed.is_file() and completed.read_bytes() == marker:
            if progress:
                progress('复用已解压的安装文件')
            return destination / 'manifest.json'
        entries = zipped.infolist()
        for entry in entries:
            relative = Path(entry.filename)
            if (relative.is_absolute() or '..' in relative.parts or '\\' in entry.filename
                    or ':' in entry.filename or stat.S_ISLNK(entry.external_attr >> 16)):
                raise ValueError('Invalid engineering archive entry')
        # A changed selection or interrupted extraction starts with a clean
        # candidate; a base install cannot inherit files from the Lenia choice.
        if temporary.exists():
            shutil.rmtree(temporary)
        temporary.mkdir(parents=True)
        files = [entry for entry in entries if not entry.is_dir()]
        meter = FileProgress('正在解压案例与平台文件' if included else '正在解压基础平台文件',
                             len(files), sum(entry.file_size for entry in files), progress)
        for entry in sorted(entries, key=lambda item: item.filename == 'manifest.json'):
            target = temporary / entry.filename
            if target.is_symlink() or any(p.is_symlink() for p in target.parents):
                raise ValueError('Redirected engineering destination')
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(entry) as source, target.open('wb') as output:
                    copy_stream(source, output, meter)
                if os.name != 'nt' and entry.create_system == 3:
                    target.chmod(stat.S_IMODE(entry.external_attr >> 16) & 0o777)
        # Empty area directories are not entries in the file inventory.
        for area, relative in manifest['files'].items():
            if relative != 'data/' + area or '\\' in area or ':' in area or '/' in area or area in {'.', '..'}:
                raise ValueError('Invalid engineering area directory')
            (temporary / relative).mkdir(parents=True, exist_ok=True)
        if destination.exists():
            shutil.rmtree(destination)
        temporary.rename(destination)
    return destination / 'manifest.json'


def required_long_path(platform, *, root=None, name=INSTANCE):
    """Check the extracted bundle and the new instance's area copies, in UTF-16 units.

    The default instance root is the installer's (host_instances.default_root).
    """
    from management_common import host_instances
    if name in host_instances.read_registry():
        return None  # Already created; a retry extracts no new bundle.
    archive = selected_engineering(platform, include_examples(platform))
    if not archive.is_file():
        return None
    root = Path(root) if root is not None else host_instances.default_root(name)
    with zipfile.ZipFile(archive) as zipped:
        spec = json.loads(zipped.read('manifest.json'))
    rows = [row['path'] for row in spec['inventory']]
    candidates = [platform / '.build/w/engineering.pending' / path for path in rows]
    # data/<area>/... becomes <instance root>/data/<area>/... (areas at their default location).
    candidates += [root / path for path in rows if path.startswith('data/')]

    def units(path):
        return len(str(path).encode('utf-16-le')) // 2
    return next((str(p) for p in sorted(candidates, key=units, reverse=True)
                 if units(p) >= 260 or units(p.parent) >= 248), None)


def invitation_url(text):
    links = re.findall(r"https?://[^\s]+/join#invite=[^\s]+", text)
    if len(links) != 1:
        raise ValueError("初始化邀请格式不正确，请查看实例 secrets 目录中的邀请文件。")
    return links[0]


@contextmanager
def launcher_lock(platform, *, system="windows"):
    """One desktop owner; uninstall must wait until that owner closes."""
    from management_common.file_lock import try_lock_descriptor, unlock_descriptor
    path = platform / ".build" / system / "launcher.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if not try_lock_descriptor(stream.fileno()):
            raise RuntimeError("Portal 启动窗口已经打开，请等待准备完成。卸载前请先关闭启动窗口。")
        try:
            yield
        finally:
            unlock_descriptor(stream.fileno())


def last_result(output):
    """The installer's JSON result follows its progress lines in merged output."""
    start = 0 if output.startswith("{") else output.rfind("\n{") + 1
    if start == 0 and not output.startswith("{"):
        raise ValueError("安装器没有返回结果，请查看日志。")
    return json.loads(output[start:])


class DefaultStartup:
    """One desktop action: prepare the default instance, then return its URL.

    The platform directory holds code and dependencies only. The instance is
    created by the shared installer at its default location and found through
    the host registry; every lifecycle action is an installer command.
    """
    name = INSTANCE
    engineering_parent = ".build/w"

    def __init__(self, platform, progress=lambda message: None, *, instance=None):
        self.platform = Path(platform)
        self.selected_name = instance
        if instance is not None:
            self.name = instance
        self.progress = progress
        self.log_path = self.platform / ".build/windows/logs/example_instance.log"
        self.timings = []

    def choices(self):
        return [instance.name for instance in platform_instances(self.platform)]

    def select(self, name):
        if name not in self.choices():
            raise ValueError("所选实例不属于此程序，或已被退役。")
        self.selected_name = self.name = name

    def validate_selection(self):
        if self.selected_name is not None:
            self.select(self.selected_name)

    def timed(self, stage, action):
        started = time.monotonic()
        try:
            return action()
        finally:
            entry = {"stage": stage, "seconds": round(time.monotonic() - started, 3)}
            self.timings.append(entry)
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write("[startup] " + json.dumps(entry) + "\n")

    def registered(self):
        from management_common import host_instances
        return self.name in host_instances.read_registry()

    def instance(self):
        from management_common import host_instances
        if not self.registered():
            raise RuntimeError("默认实例尚未完成初始化，请重试准备过程。")
        return host_instances.find(self.name)

    def running_url(self, instance):
        from scripts.tfi_release_install import observed_status
        # Never trust a saved health result, a listening port alone, or another
        # instance's response: the health payload must name this instance.
        result = observed_status(instance, request_timeout=0.5)
        return result["url"] if result["status"] == "running" else None

    def entry_url(self, instance):
        return f"http://127.0.0.1:{instance.document['network']['entry_port']}"

    def command(self, *arguments):
        return command(self.platform, *arguments)

    def definitions_current(self, instance):
        """False after a code update: the definitions still name the old code environment."""
        from scripts.instance_services import verify_definitions
        try:
            verify_definitions(instance)
        except (ValueError, FileNotFoundError):
            return False
        return True

    def account_configured(self, url, instance):
        from scripts.tfi_release_install import _LOCAL_HTTP
        try:
            # A fresh read from UserEnrollment avoids two setup subprocesses on
            # ordinary opens. Missing/old/error responses fall back to the
            # existing setup owner; no persistent "already registered" cache.
            with _LOCAL_HTTP.open(url + "/api/auth/setup-status", timeout=2) as response:
                result = json.loads(response.read(4096))
                return (response.status == 200 and isinstance(result, dict)
                        and result.get("instance") == instance.name and result.get("configured") is True)
        except (OSError, ValueError):
            return False

    def execute(self, argv):
        from management_common.installation_progress import progress_message
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
        for key in list(env):
            if key.startswith("PORTAL_"):  # The installer selects the instance by name.
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

    def included(self):
        return include_examples(self.platform)

    def offline(self):
        receipt = self.platform / "windows-package.json"
        return receipt.is_file() and json.loads(receipt.read_text(encoding="utf-8")).get(
            "initialization_requires_network") is False

    def engineering(self):
        included = self.included()
        archive = selected_engineering(self.platform, included)
        if not archive.is_file():
            raise ValueError("缺少安装工程压缩包，请重新运行安装程序。")
        return unpack_engineering(self.platform, included=included, progress=self.progress,
                                  archive=archive, parent=self.platform / self.engineering_parent)

    def initialize(self):
        if self.registered():
            # A creation that did not complete is retired, then created afresh;
            # the installer never resumes half an instance.
            self.progress("正在清理上次未完成的初始化")
            self.execute(self.command("retire", "--instance", self.name))
        bundle = self.engineering()
        self.progress("正在安装环境并初始化 Portal")
        self.execute(self.command("create", "--engineering", bundle, "--name", self.name,
                                  "--skip-file-verification", *(["--offline"] if self.offline() else []),
                                  *self.create_arguments()))

    def create_arguments(self):
        """Further installer create options; the default root and ports are the installer's."""
        return []

    def run(self):
        from scripts.instance_services import start_blocker, creation_record
        if is_elevated_process():
            raise RuntimeError("请关闭此窗口，直接双击桌面图标启动 Portal；不要选择“以管理员身份运行”。"
                               "当前用户模式的数据库不能在提升的管理员进程中启动。")
        self.validate_selection()
        initialized = not self.registered()
        if not initialized:
            current = self.instance()
            blocker = start_blocker(current)
            initialized = creation_record(current) is not None
            if blocker and not initialized:
                raise RuntimeError(blocker)
        if initialized:
            self.timed("initialize", self.initialize)
        instance = self.instance()
        if start_blocker(instance):
            raise RuntimeError("初始化没有完成，尚不能启动 Portal。请查看日志后重试。")
        url = self.entry_url(instance)
        if initialized:
            pass  # create already started and checked every component in this run.
        elif not self.definitions_current(instance):
            # The package replaced the code: prepare its dependencies, refresh
            # the service definitions and restart (the protocol's code update).
            self.progress("正在更新 Portal 程序")
            self.timed("update_code", lambda: self.execute(self.command(
                "update-code", "--instance", self.name, *(["--offline"] if self.offline() else []))))
        else:
            self.progress("正在检查已运行的 Portal")
            if self.timed("running_check", lambda: self.running_url(instance)) is None:
                self.progress("正在启动 Portal")
                # The installer's start waits until every health check passes.
                self.timed("start_services", lambda: self.execute(self.command("start", "--instance", self.name)))
        self.progress("正在打开你的研究空间")
        if self.timed("account_check", lambda: self.account_configured(url, instance)):
            return url + "/"
        # This idempotent setup command uses the instance's own account service.
        # Invitation secrets stay in its private file, never in log output.
        result = last_result(self.timed("first_admin", lambda: self.execute(self.command(
            "first-admin", "--instance", self.name, "--username", "admin", "--if-needed"))))
        if result.get("status") == "already_configured":
            return url + "/"
        if result.get("status") != "setup_link_created":
            raise RuntimeError("未能准备首次注册页面，请查看日志。")
        from scripts.tfi_release_install import FIRST_ADMIN_FILE
        return invitation_url((instance.directory("secrets") / FIRST_ADMIN_FILE).read_text(encoding="utf-8"))


def brand_window(root):
    icon = PLATFORM / "branding/tfi.ico"
    if icon.is_file():
        root.iconbitmap(str(icon))


class Launcher:
    """Transient progress only; the application UI lives in the browser."""
    def __init__(self, root, *, flow=None):
        import tkinter as tk
        from tkinter import ttk
        self.root = root
        self.events = queue.Queue()
        self.busy = False
        self.ready_url = None
        self.flow = flow or DefaultStartup(PLATFORM)
        self.flow.progress = lambda text: self.events.put(("progress", text))
        if os.name == "nt":
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
        # On the first macOS launch the destination has not been copied yet.
        # The immutable app bundle already contains the artwork.
        assets = Path(getattr(self.flow, "bundle", None) or self.flow.platform)
        mark = assets / "branding/mark.png"
        if mark.is_file():
            self.mark = tk.PhotoImage(file=str(mark))
            if sys.platform == "darwin":
                root.iconphoto(True, self.mark)
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
        choices = self.flow.choices()
        if self.flow.selected_name is None and len(choices) > 1:
            self.status.set("选择要打开的实例")
            self.selection = ttk.Frame(frame)
            self.selection.pack(fill="x", pady=12)
            selected = tk.StringVar(value=self.flow.name if self.flow.name in choices else choices[0])
            ttk.Combobox(self.selection, textvariable=selected, values=choices,
                         state="readonly").pack(side="left", fill="x", expand=True)
            def open_selected():
                try:
                    self.flow.select(selected.get())
                except ValueError as error:
                    self.fail(str(error))
                    return
                self.selection.destroy()
                self.begin()
            ttk.Button(self.selection, text="打开实例", command=open_selected).pack(side="right", padx=8)
        else:
            if self.flow.selected_name is None and len(choices) == 1:
                self.flow.select(choices[0])
            root.after_idle(self.begin)

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
        if sys.platform == "darwin":
            subprocess.Popen(["/usr/bin/open", str(self.flow.log_path)])
        else:
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
    parser.add_argument("--instance", help="Open an existing instance registered to this program")
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
        restart = launch_desktop(instance=args.instance)
    if restart:
        # Windows caches long-path support per process. Start a fresh ordinary
        # user process, only after releasing this launcher's exclusive lock.
        subprocess.Popen([sys.executable, str(Path(__file__)), *(["--instance", args.instance] if args.instance else [])], cwd=PLATFORM,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True,
                         creationflags=subprocess.CREATE_NO_WINDOW)


def launch_desktop(*, instance=None):
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    brand_window(root)
    flow = DefaultStartup(PLATFORM, instance=instance)
    flow.validate_selection()
    long_path = required_long_path(PLATFORM, name=flow.name) if not long_paths_enabled() else None
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
    Launcher(root, flow=flow)
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
