"""Portal Chat Markdown as Telegram Bot API HTML.

Telegram renders a small HTML subset, so Chat Markdown is mapped onto it:
fenced code and tables become ``<pre>``, ``>`` quotes become
``<blockquote>``, headings and ``**bold**`` become ``<b>``, list markers become
bullets, and links, inline code and ``~~strike~~`` keep their meaning.
"""
from __future__ import annotations

import html
import re


FENCE_RE = re.compile(r"```([A-Za-z0-9_+-]*)[ \t]*\n?([\s\S]*?)```")
INLINE_CODE_RE = re.compile(r"`([^`\n]+?)`")
LINK_RE = re.compile(r"\[([^\]\n]+?)\]\((https?://[^\s)]+|tg://[^\s)]+|mailto:[^\s)]+)\)")
HEADING_RE = re.compile(r"(?m)^(#{1,6})[ \t]+(.+)$")
BOLD_RE = re.compile(r"\*\*([^\n]+?)\*\*")
STRIKE_RE = re.compile(r"~~([^\n]+?)~~")
BULLET_RE = re.compile(r"(?m)^([ \t]*)[-*+][ \t]+")
TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
TABLE_RULE_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
TELEGRAM_TEXT_LIMIT = 3000


def markdown_to_telegram_html(text: str) -> str:
    """Convert Chat Markdown to Telegram Bot API HTML."""
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    cursor = 0
    for match in FENCE_RE.finditer(raw):
        out.append(_format_blocks(raw[cursor : match.start()]))
        out.append(_format_fenced_code(match.group(1), match.group(2)))
        cursor = match.end()
    out.append(_format_blocks(raw[cursor:]))
    return "".join(out)


def split_telegram_text(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> list[str]:
    """Split Markdown at line ends; a fenced block cut in two is closed and reopened."""
    raw = str(text or "")
    if len(raw) <= limit:
        return [raw]
    room = limit - 40  # space for a reopened fence line and its closing
    chunks: list[str] = []
    current = ""
    fence = ""

    def close(chunk: str) -> str:
        if not fence:
            return chunk
        return chunk + ("" if chunk.endswith("\n") else "\n") + "```"

    for line in raw.splitlines(keepends=True):
        for start in range(0, max(1, len(line)), room):
            piece = line[start:start + room]
            if len(current) + len(piece) > room and current.strip():
                chunks.append(close(current))
                current = fence
            current += piece
        stripped = line.strip()
        if stripped.startswith("```"):
            fence = "" if fence else stripped + "\n"
    if current.strip():
        chunks.append(current)
    return chunks


def _format_fenced_code(language: str, code: str) -> str:
    body = html.escape(code.strip("\n"), quote=False)
    lang = str(language or "").strip()
    if lang:
        class_name = html.escape(f"language-{lang}", quote=True)
        return f'<pre><code class="{class_name}">{body}</code></pre>'
    return f"<pre>{body}</pre>"


def _format_blocks(text: str) -> str:
    """Tables and quotes are line blocks; the remaining lines are inline text."""
    lines = text.split("\n")
    out: list[str] = []
    plain: list[str] = []
    index = 0

    def flush() -> None:
        if plain:
            out.append(_format_text_segment("\n".join(plain)))
            plain.clear()

    while index < len(lines):
        line = lines[index]
        if TABLE_ROW_RE.match(line) and index + 1 < len(lines) and TABLE_RULE_RE.match(lines[index + 1]):
            block = [line]
            index += 2
            while index < len(lines) and TABLE_ROW_RE.match(lines[index]):
                block.append(lines[index])
                index += 1
            flush()
            out.append(_format_table(block) + ("\n" if index < len(lines) else ""))
            continue
        if line.lstrip().startswith(">"):
            block = []
            while index < len(lines) and lines[index].lstrip().startswith(">"):
                block.append(re.sub(r"^\s*>\s?", "", lines[index]))
                index += 1
            flush()
            out.append(f"<blockquote>{_format_text_segment(chr(10).join(block))}</blockquote>"
                       + ("\n" if index < len(lines) else ""))
            continue
        plain.append(line)
        index += 1
    flush()
    return "".join(out)


def _format_table(rows: list[str]) -> str:
    cells = [[cell.strip() for cell in row.strip().strip("|").split("|")] for row in rows]
    widths = [max(len(row[col]) if col < len(row) else 0 for row in cells) for col in range(max(map(len, cells)))]
    rendered = []
    for number, row in enumerate(cells):
        rendered.append(" | ".join((row[col] if col < len(row) else "").ljust(widths[col]) for col in range(len(widths))).rstrip())
        if number == 0:
            rendered.append("-+-".join("-" * width for width in widths))
    return "<pre>" + html.escape("\n".join(rendered), quote=False) + "</pre>"


def _format_text_segment(text: str) -> str:
    placeholders: list[str] = []

    def stash(value: str) -> str:
        token = f"@@TGHTML{len(placeholders)}@@"
        placeholders.append(value)
        return token

    def link_repl(match: re.Match[str]) -> str:
        label = html.escape(match.group(1), quote=False)
        href = html.escape(match.group(2), quote=True)
        return stash(f'<a href="{href}">{label}</a>')

    def code_repl(match: re.Match[str]) -> str:
        return stash(f"<code>{html.escape(match.group(1), quote=False)}</code>")

    raw = LINK_RE.sub(link_repl, text)
    raw = INLINE_CODE_RE.sub(code_repl, raw)
    raw = BULLET_RE.sub(lambda match: f"{match.group(1)}• ", raw)
    escaped = html.escape(raw, quote=False)
    escaped = HEADING_RE.sub(lambda match: f"<b>{match.group(2)}</b>", escaped)
    escaped = BOLD_RE.sub(r"<b>\1</b>", escaped)
    escaped = STRIKE_RE.sub(r"<s>\1</s>", escaped)
    for index, value in enumerate(placeholders):
        escaped = escaped.replace(f"@@TGHTML{index}@@", value)
    return escaped
