"""Interactive terminal transport, separate from pipe-based command execution."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys


def _platform():
    if sys.platform not in {'linux', 'darwin', 'win32'}:
        raise NotImplementedError(f'Unsupported terminal platform: {sys.platform}')
    return sys.platform


def shell_command() -> list[str]:
    platform = _platform()
    if platform == 'win32':
        candidates = [os.environ.get('COMSPEC'), 'pwsh.exe', 'powershell.exe', 'cmd.exe']
    else:
        import pwd
        try:
            login_shell = pwd.getpwuid(os.getuid()).pw_shell
        except KeyError:
            login_shell = ''
        candidates = [os.environ.get('SHELL'), login_shell, 'bash', 'sh']
    for candidate in candidates:
        if not candidate:
            continue
        path = shutil.which(candidate)
        if path:
            if platform != 'win32':
                return [path, '-l']
            return [path, '/D'] if Path(path).name.lower() == 'cmd.exe' else [path, '-NoLogo']
    raise RuntimeError('No executable interactive shell is installed or configured')


def terminal_environment() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault('HOME', str(Path.home()))
    if _platform() != 'win32':
        import pwd
        try:
            username = pwd.getpwuid(os.getuid()).pw_name
        except KeyError:
            pass
        else:
            env.setdefault('USER', username)
            env.setdefault('LOGNAME', username)
    env['TERM'] = 'xterm-256color'
    return env


def spawn_terminal(argv: list[str], *, cwd, cols: int, rows: int):
    platform = _platform()
    if not argv or not all(isinstance(arg, str) and '\0' not in arg for arg in argv):
        raise ValueError('terminal command must be a nonempty argument vector without NUL')
    executable = shutil.which(argv[0])
    if not executable:
        raise FileNotFoundError(f'Terminal executable is not available: {argv[0]}')
    command = [executable, *argv[1:]]
    if platform == 'win32':
        from management_common.terminal_windows import WindowsTerminal
        return WindowsTerminal(command, cwd=cwd, cols=cols, rows=rows, env=terminal_environment())
    from management_common.terminal_posix import PosixTerminal
    return PosixTerminal(command, cwd=cwd, cols=cols, rows=rows, env=terminal_environment())
