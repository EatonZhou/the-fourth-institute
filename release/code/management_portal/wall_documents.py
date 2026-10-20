"""WALL reads and edits points in their Markdown source, not a second catalog."""
from __future__ import annotations

import hashlib
import json
import posixpath
import re
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from markdown_it import MarkdownIt
from markdown_it.token import Token

from management_common.knowledge_metadata import document_metaness

from .resources import get_resource_service
from .shared_state import atomic_replace_text

KINDS = frozenset({"design", "improvement", "inconsistency", "direction"})
RESOURCE_ID = re.compile(r"\b(?:decision|plan|thinking)_[0-9a-f]{12}\b")
POINT_SECTIONS = re.compile(r"设计点|核心规则|设计原则|功能设计|设计要点|改进点|问题与机会|问题.*改进|未来.*探索|演化方向|不一致点|待发布|design points|improvements", re.I)
CONTEXT_SECTIONS = re.compile(r"概念|共同语言|核心对象|参与者|定义|相关|术语|背景|参考|附录|变更记录|使用记录")
WALL_COMMENT = re.compile(r"<!--\s*wall:\s*(\{.*?\})\s*-->", re.S)
DOCUMENT_ANCHOR = re.compile(
    r"<a\b[^>]*(?:id|name)\s*=\s*([\"'])[^\"']+\1[^>]*>\s*</a>",
    re.I,
)
DOCUMENT_PREAMBLE_RULES = (
    ("anchor", DOCUMENT_ANCHOR),
    ("wall", WALL_COMMENT),
)
PARSER = MarkdownIt("commonmark").enable("table")
POINT_FORMAT_HINT = "Design 可在「## 设计点」下用一级列表或独立分节表达多个设计点。IMPROVE.md 中每份 Plan 只描述一个显著改进，整份背景、方案、边界与验收作为一个 Block。文档头部可选 metaness: 1..10，表示 meta 层次，面积随层次增大；importance 单独表示重要度。"


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def frontmatter(text: str) -> tuple[dict[str, Any], str, int]:
    lines = text.splitlines(keepends=True)
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                data = yaml.safe_load("".join(lines[1:i])) or {}
                return data if isinstance(data, dict) else {}, "".join(lines[i + 1:]), i + 1
    return {}, text, 0


def plain(text: str) -> str:
    result = []
    for token in PARSER.parseInline(text):
        for child in token.children or []:
            if child.type in {"text", "code_inline"}:
                result.append(child.content)
            elif child.type in {"softbreak", "hardbreak"}:
                result.append(" ")
    return "".join(result).strip()


def importance(value: object) -> int:
    try:
        return max(1, min(10, int(value)))
    except (TypeError, ValueError):
        return 5


@lru_cache(maxsize=4)
def _importance_suggestions(markdown: str) -> dict[str, tuple[int, str]]:
    suggestions = {}
    row: list[str] | None = None
    for token in PARSER.parse(markdown):
        if token.type == "tr_open":
            row = []
        elif token.type == "inline" and row is not None:
            row.append(plain(token.content))
        elif token.type == "tr_close" and row is not None:
            if len(row) >= 3 and "#" in row[0] and row[1].isdigit() and 1 <= int(row[1]) <= 10 and row[2].strip():
                suggestions[row[0]] = (int(row[1]), row[2])
            row = None
    return suggestions


def apply_importance(points: list[dict[str, Any]], markdown: str) -> None:
    """Apply Resident suggestions while retaining each point's explicit score."""
    suggestions = _importance_suggestions(markdown)
    for point in points:
        if "importance" in point.get("_meta", {}) or point["id"] not in suggestions:
            continue
        score, reason = suggestions[point["id"]]
        point.update(importance=score, recommendation=reason, importance_source="resident")


def inside(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()):
        raise PermissionError("Markdown 路径不在已登记工程目录内")
    if target.suffix.lower() != ".md":
        raise ValueError("WALL 只读取 Markdown 文档")
    return target


@dataclass
class Source:
    address: str
    title: str
    domain: str
    kind: str
    module_path: tuple[str, ...] = ()


