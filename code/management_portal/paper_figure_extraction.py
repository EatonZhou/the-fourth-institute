"""Figures in a PDF page: caption blocks, page rendering, crop regions and embedded images."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
import re
from typing import Any
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw

from management_common import process_runtime
from management_portal.paper_pdf import _poppler_command
from management_portal.paper_records import _now


FIGURE_NUMBER_RE = r"[0-9]+(?:\.[0-9]+)*[A-Za-z]?"


def _float_attr(node: ET.Element, name: str, default: float = 0.0) -> float:
    try:
        return float(node.attrib.get(name) or default)
    except (TypeError, ValueError):  # an attribute that is not a number
        return default


def _element_words_text(node: ET.Element) -> str:
    words = []
    for word in node.findall(".//{*}word"):
        text = "".join(word.itertext()).strip()
        if text:
            words.append(text)
    return re.sub(r"\s+", " ", " ".join(words)).strip()


def _line_words(node: ET.Element) -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    for word in node.findall(".//{*}word"):
        text = "".join(word.itertext()).strip()
        if not text:
            continue
        words.append({
            "text": text,
            "x_min": _float_attr(word, "xMin"),
            "x_max": _float_attr(word, "xMax"),
            "y_min": _float_attr(word, "yMin"),
            "y_max": _float_attr(word, "yMax"),
        })
    return words


def _sanitize_xml_1_0_text(text: str) -> str:
    if not text:
        return ""
    return "".join(
        char
        for char in text
        if char in "\t\n\r" or "\x20" <= char <= "\ud7ff" or "\ue000" <= char <= "\ufffd"
    )


def _caption_number_from_text(text: str) -> str:
    match = re.match(rf"^\s*(?i:(?:Fig\.?|F\s*igure))\s*({FIGURE_NUMBER_RE})\s*(?:(?:[:.\-|]|\u2013|\u2014)\s*|(?=[A-Z][A-Za-z0-9]))", str(text or ""))
    return str(match.group(1) or "").strip() if match else ""


def _caption_number_from_words(words: list[dict[str, Any]], index: int) -> str:
    if index >= len(words):
        return ""
    number_word = str(words[index].get("text") or "").strip()
    match = re.match(rf"^({FIGURE_NUMBER_RE})(?P<suffix>[:.\-|]|\u2013|\u2014)?", number_word)
    if not match:
        return ""
    suffix = str(match.group("suffix") or "")
    if suffix:
        return str(match.group(1) or "").strip()
    next_word = str(words[index + 1].get("text") or "").strip() if index + 1 < len(words) else ""
    if re.match(r"^[A-Z][A-Za-z0-9]", next_word):
        return str(match.group(1) or "").strip()
    if next_word in {"|", "-", "\u2013", "\u2014", ":"}:
        return str(match.group(1) or "").strip()
    return ""


def _caption_segments_for_line(line: ET.Element) -> list[dict[str, Any]]:
    words = _line_words(line)
    starts: list[tuple[int, str]] = []
    for index, word in enumerate(words[:-1]):
        text = str(word.get("text") or "").strip()
        if re.fullmatch(r"(?i:Fig\.?|Figure)", text):
            number_index = index + 1
        elif text.upper() == "F" and index + 2 < len(words) and str(words[index + 1].get("text") or "").strip().upper() == "IGURE":
            number_index = index + 2
        else:
            continue
        number = _caption_number_from_words(words, number_index)
        if number:
            starts.append((index, number))
    if not starts:
        return []
    if starts[0][0] != 0 and not any(number.count(".") >= 2 for _, number in starts):
        return []
    segments: list[dict[str, Any]] = []
    for pos, (start_index, number) in enumerate(starts):
        end_index = starts[pos + 1][0] if pos + 1 < len(starts) else len(words)
        selected = words[start_index:end_index]
        if not selected:
            continue
        segments.append({
            "number": number,
            "text": re.sub(r"\s+", " ", " ".join(str(word.get("text") or "") for word in selected)).strip(),
            "inline": start_index > 0,
            "inline_after_caption": start_index > 0 and starts[0][0] == 0,
            "x_min": min(float(word.get("x_min") or 0) for word in selected),
            "x_max": max(float(word.get("x_max") or 0) for word in selected),
            "y_min": min(float(word.get("y_min") or 0) for word in selected),
            "y_max": max(float(word.get("y_max") or 0) for word in selected),
        })
    return segments


def _figure_number_sort_key(value: Any) -> tuple[int, str]:
    text = str(value or "").strip()
    match = re.match(r"^\s*([0-9]+(?:\.[0-9]+)*)([A-Za-z]?)\s*$", text)
    if not match:
        return (10**9, text)
    parts = tuple(f"{int(part):06d}" for part in str(match.group(1) or "0").split("."))
    return (int(str(match.group(1) or "0").split(".")[0]), ".".join(parts) + str(match.group(2) or "").lower())


def _horizontal_overlap_ratio(a: dict[str, Any], b: dict[str, Any]) -> float:
    a0 = float(a.get("x_min") or 0)
    a1 = float(a.get("x_max") or 0)
    b0 = float(b.get("x_min") or 0)
    b1 = float(b.get("x_max") or 0)
    width = min(max(0.0, a1 - a0), max(0.0, b1 - b0))
    if width <= 0:
        return 0.0
    overlap = max(0.0, min(a1, b1) - max(a0, b0))
    return overlap / width if width else 0.0


def _clamp_nearby_caption_columns(captions: list[dict[str, Any]]) -> None:
    by_page: dict[int, list[dict[str, Any]]] = {}
    for caption in captions:
        by_page.setdefault(int(caption.get("page") or 0), []).append(caption)
    for page_captions in by_page.values():
        for caption in page_captions:
            page_width = max(1.0, float(caption.get("page_width") or 0))
            cap_x0 = float(caption.get("x_min") or 0)
            cap_x1 = float(caption.get("x_max") or page_width)
            cap_y0 = float(caption.get("y_min") or 0)
            cap_y1 = float(caption.get("y_max") or 0)
            right_neighbors = [
                other
                for other in page_captions
                if other is not caption
                and float(other.get("x_min") or 0) - cap_x0 >= page_width * 0.08
                and float(other.get("y_max") or 0) >= cap_y0 - 16.0
                and float(other.get("y_min") or 0) <= cap_y1 + 16.0
            ]
            if not right_neighbors:
                continue
            neighbor_x0 = min(float(other.get("x_min") or page_width) for other in right_neighbors)
            if neighbor_x0 - cap_x0 >= page_width * 0.08:
                boundary = neighbor_x0 - 2.0
                if cap_x1 > page_width * 0.50:
                    caption["x_max"] = min(cap_x1, neighbor_x0 - 6.0)
                    tail_match = re.match(r"^(.*?\.)\s+([a-z][A-Za-z0-9\- ]{1,80}\.)$", str(caption.get("caption") or "").strip())
                    if tail_match:
                        caption["caption"] = tail_match.group(1).strip()
                        for other in right_neighbors:
                            if abs(float(other.get("x_min") or 0) - neighbor_x0) <= 4.0:
                                other["caption"] = f"{str(other.get('caption') or '').rstrip()} {tail_match.group(2).strip()}".strip()
                                break
                caption["crop_x_max"] = min(float(caption.get("crop_x_max") or page_width), boundary)
                for other in right_neighbors:
                    if abs(float(other.get("x_min") or 0) - neighbor_x0) <= 4.0:
                        other["crop_x_min"] = max(float(other.get("crop_x_min") or 0), boundary)
        for caption in page_captions:
            crop_x_min = caption.get("crop_x_min")
            crop_x_max = caption.get("crop_x_max")
            if crop_x_min is None and crop_x_max is None:
                continue
            cap_x0 = float(caption.get("x_min") or 0)
            cap_x1 = float(caption.get("x_max") or page_width)
            cap_y0 = float(caption.get("y_min") or 0)
            cap_y1 = float(caption.get("y_max") or 0)
            row_reaches_right_column = any(
                float(other.get("x_min") or 0) >= page_width * 0.45
                and float(other.get("y_max") or 0) >= cap_y0 - 16.0
                and float(other.get("y_min") or 0) <= cap_y1 + 16.0
                for other in page_captions
            )
            caption["panel_x_min"] = float(crop_x_min) if crop_x_min is not None else max(0.0, cap_x0 - 18.0)
            if crop_x_max is not None:
                caption["panel_x_max"] = float(crop_x_max)
            elif row_reaches_right_column:
                caption["panel_x_max"] = max(0.0, page_width - 30.0)
            else:
                caption["panel_x_max"] = page_width * 0.5 - 4.0
        isscc_grid_page = any(
            str(caption.get("number") or "").count(".") >= 2
            and (caption.get("panel_x_min") is not None or caption.get("panel_x_max") is not None)
            for caption in page_captions
        )
        if isscc_grid_page:
            mid = page_width * 0.5
            for caption in page_captions:
                if caption.get("panel_x_min") is not None or caption.get("panel_x_max") is not None:
                    continue
                if str(caption.get("number") or "").count(".") < 2:
                    continue
                cap_x0 = float(caption.get("x_min") or 0)
                cap_x1 = float(caption.get("x_max") or page_width)
                if cap_x0 < mid and cap_x1 < page_width * 0.80:
                    caption["panel_x_min"] = max(0.0, cap_x0 - 18.0)
                    caption["panel_x_max"] = mid + 2.0
                elif cap_x0 >= mid:
                    caption["panel_x_min"] = mid + 2.0
                    caption["panel_x_max"] = page_width - 30.0


def _prepare_figure_caption_blocks(captions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    y_ordered = sorted(
        (dict(item) for item in captions),
        key=lambda item: (
            int(item.get("page") or 0),
            float(item.get("y_min") or 0),
            float(item.get("x_min") or 0),
        ),
    )
    previous_by_page: dict[int, list[dict[str, Any]]] = {}
    for caption in y_ordered:
        page = int(caption.get("page") or 0)
        cap_y = float(caption.get("y_min") or 0)
        top = 36.0
        for previous in previous_by_page.get(page, []):
            prev_y = float(previous.get("y_max") or 0)
            if prev_y >= cap_y:
                continue
            if _horizontal_overlap_ratio(caption, previous) < 0.25:
                continue
            top = max(top, prev_y + 8.0)
        caption["crop_y_min"] = top
        previous_by_page.setdefault(page, []).append(caption)
    _clamp_nearby_caption_columns(y_ordered)
    return sorted(
        y_ordered,
        key=lambda item: (
            _figure_number_sort_key(item.get("number")),
            int(item.get("page") or 0),
            float(item.get("y_min") or 0),
            float(item.get("x_min") or 0),
        ),
    )


def _caption_line_group(lines: list[ET.Element], start_index: int, segment: dict[str, Any] | None = None) -> tuple[str, float, float, float, float]:
    first = lines[start_index]
    if segment:
        texts = [str(segment.get("text") or "").strip()]
        x_min = float(segment.get("x_min") or _float_attr(first, "xMin"))
        x_max = float(segment.get("x_max") or _float_attr(first, "xMax"))
        y_min = float(segment.get("y_min") or _float_attr(first, "yMin"))
        y_max = float(segment.get("y_max") or _float_attr(first, "yMax"))
        start_x = x_min
        start_y = y_min
    else:
        texts = [_element_words_text(first)]
        x_min = _float_attr(first, "xMin")
        x_max = _float_attr(first, "xMax")
        y_min = _float_attr(first, "yMin")
        y_max = _float_attr(first, "yMax")
        start_x = x_min
        start_y = y_min
    for line in lines[start_index + 1:]:
        text = _element_words_text(line)
        if not text:
            continue
        if _caption_segments_for_line(line):
            break
        line_y = _float_attr(line, "yMin")
        if line_y - start_y > 96.0:
            break
        if abs(_float_attr(line, "xMin") - start_x) > 36.0:
            continue
        if texts[-1].rstrip().endswith(".") and re.match(r"^[a-z]", text):
            break
        texts.append(text)
        x_min = min(x_min, _float_attr(line, "xMin"))
        x_max = max(x_max, _float_attr(line, "xMax"))
        y_min = min(y_min, _float_attr(line, "yMin"))
        y_max = max(y_max, _float_attr(line, "yMax"))
    return re.sub(r"\s+", " ", " ".join(texts)).strip(), x_min, x_max, y_min, y_max


def _extract_figure_caption_blocks(pdf_path: Path) -> list[dict[str, Any]]:
    result = process_runtime.run_process(
        [_poppler_command("pdftotext"), "-bbox-layout", "-enc", "UTF-8", str(pdf_path), "-"],
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=360,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "pdftotext bbox failed").strip()[:500])
    try:
        root = ET.fromstring(_sanitize_xml_1_0_text(result.stdout))
    except Exception as exc:
        raise RuntimeError(f"pdftotext bbox parse failed: {exc}") from exc

    captions: list[dict[str, Any]] = []
    for page_index, page in enumerate(root.findall(".//{*}page"), start=1):
        page_width = _float_attr(page, "width")
        page_height = _float_attr(page, "height")
        page_lines: list[dict[str, Any]] = []
        page_captions: list[dict[str, Any]] = []
        for block in page.findall(".//{*}block"):
            lines = block.findall(".//{*}line")
            for line_index, line in enumerate(lines):
                line_text = _element_words_text(line)
                if line_text:
                    page_lines.append({
                        "text": line_text,
                        "x_min": _float_attr(line, "xMin"),
                        "x_max": _float_attr(line, "xMax"),
                        "y_min": _float_attr(line, "yMin"),
                        "y_max": _float_attr(line, "yMax"),
                    })
                for segment in _caption_segments_for_line(line):
                    figure_number = str(segment.get("number") or "").strip()
                    if not figure_number:
                        continue
                    text, x_min, x_max, y_min, y_max = _caption_line_group(lines, line_index, segment)
                    page_captions.append({
                        "page": page_index,
                        "page_width": page_width,
                        "page_height": page_height,
                        "number": figure_number,
                        "caption": text[:1200],
                        "x_min": x_min,
                        "x_max": x_max or page_width,
                        "y_min": y_min,
                        "y_max": y_max,
                        "_marker_text": str(segment.get("text") or "").strip(),
                        "_inline_caption": bool(segment.get("inline")),
                        "_inline_after_caption": bool(segment.get("inline_after_caption")),
                    })
        anchored_captions = [caption for caption in page_captions if not caption.get("_inline_caption")]
        page_captions = [
            caption
            for caption in page_captions
            if not caption.get("_inline_caption")
            or (
                caption.get("_inline_after_caption")
                or (
                    str(caption.get("number") or "").count(".") >= 2
                    and any(
                        abs(float(anchor.get("y_min") or 0) - float(caption.get("y_min") or 0)) <= 20.0
                        for anchor in anchored_captions
                    )
                )
            )
        ]
        best_by_number: dict[str, dict[str, Any]] = {}
        for caption in page_captions:
            marker = str(caption.pop("_marker_text", "") or "").strip()
            caption.pop("_inline_caption", None)
            caption.pop("_inline_after_caption", None)
            marker_tail = re.sub(r"^(?:Fig\.?|Figure)\s*[0-9]+(?:\.[0-9]+)*[A-Za-z]?\s*[:.]?\s*", "", marker, flags=re.I)
            score = (
                1 if len(marker_tail.split()) >= 2 else 0,
                1 if re.search(r"(?:[0-9]+[A-Za-z]?)\s*:\s*\S", marker) else 0,
                1 if re.match(r"^Figure\b", marker, flags=re.I) else 0,
                len(marker_tail.split()),
                len(marker),
            )
            key = str(caption.get("number") or "").lower()
            current = best_by_number.get(key)
            if current is None or score > current["score"]:
                best_by_number[key] = {"score": score, "caption": caption}
        page_captions = []
        for item in best_by_number.values():
            caption = item["caption"]
            caption["_caption_score"] = item["score"]
            page_captions.append(caption)
        for caption in page_captions:
            caption["page_lines"] = page_lines
            captions.append(caption)
    header_pages: dict[str, set[int]] = {}
    page_samples: dict[int, dict[str, Any]] = {}
    for caption in captions:
        page_samples.setdefault(int(caption.get("page") or 0), caption)
    for page, caption in page_samples.items():
        page_width = max(1.0, float(caption.get("page_width") or 0))
        page_height = max(1.0, float(caption.get("page_height") or 0))
        for line in caption.get("page_lines") or []:
            text = re.sub(r"\s+", " ", str(line.get("text") or "")).strip()
            line_width = float(line.get("x_max") or 0) - float(line.get("x_min") or 0)
            if (
                float(line.get("y_max") or 0) <= page_height * 0.10
                and line_width >= page_width * 0.15
                and 3 <= len(re.findall(r"[A-Za-z]+", text)) <= 20
                and not _looks_like_figure_internal_text(text)
            ):
                header_pages.setdefault(text.casefold(), set()).add(page)
    repeated_headers = {text for text, pages in header_pages.items() if len(pages) >= 2}
    if repeated_headers:
        for caption in captions:
            for line in caption.get("page_lines") or []:
                text = re.sub(r"\s+", " ", str(line.get("text") or "")).strip().casefold()
                if text in repeated_headers:
                    line["is_running_header"] = True
    return captions


def _render_pdf_page_png(pdf_path: Path, page: int, output_prefix: Path, dpi: int = 160) -> Path:
    result = process_runtime.run_process(
        [
            _poppler_command("pdftoppm"),
            "-png",
            "-r",
            str(dpi),
            "-f",
            str(page),
            "-l",
            str(page),
            "-singlefile",
            str(pdf_path),
            str(output_prefix),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=270,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "pdftoppm failed").strip()[:500])
    path = output_prefix.with_suffix(".png")
    if not path.is_file():
        raise RuntimeError("pdftoppm did not write page image")
    return path


def _pdfimages_pages(pdf_path: Path) -> list[int]:
    result = process_runtime.run_process(
        [_poppler_command("pdfimages"), "-list", str(pdf_path)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=270,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "pdfimages list failed").strip()[:500])
    pages: list[int] = []
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if not parts or not parts[0].isdigit():
            continue
        try:
            pages.append(int(parts[0]))
        except ValueError:  # isdigit() also admits digits int() rejects, such as "²"
            pages.append(0)
    return pages


def _extract_embedded_pdf_images(
    pdf_path: Path,
    extracted_dir: Path,
    paper_root: Path,
    paper_id: str,
    actor: str,
    *,
    limit: int = 40,
) -> list[dict]:
    prefix = extracted_dir / "_embedded"
    for old in extracted_dir.glob("_embedded-*"):
        try:
            old.unlink()
        except OSError:
            pass
    pages = _pdfimages_pages(pdf_path)
    result = process_runtime.run_process(
        [_poppler_command("pdfimages"), "-png", str(pdf_path), str(prefix)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=360,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "pdfimages failed").strip()[:500])
    images = sorted(path for path in extracted_dir.glob("_embedded-*") if path.is_file())
    figures: list[dict] = []
    seen_content: set[str] = set()
    for source in images:
        if len(figures) >= limit:
            break
        try:
            with Image.open(source) as image:
                width, height = image.size
        except Exception as exc:
            raise RuntimeError(f"pdfimages produced unreadable image: {source.name}") from exc
        size = source.stat().st_size
        if width < 160 or height < 100 or size < 8_000:
            continue
        content_key = _figure_pixel_digest(source)
        if content_key in seen_content:
            continue
        seen_content.add(content_key)
        index = len(figures) + 1
        figure_id = f"fig-{index:03d}"
        target = extracted_dir / f"{figure_id}.png"
        # pdfimages can produce PBM/PPM for masks despite -png. Store real PNG.
        with Image.open(source) as image:
            image.save(target, format="PNG")
        rel_path = target.relative_to(paper_root).as_posix()
        source_index = int(source.stem.rsplit("-", 1)[-1])
        page = pages[source_index] if source_index < len(pages) else 0
        figures.append({
            "figure_id": figure_id,
            "paper_id": paper_id,
            "index": index,
            "label": f"Figure {index}",
            "caption": "",
            "page": page,
            "width": width,
            "height": height,
            "size_bytes": target.stat().st_size,
            "extraction_mode": "embedded_pdf_image",
            "image_rel_path": rel_path,
            "created_by": actor,
            "created_at": _now(),
            "updated_at": _now(),
        })
    for leftover in extracted_dir.glob("_embedded-*"):
        try:
            leftover.unlink()
        except OSError:
            pass
    return figures


def _nonwhite_row_bounds(
    image: Image.Image,
    *,
    threshold: int = 245,
    gap_limit_cap: int | None = None,
    gap_limit_floor: int | None = None,
) -> tuple[int, int] | None:
    gray = image.convert("L")
    width, height = gray.size
    pixels = gray.load()
    # At low DPI, the two vertical frame edges can each be one pixel wide.
    # Counting them keeps the chart's title, ticks and body in one region.
    min_count = max(2, int(width * 0.003))
    rows: list[int] = []
    for y in range(height):
        count = 0
        for x in range(width):
            if pixels[x, y] < threshold:
                count += 1
                if count >= min_count:
                    rows.append(y)
                    break
    if not rows:
        return None
    bottom = rows[-1]
    gap_limit = max(18, int(height * 0.035))
    if gap_limit_floor is not None:
        gap_limit = max(gap_limit, max(1, int(gap_limit_floor)))
    if gap_limit_cap is not None:
        gap_limit = min(gap_limit, max(1, int(gap_limit_cap)))
    min_height = max(36, int(height * 0.10))
    previous = bottom
    top = rows[0]
    for row in reversed(rows[:-1]):
        if previous - row > gap_limit and bottom - previous >= min_height:
            top = previous
            break
        previous = row
    return max(0, top), min(height - 1, bottom)


def _nonwhite_col_bounds(image: Image.Image, y_min: int, y_max: int, *, threshold: int = 245) -> tuple[int, int] | None:
    gray = image.convert("L")
    width, height = gray.size
    pixels = gray.load()
    y0 = max(0, min(height - 1, y_min))
    y1 = max(y0, min(height - 1, y_max))
    min_count = max(3, int((y1 - y0 + 1) * 0.003))
    cols: list[int] = []
    for x in range(width):
        count = 0
        for y in range(y0, y1 + 1):
            if pixels[x, y] < threshold:
                count += 1
                if count >= min_count:
                    cols.append(x)
                    break
    if not cols:
        return None
    return cols[0], cols[-1]


def _is_nature_style_full_width_caption(caption: dict[str, Any]) -> bool:
    page_width = max(1.0, float(caption.get("page_width") or 0))
    cap_x0 = float(caption.get("x_min") or 0)
    cap_x1 = float(caption.get("x_max") or page_width)
    cap_width = max(0.0, cap_x1 - cap_x0)
    figure_number = str(caption.get("number") or "").strip()
    caption_text = str(caption.get("caption") or "").strip()
    return (
        bool(re.fullmatch(r"\d+", figure_number))
        and bool(re.match(r"^Fig\.\s*\d+\s*\|", caption_text, flags=re.I))
        and cap_x0 <= page_width * 0.14
        and cap_width >= page_width * 0.35
    )


def _page_has_two_text_columns(caption: dict[str, Any]) -> bool:
    page_width = max(1.0, float(caption.get("page_width") or 0))
    mid = page_width * 0.5
    left_lines = 0
    right_lines = 0
    for line in caption.get("page_lines") or []:
        if not isinstance(line, dict):
            continue
        text = re.sub(r"\s+", " ", str(line.get("text") or "")).strip()
        if len(text) < 35:
            continue
        if re.match(r"^(Fig\.|Figure|Table|Extended Data Fig\.)\s+", text, flags=re.I):
            continue
        line_x0 = float(line.get("x_min") or 0)
        line_x1 = float(line.get("x_max") or 0)
        if line_x0 < mid * 0.85 and line_x1 <= mid + 36.0:
            left_lines += 1
        elif line_x0 >= mid * 0.92:
            right_lines += 1
    return left_lines >= 5 and right_lines >= 5


def _caption_crop_region(caption: dict[str, Any]) -> tuple[float, float, float, float]:
    page_width = max(1.0, float(caption.get("page_width") or 0))
    page_height = max(1.0, float(caption.get("page_height") or 0))
    cap_x0 = float(caption.get("x_min") or 0)
    cap_x1 = float(caption.get("x_max") or page_width)
    cap_y0 = float(caption.get("y_min") or 0)
    cap_width = max(0.0, cap_x1 - cap_x0)
    likely_full_width_main_figure = _is_nature_style_full_width_caption(caption)
    mid = page_width * 0.5
    center = (cap_x0 + cap_x1) / 2.0
    panel_x_min = caption.get("panel_x_min")
    panel_x_max = caption.get("panel_x_max")
    crosses_midline = (
        cap_x0 <= page_width * 0.15
        and cap_x1 >= page_width * 0.55
        and caption.get("crop_x_max") is None
    )
    if panel_x_min is not None or panel_x_max is not None:
        x0 = float(panel_x_min) if panel_x_min is not None else cap_x0 - 18.0
        x1 = float(panel_x_max) if panel_x_max is not None else cap_x1 + 18.0
    elif cap_width >= page_width * 0.50 or likely_full_width_main_figure or crosses_midline:
        x0, x1 = 36.0, page_width - 36.0
    elif abs(center - mid) <= 36.0 and cap_width >= page_width * 0.12:
        x0, x1 = 36.0, page_width - 36.0
    elif _page_has_two_text_columns(caption):
        if abs(center - mid) <= 36.0 and cap_width >= page_width * 0.30:
            x0, x1 = 36.0, page_width - 36.0
        elif center < mid:
            x0, x1 = 36.0, mid - 4.0
        else:
            x0, x1 = mid + 4.0, page_width - 36.0
    else:
        # A short caption does not bound a single-column figure's content.
        # Let pixel bounds trim the page region, retaining chart labels.
        x0, x1 = 36.0, page_width - 36.0
    crop_x_min = caption.get("crop_x_min")
    crop_x_max = caption.get("crop_x_max")
    if crop_x_min is not None:
        x0 = max(x0, float(crop_x_min))
    if crop_x_max is not None:
        x1 = min(x1, float(crop_x_max))
    bottom_guard = 4.0
    for line in caption.get("page_lines") or []:
        line_x0 = float(line.get("x_min") or 0)
        line_x1 = float(line.get("x_max") or 0)
        line_y0 = float(line.get("y_min") or 0)
        line_y1 = float(line.get("y_max") or 0)
        text = re.sub(r"\s+", " ", str(line.get("text") or "")).strip()
        if (
            line_y0 < cap_y0
            and line_y1 > cap_y0 - 4.0
            and line_x1 >= x0
            and line_x0 <= x1
            and not re.match(r"^(?:Fig\.?|Figure)\s+", text, flags=re.I)
        ):
            bottom_guard = 0.25
            break
    return (
        max(0.0, x0),
        max(0.0, min(page_height, float(caption.get("crop_y_min") or 36.0))),
        min(page_width, x1),
        max(0.0, cap_y0 - bottom_guard),
    )


def _right_body_text_stats(caption: dict[str, Any], y_min: float, y_max: float) -> tuple[int, float | None]:
    page_width = max(1.0, float(caption.get("page_width") or 0))
    buckets: dict[int, list[float]] = {}
    for line in caption.get("page_lines") or []:
        if not isinstance(line, dict):
            continue
        text = re.sub(r"\s+", " ", str(line.get("text") or "")).strip()
        if len(text) < 35:
            continue
        if re.match(r"^(Fig\.|Figure|Extended Data Fig\.)\s+", text, flags=re.I):
            continue
        line_x0 = float(line.get("x_min") or 0)
        line_x1 = float(line.get("x_max") or 0)
        line_y0 = float(line.get("y_min") or 0)
        line_y1 = float(line.get("y_max") or 0)
        if line_x0 < page_width * 0.45 or line_x1 < page_width * 0.70:
            continue
        if line_y0 < y_min or line_y1 > y_max:
            continue
        bucket = int(round(line_x0 / 8.0) * 8)
        buckets.setdefault(bucket, []).append(line_x0)
    if not buckets:
        return 0, None
    _, values = max(buckets.items(), key=lambda item: len(item[1]))
    return len(values), min(values)


def _body_text_column_stats(
    caption: dict[str, Any],
    y_min: float,
    y_max: float,
    x_min: float,
    x_max: float,
) -> tuple[int, float | None]:
    buckets: dict[int, list[float]] = {}
    for line in caption.get("page_lines") or []:
        if not isinstance(line, dict):
            continue
        text = re.sub(r"\s+", " ", str(line.get("text") or "")).strip()
        if len(text) < 20 and not re.search(r"[A-Za-z]{3,}[.!?]$", text):
            continue
        if re.match(r"^(Fig\.|Figure|Extended Data Fig\.)\s+", text, flags=re.I):
            continue
        if _looks_like_figure_internal_text(text):
            continue
        line_x0 = float(line.get("x_min") or 0)
        line_x1 = float(line.get("x_max") or 0)
        if line_x1 < x_min or line_x0 > x_max:
            continue
        line_y0 = float(line.get("y_min") or 0)
        line_y1 = float(line.get("y_max") or 0)
        if line_y0 < y_min or line_y1 > y_max:
            continue
        bucket = int(round(line_x0 / 8.0) * 8)
        buckets.setdefault(bucket, []).append(line_y1)
    if not buckets:
        return 0, None
    _, values = max(buckets.items(), key=lambda item: len(item[1]))
    return len(values), max(values)


def _body_text_lines_for_bounds(
    caption: dict[str, Any],
    y_min: float,
    y_max: float,
    x_min: float,
    x_max: float,
) -> list[dict[str, Any]]:
    buckets: dict[int, list[dict[str, Any]]] = {}
    candidates: list[dict[str, Any]] = []
    eligible_lines: list[dict[str, Any]] = []
    region_width = max(1.0, x_max - x_min)
    for line in caption.get("page_lines") or []:
        if not isinstance(line, dict):
            continue
        text = re.sub(r"\s+", " ", str(line.get("text") or "")).strip()
        structural_heading = bool(
            re.match(r"^[A-Z]\.(?:\d+\.)?\s+\S", text)
            or re.match(r"^Full Results\b", text, flags=re.I)
            or re.fullmatch(r"\(?see\s+Figure\s+\d+[A-Za-z]?\)?\.?", text, flags=re.I)
        )
        if len(text) < 20 and not re.search(r"[A-Za-z]{3,}[.!?]$", text) and not structural_heading:
            continue
        if re.match(r"^(Fig\.|Figure|Extended Data Fig\.)\s+", text, flags=re.I):
            continue
        if _looks_like_figure_internal_text(text):
            continue
        line_x0 = float(line.get("x_min") or 0)
        line_x1 = float(line.get("x_max") or 0)
        if line_x1 < x_min or line_x0 > x_max:
            continue
        line_y0 = float(line.get("y_min") or 0)
        line_y1 = float(line.get("y_max") or 0)
        if line_y1 < y_min or line_y1 > y_max:
            continue
        eligible_lines.append(line)
        bucket = int(round(line_x0 / 8.0) * 8)
        buckets.setdefault(bucket, []).append(line)
        if line_x1 - line_x0 >= region_width * 0.04 and line_y1 - line_y0 <= 18.0:
            candidates.append(line)
    body_lines: list[dict[str, Any]] = []
    for lines in buckets.values():
        if len(lines) >= 5:
            body_lines.extend(lines)

    groups: list[list[dict[str, Any]]] = []
    for line in sorted(candidates, key=lambda item: (float(item.get("y_min") or 0), float(item.get("x_min") or 0))):
        line_x0 = float(line.get("x_min") or 0)
        line_x1 = float(line.get("x_max") or 0)
        line_y0 = float(line.get("y_min") or 0)
        best_group: list[dict[str, Any]] | None = None
        best_gap = 99.0
        for group in groups:
            previous = group[-1]
            previous_y0 = float(previous.get("y_min") or 0)
            previous_y1 = float(previous.get("y_max") or 0)
            start_gap = line_y0 - previous_y0
            vertical_gap = line_y0 - previous_y1
            if start_gap < 2.0 or start_gap > 18.0 or vertical_gap > 8.0:
                continue
            previous_x0 = float(previous.get("x_min") or 0)
            previous_x1 = float(previous.get("x_max") or 0)
            overlap = max(0.0, min(line_x1, previous_x1) - max(line_x0, previous_x0))
            min_width = max(1.0, min(line_x1 - line_x0, previous_x1 - previous_x0))
            if overlap / min_width < 0.35:
                continue
            if vertical_gap < best_gap:
                best_group = group
                best_gap = vertical_gap
        if best_group is None:
            groups.append([line])
        else:
            best_group.append(line)
    for group in groups:
        wide_line_count = sum(
            1
            for line in group
            if float(line.get("x_max") or 0) - float(line.get("x_min") or 0) >= region_width * 0.32
        )
        texts = [re.sub(r"\s+", " ", str(line.get("text") or "")).strip() for line in group]
        two_line_prose = len(group) == 2 and wide_line_count == 2 and any(text.endswith((".", "!", "?")) for text in texts)
        figure_number = re.escape(str(caption.get("number") or "").strip())
        current_figure_leadin = bool(
            figure_number
            and texts
            and re.match(rf"^(?:In\s+)?Figure\s+{figure_number}\b", texts[0], flags=re.I)
        )
        if (len(group) >= 3 and wide_line_count >= 2) or two_line_prose or current_figure_leadin:
            body_lines.extend(group)

    page_height = max(1.0, float(caption.get("page_height") or 0))
    first_page_cutoff = min(y_max - 60.0, page_height * 0.33)
    header_pattern = re.compile(
        r"\b(?:Proceedings|Conference|Symposium|USENIX|MICRO|ISCA|ASPLOS|SOSP|NeurIPS|ICML|ICLR|IEEE|ACM)\b",
        flags=re.I,
    )
    appendix_headings = [
        line
        for line in eligible_lines
        if re.match(r"^[A-Z]\.(?:\d+\.)?\s+\S", re.sub(r"\s+", " ", str(line.get("text") or "")).strip())
    ]
    for line in eligible_lines:
        line_x0 = float(line.get("x_min") or 0)
        line_x1 = float(line.get("x_max") or 0)
        line_y1 = float(line.get("y_max") or 0)
        text = re.sub(r"\s+", " ", str(line.get("text") or "")).strip()
        line_width = line_x1 - line_x0
        upper_region_line = line_y1 <= y_min + max(1.0, y_max - y_min) * 0.55
        prose_fragment = (
            upper_region_line
            and line_width >= region_width * 0.35
            and len(re.findall(r"[A-Za-z]+", text)) >= 4
            and text.endswith((".", "!", "?"))
        )
        section_heading = (
            len(text.split()) <= 8
            and bool(re.search(r"\b(?:Results?|Analysis|Experiments?|Evaluation|Discussion|Conclusion|Methodology|Background)\b", text, flags=re.I))
        )
        figure_reference_fragment = bool(re.fullmatch(r"\(?see\s+Figure\s+\d+[A-Za-z]?\)?\.?", text, flags=re.I))
        appendix_heading = bool(re.match(r"^[A-Z]\.(?:\d+\.)?\s+\S", text))
        full_results_heading = bool(re.match(r"^Full Results\b", text, flags=re.I))
        appendix_intro_line = any(
            0.0 <= float(line.get("y_min") or 0) - float(heading.get("y_max") or 0) <= 36.0
            for heading in appendix_headings
        )
        first_page_title_line = (
            int(caption.get("page") or 0) == 1
            and line_y1 <= first_page_cutoff
            and line_width >= region_width * 0.15
        )
        running_header_line = line_y1 <= page_height * 0.10 and (
            bool(header_pattern.search(text))
            or bool(re.search(r"\bet\s+al\.\s*$", text, flags=re.I))
            or bool(line.get("is_running_header"))
        )
        if (
            first_page_title_line
            or running_header_line
            or prose_fragment
            or section_heading
            or figure_reference_fragment
            or appendix_heading
            or full_results_heading
            or appendix_intro_line
        ):
            body_lines.append(line)

    unique_lines: list[dict[str, Any]] = []
    seen: set[int] = set()
    for line in body_lines:
        identity = id(line)
        if identity in seen:
            continue
        seen.add(identity)
        unique_lines.append(line)
    return unique_lines


def _table_boundary_above_figure(
    caption: dict[str, Any],
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
) -> float | None:
    lines = sorted(
        (
            line
            for line in (caption.get("page_lines") or [])
            if isinstance(line, dict)
            and float(line.get("y_max") or 0) >= y_min
            and float(line.get("y_min") or 0) <= y_max
            and float(line.get("x_max") or 0) >= x_min
            and float(line.get("x_min") or 0) <= x_max
        ),
        key=lambda line: (float(line.get("y_min") or 0), float(line.get("x_min") or 0)),
    )
    for index, line in enumerate(lines):
        text = re.sub(r"\s+", " ", str(line.get("text") or "")).strip()
        if not re.match(r"^Table\s+[A-Za-z0-9]", text, flags=re.I):
            continue
        boundary = float(line.get("y_max") or 0)
        if text.endswith((".", "!", "?")):
            return boundary
        for following in lines[index + 1 :]:
            following_y0 = float(following.get("y_min") or 0)
            following_y1 = float(following.get("y_max") or 0)
            if following_y0 - boundary > 14.0:
                break
            boundary = max(boundary, following_y1)
            following_text = re.sub(r"\s+", " ", str(following.get("text") or "")).strip()
            if following_text.endswith((".", "!", "?")):
                break
        return boundary
    return None


def _looks_like_figure_internal_text(text: str) -> bool:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return False
    if re.match(r"^\([a-z]\)\s+", text, flags=re.I):
        return True
    if re.match(r"^(?:\d+\s+)?(?:def|class|while|for|if|elif|else|return)\b", text):
        return True
    if re.search(r"(?:[A-Za-z_]\w*\.)+[A-Za-z_]\w*\s*=", text):
        return True
    code_keywords = (
        " return ",
        " if ",
        " elif ",
        " else:",
        " for ",
        " while ",
        " def ",
        "client.",
        "print(",
    )
    code_symbols = (
        " = ",
        " == ",
        "{",
        "}",
        "[",
        "]",
    )
    padded = f" {text} "
    if any(marker in padded for marker in code_keywords) and any(marker in padded for marker in code_symbols):
        return True
    label_markers = (
        "ML API",
        "API output",
        "App decision",
        "Ground-truth",
        "Code snippet",
        "Label output",
        "Multi-Choice",
        "Multi-Select",
        "True-False",
    )
    return any(marker in text for marker in label_markers)


def _figure_internal_line_count(caption: dict[str, Any], y_min: float, y_max: float) -> int:
    count = 0
    for line in caption.get("page_lines") or []:
        if not isinstance(line, dict):
            continue
        text = re.sub(r"\s+", " ", str(line.get("text") or "")).strip()
        if not _looks_like_figure_internal_text(text):
            continue
        line_y0 = float(line.get("y_min") or 0)
        line_y1 = float(line.get("y_max") or 0)
        if line_y1 < y_min or line_y0 > y_max:
            continue
        count += 1
    return count


def _has_merged_subfigure_labels(caption: dict[str, Any], y_min: float, y_max: float) -> bool:
    for line in caption.get("page_lines") or []:
        if not isinstance(line, dict):
            continue
        line_y0 = float(line.get("y_min") or 0)
        line_y1 = float(line.get("y_max") or 0)
        if line_y1 < y_min or line_y0 > y_max:
            continue
        text = re.sub(r"\s+", " ", str(line.get("text") or "")).strip()
        if len(re.findall(r"\([a-z]\)", text, flags=re.I)) >= 2:
            return True
    return False


def _candidate_with_body_text_masked_for_bounds(
    candidate: Image.Image,
    caption: dict[str, Any],
    x0: float,
    y0: float,
    y1: float,
    scale_x: float,
    scale_y: float,
    body_lines: list[dict[str, Any]] | None = None,
) -> Image.Image:
    if body_lines is None:
        body_lines = _body_text_lines_for_bounds(caption, y0, y1, x0, x0 + (candidate.width / scale_x))
    if not body_lines:
        return candidate
    masked = candidate.copy()
    draw = ImageDraw.Draw(masked)
    for line in body_lines:
        lx0 = int((float(line.get("x_min") or 0) - x0) * scale_x) - 4
        lx1 = int((float(line.get("x_max") or 0) - x0) * scale_x) + 4
        ly0 = int((float(line.get("y_min") or 0) - y0) * scale_y) - 4
        ly1 = int((float(line.get("y_max") or 0) - y0) * scale_y) + 4
        left = max(0, min(masked.width, lx0))
        right = max(0, min(masked.width, lx1))
        top = max(0, min(masked.height, ly0))
        bottom = max(0, min(masked.height, ly1))
        if right <= left or bottom <= top:
            continue
        draw.rectangle(
            (left, top, right, bottom),
            fill="white",
        )
    return masked


def _crop_figure_below_heading(page_image: Image.Image, caption: dict[str, Any]) -> Image.Image | None:
    page_width = max(1.0, float(caption.get("page_width") or 0))
    page_height = max(1.0, float(caption.get("page_height") or 0))
    scale_x, scale_y = page_image.width / page_width, page_image.height / page_height
    lines = caption.get("page_lines") or []
    if not lines:
        return None
    masked = page_image.convert("L")
    draw = ImageDraw.Draw(masked)
    for line in lines:
        draw.rectangle((
            max(0, (float(line.get("x_min") or 0) - 2) * scale_x),
            max(0, (float(line.get("y_min") or 0) - 2) * scale_y),
            min(page_image.width, (float(line.get("x_max") or 0) + 2) * scale_x),
            min(page_image.height, (float(line.get("y_max") or 0) + 2) * scale_y),
        ), fill=255)
    graphics = masked.point(lambda value: 255 if value < 235 else 0)
    caption_top = max(0, min(page_image.height, int(float(caption.get("y_min") or 0) * scale_y)))
    caption_bottom = max(caption_top, min(page_image.height, int(float(caption.get("y_max") or 0) * scale_y)))
    following = sorted(
        (float(line.get("y_min") or 0), _caption_number_from_text(str(line.get("text") or "")))
        for line in lines
        if float(line.get("y_min") or 0) > float(caption.get("y_max") or 0)
        and _caption_number_from_text(str(line.get("text") or ""))
    )
    caption_number = str(caption.get("number") or _caption_number_from_text(str(caption.get("caption") or "")))
    matching_lower_caption = bool(following and caption_number and following[0][1] == caption_number)
    preceding = graphics.crop((0, 0, page_image.width, caption_top)).getbbox()
    bottom = min(page_image.height, int(following[0][0] * scale_y)) if following else page_image.height
    content_top = caption_top if matching_lower_caption else caption_bottom
    bounds = graphics.crop((0, content_top, page_image.width, bottom)).getbbox()
    if not bounds or bounds[2] - bounds[0] < 100 * scale_x or bounds[3] - bounds[1] < 25 * scale_y:
        return None
    if preceding and not matching_lower_caption:
        # A heading can sit inside a figure's outer frame. Require both sides
        # to continue through the heading into the chart; an unrelated rule or
        # an earlier figure above the caption must not select this branch.
        tolerance = max(2, math.ceil(3 * scale_x))
        side_bottom = min(bottom, caption_bottom + math.ceil(15 * scale_y))
        frame = (
            caption_top - preceding[1] <= max(24 * scale_y, 2 * (caption_bottom - caption_top))
            and abs(preceding[0] - bounds[0]) <= tolerance
            and abs(preceding[2] - bounds[2]) <= tolerance
        )
        if frame:
            for edge in (preceding[0], preceding[2] - 1):
                rows = graphics.crop((
                    max(0, edge - tolerance), preceding[1],
                    min(page_image.width, edge + tolerance + 1), side_bottom,
                )).getprojection()[1]
                if not rows or sum(rows) < 0.9 * len(rows):
                    frame = False
                    break
        if not frame:
            return None
        content_top = preceding[1]
        bounds = graphics.crop((0, content_top, page_image.width, bottom)).getbbox()
    graphic_bounds = (
        bounds[0] / scale_x, (content_top + bounds[1]) / scale_y,
        bounds[2] / scale_x, (content_top + bounds[3]) / scale_y,
    )
    label_heights = sorted(
        max(1.0, float(line.get("y_max") or 0) - float(line.get("y_min") or 0))
        for line in lines
        if float(line.get("y_min") or 0) >= graphic_bounds[1]
        and float(line.get("y_max") or 0) <= graphic_bounds[3]
        and float(line.get("x_max") or 0) >= graphic_bounds[0]
        and float(line.get("x_min") or 0) <= graphic_bounds[2]
    )
    label_height = label_heights[len(label_heights) // 2] if label_heights else 12.0
    # This branch has identified a chart below its own heading. Preserve that
    # heading as well as the plot; text masking is only for finding graphics.
    included_bounds = [
        min(graphic_bounds[0], float(caption.get("x_min") or 0)),
        min(graphic_bounds[1], caption_top / scale_y),
        max(graphic_bounds[2], float(caption.get("x_max") or 0)),
        graphic_bounds[3],
    ]
    for line in lines:
        text = str(line.get("text") or "").strip()
        line_bounds = tuple(float(line.get(key) or 0) for key in ("x_min", "y_min", "x_max", "y_max"))
        if not text or _caption_number_from_text(text) or line_bounds[1] < content_top / scale_y:
            continue
        if (line_bounds[1] if matching_lower_caption else line_bounds[3]) > bottom / scale_y:
            continue
        height = max(1.0, line_bounds[3] - line_bounds[1])
        horizontal_gap = max(graphic_bounds[0] - line_bounds[2], line_bounds[0] - graphic_bounds[2], 0)
        vertical_gap = max(graphic_bounds[1] - line_bounds[3], line_bounds[1] - graphic_bounds[3], 0)
        # A large following section heading must not enlarge its own admission
        # distance and pull unrelated prose into the figure crop.
        if horizontal_gap > max(48.0, 4 * height) or vertical_gap > max(36.0, 3 * min(height, label_height)):
            continue
        prose = len(text) > 160 or (len(text.split()) >= 8 and re.search(r"[.!?。！？]$", text))
        if prose and (horizontal_gap > 0 or vertical_gap > 0):
            continue
        included_bounds[0] = min(included_bounds[0], line_bounds[0])
        included_bounds[1] = min(included_bounds[1], line_bounds[1])
        included_bounds[2] = max(included_bounds[2], line_bounds[2])
        included_bounds[3] = max(included_bounds[3], line_bounds[3])
    # Footer annotations can contain full sentences without a "Note:" prefix.
    # A compact block aligned within the plot belongs with its nearby labels;
    # full-width body prose and separated sections must not extend the crop.
    note_left = None
    note_continues = False
    footer_lines = sorted(lines, key=lambda item: float(item.get("y_min") or 0))
    uninterrupted = True
    for index, line in enumerate(footer_lines):
        text = str(line.get("text") or "").strip()
        x0, y0, x1, y1 = (float(line.get(key) or 0) for key in ("x_min", "y_min", "x_max", "y_max"))
        if y0 < included_bounds[3] or y1 > bottom / scale_y:
            continue
        height = max(1.0, y1 - y0)
        explicit_note = re.match(r"^(?:Caption|Notes?|Disclaimer)\s*[:：]", text, flags=re.I)
        continuation = note_continues and note_left is not None and abs(x0 - note_left) <= height
        inset_tolerance = max(12.0, 2 * min(height, label_height))
        compact_note = (
            len(text) >= 20 and re.search(r"[.!?。！？]$", text)
            and included_bounds[0] - inset_tolerance <= x0
            and x1 <= graphic_bounds[2] + inset_tolerance
            and (note_left is None or abs(x0 - note_left) <= height)
        )
        if not (explicit_note or continuation or compact_note):
            note_continues = False
            if text:
                uninterrupted = False
            continue
        gap_limit = max(24.0, 2 * min(height, label_height)) if explicit_note or compact_note else 1.5 * height
        if compact_note and uninterrupted and note_left is None and index + 1 < len(footer_lines):
            following_line = footer_lines[index + 1]
            following_text = str(following_line.get("text") or "").strip()
            nx0, ny0, nx1, ny1 = (float(following_line.get(key) or 0)
                                  for key in ("x_min", "y_min", "x_max", "y_max"))
            # A pair of compact sentences aligned with the plot provides more
            # evidence than a lone sentence separated from its axis labels.
            # Bound the extra whitespace by the plot size, and never cross a
            # section heading or absorb full-width body text.
            aligned_pair = (
                len(following_text) >= 20 and re.search(r"[.!?。！？]$", following_text)
                and not _caption_number_from_text(following_text)
                and abs(nx0 - x0) <= height
                # Axis labels are part of the figure's horizontal extent too.
                # Notes aligned with those labels can start left of the plot.
                and x0 >= included_bounds[0] - inset_tolerance
                # The heading starts at the page's text margin, where body
                # prose (and often an axis title) also starts. An unprefixed
                # pair flush with the heading is indistinguishable from that
                # prose unless it is aligned with the plot itself.
                and (
                    explicit_note
                    or x0 >= graphic_bounds[0] - inset_tolerance
                    or abs(x0 - float(caption.get("x_min") or 0)) > height
                )
                and nx1 <= graphic_bounds[2] + inset_tolerance
                and 0 <= ny0 - y1 <= max(12.0, 1.5 * height)
                and 0 < ny1 - ny0 <= 1.5 * height
                and ny1 <= bottom / scale_y
            )
            if aligned_pair:
                gap_limit = max(gap_limit, min(48.0, 0.2 * (graphic_bounds[3] - graphic_bounds[1])))
        if y0 - included_bounds[3] > gap_limit or x1 < graphic_bounds[0] or x0 > graphic_bounds[2]:
            note_continues = False
            uninterrupted = False
            continue
        included_bounds[0] = min(included_bounds[0], x0)
        included_bounds[2] = max(included_bounds[2], x1)
        included_bounds[3] = y1
        note_left = x0
        note_continues = not re.search(r"[.!?。！？]$", text)
    padding_x, padding_y = int(20 * scale_x), int(20 * scale_y)
    if matching_lower_caption:
        bottom = min(page_image.height, max(bottom, math.ceil(included_bounds[3] * scale_y)))
    crop_bottom = min(bottom, int(included_bounds[3] * scale_y) + padding_y)
    for line in lines:
        line_top = float(line.get("y_min") or 0) * scale_y
        line_bottom = float(line.get("y_max") or 0) * scale_y
        if line_top > included_bounds[3] * scale_y and line_top < crop_bottom < line_bottom:
            # Padding may include whitespace, never half of the next paragraph.
            crop_bottom = max(int(included_bounds[3] * scale_y), int(line_top) - 2)
    return page_image.crop((
        max(0, int(included_bounds[0] * scale_x) - padding_x),
        max(min(content_top, caption_top), int(included_bounds[1] * scale_y) - padding_y),
        min(page_image.width, int(included_bounds[2] * scale_x) + padding_x),
        crop_bottom,
    ))


def _crop_figure_above_caption(page_image: Image.Image, caption: dict[str, Any]) -> Image.Image | None:
    below_heading = _crop_figure_below_heading(page_image, caption)
    if below_heading is not None:
        return below_heading
    page_width = max(1.0, float(caption.get("page_width") or 0))
    page_height = max(1.0, float(caption.get("page_height") or 0))
    x0, y0, x1, y1 = _caption_crop_region(caption)
    if x1 <= x0 or y1 <= y0:
        return None
    scale_x = page_image.width / page_width
    scale_y = page_image.height / page_height
    left = max(0, int(x0 * scale_x))
    top = max(0, int(y0 * scale_y))
    right = min(page_image.width, int(x1 * scale_x))
    bottom = min(page_image.height, int(y1 * scale_y))
    if right - left < 80 or bottom - top < 60:
        return None
    candidate = page_image.crop((left, top, right, bottom))
    body_lines = _body_text_lines_for_bounds(caption, y0, y1, x0, x1)
    cap_x0 = float(caption.get("x_min") or 0)
    cap_x1 = float(caption.get("x_max") or page_width)
    left_body_lines = [
        line
        for line in body_lines
        if float(line.get("x_max") or 0) <= cap_x0 - 8.0
    ]
    if (
        len(left_body_lines) >= 5
        and cap_x0 - x0 >= (x1 - x0) * 0.25
        and cap_x1 - cap_x0 <= (x1 - x0) * 0.45
    ):
        x0 = max(x0, cap_x0 - 18.0)
        left = max(0, int(x0 * scale_x))
        candidate = page_image.crop((left, top, right, bottom))
        body_lines = _body_text_lines_for_bounds(caption, y0, y1, x0, x1)
    bounds_candidate = _candidate_with_body_text_masked_for_bounds(
        candidate, caption, x0, y0, y1, scale_x, scale_y, body_lines
    )
    nature_full_width_caption = _is_nature_style_full_width_caption(caption)
    two_text_columns = _page_has_two_text_columns(caption)
    body_text_column_count, body_text_column_y_max = _body_text_column_stats(caption, y0, y1, x0, x1)
    figure_internal_line_count = _figure_internal_line_count(caption, y0, y1)
    merged_subfigure_labels = _has_merged_subfigure_labels(caption, y0, y1)
    row_bounds = _nonwhite_row_bounds(
        bounds_candidate,
        gap_limit_cap=30 if nature_full_width_caption or body_text_column_count >= 5 else None,
        gap_limit_floor=int(candidate.height * 0.22)
        if (
            figure_internal_line_count >= 2
            or merged_subfigure_labels
            or (body_text_column_count < 5 and float(caption.get("crop_y_min") or 36.0) > 40.0)
        )
        else None,
    )
    if not row_bounds:
        return None
    row_top, row_bottom = row_bounds
    row_top_floor = 0
    minimum_remaining_height = 40 if candidate.width >= 400 else 80
    table_boundary = _table_boundary_above_figure(caption, x0, x1, y0, y1)
    if table_boundary is not None:
        table_bottom = int((table_boundary + 4.0 - y0) * scale_y)
        if row_bottom - table_bottom >= minimum_remaining_height:
            row_top = max(row_top, table_bottom)
            row_top_floor = max(row_top_floor, table_bottom)
    if body_lines:
        body_boundary = max(float(line.get("y_max") or 0) for line in body_lines)
        body_bottom = int((body_boundary + 4.0 - y0) * scale_y)
        if row_bottom - body_bottom >= minimum_remaining_height:
            row_top = max(row_top, body_bottom)
            row_top_floor = max(row_top_floor, body_bottom)
    if body_text_column_count >= 5 and body_text_column_y_max is not None:
        text_bottom = int((body_text_column_y_max + 4.0 - y0) * scale_y)
        if row_bottom - text_bottom >= minimum_remaining_height:
            row_top = max(row_top, text_bottom)
            row_top_floor = max(row_top_floor, text_bottom)
    if (nature_full_width_caption or body_text_column_count >= 5) and not two_text_columns:
        crop_pdf_y0 = y0 + (row_top / scale_y)
        crop_pdf_y1 = y0 + (row_bottom / scale_y)
        body_text_count, body_text_x_min = _right_body_text_stats(caption, crop_pdf_y0, crop_pdf_y1)
        if body_text_count >= 5:
            cap_x0 = float(caption.get("x_min") or 0)
            cap_x1 = float(caption.get("x_max") or page_width)
            if body_text_x_min is not None:
                cap_x1 = min(cap_x1, body_text_x_min - 26.0)
            narrow_left = max(0, int(max(0.0, cap_x0 - 18.0) * scale_x))
            narrow_right = min(page_image.width, int(min(page_width, cap_x1 + 18.0) * scale_x))
            if narrow_right - narrow_left >= 80:
                candidate = page_image.crop((narrow_left, top, narrow_right, bottom))
                narrow_x0 = narrow_left / scale_x
                body_lines = _body_text_lines_for_bounds(
                    caption, y0, y1, narrow_x0, narrow_x0 + (candidate.width / scale_x)
                )
                bounds_candidate = _candidate_with_body_text_masked_for_bounds(
                    candidate, caption, narrow_x0, y0, y1, scale_x, scale_y, body_lines
                )
                row_bounds = _nonwhite_row_bounds(bounds_candidate)
                if not row_bounds:
                    return None
                row_top, row_bottom = row_bounds
    # A numbered chart title can be separated from its plot by more whitespace
    # than the pixel grouping permits. Include a nearby matching title while
    # respecting the table/prose boundary already established above.
    figure_number = re.escape(str(caption.get("number") or "").strip())
    if figure_number:
        plot_top = y0 + row_top / scale_y
        for line in caption.get("page_lines") or []:
            text = re.sub(r"\s+", " ", str(line.get("text") or "")).strip()
            if not re.match(rf"^(?:Fig\.?|Figure)\s+{figure_number}(?:[.:]|\s|$)", text, flags=re.I):
                continue
            heading_top = float(line.get("y_min") or 0)
            heading_bottom = float(line.get("y_max") or 0)
            heading_height = heading_bottom - heading_top
            if (
                heading_height > 0
                and 0 <= plot_top - heading_bottom <= 2 * heading_height
                and (heading_top - y0) * scale_y >= row_top_floor
                and float(line.get("x_max") or 0) >= x0
                and float(line.get("x_min") or 0) <= x1
            ):
                row_top = min(row_top, int((heading_top - y0) * scale_y))
    col_bounds = _nonwhite_col_bounds(bounds_candidate, row_top, row_bottom)
    if not col_bounds:
        return None
    col_left, col_right = col_bounds
    pad = max(8, int(min(candidate.size) * 0.015))
    crop_box = (
        max(0, col_left - pad),
        max(0, row_top_floor, row_top - pad),
        min(candidate.width, col_right + pad),
        min(candidate.height, row_bottom + pad),
    )
    crop_width = crop_box[2] - crop_box[0]
    crop_height = crop_box[3] - crop_box[1]
    wide_short_figure = crop_width >= 300 and crop_height >= 40 and crop_width / max(1, crop_height) >= 4.0
    if crop_width < 120 or (crop_height < 80 and not wide_short_figure):
        return None
    return candidate.crop(crop_box)


def _figure_pixel_digest(path: Path) -> str:
    with Image.open(path) as image, image.convert("RGBA") as pixels:
        digest = hashlib.sha256(str(pixels.size).encode("ascii"))
        digest.update(pixels.tobytes())
        return digest.hexdigest()
