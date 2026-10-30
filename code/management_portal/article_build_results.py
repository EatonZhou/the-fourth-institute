"""What an article build leaves: the compiled PDF, the build cache, the parsed LaTeX
diagnostics and the stored build report.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from management_common.portal_paths import path as portal_path
from management_common.product_environment import read_product_env
from management_portal import article_build_store
from management_portal.resources import get_resource_service


# Compiled PDFs and LaTeX intermediates are a cache: compiling again rebuilds them.
DEFAULT_BUILD_ROOT = Path(
    read_product_env("PORTAL_ARTICLE_BUILD_ROOT", str(portal_path("staging_root") / "shared" / "portal_cache" / "article_builds"))
).expanduser()


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
    return path


def _diagnostic_severity(message: str) -> str:
    lowered = message.casefold()
    if any(
        token in lowered
        for token in (
            "error",
            "emergency stop",
            "fatal",
            "misplaced",
            "undefined control sequence",
            "runaway argument",
            "missing $",
            "extra alignment tab",
            "file ended while scanning",
        )
    ):
        return "error"
    if any(
        token in lowered
        for token in ("warning", "overfull", "underfull", "undefined")
    ):
        return "warning"
    return "info"


def _diagnostic_category(message: str) -> str:
    lowered = message.casefold()
    if any(token in lowered for token in ("not found", "missing input file", ".sty", ".cls")):
        return "dependency"
    if any(token in lowered for token in ("font", "fontspec", "typeface")):
        return "font"
    if any(token in lowered for token in ("citation", "reference", "multiply defined", "rerun")):
        return "reference"
    if any(token in lowered for token in ("overfull", "underfull", "badness")):
        return "layout"
    if any(token in lowered for token in ("shell escape", "write18", "restricted")):
        return "security"
    if any(token in lowered for token in ("latexmk", "compiler", "command not found")):
        return "environment"
    return "source"


def parse_latex_diagnostics(log: str, *, limit: int = 240) -> list[dict[str, Any]]:
    lines = str(log or "").splitlines()
    diagnostics: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, str]] = set()

    def add(message: str, severity: str = "", file_name: str = "", line: int = 0) -> None:
        clean_message = re.sub(r"\s+", " ", str(message or "")).strip()
        if not clean_message:
            return
        clean_file = str(file_name or "").strip().removeprefix("./")
        clean_severity = severity or _diagnostic_severity(clean_message)
        key = (clean_severity, clean_file, int(line or 0), clean_message)
        if key in seen:
            return
        seen.add(key)
        diagnostics.append(
            {
                "severity": clean_severity,
                "category": _diagnostic_category(clean_message),
                "message": clean_message[:1200],
                "file": clean_file,
                "line": int(line or 0),
            }
        )

    location_pattern = re.compile(
        r"^(?P<file>(?:\./)?[^:\r\n]+):(?P<line>[0-9]+):\s*(?P<message>.+)$"
    )
    warning_pattern = re.compile(
        r"^(?:LaTeX|Package\s+\S+|Class\s+\S+|pdfTeX)\s+Warning:\s*(.+)$",
        re.IGNORECASE,
    )
    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            continue
        location = location_pattern.match(line)
        if location:
            message = location.group("message").strip()
            severity = _diagnostic_severity(message)
            if severity != "info" or any(
                token in message.casefold()
                for token in ("missing", "not found", "undefined", "multiply defined")
            ):
                add(
                    message,
                    severity,
                    location.group("file"),
                    int(location.group("line")),
                )
            continue
        if line.startswith("!"):
            file_name = ""
            line_number = 0
            for nearby in lines[index + 1 : index + 7]:
                located = location_pattern.match(nearby.strip())
                if located:
                    file_name = located.group("file")
                    line_number = int(located.group("line"))
                    break
                latex_line = re.search(r"\bl\.([0-9]+)\b", nearby)
                if latex_line:
                    line_number = int(latex_line.group(1))
            add(line.lstrip("! ").strip(), "error", file_name, line_number)
        else:
            warning = warning_pattern.match(line)
            if warning:
                add(warning.group(1), "warning")
            elif line.startswith(("Overfull ", "Underfull ")):
                add(line, "warning")
            elif "Missing input file" in line or re.search(
                r"(?:LaTeX Error:\s*)?File\s+[`'][^`']+['`]\s+not found", line, re.IGNORECASE
            ):
                add(line, "error")
        if len(diagnostics) >= limit:
            break
    return diagnostics


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
