"""TeX source inspection and contained paths, without resource registration."""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from urllib.parse import unquote
from typing import Any

from management_common.secure_paths import relative_parts
from management_common.text_values import stripped_text


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


def extract_title(path: str | Path) -> str:
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


def _safe_relative_path(raw_path: str) -> str:
    decoded = unquote(raw_path)
    if not decoded or "\x00" in decoded or "\n" in decoded or "\r" in decoded:
        raise ValueError("invalid article-relative path")
    return "/".join(relative_parts(PurePosixPath(decoded).as_posix()))

IGNORED_DIRECTORIES = {
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
    title = extract_title(path)
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
            title = extract_title(candidate)
            if title:
                return title
    return ""

def tex_candidates(root: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.tex"), key=lambda item: str(item).casefold()):
        if any(part in IGNORED_DIRECTORIES for part in path.parts):
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
