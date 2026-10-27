"""Kimi's Windows shell prerequisite, shared by preparation and both launchers."""
from __future__ import annotations

import os
from pathlib import Path
import sysconfig
import shutil
import sys
import tempfile

from management_common.file_lock import lock_stream
from management_common.process_runtime import run_process

VERSION = "2.56.0.4"
ARTIFACTS = {
    "amd64": ("64-bit", "b1f3e52216e2ed6882d1bebfd4d3fdf319bec40d2edbbf9fd26ec4fd3fce7ed1"),
    "arm64": ("arm64", "044110f9d951858452ddb5e4b5415ea0f2b5b2598e794d6fec77fe28b9aa8cc0"),
}


def _root():
    from .execution.provider_updates import RUNTIMES
    return RUNTIMES


def _artifact():
    # The service environment omits PROCESSOR_ARCHITECTURE; use Python's build target.
    machine = sysconfig.get_platform().removeprefix("win-").lower()
    key = "amd64" if machine == "x86_64" else machine
    if key not in ARTIFACTS:
        raise ValueError("Git Bash is not packaged for this Windows architecture")
    arch, digest = ARTIFACTS[key]
    return {"name": "git-bash", "version": VERSION, "sha256": digest,
            "url": f"https://github.com/git-for-windows/git/releases/download/v2.56.0.windows.4/PortableGit-{VERSION}-{arch}.7z.exe"}


def _managed():
    entry = _artifact()
    return _root() / ("git-bash-" + VERSION + "-" + entry["sha256"][:12])


def shell_path():
    """Read-only discovery; do not mistake Windows' WSL bash shim for Git Bash."""
    explicit = os.environ.get("KIMI_SHELL_PATH", "").strip()
    if explicit:
        path = Path(explicit)
        if not path.is_absolute() or not path.is_file():
            raise ValueError("KIMI_SHELL_PATH must name an existing absolute bash.exe path")
        return path
    managed = _managed()
    if (managed / ".portal-validated").is_file() and (managed / "bin/bash.exe").is_file():
        return managed / "bin/bash.exe"
    roots = [Path(os.environ[key]) / suffix for key, suffix in
             (("ProgramFiles", "Git"), ("ProgramFiles(x86)", "Git"), ("LOCALAPPDATA", "Programs/Git"))
             if os.environ.get(key)]
    git = shutil.which("git.exe")
    if git:
        roots.append(Path(git).parent.parent)
    return next((root / "bin/bash.exe" for root in roots if (root / "bin/bash.exe").is_file()), None)


def available():
    return sys.platform != "win32" or shell_path() is not None


def environment():
    if sys.platform != "win32":
        return {}
    path = shell_path()
    if path is None:
        raise RuntimeError("Kimi requires Git Bash; prepare the Kimi runtime first")
    return {"KIMI_SHELL_PATH": str(path)}


def _validate(path):
    result = run_process([str(path), "--noprofile", "--norc", "-c", "printf portal-bash-ready"],
                         check=True, capture_output=True, text=True, timeout=30)
    if result.stdout.strip() != "portal-bash-ready":
        raise RuntimeError("Git Bash validation failed")


def prepare():
    if sys.platform != "win32":
        return
    path = shell_path()
    if path:
        _validate(path)
        return
    from scripts.tfi_release_native_dependencies import download
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    with (root / "git-bash.lock").open("a+") as lock, lock_stream(lock):
        path = shell_path()
        if path:
            _validate(path)
            return
        entry = _artifact()
        destination = _managed()
        archive = root / "archives" / (entry["sha256"] + ".7z.exe")
        download(entry, archive)
        # The official self-extractor is executed only after SHA256 verification.
        # Extract into a fresh staging folder so an interrupted install is not ready.
        with tempfile.TemporaryDirectory(prefix="git-bash-stage-", dir=root) as temp:
            stage = Path(temp) / "git"
            run_process([str(archive), "-y", "-o" + str(stage)], check=True,
                        capture_output=True, timeout=180)
            _validate(stage / "bin/bash.exe")
            (stage / ".portal-validated").write_text(entry["sha256"] + "\n")
            stage.rename(destination)
