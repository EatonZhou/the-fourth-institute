from __future__ import annotations
from management_common.product_environment import read_product_env
from management_common.portal_paths import path as portal_path
from management_common.secure_paths import relative_parts
from management_common import process_runtime
from management_common.clock import utc_now_iso
from management_common.text_values import stripped_text

import codecs
import functools
import hashlib
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator
from urllib.parse import quote, unquote

from management_portal.resources import get_resource_service
from management_portal.resources.registry_store import announce_registry_changes, get_registry_store

from . import registry_sync
from . import repo_workspace_backend as git_workspace_backend
from . import resource_identity_backend as repo_backend


REGISTRY_SCHEMA = "tfi.article.v1"
ARTICLE_COLLECTION = "article"
from management_portal import article_build_store
# Compiled PDFs and LaTeX intermediates are a cache: compiling again rebuilds them.
DEFAULT_BUILD_ROOT = Path(
    read_product_env("PORTAL_ARTICLE_BUILD_ROOT", str(portal_path("staging_root") / "shared" / "portal_cache" / "article_builds"))
).expanduser()
ARTICLE_DIRECTORY_NAMES = {"article", "articles", "artical", "artical_arxiv"}
ARTICLE_UPLOAD_MAX_BYTES = 25 * 1024 * 1024
def _tinytex_bin_dirs() -> tuple[Path, ...]:
    # Installed tools only; custom TeX installations continue to use PATH.
    home = Path.home()
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming") / "TinyTeX"
    elif sys.platform == "darwin":
        root = home / "Library" / "TinyTeX"
    else:
        root = home / ".TinyTeX"
    return tuple(sorted(path for path in (root / "bin").glob("*") if path.is_dir()))


TEX_BIN_DIRS = _tinytex_bin_dirs()


def _tex_search_path() -> str:
    return os.pathsep.join([*(str(item) for item in TEX_BIN_DIRS if item.is_dir()), os.environ.get("PATH", "")])


def _tex_which(name: str) -> str | None:
    return shutil.which(name, path=_tex_search_path())


SUPPORTED_LATEX_COMPILERS = {
    "pdflatex": {
        "label": "pdfLaTeX",
        "latexmk_flag": "-pdf",
        "engine_option": "-pdflatex",
    },
    "xelatex": {
        "label": "XeLaTeX",
        "latexmk_flag": "-xelatex",
        "engine_option": "-pdfxelatex",
    },
    "lualatex": {
        "label": "LuaLaTeX",
        "latexmk_flag": "-lualatex",
        "engine_option": "-pdflualatex",
    },
}
ARTICLE_COMPILE_MODES = {"normal", "draft"}
ARTICLE_ERROR_HANDLING = {"continue", "stop"}
ARTICLE_BUILD_PROFILE_SCHEMA = article_build_store.SETTINGS_SCHEMA
_ACTIVE_ARTICLE_BUILDS_LOCK = threading.RLock()
_ACTIVE_ARTICLE_BUILDS: dict[str, subprocess.Popen[str]] = {}
_RUNNING_ARTICLE_BUILDS: set[str] = set()
_STOP_REQUESTED_ARTICLE_BUILDS: set[str] = set()
ARTICLE_TEXT_SUFFIXES = {
    ".bib",
    ".cfg",
    ".cls",
    ".csv",
    ".json",
    ".latexmkrc",
    ".md",
    ".markdown",
    ".py",
    ".sty",
    ".tex",
    ".txt",
    ".yaml",
    ".yml",
}
ARTICLE_TREE_IGNORED_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "node_modules",
}
ARTICLE_TREE_IGNORED_SUFFIXES = {
    ".aux",
    ".bbl",
    ".bcf",
    ".blg",
    ".fdb_latexmk",
    ".fls",
    ".log",
    ".out",
    ".run.xml",
    ".synctex.gz",
    ".toc",
}
ARTICLE_SEMANTIC_TEX_COMMAND_LEVELS = {
    "part": 0,
    "chapter": 0,
    "section": 0,
    "subsection": 1,
    "subsubsection": 2,
    "paragraph": 3,
    "subparagraph": 4,
}
ARTICLE_FIGURE_SUFFIXES = (".pdf", ".png", ".jpg", ".jpeg", ".eps", ".svg")
ARTICLE_REFERENCE_COMMANDS = ("ref", "eqref", "pageref", "autoref", "cref", "Cref")
ARTICLE_CITATION_COMMANDS = (
    "cite",
    "citep",
    "citet",
    "citealp",
    "citeauthor",
    "citeyear",
    "parencite",
    "textcite",
    "autocite",
    "footcite",
)
ARTICLE_INCLUDE_COMMANDS = ("input", "include", "subfile")
_BIB_ENTRY_START = re.compile(r"(?im)^[ \t]*@(?P<type>[A-Za-z]+)\s*\{\s*(?P<key>[^,\s]+)\s*,")


