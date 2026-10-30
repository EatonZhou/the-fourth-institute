"""The semantic index of an article: sections, labels, references, citations, figures
and included files across the documents its primary manuscript reaches.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Iterator

from management_common.clock import utc_now_iso
from management_portal import article_files, article_registry, article_settings, article_tex


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
        argument = article_tex._balanced_argument(text, match.end() - 1)
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
        article_tex._plain_tex(match.group("braced") or match.group("quoted") or ""),
    ).strip()


def article_semantic_index(
    article_id: str,
    *,
    build_root: str | Path | None = None,
    limit: int = 8000,
) -> dict[str, Any]:
    """Build project-wide LaTeX navigation without mutating the manuscript."""
    _record, _root_item, article_root = article_registry._article_source_context(
        article_id
    )
    settings = article_settings.load_article_settings(
        article_id, build_root=build_root
    )
    tree = article_files.list_article_files(
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
            text = article_tex._read_text(path, limit=8 * 1024 * 1024)
        except OSError:
            return False
        clean = article_tex._strip_tex_comments(text)
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
        macros = article_tex._tex_macros(clean)
        count = 0
        for command, argument, offset, _end in _tex_command_arguments(
            clean, ARTICLE_SEMANTIC_TEX_COMMAND_LEVELS
        ):
            line, column = _text_line_column(clean, offset)
            self.headings.append({
                "kind": "heading",
                "command": command.casefold(),
                "title": article_tex._plain_tex(argument, macros) or command,
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
