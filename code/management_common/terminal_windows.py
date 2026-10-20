"""Windows ConPTY transport hosted inside a process_runtime-owned Job.

The private stdio frames carry terminal data/control, never emulate a PTY.
The hosted executable is attached with PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE.
A managed helper owns that console so its descendants inherit the same Job;
process_runtime remains the only owner of Job assignment and tree termination.
"""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading


class Coord(ctypes.Structure):
    _fields_ = [('X', ctypes.c_short), ('Y', ctypes.c_short)]


class StartupInfo(ctypes.Structure):
    _fields_ = [('cb', w.DWORD), ('lpReserved', w.LPWSTR), ('lpDesktop', w.LPWSTR),
                ('lpTitle', w.LPWSTR), *[(n, w.DWORD) for n in (
                    'dwX', 'dwY', 'dwXSize', 'dwYSize', 'dwXCountChars',
                    'dwYCountChars', 'dwFillAttribute', 'dwFlags')],
                ('wShowWindow', w.WORD), ('cbReserved2', w.WORD),
                ('lpReserved2', ctypes.c_void_p), ('hStdInput', w.HANDLE),
                ('hStdOutput', w.HANDLE), ('hStdError', w.HANDLE)]


class StartupInfoEx(ctypes.Structure):
    _fields_ = [('StartupInfo', StartupInfo), ('lpAttributeList', ctypes.c_void_p)]


class ProcessInfo(ctypes.Structure):
    _fields_ = [('hProcess', w.HANDLE), ('hThread', w.HANDLE),
                ('dwProcessId', w.DWORD), ('dwThreadId', w.DWORD)]


def _kernel():
    try:
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'CreatePipe': ([ctypes.POINTER(w.HANDLE), ctypes.POINTER(w.HANDLE), ctypes.c_void_p, w.DWORD], w.BOOL),
            'CreatePseudoConsole': ([Coord, w.HANDLE, w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)], ctypes.c_long),
            'ResizePseudoConsole': ([w.HANDLE, Coord], ctypes.c_long),
            'ClosePseudoConsole': ([w.HANDLE], None),
            'InitializeProcThreadAttributeList': ([ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.c_size_t)], w.BOOL),
            'UpdateProcThreadAttribute': ([ctypes.c_void_p, w.DWORD, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p], w.BOOL),
            'DeleteProcThreadAttributeList': ([ctypes.c_void_p], None),
            'CreateProcessW': ([w.LPCWSTR, w.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, w.BOOL, w.DWORD, ctypes.c_void_p, w.LPCWSTR, ctypes.POINTER(StartupInfoEx), ctypes.POINTER(ProcessInfo)], w.BOOL),
            'ReadFile': ([w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p], w.BOOL),
            'WriteFile': ([w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p], w.BOOL),
            'CloseHandle': ([w.HANDLE], w.BOOL),
            'WaitForSingleObject': ([w.HANDLE, w.DWORD], w.DWORD),
            'GetExitCodeProcess': ([w.HANDLE, ctypes.POINTER(w.DWORD)], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(api, name)
            function.argtypes, function.restype = args, result
        return api
    except (AttributeError, OSError) as error:
        raise RuntimeError('Windows Terminal requires the native ConPTY API (Windows 10 1809 / Server 2019 or newer)') from error


def _check(ok):
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())


def _hresult(result, operation):
    if result < 0:
        raise OSError(f'ConPTY {operation} failed (HRESULT 0x{result & 0xffffffff:08x})')


