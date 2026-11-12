"""Select an SSH client that can execute the configured askpass helper."""
import sys
import shutil
from pathlib import Path


def ssh_executable(*, needs_askpass: bool) -> str:
    if sys.platform != "win32" or not needs_askpass:
        return "ssh"
    # Native Win32 OpenSSH uses CreateProcess for askpass, which cannot run a
    # Python/shebang script. Use the paired POSIX tools shipped with Git for
    # Windows so the private shell launcher has defined execution semantics.
    git = shutil.which("git")
    if git:
        for root in Path(git).resolve().parents:
            ssh = root / "usr" / "bin" / "ssh.exe"
            shell = root / "usr" / "bin" / "sh.exe"
            if ssh.is_file() and shell.is_file():
                return ssh.as_posix()
    raise OSError("Windows SSH password authentication requires Git for Windows with bundled ssh and sh.")

