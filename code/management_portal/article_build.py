"""Shared latexmk execution and the Article identity/settings/report adapter.

LatexBuild accepts an explicit source tree and output/report owners. Article and
Repo previews use the same command, incremental outputs and diagnostics.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from management_common import process_runtime
from management_common.clock import utc_now_iso
from management_common.text_values import stripped_text
from management_portal import (
    article_build_profile,
    article_build_results,
    article_registry,
    article_settings,
    article_tex,
    article_toolchain,
)
from management_portal.resources import get_resource_service


_ACTIVE_ARTICLE_BUILDS_LOCK = threading.RLock()
_ACTIVE_ARTICLE_BUILDS: dict[str, subprocess.Popen[str]] = {}
_RUNNING_ARTICLE_BUILDS: set[str] = set()
_STOP_REQUESTED_ARTICLE_BUILDS: set[str] = set()


class ArticleCompilationStopped(RuntimeError):
    def __init__(self, output: str = "") -> None:
        super().__init__("LaTeX compilation stopped")
        self.output = str(output or "")


def _begin_article_build(article_id: str) -> None:
    with _ACTIVE_ARTICLE_BUILDS_LOCK:
        if article_id in _RUNNING_ARTICLE_BUILDS:
            raise RuntimeError("LaTeX compilation is already running")
        _RUNNING_ARTICLE_BUILDS.add(article_id)
        _STOP_REQUESTED_ARTICLE_BUILDS.discard(article_id)


def _finish_article_build(article_id: str) -> None:
    with _ACTIVE_ARTICLE_BUILDS_LOCK:
        _ACTIVE_ARTICLE_BUILDS.pop(article_id, None)
        _STOP_REQUESTED_ARTICLE_BUILDS.discard(article_id)
        _RUNNING_ARTICLE_BUILDS.discard(article_id)


def _terminate_article_process(process: subprocess.Popen[str]) -> None:
    # The owned group/Job may still contain descendants after its leader exits.
    process_runtime.terminate_process_tree(process, timeout=9)


def _run_article_build_command(
    article_id: str,
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    timeout = max(1, int(timeout_seconds))
    # Build output is returned only at completion. A temporary file preserves it
    # without waiting for EOF on pipes inherited by an exited leader's helpers.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as log:
        process = process_runtime.spawn_process(
            command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, env=env,
        )
        expired = None
        try:
            with _ACTIVE_ARTICLE_BUILDS_LOCK:
                _ACTIVE_ARTICLE_BUILDS[article_id] = process
            deadline = time.monotonic() + timeout
            while True:
                with _ACTIVE_ARTICLE_BUILDS_LOCK:
                    stopped = article_id in _STOP_REQUESTED_ARTICLE_BUILDS
                if stopped:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    expired = subprocess.TimeoutExpired(command, timeout)
                    break
                try:
                    process.wait(timeout=min(.1, remaining))
                    break
                except subprocess.TimeoutExpired:
                    pass
        finally:
            try:
                _terminate_article_process(process)
            finally:
                with _ACTIVE_ARTICLE_BUILDS_LOCK:
                    if _ACTIVE_ARTICLE_BUILDS.get(article_id) is process:
                        _ACTIVE_ARTICLE_BUILDS.pop(article_id, None)
        log.seek(0)
        output = log.read()
    if expired is not None:
        expired.stdout = output
        raise expired
    with _ACTIVE_ARTICLE_BUILDS_LOCK:
        stopped = article_id in _STOP_REQUESTED_ARTICLE_BUILDS
    if stopped:
        raise ArticleCompilationStopped(output)
    return subprocess.CompletedProcess(command, process.returncode, output, None)


def _controlled_article_build_environment(article_root: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "LC_ALL": "C.UTF-8",
            "LANG": "C.UTF-8",
            "TZ": "UTC",
            "PATH": article_toolchain._tex_search_path(),
            "SOURCE_DATE_EPOCH": str(
                max(
                    (int(path.stat().st_mtime) for path in article_root.rglob("*.tex")),
                    default=0,
                )
            ),
        }
    )
    for name in ("TEXINPUTS", "BIBINPUTS", "BSTINPUTS", "TEXMFOUTPUT"):
        environment.pop(name, None)
    return environment


def stop_article_compile(article_id: str) -> dict[str, Any]:
    if not get_resource_service().is_valid_id(article_id, "article"):
        raise ValueError(f"invalid article id: {article_id}")
    with _ACTIVE_ARTICLE_BUILDS_LOCK:
        running = article_id in _RUNNING_ARTICLE_BUILDS
        if running:
            _STOP_REQUESTED_ARTICLE_BUILDS.add(article_id)
    # The build thread observes the flag and is the sole owner of tree cleanup;
    # stop and timeout must not concurrently close the same Windows Job handle.
    return {
        "id": article_id,
        "running": running,
        "stop_requested": running,
    }


def compile_article_pdf(
    article_id: str,
    *,
    build_root: str | Path | None = None,
    timeout_seconds: int = 360,
    from_scratch: bool = False,
) -> dict[str, Any]:
    """Compile the registered canonical TeX manuscript into a cached PDF."""
    build = _ArticleBuild(article_id, build_root=build_root, from_scratch=from_scratch)
    build.check_preflight()
    return build.run(timeout_seconds)


class LatexBuild:
    """Shared latexmk execution with source, cache and reporting owned by its caller.

    Each report is saved as the build goes: running, then success/warning,
    error (preflight, no PDF, timeout) or stopped.
    """

    def __init__(
        self, *, build_id: str, source_root: Path, settings: dict[str, Any],
        output: Path, cache_root: Path, from_scratch: bool = False,
        load_report: Callable[[], dict[str, Any]],
        save_report: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        self.build_id = build_id
        self.source_root = source_root.resolve()
        self.output = output
        self.cache_root = cache_root
        self.load_report = load_report
        self.save_report = save_report
        self.from_scratch = from_scratch
        self.rerun_failed_build = False
        self.settings = settings
        self.primary_file = article_tex._safe_relative_path(stripped_text(self.settings.get("primary_file")))
        self.compiler = stripped_text(self.settings.get("compiler")).casefold()
        self.compiler_config = article_toolchain.SUPPORTED_LATEX_COMPILERS.get(self.compiler)
        if self.compiler_config is None:
            raise ValueError(f"unsupported LaTeX compiler: {self.compiler}")
        self.source = (self.source_root / self.primary_file).resolve(strict=False)
        try:
            self.source.relative_to(self.source_root)
        except ValueError as exc:
            raise ValueError("LaTeX primary file escapes the source root") from exc
        if not self.source_root.is_dir() or not self.source.is_file():
            raise FileNotFoundError(self.source)
        if self.source.suffix.casefold() != ".tex":
            raise ValueError("only LaTeX (.tex) articles can be compiled to PDF")

    def check_preflight(self) -> None:
        self.preflight = self.inspect_preflight()
        if self.preflight["status"] != "blocked":
            return
        diagnostics = [
            {
                "severity": item["severity"],
                "category": item["category"],
                "message": item["message"],
                "file": item.get("file", ""),
                "line": 0,
            }
            for item in self.preflight["issues"]
        ]
        self.save_report(
            {
                "status": "error",
                "started_at": utc_now_iso(),
                "compiled_at": utc_now_iso(),
                "compiler": self.compiler,
                "compiler_label": self.compiler_config["label"],
                "primary_file": self.primary_file,
                "compile_mode": self.settings.get("compile_mode", "normal"),
                "return_code": None,
                "duration_ms": 0,
                "diagnostics": diagnostics,
                "log": "\n".join(item["message"] for item in diagnostics),
                "summary": "LaTeX build preflight failed",
                "preflight": self.preflight,
                "environment_profile": self.settings["environment_profile"],
            },
        )
        raise RuntimeError(
            "LaTeX build preflight failed\n"
            + "\n".join(item["message"] for item in diagnostics)
        )

    def inspect_preflight(self) -> dict[str, Any]:
        return article_build_profile.inspect_article_build_profile(
            self.source_root, primary_file=self.primary_file, compiler=self.compiler,
        )

    def run(self, timeout_seconds: int) -> dict[str, Any]:
        self.compile_mode = stripped_text(self.settings.get("compile_mode")).casefold() or "normal"
        if self.compile_mode not in article_settings.ARTICLE_COMPILE_MODES:
            raise ValueError(f"unsupported compile mode: {self.compile_mode}")
        self.compile_root = self.cache_root / self.compile_mode
        self.started_at = utc_now_iso()
        self.started = time.monotonic()
        _begin_article_build(self.build_id)
        try:
            completed, log, diagnostics, synctex_available = self._compile(timeout_seconds)
        except ArticleCompilationStopped as exc:
            self._report(
                "stopped",
                return_code=None,
                diagnostics=article_build_results.parse_latex_diagnostics(exc.output),
                log=exc.output[-80000:],
                summary="LaTeX compilation stopped",
                from_scratch=bool(self.from_scratch),
            )
            raise
        except subprocess.TimeoutExpired as exc:
            output_text = exc.stdout or ""
            if isinstance(output_text, bytes):
                output_text = output_text.decode("utf-8", errors="replace")
            output_text = str(output_text)
            self._report(
                "error",
                return_code=None,
                diagnostics=article_build_results.parse_latex_diagnostics(output_text),
                log=output_text[-80000:],
                summary=f"LaTeX compilation timed out after {timeout_seconds} seconds",
                from_scratch=bool(self.from_scratch),
            )
            raise RuntimeError(
                f"LaTeX compilation timed out after {timeout_seconds} seconds\n"
                f"{output_text[-8000:]}"
            ) from exc
        finally:
            _finish_article_build(self.build_id)
        has_diagnostics = bool(diagnostics) or completed.returncode != 0
        report = self._report(
            "warning" if has_diagnostics else "success",
            return_code=completed.returncode,
            diagnostics=diagnostics,
            log=log[-80000:],
            summary=(
                "PDF generated with LaTeX diagnostics"
                if has_diagnostics
                else "PDF generated successfully"
            ),
            synctex_available=synctex_available,
            from_scratch=bool(self.from_scratch),
            preflight=self.preflight,
            environment_profile=self.settings["environment_profile"],
            use_project_latexmkrc=self.settings["use_project_latexmkrc"],
            shell_escape=self.settings["shell_escape"],
        )
        return {
            **report,
            "pdf_path": str(self.output),
            "pdf_size": self.output.stat().st_size,
            "warnings": has_diagnostics,
        }

    def _compile(self, timeout_seconds: int) -> tuple[subprocess.CompletedProcess[str], str, list[dict[str, Any]], bool]:
        from management_common.file_retention import cache_file
        with cache_file(self.cache_root, group='article', owner_id=self.build_id), cache_file(self.output, group='article', owner_id=self.build_id):
            return self._compile_owned_cache(timeout_seconds)

    def _compile_owned_cache(self, timeout_seconds: int) -> tuple[subprocess.CompletedProcess[str], str, list[dict[str, Any]], bool]:
        """Run latexmk and publish its PDF: the run, its output, its diagnostics, and whether SyncTeX is there."""
        output = self.output
        output.parent.mkdir(parents=True, exist_ok=True)
        if self.from_scratch:
            shutil.rmtree(self.cache_root, ignore_errors=True)
            output.unlink(missing_ok=True)
        else:
            # latexmk tracks manuscript files only: after a failed run it answers
            # "Nothing to do" until one of them changes, even when the TeX
            # installation has since gained the missing package or font.
            previous = self.load_report()
            self.rerun_failed_build = (previous.get("status") in {"error", "stopped"}
                                       or previous.get("return_code") not in (None, 0))
        self.compile_root.mkdir(parents=True, exist_ok=True)
        self._report(
            "running",
            finished=False,
            return_code=None,
            diagnostics=[],
            log="",
            from_scratch=bool(self.from_scratch),
            environment_profile=self.settings["environment_profile"],
            use_project_latexmkrc=self.settings["use_project_latexmkrc"],
            shell_escape=self.settings["shell_escape"],
        )
        compile_target = self._compile_target()
        pdf = self.compile_root / f"{self.source.stem}.pdf"
        # Retain latexmk's output for no-op builds, but distinguish a newly
        # generated diagnostic PDF from an old file left by a failed command.
        previous_pdf = pdf.stat() if pdf.is_file() else None
        completed = _run_article_build_command(
            self.build_id,
            self._command(compile_target),
            cwd=self.source_root,
            timeout_seconds=timeout_seconds,
            env=self.environment(),
        )
        log = str(completed.stdout or "")
        final_log_path = self.compile_root / f"{self.source.stem}.log"
        try:
            final_log = final_log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            final_log = ""
        diagnostics = article_build_results.parse_latex_diagnostics(final_log or log)
        current_pdf = pdf.stat() if pdf.is_file() else None
        unchanged_pdf = previous_pdf is not None and current_pdf is not None and (
            previous_pdf.st_mtime_ns, previous_pdf.st_ctime_ns, previous_pdf.st_size
        ) == (current_pdf.st_mtime_ns, current_pdf.st_ctime_ns, current_pdf.st_size)
        if current_pdf is None or (completed.returncode != 0 and unchanged_pdf):
            self._report(
                "error",
                return_code=completed.returncode,
                diagnostics=diagnostics,
                log=log[-80000:],
                summary="LaTeX compilation failed",
                from_scratch=bool(self.from_scratch),
                preflight=self.preflight,
                environment_profile=self.settings["environment_profile"],
            )
            raise RuntimeError(f"LaTeX compilation failed\n{log[-12000:]}")
        temporary_pdf = output.with_suffix(".pdf.tmp")
        shutil.copyfile(pdf, temporary_pdf)
        os.replace(temporary_pdf, output)
        synctex_available = any(
            candidate.is_file()
            for candidate in (
                self.compile_root / f"{self.source.stem}.synctex.gz",
                self.compile_root / f"{self.source.stem}.synctex",
            )
        )
        return completed, log, diagnostics, synctex_available

    def _compile_target(self) -> str:
        """The file latexmk compiles: the primary file, or in draft mode a wrapper that inputs it."""
        if self.compile_mode != "draft":
            return "./" + self.primary_file
        draft_wrapper = self.compile_root / "portal-draft.tex"
        content = (
            "\\PassOptionsToPackage{draft}{graphicx}\n"
            f"\\input{{\\detokenize{{{self.primary_file}}}}}\n"
        )
        if not draft_wrapper.is_file() or draft_wrapper.read_text(encoding="utf-8") != content:
            draft_wrapper.write_text(content, encoding="utf-8")
        return str(draft_wrapper)

    def _command(self, compile_target: str) -> list[str]:
        settings = self.settings
        command = [
            article_toolchain._latexmk_command(),
            "-norc",
            "-f" if settings["error_handling"] == "continue" else "-f-",
            str(self.compiler_config["latexmk_flag"]),
            "-synctex=1",
            "-interaction=nonstopmode",
            "-file-line-error",
            # Keep relative .bib/.bst paths anchored at the manuscript, including
            # ../ references; modern BibTeX resolves included aux paths correctly.
            "-bibtexfudge-",
        ]
        if self.rerun_failed_build:
            command.append("-g")
        project_latexmkrc = self.source_root / ".latexmkrc"
        if settings["use_project_latexmkrc"] and project_latexmkrc.is_file():
            command.extend(["-r", str(project_latexmkrc)])
        engine_flags: list[str] = []
        if settings["error_handling"] == "stop":
            engine_flags.append("-halt-on-error")
        if settings["shell_escape"]:
            engine_flags.append("-shell-escape")
        else:
            engine_flags.append("-no-shell-escape")
        if engine_flags:
            engine = article_toolchain._tex_which(self.compiler) or self.compiler
            command.append(
                f"{self.compiler_config['engine_option']}={engine} "
                f"{' '.join(engine_flags)} %O %S"
            )
        if self.compile_mode == "draft":
            command.append(f"-jobname={self.source.stem}")
        command.extend(
            [
                f"-outdir={self.compile_root}",
                compile_target,
            ]
        )
        return command

    def _report(
        self, status: str, *, return_code: int | None, diagnostics: list[dict[str, Any]], log: str,
        finished: bool = True, **details: Any,
    ) -> dict[str, Any]:
        return self.save_report(
            {
                "status": status,
                "started_at": self.started_at,
                "compiled_at": utc_now_iso() if finished else "",
                "compiler": self.compiler,
                "compiler_label": self.compiler_config["label"],
                "primary_file": self.primary_file,
                "compile_mode": self.compile_mode,
                "return_code": return_code,
                "duration_ms": int((time.monotonic() - self.started) * 1000) if finished else 0,
                "diagnostics": diagnostics,
                "log": log,
                **details,
            },
        )

    def environment(self) -> dict[str, str]:
        environment = _controlled_article_build_environment(self.source_root)
        # BibTeX stays in the source directory but writes its .bbl/.blg beside
        # the absolute aux path. Permit only this output root under TeX's
        # paranoid write policy; do not relax openout_any for the whole process.
        environment["TEXMFOUTPUT"] = str(self.compile_root)
        environment["openout_any"] = "p"
        return environment


class _ArticleBuild(LatexBuild):
    """Resolve Article identity/settings and retain the Article report contract."""

    def __init__(self, article_id: str, *, build_root: str | Path | None, from_scratch: bool) -> None:
        self.article_id = article_id
        _record, _root_item, source_root = article_registry._article_source_context(article_id)
        super().__init__(
            build_id=article_id, source_root=source_root,
            settings=article_settings.load_article_settings(article_id, build_root=build_root),
            output=article_build_results.article_build_pdf_path(article_id, build_root=build_root),
            cache_root=article_build_results.article_build_cache_path(article_id, build_root=build_root),
            from_scratch=from_scratch,
            load_report=lambda: article_build_results.load_article_build_report(article_id, build_root=build_root),
            save_report=lambda report: article_build_results._save_article_build_report(article_id, report, build_root=build_root),
        )

    def inspect_preflight(self) -> dict[str, Any]:
        return {**super().inspect_preflight(), "id": self.article_id}