def is_improvement_candidate(address: str, target: Path) -> bool:
    """Apply declared Plan semantics and reject clearly auxiliary documents."""
    normalized = posixpath.normpath(address)
    parts = normalized.split("/")
    stem = Path(normalized).stem.casefold()
    if not target.is_file():
        return "plans" in parts or stem == "plan" or stem.startswith("plan_")
    text = target.read_text(encoding="utf-8")
    metadata, body, _ = frontmatter(text)
    declared_kind = str(metadata.get("kind") or "").casefold()
    marker = WALL_COMMENT.search(body)
    if marker is not None:
        try:
            annotation = json.loads(marker.group(1))
        except (TypeError, ValueError):
            annotation = {}
        if isinstance(annotation, dict) and annotation.get("kind"):
            declared_kind = str(annotation["kind"]).casefold()
    if declared_kind:
        return declared_kind in {"plan", "improvement"}
    if "plans" in parts or stem == "plan" or stem.startswith("plan_"):
        return True
    tokens = PARSER.parse(body)
    title = next(
        (plain(tokens[index + 1].content) for index, token in enumerate(tokens[:-1]) if token.type == "heading_open" and token.tag == "h1"),
        "",
    )
    auxiliary = re.compile(r"(?:^|[-_\s])(report|review|audit|evidence)(?:$|[-_\s])|报告|审查|审阅|审计|证据", re.I)
    return auxiliary.search(f"{stem} {title}") is None


def _index_entries(text: str) -> Iterator[tuple[tuple[str, ...], list[Token]]]:
    """Prefer catalog table rows over prose references; keep row cells together."""
    tokens = PARSER.parse(text)
    path: tuple[str, ...] = ()
    row: list[Token] | None = None
    tables = []
    prose = []
    for i, token in enumerate(tokens):
        if token.type == "heading_open":
            if token.tag == "h2":
                path = (plain(tokens[i + 1].content),)
            elif token.tag == "h3" and path:
                path = (path[0], plain(tokens[i + 1].content))
        elif token.type == "tr_open":
            row = []
        elif token.type == "tr_close" and row is not None:
            tables.append((path, row))
            row = None
        elif token.type == "inline":
            if row is not None:
                row.append(token)
            else:
                prose.append((path, [token]))
    yield from tables
    yield from prose


def index_file(root: Path, filename: str) -> Path:
    canonical = inside(root, filename)
    if canonical.is_file():
        return canonical
    parent = canonical.parent
    matches = sorted((p for p in parent.iterdir()
                      if p.is_file() and p.name.casefold() == canonical.name.casefold()),
                     key=lambda p: p.name) if parent.is_dir() else []
    if len(matches) > 1:
        raise ValueError(f"索引文件名大小写重复：{filename}")
    return inside(root, str(matches[0].relative_to(root))) if matches else canonical


def index_sources(root: Path, *, spec_only: bool = False) -> list[Source]:
    root = root.resolve()
    names = {"spec.md"} if spec_only else {"spec.md", "improve.md"}
    indexes = [index_file(root, name.upper().replace(".MD", ".md")) for name in sorted(names)]
    if not spec_only:
        indexes.extend(root / name for name in (".wall/WALL.md", ".wall/directions.md", ".wall/inconsistencies.md", ".wall/dev.md", ".wall/design.md"))
    versions = tuple((str(path), _file_version(path) if path.exists() else None) for path in sorted(indexes))
    return [source for source in _indexed_sources(root, versions, spec_only)
            if source.kind != "improvement" or RESOURCE_ID.fullmatch(source.address)
            or source.address == ".wall/dev.md"
            or is_improvement_candidate(source.address, inside(root, source.address))]


