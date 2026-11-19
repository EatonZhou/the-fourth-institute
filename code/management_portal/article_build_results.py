"""What an article build leaves: the compiled PDF, the build cache, the parsed LaTeX
diagnostics and the stored build report.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from management_common.portal_paths import path as portal_path
from management_common.latex_diagnostics import parse_latex_diagnostics
from management_portal import article_build_store
from management_portal.resources import get_resource_service


# Compiled PDFs and LaTeX intermediates are a cache: compiling again rebuilds them.
DEFAULT_BUILD_ROOT = portal_path("cache") / "article_builds"


def article_build_pdf_path(
    article_id: str,
    *,
    build_root: str | Path | None = None,
) -> Path:
    if not get_resource_service().is_valid_id(article_id, "article"):
        raise ValueError(f"invalid article id: {article_id}")
    root = Path(build_root or DEFAULT_BUILD_ROOT).expanduser().resolve(strict=False)
    return root / f"{article_id}.pdf"


def article_build_cache_path(
    article_id: str,
    *,
    build_root: str | Path | None = None,
) -> Path:
    if not get_resource_service().is_valid_id(article_id, "article"):
        raise ValueError(f"invalid article id: {article_id}")
    root = Path(build_root or DEFAULT_BUILD_ROOT).expanduser().resolve(strict=False)
    return root / "cache" / article_id


def compiled_article_pdf(
    article_id: str,
    *,
    build_root: str | Path | None = None,
) -> Path:
    path = article_build_pdf_path(article_id, build_root=build_root)
    if not path.is_file():
        raise FileNotFoundError(article_id)
    from management_common.file_retention import cache_file
    from management_common.directory_cleanup_store import DirectoryPendingCleanup
    try:
        with cache_file(path, group='article', owner_id=article_id):
            return path
    except DirectoryPendingCleanup:
        raise FileNotFoundError(article_id) from None


def load_article_build_report(
    article_id: str,
    *,
    build_root: str | Path | None = None,
) -> dict[str, Any]:
    output = article_build_pdf_path(article_id, build_root=build_root)
    report = article_build_store.report(article_id)
    if report is None:
        report = {
            "schema": "tfi.article-build.v1",
            "id": article_id,
            "status": "idle",
            "diagnostics": [],
            "counts": {"all": 0, "error": 0, "warning": 0, "info": 0},
            "log": "",
        }
    report["pdf_available"] = output.is_file()
    if output.is_file():
        report["pdf_size"] = output.stat().st_size
    return report


def _save_article_build_report(
    article_id: str,
    report: dict[str, Any],
    *,
    build_root: str | Path | None = None,
) -> dict[str, Any]:
    diagnostics = (
        report.get("diagnostics")
        if isinstance(report.get("diagnostics"), list)
        else []
    )
    counts = {
        "all": len(diagnostics),
        "error": sum(
            1 for item in diagnostics if item.get("severity") == "error"
        ),
        "warning": sum(
            1 for item in diagnostics if item.get("severity") == "warning"
        ),
        "info": sum(
            1 for item in diagnostics if item.get("severity") == "info"
        ),
    }
    payload = {
        "schema": "tfi.article-build.v1",
        "id": article_id,
        **report,
        "counts": counts,
    }
    article_build_pdf_path(article_id, build_root=build_root)
    article_build_store.save_report(article_id, payload)
    return load_article_build_report(article_id, build_root=build_root)
