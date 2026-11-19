"""LaTeX Repo previews compile disposable sources through the Article build owner."""
from pathlib import Path
import subprocess
from urllib.parse import urlparse

import pytest

from management_common.file_lock import try_lock_descriptor
from management_common import latex_toolchain as article_toolchain
from management_portal import http_repo, repo_workspace_latex as latex


REPO = "repo_111111111111"


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "docs").mkdir()
    (root / "docs/main.tex").write_text(
        "\\documentclass{article}\n\\begin{document}Hello.\\end{document}\n"
    )
    monkeypatch.setattr(latex, "_source_root", lambda _id: ({}, root))
    monkeypatch.setattr(latex, "DEFAULT_BUILD_ROOT", tmp_path / "cache")
    return root


def _tex_available():
    if not article_toolchain._tex_which("latexmk") or not article_toolchain._tex_which("pdflatex"):
        pytest.skip("latexmk/pdfLaTeX required")


def test_real_relative_sources_graphics_bibliography_incremental_and_failure(project):
    _tex_available()
    (project / "docs/parts").mkdir()
    (project / "assets").mkdir()
    (project / "docs/parts/body.tex").write_text("\\section{Body} Refer to \\cite{sample}.\n")
    (project / "references.bib").write_text(
        "@book{sample,author={A. Writer},title={A Book},publisher={Press},year={2020}}\n"
    )
    # Small, generated image fixture with no external dependency.
    import struct
    import zlib
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    (project / "assets/figure.png").write_bytes(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b"")
    )
    source = project / "docs/main.tex"
    source.write_text(
        "\\documentclass{article}\n\\usepackage{graphicx}\n\\begin{document}\n"
        "\\include{parts/body}\n\\includegraphics[width=1cm]{../assets/figure.png}\n"
        "\\bibliographystyle{plain}\n\\bibliography{../references}\n\\end{document}\n"
    )
    original = {str(p.relative_to(project)): p.read_bytes() for p in project.rglob("*") if p.is_file()}
    first = latex.compile_repo_latex(REPO, "docs/main.tex", timeout_seconds=45)
    assert first["status"] == "success", first["log"]
    assert first["primary_file"] == "docs/main.tex" and first["pdf_available"]
    output = latex.repo_latex_pdf(REPO, "docs/main.tex")
    assert output.read_bytes().startswith(b"%PDF")
    assert not output.is_relative_to(project)
    cached = output.parent / "build/normal/main.pdf"
    first_mtime = cached.stat().st_mtime_ns
    repeat = latex.compile_repo_latex(REPO, "docs/main.tex", timeout_seconds=45)
    assert "Nothing to do" in repeat["log"] and cached.stat().st_mtime_ns == first_mtime
    (project / "docs/parts/body.tex").write_text("\\section{Changed body} Changed \\cite{sample}.\n")
    changed = latex.compile_repo_latex(REPO, "docs/main.tex", timeout_seconds=45)
    assert changed["status"] == "success" and cached.stat().st_mtime_ns != first_mtime
    preserved_pdf = output.read_bytes()
    source.write_text(source.read_text().replace("\\end{document}", "\\UndefinedPortalCommand\n\\end{document}"))
    with pytest.raises(latex.RepoLatexError) as failure:
        latex.compile_repo_latex(REPO, "docs/main.tex", timeout_seconds=45)
    report = failure.value.report
    assert report["status"] == "error" and report["counts"]["error"] > 0
    assert report["pdf_available"] and output.read_bytes() == preserved_pdf
    assert any(d["file"] == "docs/main.tex" for d in report["diagnostics"])
    assert set(original) == {str(p.relative_to(project)) for p in project.rglob("*") if p.is_file()}
    assert (project / "assets/figure.png").read_bytes() == original["assets/figure.png"]


