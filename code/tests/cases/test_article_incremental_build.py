"""Incremental latexmk outputs and failure publication, using disposable sources."""
import subprocess
import time

import pytest

from management_portal import article_build, article_toolchain


@pytest.fixture
def build(tmp_path, monkeypatch):
    # Exercise the build owner without registering or modifying live resources.
    value = article_build._ArticleBuild.__new__(article_build._ArticleBuild)
    value.build_id = "article_111111111111"
    value.source_root = tmp_path / "source"
    value.source_root.mkdir()
    value.primary_file = "main.tex"
    value.source = value.source_root / value.primary_file
    value.source.write_text(
        "\\documentclass{article}\n\\begin{document}Original text.\\end{document}\n"
    )
    value.compiler = "pdflatex"
    value.compiler_config = article_toolchain.SUPPORTED_LATEX_COMPILERS[value.compiler]
    value.build_root = tmp_path / "build"
    value.cache_root = value.build_root / "cache"
    value.output = value.build_root / "published.pdf"
    value.compile_mode = "normal"
    value.compile_root = value.cache_root / value.compile_mode
    value.from_scratch = False
    value.rerun_failed_build = False
    value.preflight = {}
    value.settings = {
        "error_handling": "continue", "shell_escape": False,
        "use_project_latexmkrc": False, "environment_profile": "fixture",
    }
    value.reports = []
    value.previous = {}
    value._report = lambda status, **kwargs: value.reports.append({"status": status, **kwargs})
    value.load_report = lambda: value.previous
    return value


@pytest.mark.parametrize("mode", ["normal", "draft"])
def test_unchanged_latexmk_build_reuses_pdf_and_changed_source_rebuilds(build, monkeypatch, mode):
    if not article_toolchain._tex_which("latexmk") or not article_toolchain._tex_which("pdflatex"):
        pytest.skip("latexmk/pdfLaTeX required")
    build.compile_mode = mode
    build.compile_root = build.cache_root / mode

    def run_command(_id, command, *, cwd, timeout_seconds, env):
        return subprocess.run(command, cwd=cwd, env=env, timeout=timeout_seconds,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    monkeypatch.setattr(article_build, "_run_article_build_command", run_command)
    start = time.monotonic()
    first = build._compile_owned_cache(30)
    first_seconds = time.monotonic() - start
    assert first[0].returncode == 0, first[1]
    pdf = build.compile_root / "main.pdf"
    original_mtime = pdf.stat().st_mtime_ns
    wrapper = build.compile_root / "portal-draft.tex"
    wrapper_mtime = wrapper.stat().st_mtime_ns if mode == "draft" else None
    build.previous = {"status": "success", "return_code": 0}
    start = time.monotonic()
    repeated = build._compile_owned_cache(30)
    repeat_seconds = time.monotonic() - start
    assert repeated[0].returncode == 0, repeated[1]
    assert pdf.stat().st_mtime_ns == original_mtime
    assert "Nothing to do" in repeated[1], repeated[1]
    assert "Run number 1 of rule" not in repeated[1]
    if wrapper_mtime is not None:
        assert wrapper.stat().st_mtime_ns == wrapper_mtime
    print(f"{mode}: initial={first_seconds:.3f}s unchanged={repeat_seconds:.3f}s (isolated small document)")

    build.source.write_text(build.source.read_text().replace("Original text.", "Changed text with additional content."))
    changed = build._compile_owned_cache(30)
    assert changed[0].returncode == 0, changed[1]
    assert "Run number 1 of rule" in changed[1]
    assert pdf.stat().st_mtime_ns != original_mtime
    sentinel = build.compile_root / "sentinel"
    sentinel.write_text("old auxiliary cache")
    build.from_scratch = True
    scratch = build._compile_owned_cache(30)
    assert scratch[0].returncode == 0, scratch[1]
    assert "Run number 1 of rule" in scratch[1]
    assert not sentinel.exists()


def test_failed_command_cannot_publish_cached_pdf_and_retry_is_forced(build, monkeypatch):
    build.compile_root.mkdir(parents=True)
    cached = build.compile_root / "main.pdf"
    cached.write_bytes(b"%PDF old cached result")
    build.output.write_bytes(b"%PDF last published result")
    previous_output = build.output.read_bytes()
    build.previous = {"status": "success", "return_code": 0}
    commands = []

    def failed(_id, command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 12, "Build failed before writing PDF")

    monkeypatch.setattr(article_build, "_run_article_build_command", failed)
    with pytest.raises(RuntimeError, match="LaTeX compilation failed"):
        build._compile_owned_cache(30)
    assert build.output.read_bytes() == previous_output
    assert build.reports[-1]["status"] == "error"
    build.previous = build.reports[-1]

    def recovered(_id, command, **kwargs):
        commands.append(command)
        cached.write_bytes(b"%PDF new output after dependency recovery")
        return subprocess.CompletedProcess(command, 0, "Output written")

    monkeypatch.setattr(article_build, "_run_article_build_command", recovered)
    build._compile_owned_cache(30)
    assert "-g" not in commands[0] and "-g" in commands[1]
    assert build.output.read_bytes() == cached.read_bytes()


def test_continue_mode_publishes_new_diagnostic_pdf(build, monkeypatch):
    build.compile_root.mkdir(parents=True)
    cached = build.compile_root / "main.pdf"
    cached.write_bytes(b"%PDF old")

    def warning(_id, command, **kwargs):
        cached.write_bytes(b"%PDF new with diagnostics")
        return subprocess.CompletedProcess(command, 12, "! Undefined control sequence.")

    monkeypatch.setattr(article_build, "_run_article_build_command", warning)
    result = build._compile_owned_cache(30)
    assert result[0].returncode == 12
    assert build.output.read_bytes() == b"%PDF new with diagnostics"
