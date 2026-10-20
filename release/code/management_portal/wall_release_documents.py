"""Read-only compilation of an authorized WALL project's current Design index."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .wall_documents import PARSER, frontmatter, index_file, index_sources, inside, plain, read_source

MAX_SOURCE_COUNT = 512
MAX_INPUT_BYTES = 8 * 1024 * 1024


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _chapter(body: str, fallback: str) -> tuple[str, str]:
    lines = body.splitlines()
    tokens = PARSER.parse(body)
    title = fallback
    title_heading = next((token for token in tokens if token.type == "heading_open" and token.tag == "h1" and token.level == 0), None)
    for position, token in reversed(list(enumerate(tokens))):
        if token.type != "heading_open" or token.map is None or token.level != 0:
            continue
        start, end = token.map
        content = tokens[position + 1].content
        if token is title_heading:
            title = plain(content)
            lines[start:end] = []
        else:
            level = min(6, int(token.tag[1:]) + 1)
            lines[start:end] = [f'{"#" * level} {content}']
    return title, "\n".join(lines).strip()


def preview(record: dict[str, Any], actor: str) -> dict[str, Any]:
    root = Path(record["root"]).resolve()
    index = index_file(root, "SPEC.md")
    if not index.is_file():
        raise ValueError("工程没有 SPEC.md，无法生成发布文档")
    spec_bytes = index.read_bytes()
    if len(spec_bytes) > MAX_INPUT_BYTES:
        raise ValueError("索引与正文总量超过 8 MiB，未生成部分文档")
    sources = index_sources(root, spec_only=True)
    if not sources:
        raise ValueError("SPEC.md 没有可生成的 Design 正文")
    if len(sources) > MAX_SOURCE_COUNT:
        raise ValueError(f"单次生成最多支持 {MAX_SOURCE_COUNT} 份 Design")
    total_bytes = len(spec_bytes)
    entries = []
    chapters = []
    for source in sources:
        try:
            text = read_source(root, source.address, actor)
        except PermissionError:
            raise PermissionError("当前账号不能读取全部索引正文，未生成部分文档") from None
        except (FileNotFoundError, KeyError):
            raise ValueError("索引正文缺失，未生成部分文档；请先修复 SPEC.md") from None
        content_bytes = text.encode("utf-8")
        total_bytes += len(content_bytes)
        if total_bytes > MAX_INPUT_BYTES:
            raise ValueError("索引与正文总量超过 8 MiB，未生成部分文档")
        metadata, body, _offset = frontmatter(text)
        title, chapter = _chapter(body.strip(), source.title)
        version = metadata.get("version")
        number = version.get("number") if isinstance(version, dict) else None
        entry = {"address": source.address, "title": title, "module_path": list(source.module_path),
                 "version": number, "sha256": _sha256(content_bytes)}
        entries.append(entry)
        chapters.append((entry, chapter))
    if index.read_bytes() != spec_bytes:
        raise ValueError("生成期间 SPEC.md 已变化，请重新生成")
    identity = {"schema": "portal-spec-document/v1", "project": record["id"],
                "spec": {"name": index.name, "sha256": _sha256(spec_bytes)}, "sources": entries}
    fingerprint = _sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    manifest = {**identity, "fingerprint": fingerprint,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "snapshot_policy": "各正文按实际读取字节记录版本与SHA256；跨资源非原子快照；索引前后必须一致。"}
    output = [f'# {record["title"]} 软件系统文档', "",
              "本文件由当前 SPEC 索引中的 Design 编排生成，仅供预览与下载。未执行 Release、提交代码或启动服务。",
              "正文保留设计语义，目录按索引顺序组织；设计思考和未列入 SPEC 的计划不在本文件内。", "",
              "## 来源清单", "", "```json", json.dumps(manifest, ensure_ascii=False, indent=2), "```", "",
              "## 目录", ""]
    for ordinal, entry in enumerate(entries, 1):
        output.append(f'{ordinal}. {entry["title"]} · `{entry["address"]}`')
    for ordinal, (entry, chapter) in enumerate(chapters, 1):
        output.extend(["", f'## {ordinal}. {entry["title"]}', "",
                       f'来源：`{entry["address"]}` · 版本：{entry["version"] if entry["version"] is not None else "以 SHA256 标识"}',
                       f'正文 SHA256：`{entry["sha256"]}`', "", chapter])
    markdown = "\n".join(output).rstrip() + "\n"
    return {"manifest": manifest, "markdown": markdown,
            "filename": f'portal-design-{fingerprint[:16]}.md',
            "document_sha256": _sha256(markdown.encode("utf-8"))}
