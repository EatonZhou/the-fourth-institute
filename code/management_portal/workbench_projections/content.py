from __future__ import annotations

import re
from html.parser import HTMLParser


class ContentTemplateError(ValueError):
    pass


class _ContentParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_style = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = set(str(attributes.get("class") or "").split())
        if tag in {"html", "head", "body", "nav", "base"} or classes.intersection({"portalSiteHeader", "portalNavShell", "portalBrand", "portalActivityRail", "projectionShell"}):
            raise ContentTemplateError("投影只能提供 Portal 业务内容片段，不能创建独立页面、品牌或全局导航")
        self.in_style = tag == "style" or self.in_style

    def handle_endtag(self, tag):
        if tag == "style":
            self.in_style = False

    def handle_data(self, data):
        if self.in_style and re.search(r"(?:^|[{},])\s*(?::root|html\b|body\b)|--(?:bg|panel|text|accent|border|muted)\s*:", data, re.I):
            raise ContentTemplateError("投影 CSS 只能描述业务区域，Portal 基础主题和页面布局由宿主提供")


# Options the trusted host reads from the third Projection.hatg argument; inside the
# instruction arguments they are undeclared parameters and the call is refused.
HOST_OPTIONS = ("binding", "role", "flow", "idempotency_key")
_CLOSERS = {"(": ")", "[": "]", "{": "}"}


def _skip_literal(source: str, index: int) -> int:
    """Return the index after the string, template literal or comment starting at index."""
    char = source[index]
    if source.startswith("//", index):
        end = source.find("\n", index)
        return len(source) if end < 0 else end
    if source.startswith("/*", index):
        end = source.find("*/", index + 2)
        return len(source) if end < 0 else end + 2
    index += 1
    while index < len(source):
        if source[index] == "\\":
            index += 2
            continue
        if source[index] == char:
            return index + 1
        index += 1
    return index


def _split_top_level(source: str, start: int, closer: str) -> tuple[list[str], int]:
    """Split the text from start up to the matching closer at top-level commas."""
    parts, depth_stack, part_start, index = [], [], start, start
    while index < len(source):
        char = source[index]
        if char in "'\"`" or source.startswith(("//", "/*"), index):
            index = _skip_literal(source, index)
            continue
        if char in _CLOSERS:
            depth_stack.append(_CLOSERS[char])
        elif depth_stack and char == depth_stack[-1]:
            depth_stack.pop()
        elif not depth_stack and char == closer:
            parts.append(source[part_start:index])
            return parts, index
        elif not depth_stack and char == ",":
            parts.append(source[part_start:index])
            part_start = index + 1
        index += 1
    parts.append(source[part_start:])
    return parts, index


def _object_keys(literal: str) -> list[str]:
    entries, _ = _split_top_level(literal, 1, "}")
    keys = []
    for entry in entries:
        match = re.match(r"""\s*(?:(["'])(?P<quoted>[^"']*)\1|(?P<name>[A-Za-z_$][\w$]*))\s*(?::|$|\()""", entry)
        if match and not entry.strip().startswith("..."):
            keys.append(match.group("quoted") if match.group("quoted") is not None else match.group("name"))
    return keys


def _without_comments(source: str) -> str:
    """Blank out JS comments (same length), keeping string and template literals."""
    chars, index = list(source), 0
    while index < len(source):
        if source[index] in "'\"`":
            index = _skip_literal(source, index)
        elif source.startswith(("//", "/*"), index):
            end = _skip_literal(source, index)
            chars[index:end] = " " * (end - index)
            index = end
        else:
            index += 1
    return "".join(chars)


def sdk_call_issues(source: str) -> list[str]:
    """Projection.hatg(instruction, arguments, options): host options inside arguments."""
    issues = []
    source = _without_comments(source)
    for match in re.finditer(r"Projection\s*\.\s*hatg\s*\(", source):
        arguments, _ = _split_top_level(source, match.end(), ")")
        if len(arguments) < 2 or not arguments[1].strip().startswith("{"):
            continue
        misplaced = [key for key in _object_keys(arguments[1].strip()) if key in HOST_OPTIONS]
        if misplaced:
            instruction = arguments[0].strip()
            issues.append(f"Projection.hatg({instruction}, …) 的指令参数中含有宿主选项 {'、'.join(misplaced)}")
    return issues


def validate_content(source: str) -> None:
    _ContentParser().feed(source)
    issues = sdk_call_issues(source)
    if issues:
        raise ContentTemplateError(
            "；".join(issues) + "。binding、role、flow、idempotency_key 必须放在第三个参数，例如 "
            'Projection.hatg("TriggerEvent", {prompt}, {binding: "main", flow: "analyze", idempotency_key: key})'
        )
