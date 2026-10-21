"""Terminal platform contract; only POSIX cases use native processes on Linux."""
import builtins
import importlib
import os
from pathlib import Path
import sys
import time

import pytest


def runtime():
    assert importlib.util.find_spec('management_common.terminal_runtime'), 'terminal platform owner is missing'
    return importlib.import_module('management_common.terminal_runtime')


def test_terminal_web_import_does_not_load_posix_modules(monkeypatch):
    real_import = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name in {'fcntl', 'pty', 'pwd', 'termios'}:
            raise AssertionError('platform import leaked: ' + name)
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    import management_portal.local_terminal_backend as terminal
    importlib.reload(terminal)


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX shell executable; Windows argv has its own case')
def test_shell_selection_honors_executable_path_with_spaces(tmp_path, monkeypatch):
    module = runtime()
    shell = tmp_path / 'my shell'
    shell.write_text('#!/bin/sh\n')
    shell.chmod(0o755)
    monkeypatch.setenv('SHELL', str(shell))
    assert module.shell_command()[0] == str(shell)


@pytest.mark.skipif(sys.platform not in {'linux', 'darwin'}, reason='native POSIX PTY')
def test_native_pty_unicode_resize_interrupt_and_exit(tmp_path):
    module = runtime()
    code = "import os,sys,signal,time; print('TTY',os.isatty(0),os.isatty(1),os.tcgetpgrp(0)==os.getpgrp(),flush=True); print('尺寸',os.get_terminal_size(0),flush=True); signal.signal(signal.SIGINT, lambda *a: sys.exit(23)); print(input(),flush=True); time.sleep(30)"
    term = module.spawn_terminal([sys.executable, '-u', '-c', code], cwd=tmp_path, cols=91, rows=27)
    def until(expected, timeout=4):
        data = b''; end = time.monotonic() + timeout
        while time.monotonic() < end:
            data += term.read()
            if expected in data:
                return data
            time.sleep(.01)
        pytest.fail(repr(data))
    try:
        output = until(b'lines=27')
        assert b'TTY True True True' in output
        term.resize(102, 34)
        term.write('汉🙂\n'.encode())
        assert '汉🙂' in until('汉🙂'.encode()).decode()
        term.interrupt()
        end = time.monotonic()+4
        while term.poll() is None and time.monotonic() < end:
            term.read(); time.sleep(.01)
        assert term.poll() == 23
    finally:
        term.close()
        term.close()


def test_unknown_platform_is_explicit(monkeypatch):
    module = runtime()
    monkeypatch.setattr(module.sys, 'platform', 'unsupported-os')
    with pytest.raises(NotImplementedError, match='terminal platform'):
        module.spawn_terminal(['echo'], cwd=Path.cwd(), cols=80, rows=24)


def test_conpty_missing_native_api_is_diagnostic(monkeypatch):
    from management_common import terminal_windows as windows
    monkeypatch.setattr(windows.ctypes, 'WinDLL', lambda *a, **k: object(), raising=False)
    with pytest.raises(RuntimeError, match='native ConPTY API'):
        windows._kernel()


def test_windows_command_keeps_shell_path_as_one_argument(monkeypatch):
    module = runtime()
    monkeypatch.setattr(module.sys, 'platform', 'win32')
    monkeypatch.setenv('COMSPEC', 'C:/Program Files/Shell/cmd.exe')
    monkeypatch.setattr(module.shutil, 'which', lambda value: value)
    assert module.shell_command() == ['C:/Program Files/Shell/cmd.exe', '/D']