class NativeConsole:
    """Native console handles; process Job membership is inherited from host."""
    def __init__(self, argv, cols, rows):
        self.api = _kernel()
        self.hpc = w.HANDLE()
        self.input_read, self.input_write = w.HANDLE(), w.HANDLE()
        self.output_read, self.output_write = w.HANDLE(), w.HANDLE()
        self.info = ProcessInfo()
        self.lock = threading.RLock()
        self.reader = None
        attrs = None
        try:
            _check(self.api.CreatePipe(ctypes.byref(self.input_read), ctypes.byref(self.input_write), None, 0))
            _check(self.api.CreatePipe(ctypes.byref(self.output_read), ctypes.byref(self.output_write), None, 0))
            _hresult(self.api.CreatePseudoConsole(Coord(cols, rows), self.input_read, self.output_write, 0, ctypes.byref(self.hpc)), 'create')
            needed = ctypes.c_size_t()
            self.api.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(needed))
            if not needed.value:
                raise RuntimeError('ConPTY process attribute allocation failed')
            storage = ctypes.create_string_buffer(needed.value)
            _check(self.api.InitializeProcThreadAttributeList(storage, 1, 0, ctypes.byref(needed)))
            attrs = storage
            _check(self.api.UpdateProcThreadAttribute(attrs, 0, 0x20016, self.hpc, ctypes.sizeof(self.hpc), None, None))
            startup = StartupInfoEx()
            startup.StartupInfo.cb = ctypes.sizeof(startup)
            startup.lpAttributeList = ctypes.addressof(storage)
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
            # No BREAKAWAY flag: the shell inherits process_runtime's Job.
            _check(self.api.CreateProcessW(argv[0], command, None, None, False, 0x80000 | 0x400, None, None, ctypes.byref(startup), ctypes.byref(self.info)))
        except BaseException:
            self.close()
            raise
        finally:
            if attrs is not None:
                self.api.DeleteProcThreadAttributeList(attrs)
            self._close_handle('input_read')
            self._close_handle('output_write')
            if self.info.hThread:
                self.api.CloseHandle(self.info.hThread)
                self.info.hThread = None
        self.pid = self.info.dwProcessId

    def _close_handle(self, name):
        handle = getattr(self, name)
        if handle:
            self.api.CloseHandle(handle)
            setattr(self, name, w.HANDLE())

    def write(self, data):
        offset = 0
        while offset < len(data):
            count = w.DWORD()
            part = data[offset:]
            _check(self.api.WriteFile(self.input_write, part, len(part), ctypes.byref(count), None))
            if not count.value:
                raise OSError('ConPTY input write made no progress')
            offset += count.value

    def resize(self, cols, rows):
        with self.lock:
            _hresult(self.api.ResizePseudoConsole(self.hpc, Coord(cols, rows)), 'resize')

    def read(self):
        buffer = ctypes.create_string_buffer(8192)
        count = w.DWORD()
        if not self.api.ReadFile(self.output_read, buffer, len(buffer), ctypes.byref(count), None):
            if ctypes.get_last_error() in {109, 232, 6}:  # broken/closed pipe
                return b''
            _check(False)
        return buffer.raw[:count.value]

    def poll(self):
        if not self.info.hProcess:
            return 0
        wait = self.api.WaitForSingleObject(self.info.hProcess, 0)
        if wait == 258:
            return None
        if wait != 0:
            _check(False)
        code = w.DWORD()
        _check(self.api.GetExitCodeProcess(self.info.hProcess, ctypes.byref(code)))
        return code.value

    def close(self):
        with self.lock:
            if self.hpc:
                if self.reader is None:
                    def discard():
                        try:
                            while self.read():
                                pass
                        except OSError:
                            pass
                    self.reader = threading.Thread(target=discard, daemon=True)
                    self.reader.start()
                # A read thread remains active while ClosePseudoConsole emits
                # its final frame. Never close its output handle before this.
                self.api.ClosePseudoConsole(self.hpc)
                self.hpc = w.HANDLE()
            if self.reader:
                self.reader.join(timeout=2)
            for name in ('input_read', 'input_write', 'output_read', 'output_write'):
                self._close_handle(name)
            if self.info.hProcess:
                self.api.CloseHandle(self.info.hProcess)
                self.info.hProcess = None


def _host(argv, cols, rows):
    output_lock = threading.Lock()
    def emit(message):
        payload = (json.dumps(message) + '\n').encode('ascii')
        with output_lock:
            while payload:
                written = os.write(1, payload)
                if written <= 0:
                    raise BrokenPipeError('terminal host output closed')
                payload = payload[written:]
    console = None
    try:
        console = NativeConsole(argv, cols, rows)
        emit({'ready': console.pid})
        errors = []
        def drain():
            try:
                while chunk := console.read():
                    emit({'data': base64.b64encode(chunk).decode('ascii')})
            except (OSError, RuntimeError) as error:
                errors.append(str(error))
        console.reader = threading.Thread(target=drain, daemon=True)
        console.reader.start()
        stop = threading.Event()
        def commands():
            pending = b''
            try:
                while chunk := os.read(0, 32768):
                    pending += chunk
                    while b'\n' in pending:
                        line, pending = pending.split(b'\n', 1)
                        item = json.loads(line)
                        try:
                            if 'write' in item:
                                console.write(base64.b64decode(item['write'], validate=True))
                            elif 'resize' in item:
                                console.resize(*item['resize'])
                            elif not item.get('close'):
                                raise ValueError('Unknown terminal control')
                            emit({'ack': item['id']})
                        except (OSError, ValueError) as error:
                            emit({'ack': item['id'], 'error': str(error)})
                        if item.get('close'):
                            stop.set()
                            return
            except (OSError, ValueError) as error:
                errors.append(str(error))
            finally:
                stop.set()
        threading.Thread(target=commands, daemon=True).start()
        while console.poll() is None and not stop.wait(.02):
            pass
        code = console.poll()
        console.close()
        if errors:
            emit({'error': errors[0]})
        emit({'exit': code if code is not None else 0})
        return 0
    except Exception as error:
        emit({'error': f'ConPTY startup or I/O failed: {error}'})
        return 1
    finally:
        if console:
            console.close()


