"""Repo and Article share TeX mechanics without sharing resource authority."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_repo_latex_does_not_import_article_implementation():
    source = ROOT / "management_portal/repo_workspace_latex.py"
    tree = ast.parse(source.read_text())
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "management_portal":
                imports.extend(alias.name for alias in node.names)
            elif (node.module or "").startswith("management_portal."):
                imports.append(node.module.removeprefix("management_portal."))
        elif isinstance(node, ast.Import):
            imports.extend(alias.name.removeprefix("management_portal.") for alias in node.names)
    assert not [name for name in imports if name.startswith("article_")]


def test_shared_latex_mechanics_have_no_portal_authority():
    paths = sorted((ROOT / "management_common").glob("latex_*.py"))
    assert paths, "shared TeX mechanism owner is missing"
    for path in paths:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("management_portal"), path.name
            elif isinstance(node, ast.Import):
                assert not any(alias.name.startswith("management_portal") for alias in node.names), path.name


def test_article_and_repo_build_state_is_owned_separately(monkeypatch):
    from types import SimpleNamespace
    from management_portal import article_build, repo_workspace_latex
    monkeypatch.setattr(article_build, "get_resource_service", lambda: SimpleNamespace(is_valid_id=lambda *args: True))
    identity = "article_0123456789ab"
    article_build._begin_article_build(identity)
    repo_workspace_latex._BUILDS.begin(identity)
    try:
        assert article_build.stop_article_compile(identity)["stop_requested"]
        assert identity in article_build._STOP_REQUESTED_ARTICLE_BUILDS
        assert identity not in repo_workspace_latex._BUILDS.stop_requested
        with pytest.raises(RuntimeError, match="already running"):
            article_build._begin_article_build(identity)
        repo_workspace_latex._BUILDS.finish(identity)
        assert identity in article_build._RUNNING_ARTICLE_BUILDS
    finally:
        article_build._finish_article_build(identity)
        repo_workspace_latex._BUILDS.finish(identity)


def test_shared_preflight_uses_files_without_resource_lookup(tmp_path, monkeypatch):
    from management_common import latex_profile, latex_toolchain
    from management_portal import article_build_profile
    (tmp_path / "main.tex").write_text("\\documentclass{article}\n\\begin{document}Hi\\end{document}")
    monkeypatch.setattr(latex_toolchain, "_tex_which", lambda _: "/tools/tex")
    monkeypatch.setattr(latex_toolchain, "_kpsewhich_file", lambda _: "/tools/article.cls")
    monkeypatch.setattr(latex_toolchain, "latex_environment", lambda: {"profile_id": "fixture"})
    def forbidden(*args, **kwargs):
        raise AssertionError("shared TeX preflight entered Article resource authority")
    monkeypatch.setattr(article_build_profile.repo_backend, "project_ids_for_path", forbidden)
    result = latex_profile.inspect_build_profile(tmp_path, primary_file="main.tex")
    assert result["status"] == "ready"
    assert result["primary_file"] == "main.tex"
    assert "project_ids" not in result


def test_shared_engine_rejects_source_escape_before_runtime(tmp_path):
    from management_common.latex_build import BuildRuntime, LatexBuild
    root = tmp_path / "source"
    root.mkdir()
    external = tmp_path / "outside.tex"
    external.write_text("\\documentclass{article}")
    (root / "escape.tex").symlink_to(external)
    runtime = BuildRuntime()
    with pytest.raises(ValueError, match="escapes"):
        LatexBuild(runtime=runtime, build_id="preview", source_root=root,
                   settings={"primary_file": "escape.tex", "compiler": "pdflatex"},
                   output=tmp_path / "out.pdf", cache_root=tmp_path / "cache",
                   load_report=lambda: {}, save_report=lambda report: report)
    assert not runtime.running


def test_repo_preflight_preserves_report_fields_without_article_project_query(tmp_path, monkeypatch):
    from management_portal import repo_workspace_latex
    from management_common import latex_profile
    monkeypatch.setattr(latex_profile, "inspect_build_profile", lambda *args, **kwargs: {
        "schema": "tfi.article-build-preflight.v1", "status": "ready", "primary_file": "main.tex",
    })
    build = repo_workspace_latex._RepoBuild.__new__(repo_workspace_latex._RepoBuild)
    build.source_root = tmp_path
    build.primary_file = "main.tex"
    build.compiler = "pdflatex"
    assert build.inspect_preflight() == {
        "schema": "tfi.article-build-preflight.v1", "status": "ready", "primary_file": "main.tex", "project_ids": [],
    }


def test_article_compile_report_preserves_project_and_toolchain_context(tmp_path, monkeypatch):
    from management_portal import (
        article_build, article_build_results, article_registration, article_toolchain,
    )
    from tests.cases.registry_support import write_projects

    project = tmp_path / "AssociatedProject"
    source = project / "article"
    source.mkdir(parents=True)
    (source / "main.tex").write_text(
        "\\documentclass{article}\n\\title{Associated manuscript}\n"
        "\\begin{document}Project context\\end{document}\n"
    )
    write_projects({"projects": [{"id": "AssociatedProject", "path": str(project), "status": "active"}]})
    record = article_registration.register_article(source)
    calls = []
    original_environment = article_toolchain.latex_environment

    def environment():
        calls.append(True)
        return original_environment()

    monkeypatch.setattr(article_toolchain, "latex_environment", environment)
    build_root = tmp_path / "build"
    result = article_build.compile_article_pdf(record["id"], build_root=build_root, from_scratch=True)
    report = article_build_results.load_article_build_report(record["id"], build_root=build_root)
    assert report["status"] == "success"
    assert Path(result["pdf_path"]).read_bytes().startswith(b"%PDF")
    assert report["preflight"]["project_ids"] == ["AssociatedProject"]
    assert report["preflight"]["id"] == record["id"]
    assert report["preflight"]["primary_file"] == "main.tex"
    assert calls, "Article compilation must use its own toolchain preflight seam"