def _dedupe(values: Iterable[object]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = stripped_text(value)
        if not item:
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _read_text(path: Path, limit: int = 2 * 1024 * 1024) -> str:
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        return stream.read(limit)


def _strip_tex_comments(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        output: list[str] = []
        escaped = False
        in_comment = False
        for char in line:
            if in_comment:
                output.append(" ")
                continue
            if char == "%" and not escaped:
                in_comment = True
                output.append(" ")
                continue
            output.append(char)
            escaped = char == "\\" and not escaped
            if char != "\\":
                escaped = False
        lines.append("".join(output))
    return "\n".join(lines)


def _balanced_argument(text: str, opening_index: int) -> tuple[str, int] | None:
    if opening_index < 0 or opening_index >= len(text) or text[opening_index] != "{":
        return None
    depth = 0
    escaped = False
    for index in range(opening_index, len(text)):
        char = text[index]
        if char == "\\" and not escaped:
            escaped = True
            continue
        if not escaped:
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[opening_index + 1 : index], index + 1
        escaped = False
    return None


def _tex_macros(text: str) -> dict[str, str]:
    macros: dict[str, str] = {}
    pattern = re.compile(r"\\(?:re)?newcommand\s*\{?\\([A-Za-z@]+)\}?\s*\{")
    for match in pattern.finditer(text):
        argument = _balanced_argument(text, match.end() - 1)
        if argument:
            macros[match.group(1)] = argument[0]
    return macros


def _plain_tex(value: str, macros: dict[str, str] | None = None) -> str:
    text = value
    for name, replacement in (macros or {}).items():
        text = re.sub(
            rf"\\{re.escape(name)}(?:\{{\}})?",
            lambda _match, replacement=replacement: replacement,
            text,
        )
    wrapper = re.compile(
        r"\\(?:textbf|textit|emph|textrm|textsf|texttt|textsc|mathrm|mathbf|mathit|operatorname)\s*\{([^{}]*)\}"
    )
    for _ in range(6):
        updated = wrapper.sub(r"\1", text)
        if updated == text:
            break
        text = updated
    text = re.sub(r"\\(?:Large|LARGE|large|bf|it|rm|sf|tt|normalsize|small|footnotesize)\b", " ", text)
    text = re.sub(r"\\[A-Za-z@]+\*?(?:\[[^\]]*\])?", " ", text)
    text = text.replace("~", " ").replace("\\&", "&").replace("\\%", "%")
    text = text.replace("{", " ").replace("}", " ")
    return re.sub(r"\s+", " ", text).strip(" :-\t\r\n")


def extract_article_title(path: str | Path) -> str:
    manuscript = Path(path)
    try:
        text = _read_text(manuscript)
    except OSError:
        return ""
    if manuscript.suffix.casefold() in {".md", ".markdown"}:
        match = re.search(r"(?m)^#\s+(.+?)\s*$", text)
        return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""
    clean_text = _strip_tex_comments(text)
    macros = _tex_macros(clean_text)
    for match in re.finditer(r"\\title(?:\[[^\]]*\])?\s*\{", clean_text):
        argument = _balanced_argument(clean_text, match.end() - 1)
        if argument:
            title = _plain_tex(argument[0], macros)
            if title:
                return title
    for match in re.finditer(r"\\thusetup\s*\{", clean_text):
        setup = _balanced_argument(clean_text, match.end() - 1)
        if not setup:
            continue
        title_match = re.search(r"(?:^|,)\s*title\s*=\s*\{", setup[0])
        if not title_match:
            continue
        argument = _balanced_argument(setup[0], title_match.end() - 1)
        if argument:
            title = _plain_tex(argument[0], macros)
            if title:
                return title
    return ""


def title_fingerprint(title: object) -> str:
    normalized = stripped_text(title).casefold()
    return re.sub(r"[^0-9a-z\u0080-\uffff]+", "", normalized)


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
        clean = _strip_tex_comments(_read_text(path))
    except OSError:
        return False
    return re.search(r"\\documentclass(?:\s*\[[^\]]*\])?\s*\{", clean) is not None


def _tex_program_hint(path: Path) -> str:
    if path.suffix.casefold() != ".tex":
        return ""
    try:
        text = _read_text(path, limit=16 * 1024)
    except OSError:
        return ""
    match = re.search(
        r"(?im)^\s*%\s*!\s*TeX\s+program\s*=\s*(pdflatex|xelatex|lualatex)\s*$",
        text,
    )
    return match.group(1).casefold() if match else ""


def _manuscript_title(path: Path) -> str:
    title = extract_article_title(path)
    if title or path.suffix.casefold() != ".tex":
        return title
    try:
        clean = _strip_tex_comments(_read_text(path))
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
            title = extract_article_title(candidate)
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
    titled = [(path, extract_article_title(path)) for path in candidates]
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
        relative = _safe_relative_path(primary_file)
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
        "title_fingerprint": title_fingerprint(title),
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


def _record_match_keys(record: dict[str, Any]) -> Iterator[tuple[str, ...]]:
    for root in record.get("roots") or []:
        if isinstance(root, dict) and root.get("path"):
            yield ("path", registry_sync.resolved_path(root.get("path")))
    fingerprints = _dedupe(
        [*record.get("title_fingerprints", []), title_fingerprint(record.get("title"))]
    )
    for project_id in record.get("project_ids") or [""]:
        for fingerprint in fingerprints:
            yield ("title", stripped_text(project_id).casefold(), fingerprint)


def _root_match_keys(item: dict[str, Any]) -> Iterator[tuple[str, ...]]:
    """A manuscript root belongs to the record holding its path or, within one
    project, the record whose title it shares."""
    yield ("path", registry_sync.resolved_path(item["path"]))
    fingerprint = stripped_text(item.get("title_fingerprint"))
    for project_id in item.get("project_ids") or [""]:
        yield ("title", stripped_text(project_id).casefold(), fingerprint)


ARTICLE_REGISTRY = registry_sync.RegistrySync(
    resource_type="article",
    collection=ARTICLE_COLLECTION,
    location="article root",
    record_keys=_record_match_keys,
    location_keys=_root_match_keys,
)
_validated_article = ARTICLE_REGISTRY.validated


def load_article_records() -> list[dict[str, Any]]:
    return list(ARTICLE_REGISTRY.records().values())


def is_article_storage_path(
    path: str | Path,
) -> bool:
    candidate = Path(path).expanduser().resolve(strict=False)
    for record in load_article_records():
        for root in record.get("roots") or []:
            if not isinstance(root, dict) or not root.get("path"):
                continue
            try:
                candidate.relative_to(
                    Path(stripped_text(root.get("path"))).resolve(strict=False)
                )
            except ValueError:
                continue
            return True
    for ancestor in (candidate, *candidate.parents):
        if not ancestor.is_dir() or not _is_article_directory(ancestor):
            continue
        try:
            if _primary_manuscript(ancestor) is not None:
                return True
        except OSError:
            continue
    return False


def get_article_record(article_id: str) -> dict[str, Any]:
    if not get_resource_service().is_valid_id(article_id, "article"):
        raise ValueError(f"invalid article address: {article_id}")
    record = get_registry_store().get(ARTICLE_COLLECTION, article_id)
    if record is None:
        raise KeyError(article_id)
    return _validated_article(article_id, record)


def _article_root_role(path: str) -> str:
    lowered = path.casefold()
    if "arxiv" in lowered:
        return "arxiv"
    if any(token in lowered for token in ("camera_ready", "camera-ready", "submission")):
        return "submission"
    return "canonical"


def _select_root(roots: list[dict[str, Any]]) -> dict[str, Any]:
    rank = {"canonical": 0, "submission": 1, "arxiv": 2, "variant": 3}
    available = [item for item in roots if Path(stripped_text(item.get("path"))).is_dir()]
    pool = available or roots
    if not pool:
        raise FileNotFoundError("article has no manuscript root")
    return min(
        pool,
        key=lambda item: (
            rank.get(stripped_text(item.get("role")), 9),
            len(stripped_text(item.get("path"))),
            stripped_text(item.get("path")).casefold(),
        ),
    )


def _root_entry(item: dict[str, Any]) -> dict[str, Any]:
    path = registry_sync.resolved_path(item["path"])
    return {
        "path": path,
        "role": _article_root_role(path),
        "present": Path(path).is_dir(),
        "primary_file": stripped_text(item.get("primary_file")),
        "title": stripped_text(item.get("title")),
        "title_fingerprint": stripped_text(item.get("title_fingerprint")),
    }


def _merge_record(
    existing: dict[str, Any] | None,
    article_id: str,
    group: list[dict[str, Any]],
    explicit_project_ids: Iterable[str] = (),
) -> dict[str, Any]:
    previous = dict(existing or {})
    roots = registry_sync.merge_locations(previous.get("roots") or [], map(_root_entry, group))
    canonical = _select_root(roots)
    title = stripped_text(canonical.get("title")) or stripped_text(previous.get("title")) or article_id
    project_ids = _dedupe(
        [
            *previous.get("project_ids", []),
            *explicit_project_ids,
            *[project for item in group for project in item.get("project_ids") or []],
        ]
    )
    fingerprints = _dedupe(
        [
            *previous.get("title_fingerprints", []),
            title_fingerprint(previous.get("title")),
            *[item.get("title_fingerprint") for item in roots],
        ]
    )
    now = utc_now_iso()
    return {
        "schema": REGISTRY_SCHEMA,
        "id": article_id,
        "type": "article",
        "title": title,
        "status": stripped_text(previous.get("status")) or "active",
        "project_ids": project_ids,
        "canonical_root": stripped_text(canonical.get("path")),
        "primary_file": stripped_text(canonical.get("primary_file")),
        "roots": roots,
        "title_fingerprints": fingerprints,
        "created_at": stripped_text(previous.get("created_at")) or now,
        "updated_at": now,
    }


def scan_and_sync_article_registry(
    roots: Iterable[str | Path] | None = None,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    article_roots, skipped = discover_article_roots(roots)
    groups = group_logical_articles(article_roots)
    report = ARTICLE_REGISTRY.sync(groups, merge=_merge_record, apply=apply)
    report.update(
        {
            "article_root_count": len(article_roots),
            "skipped_count": len(skipped),
            "skipped": skipped,
        }
    )
    return report


def register_article(
    path: str | Path,
    *,
    project_ids: Iterable[str] = (),
    article_id: str = "",
    primary_file: str = "",
    compiler: str = "",
    build_root: str | Path | None = None,
) -> dict[str, Any]:
    item = inspect_article_root(path, primary_file=primary_file)
    report = ARTICLE_REGISTRY.sync(
        [[item]],
        merge=functools.partial(_merge_record, explicit_project_ids=tuple(project_ids)),
        apply=True,
        forced_id=article_id,
    )
    changed = [*report["created"], *report["updated"]]
    if changed:
        record = changed[0]
        if str(item["primary_file"]).casefold().endswith(".tex"):
            update_article_settings(
                str(record.get("id") or ""),
                {
                    "primary_file": item["primary_file"],
                    "compiler": compiler or _tex_program_hint(
                        Path(item["path"]) / item["primary_file"]
                    ) or "pdflatex",
                },
                build_root=build_root,
            )
        return record
    resolved_path = str(Path(path).resolve(strict=False))
    for record in load_article_records():
        if any(
            isinstance(root, dict)
            and str(Path(stripped_text(root.get("path"))).resolve(strict=False)) == resolved_path
            for root in record.get("roots") or []
        ):
            if str(item["primary_file"]).casefold().endswith(".tex"):
                update_article_settings(
                    str(record.get("id") or ""),
                    {
                        "primary_file": item["primary_file"],
                        "compiler": compiler or _tex_program_hint(
                            Path(item["path"]) / item["primary_file"]
                        ) or "pdflatex",
                    },
                    build_root=build_root,
                )
            return record
    raise KeyError(path)


def unregister_article(article_id: str) -> dict[str, Any]:
    """Remove an article registry record while preserving every manuscript file."""
    if not get_resource_service().is_valid_id(article_id, "article"):
        raise ValueError(f"invalid article id: {article_id}")
    with get_registry_store().writer(ARTICLE_COLLECTION) as writer:
        stored = writer.get(ARTICLE_COLLECTION, article_id)
        if stored is None:
            raise KeyError(article_id)
        record = _validated_article(article_id, stored)
        writer.delete(ARTICLE_COLLECTION, article_id)
    announce_registry_changes(ARTICLE_COLLECTION, {article_id: "deleted"}, resource_type="article")
    return {
        "id": article_id,
        "deleted": True,
        "manuscript_paths": [
            stripped_text(item.get("path"))
            for item in record.get("roots") or []
            if isinstance(item, dict) and stripped_text(item.get("path"))
        ],
    }


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


def _synctex_command() -> str:
    candidates = (_tex_which("synctex"),)
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise RuntimeError("SyncTeX command line utility is not available")


def compiled_article_pdf(
    article_id: str,
    *,
    build_root: str | Path | None = None,
) -> Path:
    path = article_build_pdf_path(article_id, build_root=build_root)
    if not path.is_file():
        raise FileNotFoundError(article_id)
    return path


def _article_source_context(
    article_id: str,
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    record = get_article_record(article_id)
    root_item = _select_root(
        [item for item in record.get("roots") or [] if isinstance(item, dict)]
    )
    article_root = Path(stripped_text(root_item.get("path"))).resolve(strict=False)
    if not article_root.is_dir():
        raise FileNotFoundError(article_root)
    return record, root_item, article_root


def article_git_status(
    article_id: str,
) -> dict[str, Any]:
    _record, _root_item, article_root = _article_source_context(
        article_id
    )
    return git_workspace_backend.scoped_git_status(article_root)


def article_git_diff(
    article_id: str,
    path: str,
) -> dict[str, Any]:
    _record, _root_item, article_root = _article_source_context(
        article_id
    )
    return git_workspace_backend.scoped_git_diff(article_root, path)


def article_git_commit_diff(
    article_id: str,
    sha: str,
) -> dict[str, Any]:
    _record, _root_item, article_root = _article_source_context(
        article_id
    )
    return git_workspace_backend.scoped_git_commit_diff(article_root, sha)


def initialize_article_git(
    article_id: str,
) -> dict[str, Any]:
    _record, _root_item, article_root = _article_source_context(
        article_id
    )
    return git_workspace_backend.initialize_scoped_git_workspace(article_root)


def article_git_history(
    article_id: str,
    *,
    limit: int = 20,
) -> dict[str, Any]:
    _record, _root_item, article_root = _article_source_context(
        article_id
    )
    return git_workspace_backend.scoped_git_history(article_root, limit=limit)


def stage_article_git_paths(
    article_id: str,
    paths: Iterable[str],
    *,
    staged: bool,
) -> dict[str, Any]:
    _record, _root_item, article_root = _article_source_context(
        article_id
    )
    return git_workspace_backend.stage_scoped_git_paths(
        article_root, paths, staged=staged
    )


def commit_article_git_changes(
    article_id: str,
    message: str,
    *,
    description: str = "",
    actor: str = "",
) -> dict[str, Any]:
    _record, _root_item, article_root = _article_source_context(
        article_id
    )
    return git_workspace_backend.commit_scoped_git_changes(
        article_root,
        message,
        description=description,
        actor=actor,
    )


def _article_target_path(
    article_id: str,
    raw_path: str,
    *,
    allow_root: bool = False,
) -> tuple[Path, Path]:
    _record, _root_item, article_root = _article_source_context(
        article_id
    )
    if allow_root and not stripped_text(raw_path):
        return article_root, article_root
    relative_path = _safe_relative_path(stripped_text(raw_path))
    target = (article_root / relative_path).resolve(strict=False)
    try:
        target.relative_to(article_root)
    except ValueError as exc:
        raise ValueError("article path escapes the article root") from exc
    return article_root, target


def _is_generated_latex_file(path: Path) -> bool:
    lowered = path.name.casefold()
    return any(lowered.endswith(suffix) for suffix in ARTICLE_TREE_IGNORED_SUFFIXES)


def list_article_files(
    article_id: str,
    *,
    build_root: str | Path | None = None,
    limit: int = 2500,
) -> dict[str, Any]:
    record, root_item, article_root = _article_source_context(
        article_id
    )
    settings = load_article_settings(
        article_id, build_root=build_root
    )
    entries: list[dict[str, Any]] = []
    truncated = False
    for folder, directory_names, file_names in os.walk(article_root, followlinks=False):
        current = Path(folder)
        directory_names[:] = sorted(
            [
                name
                for name in directory_names
                if name not in ARTICLE_TREE_IGNORED_DIRECTORIES
                and not (current / name).is_symlink()
            ],
            key=str.casefold,
        )
        for name in directory_names:
            path = current / name
            relative = path.relative_to(article_root).as_posix()
            entries.append({"path": relative, "name": name, "type": "directory"})
            if len(entries) >= limit:
                truncated = True
                break
        if truncated:
            break
        for name in sorted(file_names, key=str.casefold):
            path = current / name
            if path.is_symlink() or _is_generated_latex_file(path):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            relative = path.relative_to(article_root).as_posix()
            entries.append(
                {
                    "path": relative,
                    "name": name,
                    "type": "file",
                    "size": stat.st_size,
                    "modified_at": datetime.fromtimestamp(
                        stat.st_mtime
                    ).astimezone().isoformat(timespec="seconds"),
                    "editable": (
                        path.suffix.casefold() in ARTICLE_TEXT_SUFFIXES
                        or path.name.casefold() == ".latexmkrc"
                    ),
                    "primary": relative == settings["primary_file"],
                }
            )
            if len(entries) >= limit:
                truncated = True
                break
        if truncated:
            break
    return {
        "id": article_id,
        "root": str(article_root),
        "primary_file": settings["primary_file"],
        "record_primary_file": stripped_text(
            root_item.get("primary_file") or record.get("primary_file")
        ),
        "entries": entries,
        "truncated": truncated,
    }


def read_article_file(
    article_id: str,
    relative_path: str,
    *,
    max_bytes: int = 8 * 1024 * 1024,
) -> dict[str, Any]:
    article_root, target = _article_target_path(
        article_id, relative_path
    )
    if not target.is_file():
        raise FileNotFoundError(target)
    data = target.read_bytes()
    if len(data) > max_bytes:
        raise ValueError(f"article file is too large to edit ({len(data)} bytes)")
    if b"\x00" in data:
        raise ValueError("binary article files cannot be opened in the text editor")
    stat = target.stat()
    revision = hashlib.sha256(data).hexdigest()
    return {
        "id": article_id,
        "path": target.relative_to(article_root).as_posix(),
        "name": target.name,
        "content": data.decode("utf-8", errors="replace"),
        "size": len(data),
        "revision": revision,
        "modified_at": datetime.fromtimestamp(
            stat.st_mtime
        ).astimezone().isoformat(timespec="seconds"),
    }


def resolve_article_raw_file(
    article_id: str,
    relative_path: str,
) -> Path:
    _article_root, target = _article_target_path(
        article_id, relative_path
    )
    if not target.is_file():
        raise FileNotFoundError(target)
    return target


def describe_article_file(
    article_id: str,
    relative_path: str,
) -> dict[str, Any]:
    target = resolve_article_raw_file(
        article_id, relative_path
    )
    stat = target.stat()
    with target.open("rb") as stream:
        sample = stream.read(4096)
    binary = b"\x00" in sample
    try:
        codecs.getincrementaldecoder("utf-8")().decode(sample, final=False)
    except UnicodeDecodeError:
        binary = True
    raw_url = f"/api/articles/{article_id}/raw?path={quote(relative_path, safe='')}"
    return {
        "id": article_id,
        "path": relative_path,
        "name": target.name,
        "mediaType": mimetypes.guess_type(target.name)[0] or "application/octet-stream",
        "binary": binary,
        "size": stat.st_size,
        "version": str(stat.st_mtime_ns),
        "rawUrl": raw_url,
        "downloadUrl": f"{raw_url}&download=1",
    }


def write_article_file(
    article_id: str,
    relative_path: str,
    content: str,
    *,
    max_bytes: int = 8 * 1024 * 1024,
) -> dict[str, Any]:
    article_root, target = _article_target_path(
        article_id, relative_path
    )
    if not target.is_file():
        raise FileNotFoundError(target)
    encoded = str(content).encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError(f"article file is too large to save ({len(encoded)} bytes)")
    mode = target.stat().st_mode
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            from management_common.file_permissions import prepare_atomic_permissions
            prepare_atomic_permissions(Path(temporary_name), target, mode)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, target)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
    stat = target.stat()
    return {
        "id": article_id,
        "path": target.relative_to(article_root).as_posix(),
        "size": stat.st_size,
        "revision": hashlib.sha256(encoded).hexdigest(),
        "modified_at": datetime.fromtimestamp(
            stat.st_mtime
        ).astimezone().isoformat(timespec="seconds"),
    }


def create_article_entry(
    article_id: str,
    relative_path: str,
    kind: str,
) -> dict[str, Any]:
    article_root, target = _article_target_path(
        article_id, relative_path
    )
    entry_kind = stripped_text(kind).casefold()
    if entry_kind not in {"file", "directory"}:
        raise ValueError("article entry kind must be file or directory")
    if target.exists():
        raise FileExistsError(target)
    if not target.parent.is_dir():
        raise FileNotFoundError(target.parent)
    if entry_kind == "directory":
        target.mkdir()
    else:
        target.touch(exist_ok=False)
    return {
        "id": article_id,
        "path": target.relative_to(article_root).as_posix(),
        "name": target.name,
        "type": entry_kind,
    }


def upload_article_file(
    article_id: str,
    *,
    filename: str,
    data: bytes,
    directory: str = "",
    max_bytes: int = ARTICLE_UPLOAD_MAX_BYTES,
) -> dict[str, Any]:
    clean_name = Path(stripped_text(filename)).name
    if not clean_name or clean_name in {".", ".."} or "\x00" in clean_name:
        raise ValueError("uploaded article filename is invalid")
    if len(data) > max_bytes:
        raise ValueError(f"uploaded article file is too large ({len(data)} bytes)")
    article_root, target_directory = _article_target_path(
        article_id,
        directory,
        allow_root=True,
    )
    if not target_directory.is_dir():
        raise FileNotFoundError(target_directory)
    destination = (target_directory / clean_name).resolve(strict=False)
    try:
        destination.relative_to(article_root)
    except ValueError as exc:
        raise ValueError("uploaded article path escapes the article root") from exc
    if destination.exists():
        raise FileExistsError(destination)
    with destination.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return {
        "id": article_id,
        "path": destination.relative_to(article_root).as_posix(),
        "name": destination.name,
        "type": "file",
        "size": destination.stat().st_size,
    }


def search_article_files(
    article_id: str,
    query: str,
    *,
    limit: int = 200,
) -> dict[str, Any]:
    needle = stripped_text(query)
    if not needle:
        return {"id": article_id, "query": "", "results": [], "truncated": False}
    if len(needle) > 200:
        raise ValueError("article search query is too long")
    tree = list_article_files(article_id)
    lowered = needle.casefold()
    results: list[dict[str, Any]] = []
    truncated = False
    for entry in tree["entries"]:
        if entry.get("type") != "file" or not entry.get("editable"):
            continue
        try:
            payload = read_article_file(
                article_id,
                str(entry.get("path") or ""),
                max_bytes=2 * 1024 * 1024,
            )
        except (OSError, ValueError):
            continue
        for line_number, line in enumerate(
            str(payload.get("content") or "").splitlines(), start=1
        ):
            column = line.casefold().find(lowered)
            if column < 0:
                continue
            results.append(
                {
                    "path": entry["path"],
                    "line": line_number,
                    "column": column + 1,
                    "preview": line.strip()[:320],
                }
            )
            if len(results) >= max(1, min(int(limit), 500)):
                truncated = True
                break
        if truncated:
            break
    return {
        "id": article_id,
        "query": needle,
        "results": results,
        "truncated": truncated,
    }


def _text_line_column(text: str, offset: int) -> tuple[int, int]:
    bounded = max(0, min(int(offset), len(text)))
    line = text.count("\n", 0, bounded) + 1
    line_start = text.rfind("\n", 0, bounded) + 1
    return line, bounded - line_start + 1


def _tex_command_arguments(
    text: str,
    commands: Iterable[str],
    *,
    optional_arguments: bool = True,
) -> Iterator[tuple[str, str, int, int]]:
    names = "|".join(re.escape(name) for name in commands)
    optional = r"(?:\s*\[[^\]\r\n]*\])*" if optional_arguments else ""
    pattern = re.compile(
        rf"\\(?P<command>{names})\*?{optional}\s*\{{",
        re.IGNORECASE,
    )
    for match in pattern.finditer(text):
        argument = _balanced_argument(text, match.end() - 1)
        if argument is None:
            continue
        yield match.group("command"), argument[0], match.start(), argument[1]


def _resolved_article_reference(
    article_root: Path,
    source_path: Path,
    raw_target: str,
    *,
    suffixes: Iterable[str],
) -> tuple[str, bool]:
    cleaned = str(raw_target or "").strip().replace("\\", "/")
    if not cleaned or any(token in cleaned for token in ("#", "$", "{", "}")):
        return cleaned, False
    candidate = (source_path.parent / cleaned).resolve(strict=False)
    try:
        candidate.relative_to(article_root)
    except ValueError:
        return cleaned, False
    candidates = [candidate]
    if not candidate.suffix:
        candidates.extend(candidate.with_suffix(suffix) for suffix in suffixes)
    existing = next((item for item in candidates if item.is_file()), None)
    resolved = existing or candidates[0]
    return resolved.relative_to(article_root).as_posix(), existing is not None


def _bib_field(block: str, name: str) -> str:
    match = re.search(
        rf"(?is)(?:^|,)\s*{re.escape(name)}\s*=\s*(?:\{{(?P<braced>.*?)\}}|\"(?P<quoted>.*?)\")\s*(?:,|$)",
        block,
    )
    if not match:
        return ""
    return re.sub(
        r"\s+",
        " ",
        _plain_tex(match.group("braced") or match.group("quoted") or ""),
    ).strip()


def article_semantic_index(
    article_id: str,
    *,
    build_root: str | Path | None = None,
    limit: int = 8000,
) -> dict[str, Any]:
    """Build project-wide LaTeX navigation without mutating the manuscript."""
    _record, _root_item, article_root = _article_source_context(
        article_id
    )
    settings = load_article_settings(
        article_id, build_root=build_root
    )
    tree = list_article_files(
        article_id,
        build_root=build_root,
        limit=max(2500, limit),
    )
    index = _ArticleSemanticIndex(article_root)
    for relative in _semantic_source_paths(tree):
        if index.add_file(relative) and index.total_items >= limit:
            break
    index.add_problems()
    primary = str(settings.get("primary_file") or "")
    index.order_documents(primary)
    return index.payload(
        article_id,
        primary,
        truncated=index.total_items >= limit or bool(tree.get("truncated")),
    )


def _semantic_source_paths(tree: dict[str, Any]) -> list[str]:
    """The editable LaTeX and BibTeX files of a file listing."""
    return [
        str(entry.get("path") or "")
        for entry in tree["entries"]
        if entry.get("type") == "file"
        and entry.get("editable")
        and Path(str(entry.get("path") or "")).suffix.casefold() in {".tex", ".bib"}
    ]


def _reachable_documents(primary: str, include_targets: dict[str, list[str]]) -> list[str]:
    """The files the primary file includes, transitively, in breadth-first order."""
    reachable: list[str] = []
    pending = [primary] if primary else []
    seen: set[str] = set()
    while pending:
        current = pending.pop(0)
        if current in seen:
            continue
        seen.add(current)
        reachable.append(current)
        pending.extend(include_targets.get(current, []))
    return reachable


class _ArticleSemanticIndex:
    """The navigation items of an article's LaTeX and BibTeX files, and the problems among them."""

    def __init__(self, article_root: Path) -> None:
        self.article_root = article_root
        self.headings: list[dict[str, Any]] = []
        self.labels: list[dict[str, Any]] = []
        self.references: list[dict[str, Any]] = []
        self.citations: list[dict[str, Any]] = []
        self.bibliography: list[dict[str, Any]] = []
        self.figures: list[dict[str, Any]] = []
        self.includes: list[dict[str, Any]] = []
        self.documents: list[dict[str, Any]] = []
        self.problems: list[dict[str, Any]] = []
        self.total_items = 0

    def add_file(self, relative: str) -> bool:
        """Index one file; ``False`` when it cannot be read."""
        path = self.article_root / relative
        try:
            text = _read_text(path, limit=8 * 1024 * 1024)
        except OSError:
            return False
        clean = _strip_tex_comments(text)
        bibliography = path.suffix.casefold() == ".bib"
        document = {
            "path": relative,
            "kind": "bibliography" if bibliography else "latex",
            "heading_count": 0,
            "label_count": 0,
            "citation_count": 0,
            "figure_count": 0,
        }
        if bibliography:
            document["citation_count"] = self._add_bibliography(relative, clean)
        else:
            self._add_latex(document, path, clean)
        self.documents.append(document)
        return True

    def _add_bibliography(self, relative: str, clean: str) -> int:
        starts = list(_BIB_ENTRY_START.finditer(clean))
        for index, match in enumerate(starts):
            end = starts[index + 1].start() if index + 1 < len(starts) else len(clean)
            block = clean[match.end() : end]
            line, column = _text_line_column(clean, match.start())
            self.bibliography.append({
                "kind": "bib_entry",
                "entry_type": match.group("type").casefold(),
                "key": match.group("key").strip(),
                "title": _bib_field(block, "title"),
                "author": _bib_field(block, "author"),
                "path": relative,
                "line": line,
                "column": column,
            })
            self.total_items += 1
        return len(starts)

    def _add_latex(self, document: dict[str, Any], path: Path, clean: str) -> None:
        relative = document["path"]
        document["heading_count"] = self._add_headings(relative, clean)
        self._add_labels(relative, clean)
        document["label_count"] = sum(1 for item in self.labels if item["path"] == relative)
        self._add_keys(self.references, "reference", relative, clean, ARTICLE_REFERENCE_COMMANDS)
        self._add_keys(self.citations, "citation", relative, clean, ARTICLE_CITATION_COMMANDS)
        document["citation_count"] = sum(
            1 for item in self.citations if item["path"] == relative
        )
        self._add_targets(self.figures, "figure", path, relative, clean, ("includegraphics",),
                          suffixes=ARTICLE_FIGURE_SUFFIXES, with_command=False)
        document["figure_count"] = sum(
            1 for item in self.figures if item["path"] == relative
        )
        self._add_targets(self.includes, "include", path, relative, clean, ARTICLE_INCLUDE_COMMANDS,
                          suffixes=(".tex",), with_command=True)

    def _add_headings(self, relative: str, clean: str) -> int:
        macros = _tex_macros(clean)
        count = 0
        for command, argument, offset, _end in _tex_command_arguments(
            clean, ARTICLE_SEMANTIC_TEX_COMMAND_LEVELS
        ):
            line, column = _text_line_column(clean, offset)
            self.headings.append({
                "kind": "heading",
                "command": command.casefold(),
                "title": _plain_tex(argument, macros) or command,
                "level": ARTICLE_SEMANTIC_TEX_COMMAND_LEVELS[command.casefold()],
                "path": relative,
                "line": line,
                "column": column,
            })
            count += 1
            self.total_items += 1
        return count

    def _add_labels(self, relative: str, clean: str) -> None:
        for _command, argument, offset, _end in _tex_command_arguments(
            clean, ("label",), optional_arguments=False
        ):
            line, column = _text_line_column(clean, offset)
            key = argument.strip()
            if not key:
                continue
            self.labels.append({
                "kind": "label",
                "key": key,
                "path": relative,
                "line": line,
                "column": column,
            })
            self.total_items += 1

    def _add_keys(
        self, items: list[dict[str, Any]], kind: str, relative: str, clean: str, commands: Iterable[str],
    ) -> None:
        """Each key a command names (``\\cite{a, b}`` names two)."""
        for command, argument, offset, _end in _tex_command_arguments(clean, commands):
            line, column = _text_line_column(clean, offset)
            for key in (part.strip() for part in argument.split(",")):
                if not key:
                    continue
                items.append({
                    "kind": kind,
                    "command": command,
                    "key": key,
                    "path": relative,
                    "line": line,
                    "column": column,
                })
                self.total_items += 1

    def _add_targets(
        self, items: list[dict[str, Any]], kind: str, path: Path, relative: str, clean: str,
        commands: Iterable[str], *, suffixes: Iterable[str], with_command: bool,
    ) -> None:
        """The files commands refer to, resolved inside the article root."""
        for command, argument, offset, _end in _tex_command_arguments(clean, commands):
            line, column = _text_line_column(clean, offset)
            resolved, exists = _resolved_article_reference(
                self.article_root,
                path,
                argument,
                suffixes=suffixes,
            )
            items.append({
                "kind": kind,
                **({"command": command} if with_command else {}),
                "target": argument.strip(),
                "resolved_path": resolved,
                "exists": exists,
                "path": relative,
                "line": line,
                "column": column,
            })
            self.total_items += 1

    def add_problems(self) -> None:
        """Link references and citations to their definitions; list what is duplicate or missing."""
        label_locations: dict[str, list[dict[str, Any]]] = {}
        for item in self.labels:
            label_locations.setdefault(item["key"], []).append(item)
        self._duplicate_labels(label_locations)
        self._link_references(label_locations)
        self._link_citations({item["key"]: item for item in self.bibliography})
        self._missing_targets(self.figures, "missing_figure", "图片文件 “{target}” 不存在")
        self._missing_targets(self.includes, "missing_include", "包含的 TeX 文件 “{target}” 不存在")

    def _duplicate_labels(self, label_locations: dict[str, list[dict[str, Any]]]) -> None:
        for key, locations in label_locations.items():
            if len(locations) < 2:
                continue
            for item in locations:
                self.problems.append({
                    "kind": "duplicate_label",
                    "severity": "warning",
                    "message": f"标签 “{key}” 在工程中定义了 {len(locations)} 次",
                    "key": key,
                    "path": item["path"],
                    "line": item["line"],
                    "column": item["column"],
                })

    def _link_references(self, label_locations: dict[str, list[dict[str, Any]]]) -> None:
        for item in self.references:
            if item["key"] in label_locations:
                item["definition"] = {
                    key: value
                    for key, value in label_locations[item["key"]][0].items()
                    if key in {"path", "line", "column"}
                }
                continue
            self.problems.append({
                "kind": "undefined_reference",
                "severity": "warning",
                "message": f"引用的标签 “{item['key']}” 未在工程中定义",
                **{key: item[key] for key in ("key", "path", "line", "column")},
            })

    def _link_citations(self, bibliography_by_key: dict[str, dict[str, Any]]) -> None:
        for item in self.citations:
            if item["key"] in bibliography_by_key:
                definition = bibliography_by_key[item["key"]]
                item["definition"] = {
                    key: definition[key] for key in ("path", "line", "column")
                }
                continue
            self.problems.append({
                "kind": "undefined_citation",
                "severity": "warning",
                "message": f"文献 “{item['key']}” 未在 .bib 文件中定义",
                **{key: item[key] for key in ("key", "path", "line", "column")},
            })

    def _missing_targets(self, items: list[dict[str, Any]], kind: str, message: str) -> None:
        for item in items:
            if item["exists"]:
                continue
            self.problems.append({
                "kind": kind,
                "severity": "error",
                "message": message.format(target=item["target"]),
                "path": item["path"],
                "line": item["line"],
                "column": item["column"],
                "target": item["target"],
            })

    def order_documents(self, primary: str) -> None:
        """Put the files the primary file reaches first, in include order, and mark them."""
        include_targets: dict[str, list[str]] = {}
        for item in self.includes:
            if item["exists"]:
                include_targets.setdefault(item["path"], []).append(item["resolved_path"])
        reachable = _reachable_documents(primary, include_targets)
        reachable_set = set(reachable)
        document_rank = {path: index for index, path in enumerate(reachable)}
        self.documents.sort(
            key=lambda item: (
                0 if item["path"] in reachable_set else 1,
                document_rank.get(item["path"], 10_000),
                item["path"].casefold(),
            )
        )
        for document in self.documents:
            document["reachable_from_primary"] = document["path"] in reachable_set

    def payload(self, article_id: str, primary: str, *, truncated: bool) -> dict[str, Any]:
        return {
            "schema": "tfi.article-semantic-index.v1",
            "id": article_id,
            "primary_file": primary,
            "generated_at": utc_now_iso(),
            "truncated": truncated,
            "documents": self.documents,
            "headings": self.headings,
            "labels": self.labels,
            "references": self.references,
            "citations": self.citations,
            "bibliography": self.bibliography,
            "figures": self.figures,
            "includes": self.includes,
            "problems": self.problems,
            "counts": {
                "documents": len(self.documents),
                "headings": len(self.headings),
                "labels": len(self.labels),
                "references": len(self.references),
                "citations": len(self.citations),
                "bibliography": len(self.bibliography),
                "figures": len(self.figures),
                "problems": len(self.problems),
            },
        }


@functools.lru_cache(maxsize=1)
def latex_environment() -> dict[str, Any]:
    available: list[str] = []
    version = ""
    for compiler in SUPPORTED_LATEX_COMPILERS:
        command = _tex_which(compiler)
        if not command:
            continue
        available.append(compiler)
        if not version:
            try:
                completed = subprocess.run(
                    [command, "--version"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    errors="replace",
                    timeout=12,
                    check=False,
                )
                first_line = next(
                    (
                        line.strip()
                        for line in str(completed.stdout or "").splitlines()
                        if line.strip()
                    ),
                    "",
                )
                match = re.search(r"TeX Live\s+([0-9]{4})", first_line)
                version = f"TeX Live {match.group(1)}" if match else first_line[:160]
            except (OSError, subprocess.SubprocessError):
                version = ""
    latexmk = _tex_which("latexmk") or ""
    texmf_root = ""
    kpsewhich = _tex_which("kpsewhich")
    if kpsewhich:
        try:
            texmf_root = subprocess.run(
                [kpsewhich, "--var-value=TEXMFROOT"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=12,
                check=False,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            texmf_root = ""
    package_database = Path(texmf_root) / "tlpkg" / "texlive.tlpdb"
    package_database_identity = ""
    try:
        stat = package_database.stat()
        package_database_identity = f"{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        package_database_identity = "unavailable"
    identity = "\n".join(
        [
            latexmk,
            version or "TeX Live",
            texmf_root,
            package_database_identity,
            *(f"{name}:{_tex_which(name) or ''}" for name in sorted(available)),
        ]
    )
    return {
        "latexmk": latexmk,
        "version": version or "TeX Live",
        "profile_id": f"portal-texlive-{hashlib.sha256(identity.encode()).hexdigest()[:12]}",
        "policy": "使用已配置的原生 TeX 工具链；隔离输出、固定区域设置，不执行任意构建命令",
        "texmf_root": texmf_root,
        "package_database_identity": package_database_identity,
        "available_compilers": [
            {
                "id": compiler,
                "label": SUPPORTED_LATEX_COMPILERS[compiler]["label"],
                "available": compiler in available,
            }
            for compiler in SUPPORTED_LATEX_COMPILERS
        ],
    }


@functools.lru_cache(maxsize=1024)
def _kpsewhich_file(name: str) -> str:
    command = _tex_which("kpsewhich")
    if not command or not name or not re.fullmatch(r"[A-Za-z0-9_.+@/-]+", name):
        return ""
    try:
        return subprocess.run(
            [command, name],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=12,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


@functools.lru_cache(maxsize=512)
def _fontconfig_family(name: str) -> str:
    command = shutil.which("fc-match")
    if not command or not name or any(character in name for character in "\\{}#"):
        return ""
    try:
        return subprocess.run(
            [command, "--format=%{family}", name],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=12,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _font_name_matches(requested: str, resolved: str) -> bool:
    normalize = lambda value: re.sub(r"[^a-z0-9]+", "", value.casefold())
    target = normalize(requested)
    return bool(target) and any(
        normalize(candidate) == target for candidate in resolved.split(",")
    )


def _latex_dependency_files(root: Path, source: Path) -> list[Path]:
    pending = [source]
    visited: set[Path] = set()
    while pending and len(visited) < 512:
        current = pending.pop()
        if current in visited or not current.is_file():
            continue
        visited.add(current)
        try:
            text = current.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in re.finditer(
            r"\\(?:input|include)\s*\{\s*([^{}]+?)\s*\}", text
        ):
            raw = match.group(1).strip()
            if not raw or "\\" in raw or "#" in raw:
                continue
            candidate = (current.parent / raw).with_suffix(Path(raw).suffix or ".tex")
            candidate = candidate.resolve(strict=False)
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            if candidate.is_file() and candidate not in visited:
                pending.append(candidate)
    return sorted(visited, key=lambda item: str(item).casefold())


def inspect_article_build_profile(
    path: str | Path,
    *,
    primary_file: str = "",
    compiler: str = "",
    require_toolchain: bool = True,
) -> dict[str, Any]:
    root = Path(path).expanduser().resolve(strict=False)
    if not root.is_dir():
        raise FileNotFoundError(f"article directory not found: {root}")
    candidates = _article_tex_candidates(root)
    selected = stripped_text(primary_file)
    if not selected and candidates:
        selected = str(candidates[0]["path"])
    selected_candidate = next(
        (item for item in candidates if item["path"] == selected), None
    )
    standalone = [item for item in candidates if item["standalone"]]
    issues: list[dict[str, Any]] = []

    def issue(
        severity: str,
        category: str,
        message: str,
        *,
        dependency: str = "",
        file_name: str = "",
    ) -> None:
        if not require_toolchain and category in {"environment", "dependency", "font"}:
            severity = "warning"
        issues.append(
            {
                "severity": severity,
                "category": category,
                "message": message,
                "dependency": dependency,
                "file": file_name,
            }
        )

    if not candidates:
        issue("error", "configuration", "目录中没有可编译的 .tex 文档")
    elif not selected_candidate:
        issue("error", "configuration", f"主文档不存在或不在候选列表中：{selected}")
    elif not selected_candidate["standalone"]:
        issue("error", "configuration", f"主文档缺少 \\documentclass：{selected}", file_name=selected)
    if not primary_file and len(standalone) > 1:
        issue(
            "warning",
            "configuration",
            f"检测到 {len(standalone)} 个可独立编译的主文档，请确认主文档",
        )

    selected_compiler = stripped_text(compiler).casefold()
    if not selected_compiler:
        selected_compiler = (
            str(selected_candidate.get("compiler_hint") or "")
            if selected_candidate
            else ""
        ) or "pdflatex"
    if selected_compiler not in SUPPORTED_LATEX_COMPILERS:
        issue("error", "configuration", f"不支持的 LaTeX 编译器：{selected_compiler}")
    elif not _tex_which(selected_compiler):
        issue("error", "environment", f"缺少 TeX 编译器 {selected_compiler}；请自行安装并加入 Portal 进程的 PATH 后再编译")
    if not _tex_which("latexmk"):
        issue("error", "environment", "缺少 latexmk；请自行安装并加入 Portal 进程的 PATH 后再编译")

    dependencies: list[dict[str, str]] = []
    if selected_candidate and selected_candidate["standalone"]:
        source = root / selected
        seen_dependencies: set[tuple[str, str]] = set()
        for dependency_file in _latex_dependency_files(root, source):
            try:
                text = dependency_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            relative = dependency_file.relative_to(root).as_posix()
            declarations = [
                ("document-class", match.group(1), ".cls")
                for match in re.finditer(
                    r"\\documentclass(?:\s*\[[^\]]*\])?\s*\{\s*([^{}]+?)\s*\}",
                    text,
                )
            ]
            declarations.extend(
                ("latex-package", name.strip(), ".sty")
                for match in re.finditer(
                    r"\\(?:usepackage|RequirePackage)(?:\s*\[[^\]]*\])?\s*\{\s*([^{}]+?)\s*\}",
                    text,
                )
                for name in match.group(1).split(",")
                if name.strip()
            )
            for kind, name, suffix in declarations:
                key = (kind, name)
                if key in seen_dependencies or not re.fullmatch(r"[A-Za-z0-9_.+@/-]+", name):
                    continue
                seen_dependencies.add(key)
                target_name = name if name.casefold().endswith(suffix) else f"{name}{suffix}"
                local_matches = [root / target_name, dependency_file.parent / target_name]
                resolved = next((str(item) for item in local_matches if item.is_file()), "")
                resolved = resolved or _kpsewhich_file(target_name)
                dependencies.append(
                    {"kind": kind, "name": name, "file": target_name, "resolved": resolved}
                )
                if not resolved:
                    issue(
                        "error",
                        "dependency",
                        f"缺少 {target_name}（由 {relative} 引用）",
                        dependency=name,
                        file_name=relative,
                    )
            font_matches = [
                match.group(1).strip()
                for match in re.finditer(
                    r"\\(?:set(?:main|sans|mono)font|setCJK(?:main|sans|mono)font)"
                    r"(?:\s*\[[^\]]*\])?\s*\{\s*([^{}]+?)\s*\}",
                    text,
                )
            ]
            font_matches.extend(
                match.group(1).strip()
                for match in re.finditer(
                    r"\\newfontfamily\s*\\[A-Za-z@]+(?:\s*\[[^\]]*\])?"
                    r"\s*\{\s*([^{}]+?)\s*\}",
                    text,
                )
            )
            for font_name in font_matches:
                key = ("font", font_name)
                if key in seen_dependencies:
                    continue
                seen_dependencies.add(key)
                resolved_family = _fontconfig_family(font_name)
                resolved = resolved_family if _font_name_matches(font_name, resolved_family) else ""
                dependencies.append(
                    {"kind": "font", "name": font_name, "file": "", "resolved": resolved}
                )
                if not resolved:
                    issue(
                        "error",
                        "font",
                        f"缺少字体 {font_name}（由 {relative} 引用）",
                        dependency=font_name,
                        file_name=relative,
                    )

    environment = latex_environment()
    blockers = sum(1 for item in issues if item["severity"] == "error")
    return {
        "schema": "tfi.article-build-preflight.v1",
        "path": str(root),
        "status": "blocked" if blockers else ("warning" if issues else "ready"),
        "requires_selection": not bool(primary_file) and len(standalone) > 1,
        "primary_file": selected,
        "compiler": selected_compiler,
        "candidates": candidates,
        "dependencies": dependencies,
        "issues": issues,
        "counts": {
            "error": blockers,
            "warning": sum(1 for item in issues if item["severity"] == "warning"),
        },
        "environment": environment,
        "project_ids": repo_backend.project_ids_for_path(root),
    }


def article_build_preflight(
    article_id: str,
    *,
    build_root: str | Path | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _record, _root_item, article_root = _article_source_context(
        article_id
    )
    profile = settings or load_article_settings(
        article_id, build_root=build_root
    )
    result = inspect_article_build_profile(
        article_root,
        primary_file=str(profile.get("primary_file") or ""),
        compiler=str(profile.get("compiler") or ""),
    )
    result["id"] = article_id
    return result


def load_article_settings(
    article_id: str,
    *,
    build_root: str | Path | None = None,
) -> dict[str, Any]:
    record, root_item, article_root = _article_source_context(
        article_id
    )
    default_primary_file = stripped_text(
        root_item.get("primary_file") or record.get("primary_file")
    )
    try:
        default_primary_path = article_root / _safe_relative_path(default_primary_file)
    except ValueError:
        default_primary_path = article_root
    defaults = {
        "compiler": _tex_program_hint(default_primary_path) or "pdflatex",
        "primary_file": default_primary_file,
        "use_project_latexmkrc": False,
        "shell_escape": False,
        "auto_compile": False,
        "compile_mode": "normal",
        "syntax_check": True,
        "error_handling": "continue",
    }
    stored = article_build_store.settings(article_id) or {}
    compiler = stripped_text(stored.get("compiler") or defaults["compiler"]).casefold()
    if compiler not in SUPPORTED_LATEX_COMPILERS:
        compiler = defaults["compiler"]
    try:
        primary_file = _safe_relative_path(
            stripped_text(stored.get("primary_file") or defaults["primary_file"])
        )
    except ValueError:
        primary_file = _safe_relative_path(defaults["primary_file"])
    compile_mode = stripped_text(
        stored.get("compile_mode") or defaults["compile_mode"]
    ).casefold()
    if compile_mode not in ARTICLE_COMPILE_MODES:
        compile_mode = defaults["compile_mode"]
    error_handling = stripped_text(
        stored.get("error_handling") or defaults["error_handling"]
    ).casefold()
    if error_handling not in ARTICLE_ERROR_HANDLING:
        error_handling = defaults["error_handling"]
    environment = latex_environment()
    primary_candidates = [
        str(item["path"])
        for item in _article_tex_candidates(article_root)
        if item.get("standalone")
    ]
    return {
        "schema": ARTICLE_BUILD_PROFILE_SCHEMA,
        "id": article_id,
        "compiler": compiler,
        "compiler_label": SUPPORTED_LATEX_COMPILERS[compiler]["label"],
        "primary_file": primary_file,
        "primary_candidates": primary_candidates,
        "use_project_latexmkrc": bool(
            stored.get("use_project_latexmkrc", defaults["use_project_latexmkrc"])
        ),
        "shell_escape": bool(stored.get("shell_escape", defaults["shell_escape"])),
        "auto_compile": bool(stored.get("auto_compile", defaults["auto_compile"])),
        "compile_mode": compile_mode,
        "syntax_check": bool(stored.get("syntax_check", defaults["syntax_check"])),
        "error_handling": error_handling,
        "tex_live_version": environment["version"],
        "latexmk": environment["latexmk"],
        "synctex_available": bool(_tex_which("synctex")),
        "environment_profile": environment["profile_id"],
        "environment_policy": environment["policy"],
        "available_compilers": environment["available_compilers"],
        "updated_at": stripped_text(stored.get("updated_at")),
    }


def update_article_settings(
    article_id: str,
    settings: dict[str, Any],
    *,
    build_root: str | Path | None = None,
) -> dict[str, Any]:
    if not isinstance(settings, dict):
        raise ValueError("article settings must be an object")
    current = load_article_settings(
        article_id, build_root=build_root
    )
    compiler = stripped_text(settings.get("compiler", current["compiler"])).casefold()
    if compiler not in SUPPORTED_LATEX_COMPILERS:
        raise ValueError(f"unsupported LaTeX compiler: {compiler or '(empty)'}")
    primary_file = _safe_relative_path(
        stripped_text(settings.get("primary_file", current["primary_file"]))
    )
    compile_mode = stripped_text(
        settings.get("compile_mode", current["compile_mode"])
    ).casefold()
    if compile_mode not in ARTICLE_COMPILE_MODES:
        raise ValueError(f"unsupported compile mode: {compile_mode or '(empty)'}")
    error_handling = stripped_text(
        settings.get("error_handling", current["error_handling"])
    ).casefold()
    if error_handling not in ARTICLE_ERROR_HANDLING:
        raise ValueError(
            f"unsupported compile error handling: {error_handling or '(empty)'}"
        )
    auto_compile = settings.get("auto_compile", current["auto_compile"])
    syntax_check = settings.get("syntax_check", current["syntax_check"])
    use_project_latexmkrc = settings.get(
        "use_project_latexmkrc", current["use_project_latexmkrc"]
    )
    shell_escape = settings.get("shell_escape", current["shell_escape"])
    if not isinstance(auto_compile, bool):
        raise ValueError("auto_compile must be a boolean")
    if not isinstance(syntax_check, bool):
        raise ValueError("syntax_check must be a boolean")
    if not isinstance(use_project_latexmkrc, bool):
        raise ValueError("use_project_latexmkrc must be a boolean")
    if not isinstance(shell_escape, bool):
        raise ValueError("shell_escape must be a boolean")
    article_root, source = _article_target_path(
        article_id, primary_file
    )
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.suffix.casefold() != ".tex":
        raise ValueError("the main document must be a .tex file")
    if not _is_standalone_tex_document(source):
        raise ValueError(
            f"the main document must contain \\documentclass: {source.relative_to(article_root).as_posix()}"
        )
    article_build_store.save_settings(article_id, {
            "schema": ARTICLE_BUILD_PROFILE_SCHEMA,
            "id": article_id,
            "compiler": compiler,
            "primary_file": source.relative_to(article_root).as_posix(),
            "use_project_latexmkrc": use_project_latexmkrc,
            "shell_escape": shell_escape,
            "auto_compile": auto_compile,
            "compile_mode": compile_mode,
            "syntax_check": syntax_check,
            "error_handling": error_handling,
            "updated_at": utc_now_iso(),
    })
    return load_article_settings(
        article_id, build_root=build_root
    )


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


def _latexmk_command() -> str:
    for candidate in (_tex_which("latexmk"),):
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise RuntimeError("latexmk is not available on the Portal host")


class ArticleCompilationStopped(RuntimeError):
    def __init__(self, output: str = "") -> None:
        super().__init__("LaTeX compilation stopped")
        self.output = str(output or "")


def _begin_article_build(article_id: str) -> None:
    with _ACTIVE_ARTICLE_BUILDS_LOCK:
        if article_id in _RUNNING_ARTICLE_BUILDS:
            raise RuntimeError("LaTeX compilation is already running")
        _RUNNING_ARTICLE_BUILDS.add(article_id)
        _STOP_REQUESTED_ARTICLE_BUILDS.discard(article_id)


def _finish_article_build(article_id: str) -> None:
    with _ACTIVE_ARTICLE_BUILDS_LOCK:
        _ACTIVE_ARTICLE_BUILDS.pop(article_id, None)
        _STOP_REQUESTED_ARTICLE_BUILDS.discard(article_id)
        _RUNNING_ARTICLE_BUILDS.discard(article_id)


def _terminate_article_process(process: subprocess.Popen[str]) -> None:
    # The owned group/Job may still contain descendants after its leader exits.
    process_runtime.terminate_process_tree(process, timeout=9)


def _run_article_build_command(
    article_id: str,
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    timeout = max(1, int(timeout_seconds))
    # Build output is returned only at completion. A temporary file preserves it
    # without waiting for EOF on pipes inherited by an exited leader's helpers.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as log:
        process = process_runtime.spawn_process(
            command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, env=env,
        )
        expired = None
        try:
            with _ACTIVE_ARTICLE_BUILDS_LOCK:
                _ACTIVE_ARTICLE_BUILDS[article_id] = process
            deadline = time.monotonic() + timeout
            while True:
                with _ACTIVE_ARTICLE_BUILDS_LOCK:
                    stopped = article_id in _STOP_REQUESTED_ARTICLE_BUILDS
                if stopped:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    expired = subprocess.TimeoutExpired(command, timeout)
                    break
                try:
                    process.wait(timeout=min(.1, remaining))
                    break
                except subprocess.TimeoutExpired:
                    pass
        finally:
            try:
                _terminate_article_process(process)
            finally:
                with _ACTIVE_ARTICLE_BUILDS_LOCK:
                    if _ACTIVE_ARTICLE_BUILDS.get(article_id) is process:
                        _ACTIVE_ARTICLE_BUILDS.pop(article_id, None)
        log.seek(0)
        output = log.read()
    if expired is not None:
        expired.stdout = output
        raise expired
    with _ACTIVE_ARTICLE_BUILDS_LOCK:
        stopped = article_id in _STOP_REQUESTED_ARTICLE_BUILDS
    if stopped:
        raise ArticleCompilationStopped(output)
    return subprocess.CompletedProcess(command, process.returncode, output, None)


def _controlled_article_build_environment(article_root: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "LC_ALL": "C.UTF-8",
            "LANG": "C.UTF-8",
            "TZ": "UTC",
            "PATH": _tex_search_path(),
            "SOURCE_DATE_EPOCH": str(
                max(
                    (int(path.stat().st_mtime) for path in article_root.rglob("*.tex")),
                    default=0,
                )
            ),
        }
    )
    for name in ("TEXINPUTS", "BIBINPUTS", "BSTINPUTS", "TEXMFOUTPUT"):
        environment.pop(name, None)
    return environment


def stop_article_compile(article_id: str) -> dict[str, Any]:
    if not get_resource_service().is_valid_id(article_id, "article"):
        raise ValueError(f"invalid article id: {article_id}")
    with _ACTIVE_ARTICLE_BUILDS_LOCK:
        running = article_id in _RUNNING_ARTICLE_BUILDS
        if running:
            _STOP_REQUESTED_ARTICLE_BUILDS.add(article_id)
    # The build thread observes the flag and is the sole owner of tree cleanup;
    # stop and timeout must not concurrently close the same Windows Job handle.
    return {
        "id": article_id,
        "running": running,
        "stop_requested": running,
    }


def compile_article_pdf(
    article_id: str,
    *,
    build_root: str | Path | None = None,
    timeout_seconds: int = 360,
    from_scratch: bool = False,
) -> dict[str, Any]:
    """Compile the registered canonical TeX manuscript into a cached PDF."""
    build = _ArticleBuild(article_id, build_root=build_root, from_scratch=from_scratch)
    build.check_preflight()
    return build.run(timeout_seconds)


class _ArticleBuild:
    """One latexmk build of an article's primary file, and the build reports it leaves.

    Each report is saved as the build goes: running, then success/warning,
    error (preflight, no PDF, timeout) or stopped.
    """

    def __init__(self, article_id: str, *, build_root: str | Path | None, from_scratch: bool) -> None:
        self.article_id = article_id
        self.build_root = build_root
        self.from_scratch = from_scratch
        _record, _root_item, self.article_root = _article_source_context(
            article_id
        )
        self.settings = load_article_settings(
            article_id, build_root=build_root
        )
        self.primary_file = _safe_relative_path(stripped_text(self.settings.get("primary_file")))
        self.compiler = stripped_text(self.settings.get("compiler")).casefold()
        self.compiler_config = SUPPORTED_LATEX_COMPILERS.get(self.compiler)
        if self.compiler_config is None:
            raise ValueError(f"unsupported LaTeX compiler: {self.compiler}")
        self.source = (self.article_root / self.primary_file).resolve(strict=False)
        try:
            self.source.relative_to(self.article_root)
        except ValueError as exc:
            raise ValueError("article primary file escapes the article root") from exc
        if not self.article_root.is_dir() or not self.source.is_file():
            raise FileNotFoundError(self.source)
        if self.source.suffix.casefold() != ".tex":
            raise ValueError("only LaTeX (.tex) articles can be compiled to PDF")

    def check_preflight(self) -> None:
        self.preflight = article_build_preflight(
            self.article_id,
            build_root=self.build_root,
            settings=self.settings,
        )
        if self.preflight["status"] != "blocked":
            return
        diagnostics = [
            {
                "severity": item["severity"],
                "category": item["category"],
                "message": item["message"],
                "file": item.get("file", ""),
                "line": 0,
            }
            for item in self.preflight["issues"]
        ]
        _save_article_build_report(
            self.article_id,
            {
                "status": "error",
                "started_at": utc_now_iso(),
                "compiled_at": utc_now_iso(),
                "compiler": self.compiler,
                "compiler_label": self.compiler_config["label"],
                "primary_file": self.primary_file,
                "compile_mode": self.settings.get("compile_mode", "normal"),
                "return_code": None,
                "duration_ms": 0,
                "diagnostics": diagnostics,
                "log": "\n".join(item["message"] for item in diagnostics),
                "summary": "LaTeX build preflight failed",
                "preflight": self.preflight,
                "environment_profile": self.settings["environment_profile"],
            },
            build_root=self.build_root,
        )
        raise RuntimeError(
            "LaTeX build preflight failed\n"
            + "\n".join(item["message"] for item in diagnostics)
        )

    def run(self, timeout_seconds: int) -> dict[str, Any]:
        self.compile_mode = stripped_text(self.settings.get("compile_mode")).casefold() or "normal"
        if self.compile_mode not in ARTICLE_COMPILE_MODES:
            raise ValueError(f"unsupported compile mode: {self.compile_mode}")
        self.output = article_build_pdf_path(self.article_id, build_root=self.build_root)
        self.cache_root = article_build_cache_path(self.article_id, build_root=self.build_root)
        self.compile_root = self.cache_root / self.compile_mode
        self.started_at = utc_now_iso()
        self.started = time.monotonic()
        _begin_article_build(self.article_id)
        try:
            completed, log, diagnostics, synctex_available = self._compile(timeout_seconds)
        except ArticleCompilationStopped as exc:
            self._report(
                "stopped",
                return_code=None,
                diagnostics=parse_latex_diagnostics(exc.output),
                log=exc.output[-80000:],
                summary="LaTeX compilation stopped",
                from_scratch=bool(self.from_scratch),
            )
            raise
        except subprocess.TimeoutExpired as exc:
            output_text = exc.stdout or ""
            if isinstance(output_text, bytes):
                output_text = output_text.decode("utf-8", errors="replace")
            output_text = str(output_text)
            self._report(
                "error",
                return_code=None,
                diagnostics=parse_latex_diagnostics(output_text),
                log=output_text[-80000:],
                summary=f"LaTeX compilation timed out after {timeout_seconds} seconds",
                from_scratch=bool(self.from_scratch),
            )
            raise RuntimeError(
                f"LaTeX compilation timed out after {timeout_seconds} seconds\n"
                f"{output_text[-8000:]}"
            ) from exc
        finally:
            _finish_article_build(self.article_id)
        has_diagnostics = bool(diagnostics) or completed.returncode != 0
        report = self._report(
            "warning" if has_diagnostics else "success",
            return_code=completed.returncode,
            diagnostics=diagnostics,
            log=log[-80000:],
            summary=(
                "PDF generated with LaTeX diagnostics"
                if has_diagnostics
                else "PDF generated successfully"
            ),
            synctex_available=synctex_available,
            from_scratch=bool(self.from_scratch),
            preflight=self.preflight,
            environment_profile=self.settings["environment_profile"],
            use_project_latexmkrc=self.settings["use_project_latexmkrc"],
            shell_escape=self.settings["shell_escape"],
        )
        return {
            **report,
            "pdf_path": str(self.output),
            "pdf_size": self.output.stat().st_size,
            "warnings": has_diagnostics,
        }

    def _compile(self, timeout_seconds: int) -> tuple[subprocess.CompletedProcess[str], str, list[dict[str, Any]], bool]:
        """Run latexmk and publish its PDF: the run, its output, its diagnostics, and whether SyncTeX is there."""
        output = self.output
        output.parent.mkdir(parents=True, exist_ok=True)
        if self.from_scratch:
            shutil.rmtree(self.cache_root, ignore_errors=True)
            output.unlink(missing_ok=True)
        self.compile_root.mkdir(parents=True, exist_ok=True)
        self._report(
            "running",
            finished=False,
            return_code=None,
            diagnostics=[],
            log="",
            from_scratch=bool(self.from_scratch),
            environment_profile=self.settings["environment_profile"],
            use_project_latexmkrc=self.settings["use_project_latexmkrc"],
            shell_escape=self.settings["shell_escape"],
        )
        compile_target = self._compile_target()
        pdf = self.compile_root / f"{self.source.stem}.pdf"
        pdf.unlink(missing_ok=True)
        completed = _run_article_build_command(
            self.article_id,
            self._command(compile_target),
            cwd=self.article_root,
            timeout_seconds=timeout_seconds,
            env=_controlled_article_build_environment(self.article_root),
        )
        log = str(completed.stdout or "")
        final_log_path = self.compile_root / f"{self.source.stem}.log"
        try:
            final_log = final_log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            final_log = ""
        diagnostics = parse_latex_diagnostics(final_log or log)
        if not pdf.is_file():
            self._report(
                "error",
                return_code=completed.returncode,
                diagnostics=diagnostics,
                log=log[-80000:],
                summary="LaTeX compilation failed",
                from_scratch=bool(self.from_scratch),
                preflight=self.preflight,
                environment_profile=self.settings["environment_profile"],
            )
            raise RuntimeError(f"LaTeX compilation failed\n{log[-12000:]}")
        temporary_pdf = output.parent / f".{self.article_id}.pdf.tmp"
        shutil.copyfile(pdf, temporary_pdf)
        os.replace(temporary_pdf, output)
        synctex_available = any(
            candidate.is_file()
            for candidate in (
                self.compile_root / f"{self.source.stem}.synctex.gz",
                self.compile_root / f"{self.source.stem}.synctex",
            )
        )
        return completed, log, diagnostics, synctex_available

    def _compile_target(self) -> str:
        """The file latexmk compiles: the primary file, or in draft mode a wrapper that inputs it."""
        if self.compile_mode != "draft":
            return self.primary_file
        draft_wrapper = self.compile_root / "portal-draft.tex"
        draft_wrapper.write_text(
            "\\PassOptionsToPackage{draft}{graphicx}\n"
            f"\\input{{\\detokenize{{{self.primary_file}}}}}\n",
            encoding="utf-8",
        )
        return str(draft_wrapper)

    def _command(self, compile_target: str) -> list[str]:
        settings = self.settings
        command = [
            _latexmk_command(),
            "-norc",
            "-f" if settings["error_handling"] == "continue" else "-f-",
            str(self.compiler_config["latexmk_flag"]),
            "-synctex=1",
            "-interaction=nonstopmode",
            "-file-line-error",
        ]
        project_latexmkrc = self.article_root / ".latexmkrc"
        if settings["use_project_latexmkrc"] and project_latexmkrc.is_file():
            command.extend(["-r", str(project_latexmkrc)])
        engine_flags: list[str] = []
        if settings["error_handling"] == "stop":
            engine_flags.append("-halt-on-error")
        if settings["shell_escape"]:
            engine_flags.append("-shell-escape")
        if engine_flags:
            engine = _tex_which(self.compiler) or self.compiler
            command.append(
                f"{self.compiler_config['engine_option']}={engine} "
                f"{' '.join(engine_flags)} %O %S"
            )
        if self.compile_mode == "draft":
            command.append(f"-jobname={self.source.stem}")
        command.extend(
            [
                f"-outdir={self.compile_root}",
                compile_target,
            ]
        )
        return command

    def _report(
        self, status: str, *, return_code: int | None, diagnostics: list[dict[str, Any]], log: str,
        finished: bool = True, **details: Any,
    ) -> dict[str, Any]:
        return _save_article_build_report(
            self.article_id,
            {
                "status": status,
                "started_at": self.started_at,
                "compiled_at": utc_now_iso() if finished else "",
                "compiler": self.compiler,
                "compiler_label": self.compiler_config["label"],
                "primary_file": self.primary_file,
                "compile_mode": self.compile_mode,
                "return_code": return_code,
                "duration_ms": int((time.monotonic() - self.started) * 1000) if finished else 0,
                "diagnostics": diagnostics,
                "log": log,
                **details,
            },
            build_root=self.build_root,
        )


def _article_synctex_artifacts(
    article_id: str,
    *,
    build_root: str | Path | None = None,
) -> tuple[Path, Path, Path, str]:
    _record, _root_item, article_root = _article_source_context(
        article_id
    )
    settings = load_article_settings(
        article_id, build_root=build_root
    )
    primary_file = _safe_relative_path(stripped_text(settings.get("primary_file")))
    mode = stripped_text(settings.get("compile_mode")).casefold() or "normal"
    compile_root = article_build_cache_path(
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
    _root, source = _article_target_path(
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
            _synctex_command(),
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
            _synctex_command(),
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


def _safe_relative_path(raw_path: str) -> str:
    decoded = unquote(raw_path)
    if not decoded or "\x00" in decoded or "\n" in decoded or "\r" in decoded:
        raise ValueError("invalid article-relative path")
    return "/".join(relative_parts(PurePosixPath(decoded).as_posix()))


def _section_slug(value: str) -> str:
    return re.sub(r"[^0-9a-z\u0080-\uffff]+", "-", value.casefold()).strip("-")


def _article_sections(path: Path) -> list[dict[str, Any]]:
    text = _read_text(path, limit=8 * 1024 * 1024)
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

    clean = _strip_tex_comments(text)
    matches = list(
        re.finditer(r"(?m)^[ \t]*\\(section|subsection|subsubsection)\*?\s*\{", clean)
    )
    for index, match in enumerate(matches):
        argument = _balanced_argument(clean, match.end() - 1)
        if not argument:
            continue
        title = _plain_tex(argument[0], _tex_macros(clean))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(clean)
        block = clean[match.start() : end]
        labels = re.findall(r"\\label\s*\{([^{}]+)\}", block)
        keys = [_section_slug(title)]
        for label in labels:
            keys.extend([label.casefold(), label.rsplit(":", 1)[-1].casefold()])
        headings.append(
            {
                "title": title,
                "keys": _dedupe(keys),
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
    record = get_article_record(root_id)
    root_item = _select_root(
        [item for item in record.get("roots") or [] if isinstance(item, dict)]
    )
    article_root = Path(stripped_text(root_item.get("path"))).resolve(strict=False)
    primary_file = _safe_relative_path(stripped_text(root_item.get("primary_file")))
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
        relative_path = _safe_relative_path("/".join(parts[1:]))
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
    text = _read_text(path, limit=max_bytes + 1)
    start = int(resolved.get("section_start") or 0)
    end = int(resolved.get("section_end") or 0)
    data = text[start:end].encode("utf-8")
    if len(data) > max_bytes:
        raise ValueError(f"resource exceeds {max_bytes} byte content limit")
    content_type = mimetypes.guess_type(str(path))[0] or "text/plain"
    if content_type == "application/x-tex":
        content_type = "text/x-tex"
    return data, content_type, resolved
