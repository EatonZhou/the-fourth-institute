"""Repo LaTeX previews using the shared build owner and a retained source snapshot.

The caller authorizes the Repo before every operation. No Article registration,
project rc execution, or output inside the checkout is needed.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
from typing import Any
from urllib.parse import urlencode

from management_common.clock import utc_now_iso
from management_common.configuration_files import atomic_json
from management_common.file_lock import try_lock_descriptor
from management_common.file_retention import cache_file
from management_common.portal_paths import path as portal_path
from management_common.secure_paths import open_target
from management_portal import article_build, article_discovery, article_toolchain
from management_portal.repo_workspace_checkout import (
    RepoWorkspaceConflict, _safe_relative_path, _source_root,
)


DEFAULT_BUILD_ROOT = portal_path("cache") / "repo_latex"
_IGNORED_DIRECTORIES = article_discovery.ARTICLE_TREE_IGNORED_DIRECTORIES


class RepoLatexError(RuntimeError):
    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__(report.get("summary") or "LaTeX compilation failed")
        self.report = report


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


class _Preview:
    def __init__(self, repo_id: str, path: str, compiler: str = "", *, build_root: Path | None = None):
        if not re.fullmatch(r"repo_[0-9a-f]{12}", repo_id):
            raise ValueError("invalid Repo id")
        self.repo_id = repo_id
        self.path = _safe_relative_path(path)
        if Path(self.path).suffix.casefold() != ".tex":
            raise ValueError("LaTeX preview requires a .tex main document")
        _record, self.checkout = _source_root(repo_id)
        # Pin every component and refuse symlinks, just as Repo file reads do.
        with open_target(self.checkout, tuple(self.path.split("/"))) as (fd, info):
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("LaTeX main document must be a regular file")
            head = os.read(fd, 16 * 1024).decode("utf-8", errors="replace")
        hint = re.search(r"(?im)^\s*%\s*!\s*TeX\s+program\s*=\s*(pdflatex|xelatex|lualatex)\s*$", head)
        self.compiler = compiler.strip().lower() or (hint.group(1).lower() if hint else "pdflatex")
        if self.compiler not in article_toolchain.SUPPORTED_LATEX_COMPILERS:
            raise ValueError("unsupported LaTeX compiler")
        identity = json.dumps([str(self.checkout), self.path, self.compiler])
        key = hashlib.sha256(identity.encode()).hexdigest()
        self.root = Path(build_root or DEFAULT_BUILD_ROOT) / repo_id / key
        self.snapshot = self.root / "source"
        self.output = self.root / "preview.pdf"
        self.report_path = self.root / "report.json"
        self.url = f"/api/repos/{repo_id}/workspace/latex-pdf?" + urlencode({
            "path": self.path, "compiler": self.compiler,
        })
        self.build_id = f"{repo_id}:{key}"

    def report(self) -> dict[str, Any]:
        report = _json(self.report_path)
        available = self.output.is_file()
        return {
            "schema": "tfi.repo-latex-build.v1", "id": self.repo_id,
            "path": self.path, "primary_file": self.path, "compiler": self.compiler,
            "status": "idle", "diagnostics": [], "log": "",
            "counts": {"all": 0, "error": 0, "warning": 0, "info": 0},
            **report, "pdf_available": available,
            "pdf_size": self.output.stat().st_size if available else 0,
            "pdf_url": self.url,
        }

    def save(self, report: dict[str, Any]) -> dict[str, Any]:
        diagnostics = []
        for item in report.get("diagnostics", []):
            item = dict(item)
            file = str(item.get("file") or "")
            if file:
                candidate = Path(file)
                if not candidate.is_absolute():
                    candidate = self.snapshot / Path(self.path).parent / candidate
                try:
                    item["file"] = candidate.resolve().relative_to(self.snapshot.resolve()).as_posix()
                except ValueError:
                    pass
            diagnostics.append(item)
        payload = {
            **report, "primary_file": self.path, "diagnostics": diagnostics,
            "counts": {"all": len(diagnostics), **{
                level: sum(d.get("severity") == level for d in diagnostics)
                for level in ("error", "warning", "info")
            }},
        }
        # The public API never needs the cache's private physical paths.
        payload.pop("pdf_path", None)
        atomic_json(self.report_path, payload)
        return self.report()

    def sync_source(self) -> None:
        """Copy changed regular files only; retain mtimes for latexmk dependency checks.

        TeX can create files beside inputs (e.g. nested include .aux), so it runs
        against this snapshot. Removed sources are removed here as well.
        """
        manifest_path = self.root / "sources.json"
        previous = _json(manifest_path)
        current = {}
        self.snapshot.mkdir(parents=True, exist_ok=True)
        # Drop disappeared file names before copying: a previous file can now
        # be a directory containing new source files (or the other way round).
        for relative in previous:
            source = self.checkout / relative
            target = self.snapshot / relative
            if (not source.is_file() or source.is_symlink()) and target.is_file():
                target.unlink()
        for directory, folders, names in os.walk(self.checkout, followlinks=False):
            parent = Path(directory)
            folders[:] = [name for name in folders if name not in _IGNORED_DIRECTORIES
                          and not (parent / name).is_symlink()]
            for name in names:
                source = parent / name
                if name in {".git", ".hg", ".svn"} or source.is_symlink() or not source.is_file():
                    continue
                relative = source.relative_to(self.checkout).as_posix()
                target = self.snapshot / relative
                with open_target(self.checkout, tuple(relative.split("/"))) as (fd, info):
                    stamp = [info.st_size, info.st_mtime_ns, info.st_ctime_ns]
                    target_stamp = target.stat() if target.is_file() else None
                    unchanged = target_stamp and (target_stamp.st_size, target_stamp.st_mtime_ns) == (info.st_size, info.st_mtime_ns)
                    if stamp != previous.get(relative) or not unchanged:
                        if target.is_dir():
                            shutil.rmtree(target)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with os.fdopen(os.dup(fd), "rb") as incoming, target.open("wb") as outgoing:
                            shutil.copyfileobj(incoming, outgoing)
                        os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))
                    current[relative] = stamp
        for relative in previous.keys() - current.keys():
            target = self.snapshot / relative
            if target.is_file():
                target.unlink()
        atomic_json(manifest_path, current)


def repo_latex_status(repo_id: str, path: str, *, compiler: str = "", build_root: Path | None = None) -> dict[str, Any]:
    return _Preview(repo_id, path, compiler, build_root=build_root).report()


def repo_latex_pdf(repo_id: str, path: str, *, compiler: str = "", build_root: Path | None = None) -> Path:
    preview = _Preview(repo_id, path, compiler, build_root=build_root)
    if not preview.output.is_file():
        raise FileNotFoundError("This LaTeX document has no compiled PDF")
    return preview.output


def compile_repo_latex(repo_id: str, path: str, *, compiler: str = "", from_scratch: bool = False,
                       build_root: Path | None = None, timeout_seconds: int = 360) -> dict[str, Any]:
    preview = _Preview(repo_id, path, compiler, build_root=build_root)
    with cache_file(preview.root, group="article", owner_id=repo_id):
        preview.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (preview.root / ".build.lock").open("a+b") as lock:
            if not try_lock_descriptor(lock.fileno()):
                raise RepoWorkspaceConflict("This LaTeX document is already compiling")
            preview.sync_source()
            environment = article_toolchain.latex_environment()
            settings = {
                "primary_file": Path(preview.path).name, "compiler": preview.compiler,
                "compile_mode": "normal", "error_handling": "stop",
                "use_project_latexmkrc": False, "shell_escape": False,
                "environment_profile": environment["profile_id"],
            }
            build = article_build.LatexBuild(
                build_id=preview.build_id,
                source_root=preview.snapshot / Path(preview.path).parent,
                settings=settings, output=preview.output,
                cache_root=preview.root / "build", from_scratch=from_scratch,
                load_report=preview.report, save_report=preview.save,
            )
            try:
                build.check_preflight()
                build.run(timeout_seconds)
            except (RuntimeError, OSError) as exc:
                report = preview.report()
                if report["status"] not in {"error", "stopped"}:
                    report = preview.save({
                        "status": "error", "compiled_at": utc_now_iso(),
                        "summary": str(exc), "log": str(exc),
                        "diagnostics": [{"severity": "error", "category": "environment",
                                         "message": str(exc), "file": preview.path, "line": 0}],
                    })
                raise RepoLatexError(report) from exc
            return preview.report()