@lru_cache(maxsize=32)
def _indexed_sources(root: Path, versions: tuple, spec_only: bool = False) -> tuple[Source, ...]:
    """Read table/list links while keeping the index's domain and ordering."""
    sources: dict[str, Source] = {}
    spec_paths: dict[str, tuple[str, ...]] = {}
    indexes = []
    wall_sources = {".wall/directions.md": "direction", ".wall/inconsistencies.md": "inconsistency",
                    ".wall/dev.md": "improvement",
                    ".wall/design.md": "design"}
    for filename, kind in (("SPEC.md", "design"), ("IMPROVE.md", "improvement"), (".wall/WALL.md", "direction")):
        if spec_only and filename != "SPEC.md":
            continue
        index = index_file(root, filename)
        if not index.is_file():
            continue
        indexes.append((filename, index, kind))
    # Indexes may link back to each other; only their referenced bodies become Sources.
    index_paths = {index for _, index, _ in indexes}
    if spec_only:
        index_paths.update(path.resolve() for path in root.iterdir() if path.name.casefold() in {"spec.md", "improve.md"})
        index_paths.add((root / ".wall/WALL.md").resolve())
    for filename, index, kind in indexes:
        text = index.read_text(encoding="utf-8")
        for path, nodes in _index_entries(text):
            ids = [address for token in nodes for address in RESOURCE_ID.findall(token.content)]
            links = []
            for token in nodes:
                for child in token.children or []:
                    if child.type != "link_open":
                        continue
                    href = str(child.attrGet("href") or "").split("#", 1)[0]
                    if not href.lower().endswith(".md") or "://" in href:
                        continue
                    target = inside(root, str((index.parent / href).relative_to(root)))
                    if target in index_paths:
                        continue
                    address = str(target.relative_to(root))
                    target_kind = wall_sources.get(address, kind)
                    links.append((address, target.stem))
                    # A prose/list entry owns its first Markdown target; later
                    # links in the same inline token are supporting evidence.
                    # Table cells are separate inline tokens, so their primary
                    # Design and Plan targets remain independently discoverable.
                    break
            addresses = ids + [address for address, _ in links]
            linked = {spec_paths[address] for address in addresses if address in spec_paths and spec_paths[address]}
            module_path = next(iter(linked)) if kind == "improvement" and len(linked) == 1 else path
            domain = (module_path or path or ("WALL" if filename.startswith(".wall/") else "其他",))[0]
            for address in ids:
                if kind == "improvement" and not address.startswith("plan_"):
                    continue
                if kind == "design" and not address.startswith("decision_"):
                    continue
                sources.setdefault(address, Source(address, address, domain, kind, module_path))
                if kind == "design":
                    spec_paths.setdefault(address, module_path)
            for address, title in links:
                local_domain = "WALL" if address.startswith(".wall/") and not module_path else domain
                sources.setdefault(address, Source(address, title, local_domain, wall_sources.get(address, kind), module_path))
                if kind == "design":
                    spec_paths.setdefault(address, module_path)
    for address, kind in (() if spec_only else wall_sources.items()):
        path = root / address
        if path.is_file():
            address = str(path.relative_to(root))
            sources.setdefault(address, Source(address, path.stem, "WALL", kind))
    return tuple(sources.values())


def _file_version(path: Path) -> tuple:
    stat = path.stat()
    return str(path.resolve()), stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def source_version(root: Path, address: str, actor: str) -> tuple:
    if RESOURCE_ID.fullmatch(address):
        # Resolve on every check: cached content never substitutes for authorization.
        resolved = get_resource_service().resolve(address, actor=actor)
        path = str(resolved.get("resolved_path") or "")
        if not path or not resolved.get("exists"):
            raise FileNotFoundError("资源正文不可用")
        return _file_version(Path(path))
    return _file_version(inside(root, address))


def read_source(root: Path, address: str, actor: str) -> str:
    if RESOURCE_ID.fullmatch(address):
        data, _, _ = get_resource_service().read_content(address, actor=actor)
        return data.decode("utf-8")
    return inside(root, address).read_text(encoding="utf-8")


def read_resource_source_with_version(address: str, actor: str) -> tuple[tuple, str]:
    """Read a new resource source and its version with one authorized lookup."""
    data, _, resolved = get_resource_service().read_content(address, actor=actor)
    path = str(resolved.get("resolved_path") or "")
    if not path or not resolved.get("exists"):
        raise FileNotFoundError("资源正文不可用")
    return _file_version(Path(path)), data.decode("utf-8")


