"""A paper's figures: the figure records and extracting them from its PDF."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import re
import secrets
import shutil
from typing import Any
from urllib.parse import quote

from PIL import Image

from management_portal.paper_figure_extraction import (
    _crop_figure_above_caption, _extract_embedded_pdf_images, _extract_figure_caption_blocks,
    _figure_number_sort_key, _figure_pixel_digest, _prepare_figure_caption_blocks, _render_pdf_page_png,
)
from management_portal.paper_records import _now
from management_portal.paper_store import (
    LOCK, _load_json, _local_pdf_for_paper, _notify_paper_projection, _paper_bundle_path, _paper_by_id,
    _paper_job_store_lock, _paper_root, _paper_store_file, _safe_file_path, _write_json,
)


def _figures_json_path(paper: dict) -> Path | None:
    return _paper_store_file(paper or {}, "figures")


def _figures_dir(paper: dict) -> Path | None:
    path = _paper_store_file(paper or {}, "figures_dir")
    if path:
        return path
    root = str((paper or {}).get("_store_root") or "").strip()
    return Path(root) / "figures" if root else None


def _asset_version_query(item: dict[str, Any]) -> str:
    version = str((item or {}).get("updated_at") or (item or {}).get("created_at") or "").strip()
    return f"&v={quote(version)}" if version else ""


def _figure_public(figure: dict, paper_id: str) -> dict:
    figure_id = str(figure.get("figure_id") or "")
    return {
        **figure,
        "image_url": f"/paper/api/asset?id={paper_id}&kind=figure&figure_id={figure_id}{_asset_version_query(figure)}" if figure_id else "",
        "generated": [
            {
                **item,
                "image_url": f"/paper/api/asset?id={paper_id}&kind=generated_figure&figure_id={figure_id}&generated_id={item.get('generated_id')}{_asset_version_query(item)}"
                if item.get("image_rel_path")
                else "",
            }
            for item in (figure.get("generated") or [])
            if isinstance(item, dict)
        ],
    }


def _load_figures(paper: dict) -> list[dict]:
    path = _figures_json_path(paper)
    data = _load_json(path, []) if path else []
    return data if isinstance(data, list) else []


def _write_figures(paper: dict, figures: list[dict]) -> None:
    path = _figures_json_path(paper)
    if not path:
        raise ValueError("paper has no figure store")
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, figures)
    _notify_paper_projection(
        paper.get("id"),
        fields=["figures"],
    )


@contextmanager
def _figure_update(paper: dict):
    path = _figures_json_path(paper)
    if path is None:
        raise ValueError("Paper figure artifact location is missing")
    with LOCK, _paper_job_store_lock(path):
        figures = _load_figures(paper)
        yield figures
        _write_figures(paper, figures)


def _dedupe_figures_by_label(figures: list[dict]) -> list[dict]:
    labels = {str(figure.get("label") or "").strip().lower() for figure in figures}
    best_by_label: dict[str, dict] = {}
    dropped: list[dict] = []
    for figure in figures:
        label = str(figure.get("label") or "").strip().lower()
        suffix_match = re.match(r"^(figure\s+)([0-9]+)[a-z]$", label, flags=re.I)
        if suffix_match and f"{suffix_match.group(1)}{suffix_match.group(2)}".lower() in labels:
            dropped.append(figure)
            continue
        key = label or str(figure.get("figure_id") or "")
        current = best_by_label.get(key)
        if not current:
            best_by_label[key] = figure
            continue
        figure_caption_score = tuple(figure.get("_caption_score") or ())
        current_caption_score = tuple(current.get("_caption_score") or ())
        figure_score = (
            figure_caption_score,
            int(figure.get("width") or 0) * int(figure.get("height") or 0),
            int(figure.get("size_bytes") or 0),
        )
        current_score = (
            current_caption_score,
            int(current.get("width") or 0) * int(current.get("height") or 0),
            int(current.get("size_bytes") or 0),
        )
        if figure_score > current_score:
            dropped.append(current)
            best_by_label[key] = figure
        else:
            dropped.append(figure)
    for figure in dropped:
        path = _safe_file_path(str(figure.get("_path_for_cleanup") or ""))
        if path and path.is_file():
            try:
                path.unlink()
            except OSError:
                pass
    kept = sorted(
        best_by_label.values(),
        key=lambda item: (
            _figure_number_sort_key(re.sub(r"^\s*figure\s+", "", str(item.get("label") or ""), flags=re.I)),
            int(item.get("page") or 0),
            int(item.get("index") or 0),
        ),
    )
    for index, figure in enumerate(kept, start=1):
        figure["index"] = index
        figure["figure_id"] = f"fig-{index:03d}"
        figure.pop("_path_for_cleanup", None)
        figure.pop("_caption_score", None)
    return kept


def _extracted_figure_key(figure: dict, paper_root: Path) -> tuple[str, ...] | None:
    """Only verified source identity, never a display position, carries annotations."""
    mode = str(figure.get("extraction_mode") or "")
    if mode == "caption_region_crop":
        label = re.sub(r"\s+", " ", str(figure.get("label") or "")).strip().casefold()
        caption = re.sub(r"\s+", " ", str(figure.get("caption") or "")).strip()
        return (mode, label, caption) if label and caption else None
    if mode == "embedded_pdf_image":
        path = _paper_bundle_path(paper_root, str(figure.get("image_rel_path") or ""))
        if path and path.is_file():
            try:
                return (mode, _figure_pixel_digest(path))
            except (OSError, ValueError):
                pass
    return None


def _reconcile_extracted_figures(figures: list[dict], existing: list[dict], paper_root: Path) -> list[dict]:
    by_key: dict[tuple[str, ...], list[dict]] = {}
    used_ids = {str(item.get("figure_id") or "") for item in existing}
    for old in existing:
        key = _extracted_figure_key(old, paper_root)
        if key is not None:
            by_key.setdefault(key, []).append(old)
    reconciled = []
    for index, candidate in enumerate(figures, start=1):
        key = _extracted_figure_key(candidate, paper_root)
        matches = by_key.pop(key, []) if key is not None else []
        old = matches[0] if len(matches) == 1 and matches[0].get("figure_id") else {}
        if old:
            figure_id = old["figure_id"]
        else:
            # Local opaque identity survives reordering and is never allocated
            # from a recycled display position after an empty extraction.
            figure_id = f"fig-{secrets.token_hex(12)}"
            while figure_id in used_ids:
                figure_id = f"fig-{secrets.token_hex(12)}"
        used_ids.add(figure_id)
        merged = {
            "explanation": "", "generated": [], **old, **candidate,
            "figure_id": figure_id, "index": index,
            "created_by": old.get("created_by", candidate["created_by"]),
            "created_at": old.get("created_at", candidate["created_at"]),
        }
        if old and candidate.get("extraction_mode") == "embedded_pdf_image":
            merged["caption"] = str(old.get("caption") or "")
        reconciled.append(merged)
    return reconciled


def _render_extracted_figures(pdf_path: Path, extracted_dir: Path, paper_root: Path, paper_id: str, actor: str) -> list[dict]:
    """Build an unreferenced generation; the caller owns its commit and cleanup."""
    captions = _prepare_figure_caption_blocks(_extract_figure_caption_blocks(pdf_path))
    figures: list[dict] = []
    page_images: dict[int, Image.Image] = {}
    page_image_paths: list[Path] = []
    try:
        for caption in captions:
            page = int(caption.get("page") or 0)
            if page <= 0:
                continue
            if page not in page_images:
                page_path = _render_pdf_page_png(pdf_path, page, extracted_dir / f"_page-{page:03d}")
                page_image_paths.append(page_path)
                with Image.open(page_path) as page_image:
                    page_images[page] = page_image.convert("RGB")
            cropped = _crop_figure_above_caption(page_images[page], caption)
            if cropped is None:
                continue
            index = len(figures) + 1
            figure_id = f"fig-{index:03d}"
            path = extracted_dir / f"{figure_id}.png"
            with cropped:
                cropped.save(path)
                width, height = cropped.size
            size = path.stat().st_size
            wide_short_figure = width >= 300 and height >= 40 and width / max(1, height) >= 4.0
            minimum_size = 2_000 if wide_short_figure else 4_000
            if width < 120 or (height < 80 and not wide_short_figure) or size < minimum_size:
                path.unlink()
                continue
            figures.append({
                "figure_id": figure_id, "paper_id": paper_id, "index": index,
                "label": f"Figure {str(caption.get('number') or index).strip()}",
                "caption": str(caption.get("caption") or ""), "page": page,
                "width": width, "height": height, "size_bytes": size,
                "extraction_mode": "caption_region_crop",
                "image_rel_path": path.relative_to(paper_root).as_posix(),
                "_path_for_cleanup": str(path), "_caption_score": caption.get("_caption_score") or (),
                "created_by": actor, "created_at": _now(), "updated_at": _now(),
            })
    finally:
        for image in page_images.values():
            image.close()
        for path in page_image_paths:
            path.unlink(missing_ok=True)
    figures = _dedupe_figures_by_label(figures)
    if not figures:
        figures = _extract_embedded_pdf_images(pdf_path, extracted_dir, paper_root, paper_id, actor)
    return figures


def _read_figure_extraction_metadata(path: Path, *, missing_ok: bool = False) -> list[dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if missing_ok:
            return []
        raise
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise ValueError("Paper figure metadata must be a list of objects")
    return payload


def extract_figures(payload: dict, actor: str) -> dict:
    paper_id = str(payload.get("paper_id") or "").strip()
    if not paper_id:
        raise ValueError("paper_id required")
    paper = _paper_by_id(paper_id)
    if not paper:
        raise KeyError("paper not found")
    source_pdf_reference = str(paper.get("pdf_path") or "")
    pdf_path = _local_pdf_for_paper(paper)
    figures_dir = _figures_dir(paper)
    paper_root = _paper_root(paper)
    metadata_path = _figures_json_path(paper)
    if not figures_dir or not paper_root or not metadata_path:
        raise ValueError("paper has no figure store")
    # Serialize extraction per paper without holding the global metadata lock
    # during PDF rendering. Annotation writers remain free to finish meanwhile.
    with _paper_job_store_lock(figures_dir / "extraction"):
        initial_pdf = pdf_path.stat()
        extracted_root = figures_dir / "extracted"
        generation = extracted_root / f"run-{secrets.token_hex(12)}"
        generation.mkdir(parents=True)
        preserve_generation = False
        warning = ""
        try:
            figures = _render_extracted_figures(pdf_path, generation, paper_root, paper_id, actor)
            with LOCK, _paper_job_store_lock(metadata_path):
                current = _paper_by_id(paper_id)
                if not current or _paper_root(current) != paper_root:
                    raise RuntimeError("Paper changed during figure extraction")
                if str(current.get("pdf_path") or "") != source_pdf_reference:
                    raise RuntimeError("PDF changed during figure extraction")
                final_pdf = pdf_path.stat()
                if (initial_pdf.st_ino, initial_pdf.st_size, initial_pdf.st_mtime_ns) != (
                    final_pdf.st_ino, final_pdf.st_size, final_pdf.st_mtime_ns
                ):
                    raise RuntimeError("PDF changed during figure extraction")
                existing = _read_figure_extraction_metadata(metadata_path, missing_ok=True)
                figures = _reconcile_extracted_figures(figures, existing, paper_root)
                # A write or notification failure can happen after the atomic
                # metadata switch. Never delete files with an uncertain commit.
                preserve_generation = True
                try:
                    _write_figures(paper, figures)
                except Exception as exc:
                    persisted = _read_figure_extraction_metadata(metadata_path)
                    if persisted != figures:
                        prefix = generation.relative_to(paper_root).as_posix() + "/"
                        preserve_generation = any(str(item.get("image_rel_path") or "").startswith(prefix) for item in persisted)
                        raise
                    warning = f"Figures persisted; post-write handling reported: {exc}"
                preserve_generation = bool(figures)
                protected = {str(item.get("image_rel_path") or "") for item in figures}
                protected.update(str(image.get("image_rel_path") or "") for item in figures for image in item.get("generated", []))
                for old in existing:
                    rel = str(old.get("image_rel_path") or "")
                    path = _paper_bundle_path(paper_root, rel) if rel else None
                    if path and rel not in protected and path.is_relative_to(extracted_root.resolve()):
                        try:
                            path.unlink(missing_ok=True)
                            if path.parent != extracted_root:
                                path.parent.rmdir()  # remove only empty old generations
                        except OSError:
                            pass
        finally:
            if not preserve_generation:
                shutil.rmtree(generation, ignore_errors=True)
        result = {"figures": [_figure_public(item, paper_id) for item in figures], "count": len(figures)}
        if warning:
            result["persistence_warning"] = warning
        return result