class WindowsTerminal:
    def __init__(self, argv, *, cwd, cols, rows, env):
        from management_common.process_runtime import spawn_process
        self._queue = queue.Queue(maxsize=128)
        self._ready = threading.Event()
        self._finished = threading.Event()
        self._closing = threading.Event()
        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending = {}
        self._next_command = 0
        self._error = ''
        self._exit = None
        self._closed = False
        self.eof = False
        self.pid = None
        self.process = spawn_process(
            [sys.executable, '-u', str(Path(__file__).resolve()), '--host', str(cols), str(rows), *argv],
            cwd=str(cwd), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, bufsize=0,
        )
        try:
            self._reader = threading.Thread(target=self._read_frames, daemon=True)
            self._reader.start()
        except BaseException:
            from management_common.process_runtime import terminate_process_tree
            self._closing.set()
            try:
                terminate_process_tree(self.process, timeout=1.5, force=True)
            finally:
                for stream in (self.process.stdin, self.process.stdout):
                    stream.close()
            raise
        if not self._ready.wait(10) or self.pid is None:
            error = self._error or 'ConPTY host did not become ready'
            self.close()
            raise RuntimeError(error)

    def _read_frames(self):
        try:
            for line in self.process.stdout:
                item = json.loads(line)
                if 'ready' in item:
                    self.pid = item['ready']; self._ready.set()
                elif 'data' in item:
                    data = base64.b64decode(item['data'], validate=True)
                    while not self._closing.is_set():
                        try:
                            self._queue.put(data, timeout=.05)
                            break
                        except queue.Full:
                            continue
                elif 'ack' in item:
                    with self._pending_lock:
                        pending = self._pending.get(item['ack'])
                        if pending:
                            pending['error'] = item.get('error', '')
                            pending['event'].set()
                elif 'error' in item:
                    self._error = item['error']
                elif 'exit' in item:
                    self._exit = item['exit']
        except (OSError, ValueError) as error:
            self._error = f'ConPTY transport failed: {error}'
        finally:
            self._finished.set(); self._ready.set()
            with self._pending_lock:
                for pending in self._pending.values():
                    if not pending['event'].is_set():
                        pending['error'] = self._error or 'ConPTY exited before input acknowledgement; do not replay input'
                        pending['event'].set()

    def _command(self, value):
        with self._pending_lock:
            self._next_command += 1
            identity = self._next_command
            pending = {'event': threading.Event(), 'error': ''}
            self._pending[identity] = pending
        try:
            data = (json.dumps({**value, 'id': identity}) + '\n').encode('ascii')
            with self._write_lock:
                while data:
                    count = self.process.stdin.write(data)
                    if not count:
                        raise BrokenPipeError('ConPTY input transport closed')
                    data = data[count:]
            if not pending['event'].wait(5):
                raise TimeoutError('ConPTY input acknowledgement timed out; do not replay input')
            if pending['error']:
                raise RuntimeError(pending['error'])
        finally:
            with self._pending_lock:
                self._pending.pop(identity, None)

    def read(self):
        chunks = []
        size = 0
        while True:
            try:
                chunk = self._queue.get_nowait()
                chunks.append(chunk)
                size += len(chunk)
                if size >= 65536 and not self._finished.is_set():
                    break
            except queue.Empty:
                break
        self.eof = self._finished.is_set() and self._queue.empty()
        if self._error and not chunks:
            raise RuntimeError(self._error)
        return b''.join(chunks)

    def poll(self):
        if not self._finished.is_set() or not self._queue.empty():
            return None
        return self._exit if self._exit is not None else self.process.poll()

    def write(self, data):
        self._command({'write': base64.b64encode(data).decode('ascii')})

    def resize(self, cols, rows):
        self._command({'resize': [cols, rows]})

    def interrupt(self):
        self.write(b'\x03')

    def close(self):
        if self._closed:
            return
        from management_common.process_runtime import terminate_process_tree
        self._closing.set()
        try:
            if self.process.poll() is None:
                try:
                    self._command({'close': True})
                    self.process.wait(timeout=1.5)
                except (OSError, RuntimeError, subprocess.TimeoutExpired):
                    pass
        finally:
            terminate_process_tree(self.process, timeout=1.5, force=True)
            for stream in (self.process.stdin, self.process.stdout):
                stream.close()
            self._reader.join(timeout=2)
            self._closed = True


if __name__ == '__main__':
    if len(sys.argv) < 5 or sys.argv[1] != '--host':
        raise SystemExit('This module is an internal ConPTY host')
    raise SystemExit(_host(sys.argv[4:], int(sys.argv[2]), int(sys.argv[3])))