def write_source(root: Path, address: str, text: str, actor: str, *, expected_version: object = None) -> None:
    if RESOURCE_ID.fullmatch(address):
        if expected_version is None:
            raise ValueError("资源正文缺少版本，请刷新后重试")
        get_resource_service().update(address, {"text": text, "expected_version": expected_version}, actor=actor)
    else:
        atomic_replace_text(inside(root, address), text)


def _metadata(raw: str) -> dict[str, Any]:
    match = WALL_COMMENT.search(raw)
    if not match:
        return {}
    try:
        result = json.loads(match.group(1))
        return result if isinstance(result, dict) else {}
    except ValueError:
        return {}


def metadata_comment(meta: dict[str, Any]) -> str:
    encoded = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))
    return "<!-- wall: " + encoded.replace("|", "\\u007c").replace("-->", "--\\u003e") + " -->"


def has_point_annotations(text: str) -> bool:
    """Whether a document explicitly declares WALL points, beyond navigation."""
    return any(
        any(_metadata(match.group(0)).get(field) for field in ("key", "kind"))
        for match in WALL_COMMENT.finditer(text)
    )


def update_frontmatter(text: str, changes: dict[str, Any]) -> str:
    metadata, body, _ = frontmatter(text)
    metadata.update(changes)
    return "---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).rstrip() + "\n---\n" + body


def document_markdown(title: str, content: str, annotation: dict[str, Any]) -> str:
    title = str(title).strip().replace("\n", " ")
    if not title:
        raise ValueError("标题不能为空")
    body = WALL_COMMENT.sub("", str(content)).strip()
    if body.startswith("---"):
        raise ValueError("正文不接受 YAML 头部，请使用文档属性字段")
    if re.match(r"^#\s+", body):
        body = re.sub(r"^#[^\n]*", lambda match: "# " + title, body, count=1)
    else:
        body = "# " + title + "\n\n" + body
    annotation = {key: value for key, value in annotation.items() if key not in {"key", "kind", "status", "title"}}
    return metadata_comment(annotation) + "\n" + body.rstrip() + "\n"


def _document_point(source: Source, body: str, metadata: dict[str, Any], offset: int) -> list[dict[str, Any]]:
    remaining = body.lstrip()
    annotation: dict[str, Any] = {}
    while remaining:
        matched = False
        for kind, pattern in DOCUMENT_PREAMBLE_RULES:
            marker = pattern.match(remaining)
            if marker is None:
                continue
            if kind == "wall":
                annotation.update(_metadata(marker.group()))
            remaining = remaining[marker.end():].lstrip()
            matched = True
            break
        if not matched:
            break
    content = DOCUMENT_ANCHOR.sub("", WALL_COMMENT.sub("", remaining)).strip()
    tokens = PARSER.parse(content)
    if not any(token.type in {"paragraph_open", "fence", "code_block", "table_open"} for token in tokens):
        return []
    title = source.title
    for index, token in enumerate(tokens):
        if token.type == "heading_open" and token.tag == "h1":
            title = plain(tokens[index + 1].content)
            break
    else:
        first = next((token.content for token in tokens if token.type == "inline"), "")
        marked_title = re.match(r"^\*\*(.+?)\*\*\s*[:：]", first)
        if marked_title:
            title = plain(marked_title.group(1))
    paragraphs = [tokens[index + 1].content for index, token in enumerate(tokens[:-1]) if token.type == "paragraph_open"]
    preview = plain(" ".join(paragraphs))
    if preview.startswith(title):
        preview = preview[len(title):].lstrip(" ：:。\n")
    preview = preview[:450]
    return [{
        "id": f"{source.address}#plan", "source_id": source.address,
        "source_title": title, "title": title, "domain": source.domain,
        "module_path": list(source.module_path), "kind": "improvement", "status": "improvement",
        "content": content, "preview": preview, "document_scope": True,
        "importance": importance(metadata.get("importance", annotation.get("importance", 5))),
        "importance_source": "point" if "importance" in annotation else "document",
        "related": [str(value) for value in annotation.get("related", [])] if isinstance(annotation.get("related", []), list) else [],
        "resident_id": str(annotation.get("resident_id") or ""), "recommendation": "",
        "_start": offset, "_end": offset + len(body.splitlines(keepends=True)),
        "_raw": body.rstrip(), "_syntax": "document", "_meta": annotation,
    }]


