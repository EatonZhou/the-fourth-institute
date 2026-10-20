"""Windows argv boundary: native executables or a restricted batch invocation.

CRT quoting is not cmd.exe quoting. A batch script can reparse its parameters
again, so we never claim arbitrary argv can be escaped through an unknown
script. Use [interpreter.exe, script, *argv] for unrestricted literal arguments.
"""
from __future__ import annotations

import ntpath
import os
import shutil
import subprocess


def _environment_value(environment, name):
    return next((value for key, value in environment.items() if key.upper() == name), None)


def _program(value, environment):
    # Resolve PATHEXT before deciding whether cmd.exe is required. Pin the
    # selected file with lpApplicationName instead of relying on implicit shell
    # fallback or ambiguous first-token parsing in CreateProcess.
    if value.endswith((' ', '.')):
        raise ValueError('Windows executable names cannot end in a space or dot')
    selected = shutil.which(value, path=_environment_value(environment, 'PATH'))
    if selected is not None:
        return selected if ntpath.isabs(selected) else os.path.abspath(selected)
    if ntpath.dirname(value) or ntpath.splitext(value)[1].lower() in {'.cmd', '.bat'}:
        return value if ntpath.isabs(value) else os.path.abspath(value)
    return value


def _batch_token(value):
    # Quotes are syntax to cmd, not CRT backslash escapes. Percent expansion,
    # delayed expansion and command operators cannot be passed through arbitrary
    # batch bodies safely. Trailing backslashes also differ between %~1 and a
    # batch's %* forwarding to a CRT executable, so reject that ambiguity.
    if (any(char in '"%!^&|<>()' or ord(char) < 32 for char in value)
            or value.endswith('\\')):
        raise ValueError('Unsupported Windows batch argument; use a native executable or explicit interpreter argv')
    return '"' + value + '"'


def prepare(command, *, environment=None, executable=None):
    """Return (Popen args, explicit executable), without starting a process.

    Native argv remains literal. Batch argv accepts empty values, spaces and
    ordinary Unicode but rejects quotes, shell operators/expansions, controls
    and trailing backslashes before any Job/process is created. No shell=True.
    Command options must be keyword arguments at the public spawn boundary.
    """
    if isinstance(command, (str, bytes, os.PathLike)):
        raise TypeError('Windows process commands require an argv sequence, not a command string')
    argv = [os.fspath(value) for value in command]
    if not argv or any(not isinstance(value, str) or '\0' in value for value in argv):
        raise ValueError('Windows argv must be nonempty Unicode strings without NUL')
    if not argv[0]:
        raise ValueError('Windows executable cannot be empty')
    environment = os.environ if environment is None else environment
    program = _program(os.fspath(executable) if executable is not None else argv[0], environment)
    if ntpath.splitext(program)[1].lower() not in {'.cmd', '.bat'}:
        return argv, program
    if executable is not None:
        raise ValueError('Windows batch commands cannot override executable')
    # The real OS interpreter is selected independently of PATH/COMSPEC. A
    # caller-selected batch executable is never used as its own interpreter.
    system_root = _environment_value(environment, 'SYSTEMROOT') or _environment_value(os.environ, 'SYSTEMROOT')
    if not system_root or not ntpath.isabs(system_root) or not ntpath.splitdrive(system_root)[0]:
        raise ValueError('An absolute SystemRoot is required for Windows batch commands')
    interpreter = ntpath.join(system_root, 'System32', 'cmd.exe')
    tokens = [_batch_token(program), *(_batch_token(value) for value in argv[1:])]
    line = subprocess.list2cmdline([interpreter]) + ' /d /s /v:off /c "' + ' '.join(tokens) + '"'
    # cmd.exe has a smaller limit than CreateProcess; fail explicitly rather
    # than letting a wrapper truncate a command or environment expansion.
    if len(line.encode('utf-16-le')) // 2 >= 8191:
        raise ValueError('Windows batch command exceeds cmd.exe command-line limit')
    return line, interpreter
