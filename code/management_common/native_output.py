"""Capture human-readable native tool output independently of Python UTF-8 mode."""
from __future__ import annotations

import locale
import subprocess

from management_common.process_runtime import run_process


def decode_native_output(output):
    """Preserve UTF-8, then try the host code page; retain unknown bytes."""
    if output is None:
        return None
    for encoding in ("utf-8", locale.getencoding()):
        try:
            return output.decode(encoding).replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            pass
    return output.decode("utf-8", errors="backslashreplace")


def run_native_capture(command, *, env=None, cwd=None, timeout, check=False, merge_output=False):
    """Decode diagnostics after capture, including unsuccessful commands.

    This boundary is for native human-readable output. Python control output,
    JSON protocols and installation data keep their declared strict encoding.
    """
    result = run_process(command, env=env, cwd=cwd, timeout=timeout,
                         stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT if merge_output else subprocess.PIPE)
    result.stdout = decode_native_output(result.stdout)
    result.stderr = decode_native_output(result.stderr)
    if check:
        result.check_returncode()
    return result


def run_utf8_capture(command, *, env=None, cwd=None, timeout, check=False):
    """Keep successful control stdout strict; decode diagnostic stderr separately.

    A Python child can inherit stderr from a localized native tool. Such
    diagnostics must not corrupt its UTF-8 JSON stdout or hide an exit code.
    Failed commands have no valid control response: retain both diagnostic
    streams using the native-output boundary.
    """
    result = run_process(command, env=env, cwd=cwd, timeout=timeout,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    result.stderr = decode_native_output(result.stderr)
    result.stdout = (decode_native_output(result.stdout) if result.returncode
                     else result.stdout.decode("utf-8"))
    if check:
        result.check_returncode()
    return result
