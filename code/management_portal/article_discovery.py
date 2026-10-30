"""Finding articles on disk: article directories, their standalone manuscripts, the
primary manuscript and its title, and logical articles across copies.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from management_common.text_values import stripped_text
from management_portal import article_tex, resource_identity_backend as repo_backend


ARTICLE_DIRECTORY_NAMES = {"article", "articles", "artical", "artical_arxiv"}

ARTICLE_TREE_IGNORED_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "node_modules",
}


def _manuscript_rank(path: Path) -> tuple[int, str]:
    name = path.name.casefold()
    if name == "main.tex":
        rank = 0
    elif name == "main_zwh.tex":
        rank = 1
    elif name.startswith("main_") and not any(token in name for token in ("old", "copy")):
        rank = 2
    elif name in {"article.tex", "paper.tex", "manuscript.tex"}:
        rank = 3
    elif name in {"main.md", "article.md", "paper.md", "manuscript.md"}:
        rank = 4
    elif any(token in name for token in ("sample", "template", "old", "copy")):
        rank = 40
    else:
        rank = 10
    return rank, name


def _is_standalone_tex_document(path: Path) -> bool:
    if path.suffix.casefold() != ".tex":
        return False
    try:
        clean = article_tex._strip_tex_comments(article_tex._read_text(path))
    except OSError:
        return False
    return re.search(r"\\documentclass(?:\s*\[[^\]]*\])?\s*\{", clean) is not None


def _tex_program_hint(path: Path) -> str:
    if path.suffix.casefold() != ".tex":
        return ""
    try:
        text = article_tex._read_text(path, limit=16 * 1024)
    except OSError:
        return ""
    match = re.search(
        r"(?im)^\s*%\s*!\s*TeX\s+program\s*=\s*(pdflatex|xelatex|lualatex)\s*$",
        text,
    )
    return match.group(1).casefold() if match else ""


def _manuscript_title(path: Path) -> str:
    title = article_tex.extract_article_title(path)
    if title or path.suffix.casefold() != ".tex":
        return title
    try:
        clean = article_tex._strip_tex_comments(article_tex._read_text(path))
    except OSError:
        return ""
    preamble = re.split(r"\\begin\s*\{document\}", clean, maxsplit=1)[0]
    root = path.parent.resolve(strict=False)
    for match in re.finditer(r"\\(?:input|include)\s*\{([^{}]+)\}", preamble):
        relative = match.group(1).strip()
        if not relative or "\\" in relative:
            continue
        candidate = (root / relative).with_suffix(Path(relative).suffix or ".tex")
        candidate = candidate.resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.is_file():
            title = article_tex.extract_article_title(candidate)
            if title:
                return title
    return ""


def _primary_manuscript(root: Path) -> Path | None:
    candidates = [
        path
        for path in root.iterdir()
        if path.is_file() and path.suffix.casefold() in {".tex", ".md", ".markdown"}
    ]
    candidates = [
        path
        for path in candidates
        if path.name.casefold() not in {"readme.md", "readme.markdown"}
    ]
    standalone_tex = [path for path in candidates if _is_standalone_tex_document(path)]
    if standalone_tex:
        return min(standalone_tex, key=_manuscript_rank)
    titled = [(path, article_tex.extract_article_title(path)) for path in candidates]
    titled_candidates = [path for path, title in titled if title]
    pool = titled_candidates or candidates
    return min(pool, key=_manuscript_rank) if pool else None


def _article_tex_candidates(root: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.tex"), key=lambda item: str(item).casefold()):
        if any(part in ARTICLE_TREE_IGNORED_DIRECTORIES for part in path.parts):
            continue
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            continue
        standalone = _is_standalone_tex_document(path)
        candidates.append(
            {
                "path": relative,
                "title": _manuscript_title(path) or path.stem,
                "compiler_hint": _tex_program_hint(path) or "",
                "standalone": standalone,
                "rank": list(_manuscript_rank(path)),
            }
        )
    candidates.sort(
        key=lambda item: (
            not bool(item["standalone"]),
            tuple(item["rank"]),
            str(item["path"]).casefold(),
        )
    )
    for index, candidate in enumerate(candidates):
        candidate["recommended"] = index == 0
        candidate.pop("rank", None)
    return candidates


def inspect_article_root(
    path: str | Path,
    *,
    primary_file: str = "",
) -> dict[str, Any]:
    root = Path(path).expanduser().resolve(strict=False)
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"article directory not found: {root}")
    primary = None
    if stripped_text(primary_file):
        relative = article_tex._safe_relative_path(primary_file)
        primary = (root / relative).resolve(strict=False)
        try:
            primary.relative_to(root)
        except ValueError as exc:
            raise ValueError("article primary file escapes the article root") from exc
        if not primary.is_file():
            raise FileNotFoundError(primary)
        if primary.suffix.casefold() not in {".tex", ".md", ".markdown"}:
            raise ValueError("article primary file must be .tex, .md, or .markdown")
    else:
        primary = _primary_manuscript(root)
    if primary is None:
        raise ValueError(f"article directory has no manuscript: {root}")
    project_ids = repo_backend.project_ids_for_path(root)
    title = _manuscript_title(primary)
    if not title:
        title = f"{project_ids[0]} Article" if project_ids else root.name
    return {
        "path": str(root),
        "title": title,
        "title_fingerprint": article_tex.title_fingerprint(title),
        "primary_file": primary.relative_to(root).as_posix(),
        "project_ids": project_ids,
    }


def _is_article_directory(path: Path) -> bool:
    name = path.name.casefold()
    return (
        name in ARTICLE_DIRECTORY_NAMES
        or name.startswith("article_")
        or name.startswith("artical_")
    )


def discover_article_roots(
    roots: Iterable[str | Path] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    candidates: set[Path] = set()
    if roots is None:
        for _project_id, project_root in repo_backend.project_roots():
            if not project_root.is_dir():
                continue
            candidates.update(
                child.resolve(strict=False)
                for child in project_root.iterdir()
                if child.is_dir() and _is_article_directory(child)
            )
    else:
        for raw in roots:
            root = Path(raw).expanduser().resolve(strict=False)
            if not root.is_dir():
                continue
            if _is_article_directory(root):
                candidates.add(root)
            else:
                candidates.update(
                    child.resolve(strict=False)
                    for child in root.iterdir()
                    if child.is_dir() and _is_article_directory(child)
                )

    articles: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for path in sorted(candidates, key=lambda item: str(item).casefold()):
        try:
            articles.append(
                inspect_article_root(path)
            )
        except ValueError as exc:
            skipped.append({"path": str(path), "reason": str(exc)})
    return articles, skipped


def group_logical_articles(roots: Iterable[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in roots:
        project_id = next(iter(item.get("project_ids") or []), "")
        fingerprint = stripped_text(item.get("title_fingerprint")) or str(item.get("path"))
        groups.setdefault((project_id.casefold(), fingerprint), []).append(item)
    return [
        sorted(group, key=lambda item: str(item.get("path") or "").casefold())
        for _key, group in sorted(groups.items())
    ]
