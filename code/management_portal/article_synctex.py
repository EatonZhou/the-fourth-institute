"""SyncTeX between an article's sources and its compiled PDF."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from management_common.text_values import stripped_text
from management_portal import (
    article_build_results,
    article_registry,
    article_settings,
    article_tex,
    article_toolchain,
)


def _article_synctex_artifacts(
    article_id: str,
    *,
    build_root: str | Path | None = None,
) -> tuple[Path, Path, Path, str]:
    _record, _root_item, article_root = article_registry._article_source_context(
        article_id
    )
    settings = article_settings.load_article_settings(
        article_id, build_root=build_root
    )
    primary_file = article_tex._safe_relative_path(stripped_text(settings.get("primary_file")))
    mode = stripped_text(settings.get("compile_mode")).casefold() or "normal"
    compile_root = article_build_results.article_build_cache_path(
        article_id, build_root=build_root
    ) / mode
    stem = Path(primary_file).stem
    pdf = compile_root / f"{stem}.pdf"
    synctex = compile_root / f"{stem}.synctex.gz"
    if not synctex.is_file():
        uncompressed = compile_root / f"{stem}.synctex"
        if uncompressed.is_file():
            synctex = uncompressed
    if not pdf.is_file() or not synctex.is_file():
        raise FileNotFoundError(
            "SyncTeX data is unavailable; recompile the Article to generate it"
        )
    return article_root, pdf, synctex, primary_file


def _synctex_records(output: str, *, start_key: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for raw_line in str(output or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("SyncTeX result"):
            continue
        key, separator, value = line.partition(":")
        if not separator:
            continue
        clean_key = key.strip()
        if clean_key == start_key and current:
            records.append(current)
            current = {}
        current[clean_key] = value.strip()
    if current:
        records.append(current)
    return records


def _synctex_number(value: object, *, integer: bool = False) -> int | float:
    try:
        number = float(str(value or "0").strip())
    except ValueError:
        number = 0.0
    return int(number) if integer else number


def article_source_to_pdf(
    article_id: str,
    *,
    path: str,
    line: int,
    column: int = 1,
    page_hint: int = 0,
    build_root: str | Path | None = None,
) -> dict[str, Any]:
    article_root, pdf, synctex, _primary = _article_synctex_artifacts(
        article_id, build_root=build_root
    )
    _root, source = article_registry._article_target_path(
        article_id, path
    )
    if not source.is_file():
        raise FileNotFoundError(source)
    line_number = max(1, int(line or 1))
    column_number = max(1, int(column or 1))
    _page_hint = max(0, int(page_hint or 0))
    source_name = source.relative_to(article_root).as_posix()
    # TeX Live's currently deployed SyncTeX 1.5 documents page hints but
    # interprets the optional fourth field as part of the input tag. Keeping
    # the stable three-field form works across both old and new clients.
    input_spec = f"{line_number}:{column_number}:{source_name}"
    completed = subprocess.run(
        [
            article_toolchain._synctex_command(),
            "view",
            "-i",
            input_spec,
            "-o",
            str(pdf),
            "-d",
            str(synctex.parent),
        ],
        cwd=article_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        timeout=24,
        check=False,
    )
    records = _synctex_records(completed.stdout, start_key="Page")
    candidates = [
        {
            "page": _synctex_number(item.get("Page"), integer=True),
            "x": _synctex_number(item.get("x")),
            "y": _synctex_number(item.get("y")),
            "h": _synctex_number(item.get("h")),
            "v": _synctex_number(item.get("v")),
            "width": _synctex_number(item.get("W")),
            "height": abs(_synctex_number(item.get("H"))),
        }
        for item in records
        if _synctex_number(item.get("Page"), integer=True) > 0
    ]
    if completed.returncode != 0 or not candidates:
        details = re.sub(r"\s+", " ", str(completed.stdout or "")).strip()
        raise LookupError(details or "No PDF position corresponds to this source line")
    return {
        "schema": "tfi.article-synctex.v1",
        "id": article_id,
        "direction": "source-to-pdf",
        "source": {
            "path": source_name,
            "line": line_number,
            "column": column_number,
        },
        "match": candidates[0],
        "candidates": candidates[:20],
    }


def article_pdf_to_source(
    article_id: str,
    *,
    page: int,
    x: float,
    y: float,
    build_root: str | Path | None = None,
) -> dict[str, Any]:
    article_root, pdf, synctex, _primary = _article_synctex_artifacts(
        article_id, build_root=build_root
    )
    page_number = max(1, int(page or 1))
    x_point = max(0.0, float(x or 0))
    y_point = max(0.0, float(y or 0))
    completed = subprocess.run(
        [
            article_toolchain._synctex_command(),
            "edit",
            "-o",
            f"{page_number}:{x_point:.4f}:{y_point:.4f}:{pdf}",
            "-d",
            str(synctex.parent),
        ],
        cwd=article_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        timeout=24,
        check=False,
    )
    records = _synctex_records(completed.stdout, start_key="Input")
    candidates: list[dict[str, Any]] = []
    for item in records:
        raw_input = stripped_text(item.get("Input"))
        if not raw_input:
            continue
        input_path = Path(raw_input)
        source = (
            input_path.resolve(strict=False)
            if input_path.is_absolute()
            else (article_root / input_path).resolve(strict=False)
        )
        try:
            relative = source.relative_to(article_root).as_posix()
        except ValueError:
            continue
        candidates.append(
            {
                "path": relative,
                "line": max(1, _synctex_number(item.get("Line"), integer=True)),
                "column": max(
                    1,
                    _synctex_number(item.get("Column"), integer=True) + 1,
                ),
            }
        )
    if completed.returncode != 0 or not candidates:
        details = re.sub(r"\s+", " ", str(completed.stdout or "")).strip()
        raise LookupError(details or "No source position corresponds to this PDF point")
    return {
        "schema": "tfi.article-synctex.v1",
        "id": article_id,
        "direction": "pdf-to-source",
        "pdf": {"page": page_number, "x": x_point, "y": y_point},
        "match": candidates[0],
        "candidates": candidates[:20],
    }