def test_conpty_transport_acknowledges_write_resize_and_preserves_final_output(tmp_path, monkeypatch):
    """Linux transport integration, not a Windows-native acceptance result."""
    from management_common import terminal_windows as windows, process_runtime
    host = tmp_path / 'host.py'
    host.write_text('''import sys,json,base64
print(json.dumps({'ready':123}),flush=True)
for line in sys.stdin:
 item=json.loads(line)
 if 'write' in item:
  print(json.dumps({'data':item['write']}),flush=True)
 if 'resize' in item:
  assert item['resize']==[101,33]
 print(json.dumps({'ack':item['id']}),flush=True)
 if item.get('close'):
  print(json.dumps({'data':base64.b64encode(b'final').decode()}),flush=True)
  print(json.dumps({'exit':0}),flush=True)
  break
''')
    original = process_runtime.spawn_process
    monkeypatch.setattr(process_runtime, 'spawn_process', lambda argv, **kw: original([sys.executable, '-u', str(host)], **kw))
    transport = windows.WindowsTerminal(['unused'], cwd=tmp_path, cols=80, rows=24, env=dict(os.environ))
    try:
        transport.write('汉🙂'.encode())
        assert transport.read().decode() == '汉🙂'
        transport.resize(101,33)
        transport.interrupt()
        assert transport.read() == b'\x03'
        transport._command({'close':True})
        assert transport._finished.wait(3)
        assert transport.poll() is None  # final bytes must be drained first
        assert transport.read() == b'final'
        assert transport.poll() == 0
    finally:
        transport.close()


@pytest.mark.skipif(sys.platform != 'win32', reason='requires native Windows ConPTY')
def test_windows_native_console_is_real_and_unicode_round_trips(tmp_path):
    term = runtime().spawn_terminal([sys.executable, '-u', '-c', "import os; print('TTY',os.isatty(0),os.isatty(1),flush=True); print(input(),flush=True)"], cwd=tmp_path, cols=80, rows=24)
    try:
        term.write('汉🙂\r'.encode())
        data=b''; end=time.monotonic()+5
        while time.monotonic()<end:
            data+=term.read()
            if term.poll() is not None:
                break
            time.sleep(.01)
        assert b'TTY True True' in data
        assert '汉🙂' in data.decode()
    finally:
        term.close()


@pytest.mark.parametrize('failure', ['write_error', 'unknown_receipt'])
def test_conpty_input_failure_is_not_replayed(tmp_path, monkeypatch, failure):
    from management_common import terminal_windows as windows, process_runtime
    record = tmp_path / 'accepted.jsonl'
    host = tmp_path / 'host-fail.py'
    host.write_text('''import sys,json
print(json.dumps({'ready':123}),flush=True)
for line in sys.stdin:
 item=json.loads(line)
 with open(sys.argv[1],'a') as f: f.write(line)
 if 'write' in item:
  if sys.argv[2]=='write_error': print(json.dumps({'ack':item['id'],'error':'native write failed'}),flush=True)
  break
 print(json.dumps({'ack':item['id']}),flush=True)
''')
    original = process_runtime.spawn_process
    monkeypatch.setattr(process_runtime, 'spawn_process', lambda argv, **kw: original([sys.executable, '-u', str(host), str(record), failure], **kw))
    transport = windows.WindowsTerminal(['unused'], cwd=tmp_path, cols=80, rows=24, env=dict(os.environ))
    try:
        with pytest.raises(RuntimeError, match='native write failed|before input acknowledgement'):
            transport.write(b'one command\n')
    finally:
        transport.close()
    import json
    commands = [json.loads(line) for line in record.read_text().splitlines()]
    assert sum('write' in command for command in commands) == 1


@pytest.mark.skipif(sys.platform not in {'linux','darwin'}, reason='native POSIX PTY')
def test_close_stops_shell_foreground_job_without_affecting_peer(tmp_path):
    from management_common import process_runtime
    module = runtime()
    term = module.spawn_terminal(module.shell_command(), cwd=tmp_path, cols=80, rows=24)
    peer = module.spawn_terminal([sys.executable, '-c', 'import time; time.sleep(30)'], cwd=tmp_path, cols=80, rows=24)
    marker = tmp_path / 'child.pid'
    try:
        term.write(b'sleep 30 & echo $! > child.pid; wait\n')
        end = time.monotonic()+4
        while not marker.exists() and time.monotonic()<end:
            term.read(); time.sleep(.01)
        pid = int(marker.read_text())
        term.close()
        end=time.monotonic()+3
        while process_runtime.process_alive(pid) and time.monotonic()<end:
            time.sleep(.02)
        assert not process_runtime.process_alive(pid)
        assert peer.poll() is None
    finally:
        term.close(); peer.close()


