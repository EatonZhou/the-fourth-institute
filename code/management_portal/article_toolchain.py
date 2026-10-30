"""The TeX tools this host offers an article build: TinyTeX or ``PATH``, the supported
compilers, the engines' versions, kpsewhich and fontconfig lookups, latexmk and SyncTeX.
"""
from __future__ import annotations

import functools
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def _tinytex_bin_dirs() -> tuple[Path, ...]:
    # Installed tools only; custom TeX installations continue to use PATH.
    home = Path.home()
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming") / "TinyTeX"
    elif sys.platform == "darwin":
        root = home / "Library" / "TinyTeX"
    else:
        root = home / ".TinyTeX"
    return tuple(sorted(path for path in (root / "bin").glob("*") if path.is_dir()))


TEX_BIN_DIRS = _tinytex_bin_dirs()


def _tex_search_path() -> str:
    return os.pathsep.join([*(str(item) for item in TEX_BIN_DIRS if item.is_dir()), os.environ.get("PATH", "")])


def _tex_which(name: str) -> str | None:
    return shutil.which(name, path=_tex_search_path())


SUPPORTED_LATEX_COMPILERS = {
    "pdflatex": {
        "label": "pdfLaTeX",
        "latexmk_flag": "-pdf",
        "engine_option": "-pdflatex",
    },
    "xelatex": {
        "label": "XeLaTeX",
        "latexmk_flag": "-xelatex",
        "engine_option": "-pdfxelatex",
    },
    "lualatex": {
        "label": "LuaLaTeX",
        "latexmk_flag": "-lualatex",
        "engine_option": "-pdflualatex",
    },
}


def _synctex_command() -> str:
    candidates = (_tex_which("synctex"),)
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise RuntimeError("SyncTeX command line utility is not available")


@functools.lru_cache(maxsize=1)
def latex_environment() -> dict[str, Any]:
    available: list[str] = []
    version = ""
    for compiler in SUPPORTED_LATEX_COMPILERS:
        command = _tex_which(compiler)
        if not command:
            continue
        available.append(compiler)
        if not version:
            try:
                completed = subprocess.run(
                    [command, "--version"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    errors="replace",
                    timeout=12,
                    check=False,
                )
                first_line = next(
                    (
                        line.strip()
                        for line in str(completed.stdout or "").splitlines()
                        if line.strip()
                    ),
                    "",
                )
                match = re.search(r"TeX Live\s+([0-9]{4})", first_line)
                version = f"TeX Live {match.group(1)}" if match else first_line[:160]
            except (OSError, subprocess.SubprocessError):
                version = ""
    latexmk = _tex_which("latexmk") or ""
    texmf_root = ""
    kpsewhich = _tex_which("kpsewhich")
    if kpsewhich:
        try:
            texmf_root = subprocess.run(
                [kpsewhich, "--var-value=TEXMFROOT"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=12,
                check=False,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            texmf_root = ""
    package_database = Path(texmf_root) / "tlpkg" / "texlive.tlpdb"
    package_database_identity = ""
    try:
        stat = package_database.stat()
        package_database_identity = f"{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        package_database_identity = "unavailable"
    identity = "\n".join(
        [
            latexmk,
            version or "TeX Live",
            texmf_root,
            package_database_identity,
            *(f"{name}:{_tex_which(name) or ''}" for name in sorted(available)),
        ]
    )
    return {
        "latexmk": latexmk,
        "version": version or "TeX Live",
        "profile_id": f"portal-texlive-{hashlib.sha256(identity.encode()).hexdigest()[:12]}",
        "policy": "使用已配置的原生 TeX 工具链；隔离输出、固定区域设置，不执行任意构建命令",
        "texmf_root": texmf_root,
        "package_database_identity": package_database_identity,
        "available_compilers": [
            {
                "id": compiler,
                "label": SUPPORTED_LATEX_COMPILERS[compiler]["label"],
                "available": compiler in available,
            }
            for compiler in SUPPORTED_LATEX_COMPILERS
        ],
    }


@functools.lru_cache(maxsize=1024)
def _kpsewhich_file(name: str) -> str:
    command = _tex_which("kpsewhich")
    if not command or not name or not re.fullmatch(r"[A-Za-z0-9_.+@/-]+", name):
        return ""
    try:
        return subprocess.run(
            [command, name],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=12,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


@functools.lru_cache(maxsize=512)
def _fontconfig_family(name: str) -> str:
    command = shutil.which("fc-match")
    if not command or not name or any(character in name for character in "\\{}#"):
        return ""
    try:
        return subprocess.run(
            [command, "--format=%{family}", name],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=12,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _latexmk_command() -> str:
    for candidate in (_tex_which("latexmk"),):
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise RuntimeError("latexmk is not available on the Portal host")