def remove_index_reference(text: str, address: str) -> str:
    tokens = PARSER.parse(text)
    containers = []
    spans = []
    for token in tokens:
        if token.nesting == 1:
            containers.append(token)
        elif token.nesting == -1:
            if containers:
                containers.pop()
        elif token.type == "inline":
            matches = address in RESOURCE_ID.findall(token.content)
            matches = matches or any(child.type == "link_open" and posixpath.normpath(str(child.attrGet("href") or "").split("#", 1)[0]) == address for child in token.children or [])
            if not matches:
                continue
            owner = next((parent for parent in containers if parent.type == "list_item_open" and parent.map), None)
            if owner is None:
                owner = next((parent for parent in reversed(containers) if parent.type in {"tr_open", "paragraph_open"} and parent.map), None)
            if owner is not None:
                spans.append(tuple(owner.map))
    lines = text.splitlines(keepends=True)
    return "".join(line for index, line in enumerate(lines) if not any(start <= index < end for start, end in spans))


def _merge_point_tables(
    spans: list[tuple[int, int, str]], tables: list[tuple[int, int]], lines: list[str],
) -> set[int]:
    owners: set[int] = set()
    for table_start, table_end in tables:
        for position, (start, end, syntax) in enumerate(spans):
            if syntax != "list" or start > table_start:
                continue
            contained = table_start < end
            adjacent = end <= table_start and not "".join(lines[end:table_start]).strip()
            introduction = plain(WALL_COMMENT.sub("", "".join(lines[start:end]))).rstrip()
            if not contained and not (adjacent and (start in owners or introduction.endswith((":", "：")))):
                continue
            spans[position] = (start, max(end, table_end), syntax)
            spans[:] = [span for span in spans if span[2] != "table" or not table_start <= span[0] < table_end]
            owners.add(start)
            break
    return owners