def test_sources_stay_in_checkout_and_compiler_is_enumerated(project, tmp_path):
    outside = tmp_path / "outside.tex"
    outside.write_text("secret")
    (project / "linked.tex").symlink_to(outside)
    for path in ("../outside.tex", str(outside), ".git/config.tex", "linked.tex"):
        with pytest.raises(ValueError):
            latex.repo_latex_status(REPO, path)
    with pytest.raises(ValueError, match="compiler"):
        latex.repo_latex_status(REPO, "docs/main.tex", compiler="pdflatex; touch pwned")
    assert latex.repo_latex_status(REPO, "docs/main.tex")["status"] == "idle"
    with pytest.raises(FileNotFoundError):
        latex.repo_latex_pdf(REPO, "docs/main.tex")


def test_build_cache_identity_and_nonblocking_cross_process_lock(project):
    first = latex._Preview(REPO, "docs/main.tex")
    second = latex._Preview(REPO, "docs/main.tex", "xelatex")
    (project / "other.tex").write_text((project / "docs/main.tex").read_text())
    third = latex._Preview(REPO, "other.tex")
    assert len({p.root for p in (first, second, third)}) == 3
    first.root.mkdir(parents=True)
    with (first.root / ".build.lock").open("a+b") as lock:
        assert try_lock_descriptor(lock.fileno())
        with pytest.raises(latex.RepoWorkspaceConflict, match="already compiling"):
            latex.compile_repo_latex(REPO, "docs/main.tex")


def test_project_rc_disabled_and_dash_filename_never_becomes_option(project, monkeypatch):
    _tex_available()
    (project / "-shell-escape.tex").write_text((project / "docs/main.tex").read_text())
    (project / ".latexmkrc").write_text("die 'project rc must not run';\n")
    commands = []
    def run(_id, command, *, cwd, **kwargs):
        commands.append(command)
        assert cwd != project
        out = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("-outdir=")))
        (out / "-shell-escape.pdf").write_bytes(b"%PDF fixture")
        return subprocess.CompletedProcess(command, 0, "OK")
    monkeypatch.setattr(latex._BUILDS, "run", run)
    latex.compile_repo_latex(REPO, "-shell-escape.tex")
    assert commands[0][-1] == "./-shell-escape.tex"
    assert "-norc" in commands[0] and "-r" not in commands[0]
    assert any("-no-shell-escape" in arg for arg in commands[0])


def test_http_rechecks_viewer_and_contributor_for_cache_and_compile(project, monkeypatch):
    allowed = {"read": True, "write": False}
    def read(_id, actor):
        assert actor == "current-principal"
        if not allowed["read"]:
            raise PermissionError("read denied")
    api = http_repo.RepoHttp(
        resource_service=lambda: None, read_json=lambda body: body, require_resource=read,
        notify_index=lambda **kwargs: pytest.fail("compilation must not update Repo index"),
        respond=lambda req, status, body: (status, body),
        exception_response=lambda req, exc: (_ for _ in ()).throw(exc),
    )
    monkeypatch.setattr(http_repo.permission_system, "allows", lambda *args: allowed["write"])
    endpoint = urlparse("/api/repos/x/workspace/latex?path=docs/main.tex")
    params = {"repo_id": REPO, "action": "latex"}
    assert api.workspace_get({}, endpoint, "current-principal", params)[0] == 200
    monkeypatch.setattr(latex, "compile_repo_latex", lambda *args, **kwargs: {"status": "success"})
    assert api.workspace_post({"path": "docs/main.tex"}, endpoint, "current-principal", params)[0] == 403
    allowed["write"] = True
    assert api.workspace_post({"path": "docs/main.tex"}, endpoint, "current-principal", params) == (200, {"status": "success"})
    def fail(*args, **kwargs):
        raise latex.RepoLatexError({"status": "error", "log": "diagnostic log"})
    monkeypatch.setattr(latex, "compile_repo_latex", fail)
    result = api.workspace_post({"path": "docs/main.tex"}, endpoint, "current-principal", params)
    assert result[0] == 422 and result[1]["report"]["log"] == "diagnostic log"
    allowed["read"] = False
    assert api.workspace_get({}, endpoint, "current-principal", params)[0] == 403
    assert api.workspace_get({}, endpoint, "current-principal", {**params, "action": "latex-pdf"})[0] == 403