@pytest.mark.parametrize('fail_create', [False, True])
def test_conpty_native_api_contract_and_handle_cleanup(monkeypatch, fail_create):
    """Win32 boundary simulation on Linux; not native Windows execution."""
    from management_common import terminal_windows as windows
    class Kernel:
        def __init__(self):
            self.next = 10; self.closed = []; self.console_closed = 0
            self.writes = []; self.resizes = []; self.command = None
        def CreatePipe(self, read, write, *_args):
            read._obj.value=self.next; write._obj.value=self.next+1; self.next+=2
            return 1
        def CreatePseudoConsole(self, size, _read, _write, flags, result):
            assert (size.X,size.Y,flags)==(90,28,0)
            result._obj.value=20; return 0
        def InitializeProcThreadAttributeList(self, storage, _count, _flags, needed):
            needed._obj.value=64; return bool(storage)
        def UpdateProcThreadAttribute(self, _attrs, _flags, attribute, hpc, *_rest):
            assert attribute==0x20016 and hpc.value==20
            return 1
        def DeleteProcThreadAttributeList(self, _attrs):
            pass
        def CreateProcessW(self, executable, command, _pa, _ta, inherit, flags, env, cwd, startup, info):
            self.command = (executable,command.value)
            assert not inherit and flags & 0x80000 and not flags & 0x1000000
            assert env is None and cwd is None and startup._obj.lpAttributeList
            if fail_create:
                raise OSError('synthetic create failure')
            info._obj.hProcess=30; info._obj.hThread=31; info._obj.dwProcessId=41
            return 1
        def CloseHandle(self, handle):
            self.closed.append(handle.value if hasattr(handle,'value') else handle); return 1
        def ClosePseudoConsole(self, _handle):
            self.console_closed+=1
        def ReadFile(self, _handle, _buffer, _size, count, _overlapped):
            count._obj.value=0; return 1
        def WriteFile(self, _handle, payload, size, count, _overlapped):
            self.writes.append(payload); count._obj.value=min(size,2); return 1
        def ResizePseudoConsole(self, _handle, size):
            self.resizes.append((size.X,size.Y)); return 0
    api=Kernel()
    monkeypatch.setattr(windows, '_kernel', lambda:api)
    argv=['C:\\Program Files\\shell.exe','argument with spaces','quote"value']
    if fail_create:
        with pytest.raises(OSError, match='synthetic create failure'):
            windows.NativeConsole(argv,90,28)
    else:
        console=windows.NativeConsole(argv,90,28)
        assert console.pid==41
        console.write(b'abcd')
        assert api.writes==[b'abcd',b'cd']
        console.resize(101,33)
        assert api.resizes==[(101,33)]
        console.close();console.close()
        assert api.command[0]==argv[0] and api.command[1].startswith('"C:\\Program Files\\shell.exe"')
    assert api.console_closed==1
    assert set(api.closed)==({10,11,12,13} if fail_create else {10,11,12,13,30,31})


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX login database')
def test_shell_works_without_passwd_entry(monkeypatch):
    import pwd
    module=runtime()
    monkeypatch.setenv('SHELL', '/bin/sh')
    def missing(_uid):
        raise KeyError('unmapped uid')
    monkeypatch.setattr(pwd,'getpwuid',missing)
    assert module.shell_command()[0] == '/bin/sh'
    assert module.terminal_environment()['TERM'] == 'xterm-256color'


def test_conpty_reader_start_failure_releases_host_and_streams(monkeypatch, tmp_path):
    import io
    from types import SimpleNamespace
    from management_common import process_runtime, terminal_windows as windows
    process = SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO())
    terminated = []
    monkeypatch.setattr(process_runtime, 'spawn_process', lambda *a, **kw: process)
    monkeypatch.setattr(process_runtime, 'terminate_process_tree',
                        lambda target, **kw: terminated.append(target))
    def unavailable(_thread):
        raise RuntimeError('cannot start terminal reader')
    monkeypatch.setattr(windows.threading.Thread, 'start', unavailable)
    with pytest.raises(RuntimeError, match='cannot start terminal reader'):
        windows.WindowsTerminal(['shell'], cwd=tmp_path, cols=80, rows=24, env={})
    assert terminated == [process]
    assert process.stdin.closed and process.stdout.closed
