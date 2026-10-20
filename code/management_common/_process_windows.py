"""Windows Job ownership. Imported only by the Windows process backend.

No taskkill fallback: a vanished parent PID does not prove its tree exited.
The Job handle and active-process count remain authoritative after parent exit.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as w
from functools import lru_cache
import hashlib
import os
import subprocess
import time
import weakref

CREATE_SUSPENDED = 0x4
CREATE_NEW_PROCESS_GROUP = 0x200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
DETACHED_PROCESS = 0x8
KILL_ON_JOB_CLOSE = 0x2000
BREAKAWAY_OK = 0x800


class Limits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", w.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", w.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]


class IOCounts(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", Limits), ("IoInfo", IOCounts),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class Accounting(ctypes.Structure):
    _fields_ = [(name, ctypes.c_longlong) for name in (
        "TotalUserTime", "TotalKernelTime", "ThisPeriodTotalUserTime", "ThisPeriodTotalKernelTime")] + [
        (name, w.DWORD) for name in ("TotalPageFaultCount", "TotalProcesses", "ActiveProcesses", "TotalTerminatedProcesses")]


class ThreadEntry(ctypes.Structure):
    _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ThreadID", w.DWORD),
                ("th32OwnerProcessID", w.DWORD), ("tpBasePri", w.LONG), ("tpDeltaPri", w.LONG), ("dwFlags", w.DWORD)]


@lru_cache(maxsize=1)
def _api():
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateJobObjectW": ([ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
        "OpenJobObjectW": ([w.DWORD, w.BOOL, w.LPCWSTR], w.HANDLE),
        "SetInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
        "QueryInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p], w.BOOL),
        "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
        "IsProcessInJob": ([w.HANDLE, w.HANDLE, ctypes.POINTER(w.BOOL)], w.BOOL),
        "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
        "CloseHandle": ([w.HANDLE], w.BOOL),
        "OpenProcess": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
        "GetExitCodeProcess": ([w.HANDLE, ctypes.POINTER(w.DWORD)], w.BOOL),
        "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
        "CreateToolhelp32Snapshot": ([w.DWORD, w.DWORD], w.HANDLE),
        "Thread32First": ([w.HANDLE, ctypes.POINTER(ThreadEntry)], w.BOOL),
        "Thread32Next": ([w.HANDLE, ctypes.POINTER(ThreadEntry)], w.BOOL),
        "OpenThread": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
        "ResumeThread": ([w.HANDLE], w.DWORD),
        "GenerateConsoleCtrlEvent": ([w.DWORD, w.DWORD], w.BOOL),
    }
    for name, (args, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes, function.restype = args, result
    return api


def _check(value):
    if not value:
        raise ctypes.WinError(ctypes.get_last_error())
    return value


def _name(tree_id):
    return "Local\\PortalTree-" + hashlib.sha256(str(tree_id).encode()).hexdigest() if tree_id else None


class Job:
    def __init__(self, handle):
        self.handle = handle

    @classmethod
    def create(cls, tree_id, kill_on_owner_exit):
        handle = _check(_api().CreateJobObjectW(None, _name(tree_id)))
        job = cls(handle)
        try:
            if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
                raise FileExistsError("Process tree identity is already active")
            limits = ExtendedLimits()
            # Ordinary descendants stay owned. Only explicitly detached
            # successors can leave this Job and acquire independent ownership.
            limits.BasicLimitInformation.LimitFlags = BREAKAWAY_OK | (KILL_ON_JOB_CLOSE if kill_on_owner_exit else 0)
            _check(_api().SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
            return job
        except BaseException:
            job.close()
            raise

    @classmethod
    def open(cls, tree_id):
        if not tree_id:
            raise ValueError("Windows tree termination requires a managed process or tree_id")
        handle = _api().OpenJobObjectW(0x8 | 0x4, False, _name(tree_id))  # TERMINATE | QUERY
        if not handle and ctypes.get_last_error() == 2:
            return None
        return cls(_check(handle))

    def active(self):
        info = Accounting()
        _check(_api().QueryInformationJobObject(self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None))
        return info.ActiveProcesses

    def terminate(self):
        _check(_api().TerminateJobObject(self.handle, 1))

    def close(self):
        if self.handle:
            _check(_api().CloseHandle(self.handle))
            self.handle = None


def _resume(pid):
    # Popen closes the initial thread handle. The child is still suspended,
    # so its only thread can be reopened safely from the Toolhelp snapshot.
    api = _api()
    snapshot = api.CreateToolhelp32Snapshot(0x4, 0)  # TH32CS_SNAPTHREAD
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = ThreadEntry()
        entry.dwSize = ctypes.sizeof(entry)
        present = api.Thread32First(snapshot, ctypes.byref(entry))
        while present:
            if entry.th32OwnerProcessID == pid:
                thread = _check(api.OpenThread(0x2, False, entry.th32ThreadID))
                try:
                    if api.ResumeThread(thread) == 0xffffffff:
                        raise ctypes.WinError(ctypes.get_last_error())
                finally:
                    api.CloseHandle(thread)
                return
            present = api.Thread32Next(snapshot, ctypes.byref(entry))
        raise RuntimeError("Suspended process thread was not found")
    finally:
        api.CloseHandle(snapshot)


def spawn_options():
    return {"creationflags": CREATE_NEW_PROCESS_GROUP}


def spawn_process(*args, tree_id=None, kill_on_owner_exit=True, **kwargs):
    from ._windows_command import prepare
    if kwargs.get('shell'):
        raise ValueError('Managed Windows processes require shell=False and explicit argv')
    if len(args) > 1 or (args and 'args' in kwargs) or (not args and 'args' not in kwargs):
        raise TypeError('Pass process options as keywords after the argv sequence')
    command = args[0] if args else kwargs.pop('args')
    original_command = command if isinstance(command, (str, bytes, os.PathLike)) else list(command)
    command, executable = prepare(original_command, environment=kwargs.get('env'), executable=kwargs.get('executable'))
    kwargs['executable'] = executable
    kwargs['shell'] = False
    kwargs.pop("start_new_session", None)
    if kwargs.get("pass_fds") or kwargs.get("preexec_fn") or kwargs.get("process_group") not in (None, -1):
        raise ValueError("POSIX descriptor/process-group options cannot be used on Windows")
    flags = kwargs.get("creationflags", 0) | CREATE_NEW_PROCESS_GROUP | CREATE_SUSPENDED
    if not kill_on_owner_exit:
        flags |= CREATE_BREAKAWAY_FROM_JOB
    kwargs["creationflags"] = flags
    if kwargs.get("env") is not None:
        environment = dict(kwargs["env"])
        present = {key.upper() for key in environment}
        # Windows DLL/command resolution needs these OS values even for a
        # deliberately credential-free Provider environment. Never inherit
        # arbitrary user/account variables into that environment.
        for key, value in os.environ.items():
            if key.upper() in {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP"} and key.upper() not in present:
                environment[key] = value
        kwargs["env"] = environment
    job = Job.create(tree_id, kill_on_owner_exit)
    process = None
    try:
        process = subprocess.Popen(command, **kwargs)
        # Public completion/error receipts identify the caller's argv, even
        # when the OS command line includes an explicit batch interpreter.
        process.args = original_command
        if not kill_on_owner_exit:
            # A nested host Job may stop breakaway partway up the chain.
            # Check before assigning our new Job and before the child runs.
            inherited = w.BOOL()
            _check(_api().IsProcessInJob(int(process._handle), None, ctypes.byref(inherited)))
            if inherited.value:
                raise PermissionError("Host Job prevents independent process-tree ownership")
        _check(_api().AssignProcessToJobObject(job.handle, int(process._handle)))
        process._portal_job = job
        process._portal_tree_stopped = False
        process._portal_console_group = not flags & (DETACHED_PROCESS | 0x08000000)  # CREATE_NO_WINDOW
        process._portal_job_finalizer = weakref.finalize(process, job.close)
        _resume(process.pid)
        return process
    except BaseException:
        try:
            try:
                if process is not None:
                    # The child never ran if assignment failed; no descendants exist.
                    process.kill()
                    process.wait(timeout=15)
            finally:
                try:
                    job.terminate()
                finally:
                    job.close()
        except BaseException as cleanup_error:
            from .process_runtime import ProcessCleanupError
            raise ProcessCleanupError('Windows process creation rollback is unconfirmed') from cleanup_error
        raise


def process_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    handle = _api().OpenProcess(0x1000 | 0x100000, False, pid)  # QUERY | SYNCHRONIZE
    if not handle:
        error = ctypes.get_last_error()
        if error == 5:  # ACCESS_DENIED is not proof of death.
            return True
        if error in (87, 1168):
            return False
        raise ctypes.WinError(error)
    try:
        state = _api().WaitForSingleObject(handle, 0)
        if state == 0xffffffff:
            raise ctypes.WinError(ctypes.get_last_error())
        # Exit code 259 is legal. Only the process object's signalled state
        # proves exit; GetExitCodeProcess(STILL_ACTIVE) is ambiguous.
        return state == 258  # WAIT_TIMEOUT
    finally:
        _api().CloseHandle(handle)


def process_tree_alive(process=None, *, tree_id=None):
    if getattr(process, '_portal_tree_stopped', False):
        return False
    job = getattr(process, "_portal_job", None)
    reopened = job is None or job.handle is None
    if reopened:
        job = Job.open(tree_id)
    if job is None:
        return False
    try:
        return bool(job.active())
    finally:
        if reopened:
            job.close()


def terminate_process_tree(process, timeout=15, force=False, *, tree_id=None):
    managed = process is not None and not isinstance(process, int)
    if managed and getattr(process, "_portal_tree_stopped", False):
        return
    job = getattr(process, "_portal_job", None) if managed else None
    reopened = job is None
    if reopened:
        job = Job.open(tree_id)
    if job is None:
        return  # Named Job is absent, independent of whether a PID was reused.
    def wait_empty(seconds):
        deadline = time.monotonic() + seconds
        while job.active():
            if time.monotonic() >= deadline:
                return False
            time.sleep(.02)
        return True
    try:
        graceful = (not force and managed and process.poll() is None
                    and getattr(process, "_portal_console_group", False)
                    and _api().GenerateConsoleCtrlEvent(1, process.pid))  # CTRL_BREAK_EVENT
        if not graceful or not wait_empty(timeout):
            job.terminate()
        if not wait_empty(max(timeout, .1)):
            raise TimeoutError("Windows Job still contains active processes")
        if managed:
            process.wait(timeout=max(timeout, .1))
            process._portal_tree_stopped = True
        job.close()
    finally:
        if reopened:
            job.close()