def parse_points(source: Source, text: str) -> list[dict[str, Any]]:
    meta, body, offset = frontmatter(text)
    if meta.get("status") == "obsolete":
        return []
    try:
        degree = document_metaness(meta)
    except ValueError:
        degree = None
    metaness_fields = {"metaness": degree if degree is not None else 1,
                       "metaness_source": "document" if degree is not None else "default"}
    if source.kind == "improvement" and source.address != ".wall/dev.md":
        return [{**point, **metaness_fields} for point in _document_point(source, body, meta, offset)]
    tokens = PARSER.parse(body)
    lines = body.splitlines(keepends=True)
    title = source.title
    headings: list[tuple[int, str]] = []
    # The source's structure determines point boundaries. Nested evidence stays
    # attached to its parent point instead of becoming extra cards.
    spans: list[tuple[int, int, str]] = []
    heading_spans: list[tuple[int, int, str]] = []
    tables: list[tuple[int, int]] = []
    depth = 0
    active = False
    table_header = False
    implicit_spans: list[tuple[int, int, str]] = []
    explicit_spans: list[tuple[int, int, str]] = []
    for i, token in enumerate(tokens):
        # Headings inside list/quote evidence do not change the document scope.
        if token.type == "heading_open" and token.level == 0:
            label = tokens[i + 1].content
            level = int(token.tag[1:])
            headings = [(n, h) for n, h in headings if n < level] + [(level, label)]
            if level == 1:
                title = plain(label)
            active = any(n >= 2 and POINT_SECTIONS.search(h) for n, h in headings)
            if token.map:
                heading_spans.append((token.map[0], level, label))
        elif token.type == "list_item_open":
            annotated = bool(token.map and token.map[0] and WALL_COMMENT.fullmatch(lines[token.map[0] - 1].strip()))
            if depth == 0 and token.map and annotated:
                explicit_spans.append((*token.map, "list"))
            if depth == 0 and token.map and (active or source.address == ".wall/dev.md"):
                spans.append((*token.map, "list"))
            elif depth == 0 and token.map and not any(CONTEXT_SECTIONS.search(h) for _, h in headings):
                implicit_spans.append((*token.map, "list"))
            depth += 1
        elif token.type == "list_item_close":
            depth -= 1
        elif token.type == "table_open" and token.map:
            tables.append(tuple(token.map))
        elif token.type == "thead_open":
            table_header = True
        elif token.type == "thead_close":
            table_header = False
        elif token.type == "tr_open" and not table_header and depth == 0 and token.map:
            if active:
                spans.append((*token.map, "table"))
            elif any(re.search(r"接口|Instruction|RPC|逐方法|合同", h) for _, h in headings):
                implicit_spans.append((*token.map, "table"))
    table_owners = (_merge_point_tables(spans, tables, lines)
                    | _merge_point_tables(implicit_spans, tables, lines)
                    | _merge_point_tables(explicit_spans, tables, lines))
    # Some designs use one subsection per point, with no list beneath it.
    for i, (start, level, label) in enumerate(heading_spans):
        annotated = bool(start and WALL_COMMENT.fullmatch(lines[start - 1].strip()))
        if level < 2 or not (annotated or level >= 3 and re.match(r"^(?:\d+[.、\s]|设计点\s*\d+)", plain(label))):
            continue
        end = next((s for s, n, _ in heading_spans[i + 1:] if n <= level), len(lines))
        # A new explicitly marked point ends the preceding heading-style point.
        # Its marker belongs to the new point, so editing either stays isolated.
        end = min([end] + [s - 1 for s, _, _ in explicit_spans if start < s < end])
        spans = [(s, e, t) for s, e, t in spans if not (start <= s < end)]
        spans.append((start, end, "heading"))
    if not spans and source.kind in {"design", "improvement"}:
        spans = implicit_spans
        # Protocols sometimes describe one public method per subsection.
        for i, (start, level, label) in enumerate(heading_spans):
            if source.kind != "design" or level != 3 or not re.match(r"^`[^`]+`$", label.strip()):
                continue
            end = next((s for s, n, _ in heading_spans[i + 1:] if n <= level), len(lines))
            if not any(start <= s < end for s, _, _ in spans):
                spans.append((start, end, "heading"))
        headings = []
        for i, (start, level, label) in enumerate(heading_spans):
            headings = [(n, h) for n, h in headings if n < level] + [(level, label)]
            if level < 2 or any(CONTEXT_SECTIONS.search(plain(h)) for _, h in headings):
                continue
            if any(s <= start < e for s, e, _ in spans):
                continue
            if i + 1 < len(heading_spans) and heading_spans[i + 1][1] > level:
                continue
            end = next((s for s, n, _ in heading_spans[i + 1:] if n <= level), len(lines))
            contained = [(s, e, syntax) for s, e, syntax in spans if start <= s < end]
            if contained:
                if len(contained) != 1 or contained[0][2] != "list":
                    continue
                first = lines[contained[0][0]]
                if contained[0][0] in table_owners or re.match(r"^\s*(?:[-+*]|\d+[.)])\s+\*\*", first) or POINT_SECTIONS.search(label) or WALL_COMMENT.search("".join(lines[start:end])):
                    continue
                spans.remove(contained[0])
            if "".join(lines[start + 1:end]).strip():
                spans.append((start, end, "heading"))
    existing_starts = {start for start, _, _ in spans}
    spans.extend(span for span in explicit_spans if span[0] not in existing_starts)
    points = []
    duplicates: dict[str, int] = {}
    for start, end, syntax in sorted(spans):
        owns_table = start in table_owners
        # A point annotation immediately before a list belongs to that point.
        if start and WALL_COMMENT.fullmatch(lines[start - 1].strip()):
            start -= 1
        raw = "".join(lines[start:end]).rstrip()
        annotation = _metadata(raw)
        content = WALL_COMMENT.sub("", raw).strip()
        first = content.splitlines()[0] if content else ""
        first = re.sub(r"^\s*(?:[-+*]|\d+[.)]|#{1,6})\s+", "", first)
        strong = re.match(r"\*\*(.+?)\*\*", first)
        label = plain(strong.group(1) if strong else first).strip("| ：:。")
        if syntax == "table":
            label = plain(first.strip("|").split("|", 1)[0]).strip()
        if not label:
            continue
        basis = str(annotation.get("key") or digest(label))
        duplicates[basis] = duplicates.get(basis, 0) + 1
        key = basis if duplicates[basis] == 1 else f"{basis}-{duplicates[basis]}"
        kind = str(annotation.get("kind") or source.kind)
        if kind not in KINDS:
            kind = source.kind
        status = "dev" if annotation.get("status") == "dev" else kind
        preview = plain(re.sub(r"^\s*(?:[-+*]|\d+[.)]|#{1,6})\s+", "", content)).strip("| ")
        if preview.startswith(label):
            preview = preview[len(label):].lstrip(" |.:：。\n")
        display_label = re.sub(r"\s*(?:如下(?:所示)?|见下表)$", "", label).rstrip(" ：:。") if owns_table else label
        point = {
            **metaness_fields,
            "id": f"{source.address}#{key}", "source_id": source.address,
            "source_title": title, "domain": str(annotation.get("domain") or source.domain), "kind": kind,
            "module_path": list(source.module_path),
            "status": status, "title": str(annotation.get("title") or display_label or label),
            "content": content, "preview": preview[:450],
            "importance": importance(annotation.get("importance", meta.get("importance", 5))),
            "importance_source": "point" if "importance" in annotation else "document",
            "related": [str(v) for v in annotation.get("related", [])] if isinstance(annotation.get("related", []), list) else [],
            "resident_id": str(annotation.get("resident_id") or ""),
            "recommendation": str(annotation.get("recommendation") or ""),
            "_start": start + offset, "_end": end + offset,
            "_raw": raw, "_syntax": syntax, "_meta": annotation,
        }
        points.append(point)
    return points


