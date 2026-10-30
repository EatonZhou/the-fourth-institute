"""Article sections as resources: section addresses and their text."""
from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from management_common.text_values import stripped_text
from management_portal import article_registry, article_tex
from management_portal.resources import get_resource_service


def _section_slug(value: str) -> str:
    return re.sub(r"[^0-9a-z\u0080-\uffff]+", "-", value.casefold()).strip("-")


def _article_sections(path: Path) -> list[dict[str, Any]]:
    text = article_tex._read_text(path, limit=8 * 1024 * 1024)
    headings: list[dict[str, Any]] = []
    if path.suffix.casefold() in {".md", ".markdown"}:
        matches = list(re.finditer(r"(?m)^(#{1,6})\s+(.+?)\s*$", text))
        for index, match in enumerate(matches):
            title = match.group(2).strip()
            headings.append(
                {
                    "title": title,
                    "keys": [_section_slug(title)],
                    "start": match.start(),
                    "end": matches[index + 1].start() if index + 1 < len(matches) else len(text),
                }
            )
        return headings

    clean = article_tex._strip_tex_comments(text)
    matches = list(
        re.finditer(r"(?m)^[ \t]*\\(section|subsection|subsubsection)\*?\s*\{", clean)
    )
    for index, match in enumerate(matches):
        argument = article_tex._balanced_argument(clean, match.end() - 1)
        if not argument:
            continue
        title = article_tex._plain_tex(argument[0], article_tex._tex_macros(clean))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(clean)
        block = clean[match.start() : end]
        labels = re.findall(r"\\label\s*\{([^{}]+)\}", block)
        keys = [_section_slug(title)]
        for label in labels:
            keys.extend([label.casefold(), label.rsplit(":", 1)[-1].casefold()])
        headings.append(
            {
                "title": title,
                "keys": article_tex._dedupe(keys),
                "start": match.start(),
                "end": end,
                "text": block,
            }
        )
    return headings


def resolve_article_address(
    address: str,
) -> dict[str, Any]:
    clean_address = unquote(stripped_text(address)).strip("/")
    root_id, separator, suffix = clean_address.partition("/")
    if not get_resource_service().is_valid_id(root_id, "article"):
        raise ValueError(f"invalid article address: {address}")
    record = article_registry.get_article_record(root_id)
    root_item = article_registry._select_root(
        [item for item in record.get("roots") or [] if isinstance(item, dict)]
    )
    article_root = Path(stripped_text(root_item.get("path"))).resolve(strict=False)
    primary_file = article_tex._safe_relative_path(stripped_text(root_item.get("primary_file")))
    primary_path = (article_root / primary_file).resolve(strict=False)
    try:
        primary_path.relative_to(article_root)
    except ValueError as exc:
        raise ValueError("article primary file escapes the article root") from exc
    result: dict[str, Any] = {
        "schema": "tfi.resource_resolution.v2",
        "address": clean_address,
        "root_id": root_id,
        "resource_type": "article",
        "target_kind": "article",
        "record": record,
        "article_root": str(article_root),
        "primary_file": primary_file,
        "relative_path": primary_file,
        "resolved_path": str(primary_path),
        "exists": article_root.is_dir(),
    }
    if not separator:
        return result
    parts = suffix.split("/")
    if len(parts) >= 2 and parts[0] == "file":
        relative_path = article_tex._safe_relative_path("/".join(parts[1:]))
        target = (article_root / relative_path).resolve(strict=False)
        try:
            target.relative_to(article_root)
        except ValueError as exc:
            raise ValueError("article-relative path escapes the article root") from exc
        result.update(
            {
                "target_kind": "file",
                "relative_path": relative_path,
                "resolved_path": str(target),
                "exists": target.is_file(),
            }
        )
        return result
    if len(parts) == 2 and parts[0] == "section":
        section_key = stripped_text(parts[1]).casefold()
        if not section_key or section_key in {".", ".."}:
            raise ValueError("invalid article section key")
        primary = (article_root / stripped_text(root_item.get("primary_file"))).resolve(strict=False)
        sections = _article_sections(primary) if primary.is_file() else []
        section = next(
            (item for item in sections if section_key in item.get("keys", [])), None
        )
        result.update(
            {
                "target_kind": "article_section",
                "section": section_key,
                "section_title": stripped_text((section or {}).get("title")),
                "resolved_path": str(primary),
                "section_start": int((section or {}).get("start") or 0),
                "section_end": int((section or {}).get("end") or 0),
                "exists": section is not None,
            }
        )
        return result
    raise ValueError(f"unsupported nested article address: {address}")


def read_article_section(
    resolved: dict[str, Any], *, max_bytes: int = 4 * 1024 * 1024
) -> tuple[bytes, str, dict[str, Any]]:
    if resolved.get("target_kind") != "article_section" or not resolved.get("exists"):
        raise FileNotFoundError(resolved.get("address") or "article section")
    path = Path(stripped_text(resolved.get("resolved_path")))
    text = article_tex._read_text(path, limit=max_bytes + 1)
    start = int(resolved.get("section_start") or 0)
    end = int(resolved.get("section_end") or 0)
    data = text[start:end].encode("utf-8")
    if len(data) > max_bytes:
        raise ValueError(f"resource exceeds {max_bytes} byte content limit")
    content_type = mimetypes.guess_type(str(path))[0] or "text/plain"
    if content_type == "application/x-tex":
        content_type = "text/x-tex"
    return data, content_type, resolved