def source_diagnostics(source: Source, text: str, points: list[dict[str, Any]]) -> list[dict[str, str]]:
    metadata, body, _ = frontmatter(text)
    diagnostics = []
    try:
        document_metaness(metadata)
    except ValueError as exc:
        diagnostics.append({"source_id": source.address, "kind": source.kind, "code": "invalid_metaness", "message": str(exc)})
    if metadata.get("status") == "obsolete":
        return diagnostics
    if points:
        return diagnostics
    if not any(token.type in {"paragraph_open", "fence", "code_block", "table_open"} for token in PARSER.parse(body)):
        return diagnostics
    return diagnostics + [{"source_id": source.address, "kind": source.kind, "code": "no_points",
             "message": "正文未识别为独立的设计点或工作项。",
             "preview": plain(WALL_COMMENT.sub("", body))[:240]}]


def public_point(point: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in point.items() if not key.startswith("_")}


def point_markdown(title: str, content: str, meta: dict[str, Any]) -> str:
    title = str(title).strip().replace("\n", " ")
    if not title:
        raise ValueError("标题不能为空")
    body = str(content).strip()
    body = re.sub(r"^\s*(?:[-+*]|\d+[.)])\s+", "", body)
    prefix = f"**{title}**"
    if not body.startswith(prefix):
        body = prefix + ("。" + body if body else "")
    # Indent continuations so lists, fences and paragraphs remain one point.
    lines = body.splitlines()
    return metadata_comment(meta) + "\n- " + lines[0] + "\n" + "".join("  " + s + "\n" for s in lines[1:]) + "\n"


def replace_point(text: str, point: dict[str, Any], replacement: str) -> str:
    lines = text.splitlines(keepends=True)
    start, end = point["_start"], point["_end"]
    if "".join(lines[start:end]).rstrip() != point["_raw"]:
        raise ValueError("文档刚刚有更新，请刷新后重试")
    return "".join(lines[:start]) + replacement + "".join(lines[end:])
