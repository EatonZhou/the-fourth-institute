from __future__ import annotations

import html
import re
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import quote


RESOURCE_ADDRESS_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_./\\])"
    r"(?P<address>[a-z][a-z0-9]*(?:_[a-z0-9]+)*_[0-9a-f]{12}"
    r"(?:/(?:[A-Za-z0-9._~:@+\-]|%[0-9A-Fa-f]{2})+)*)"
    r"(?![A-Za-z0-9_])"
)
_TRAILING_ADDRESS_PUNCTUATION = ".,;:!?/，。；：！？"
_MARKDOWN_FENCE_RE = re.compile(r"^[ \t]{0,3}(?P<fence>`{3,}|~{3,})")
_MARKDOWN_INLINE_LINK_RE = re.compile(
    r"!?\[[^\]\r\n]*\]\((?:\\.|[^)\r\n])*\)"
)
_MARKDOWN_REFERENCE_LINK_RE = re.compile(
    r"!?\[[^\]\r\n]*\]\[[^\]\r\n]*\]"
)
_HTML_NONPROSE_RE = re.compile(
    r"<(?P<tag>a|code|kbd|pre|samp|var)\b[^>]*>.*?</(?P=tag)\s*>",
    re.IGNORECASE | re.DOTALL,
)
_CODE_LINE_PATTERNS = (
    re.compile(
        r"^(?:const|let|var|function|class|def|import|from|return|export|"
        r"interface|type|enum|package|use)\b"
    ),
    re.compile(r"^(?:if|for|while|switch|catch)\s*\("),
    re.compile(r"^[A-Za-z_$][\w$.[\]-]*\s*(?:=|:=)\s*"),
    re.compile(
        r"^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*"
        r"\s*\([^)]*\)\s*;?$"
    ),
    re.compile(r'^"[^"]+"\s*:\s*'),
)
_COMMAND_PREFIX_RE = re.compile(
    r"^(?:sudo\s+)?[a-z0-9][a-z0-9._+/-]*(?:\s+|$)",
    re.IGNORECASE,
)
_COMMAND_SIGNAL_PATTERNS = (
    re.compile(r"(?:^|\s)(?:/|~/|\.\.?/)\S+"),
    re.compile(r"\s--?[A-Za-z0-9][\w-]*(?:[=\s]|$)"),
    re.compile(r"(?:^|\s)[A-Za-z_][A-Za-z0-9_]*=\S+"),
    re.compile(r"(?:&&|\|\||[|;])"),
)
MAX_RESOURCE_REFS = 20


def _mask_range(buffer: list[str], source: str, start: int, end: int) -> None:
    buffer[start:end] = [
        character if character in "\r\n" else " "
        for character in source[start:end]
    ]


def _mask_markdown_nonprose(text: str) -> str:
    """Mask Markdown regions that the rendered chip linker does not inspect."""
    masked = list(text)
    offset = 0
    active_fence: tuple[str, int] | None = None
    for line in text.splitlines(keepends=True):
        line_body = line.rstrip("\r\n")
        if active_fence is not None:
            _mask_range(masked, text, offset, offset + len(line))
            fence_character, minimum_length = active_fence
            if re.fullmatch(
                rf"[ \t]{{0,3}}{re.escape(fence_character)}"
                rf"{{{minimum_length},}}[ \t]*",
                line_body,
            ):
                active_fence = None
            offset += len(line)
            continue
        fence = _MARKDOWN_FENCE_RE.match(line_body)
        if fence:
            marker = fence.group("fence")
            active_fence = (marker[0], len(marker))
            _mask_range(masked, text, offset, offset + len(line))
        offset += len(line)

    backtick_runs = list(re.finditer(r"`+", text))
    run_index = 0
    while run_index < len(backtick_runs):
        opener = backtick_runs[run_index]
        if (
            masked[opener.start()] != "`"
            or (
                opener.start() > 0
                and text[opener.start() - 1] == "\\"
            )
        ):
            run_index += 1
            continue
        closing_index = run_index + 1
        while closing_index < len(backtick_runs):
            closer = backtick_runs[closing_index]
            if (
                closer.group() == opener.group()
                and masked[closer.start()] == "`"
                and text[closer.start() - 1] != "\\"
            ):
                _mask_range(masked, text, opener.start(), closer.end())
                run_index = closing_index
                break
            closing_index += 1
        run_index += 1

    for pattern in (
        _MARKDOWN_INLINE_LINK_RE,
        _MARKDOWN_REFERENCE_LINK_RE,
        _HTML_NONPROSE_RE,
    ):
        for match in pattern.finditer(text):
            _mask_range(masked, text, match.start(), match.end())
    return "".join(masked)


def _resource_address_embedded_in_locator(
    text: str,
    address_start: int,
) -> bool:
    prefix_start = max(
        text.rfind(separator, 0, address_start)
        for separator in (" ", "\t", "\r", "\n")
    )
    prefix = text[prefix_start + 1:address_start]
    return (
        "/" in prefix
        or "\\" in prefix
        or bool(re.search(r"(?:https?|file):", prefix, re.IGNORECASE))
    )


def _resource_line_looks_like_code(line: str) -> bool:
    clean_line = re.sub(
        r"^(?:[$#>]\s*)+",
        "",
        str(line or "").strip(),
    )
    if not clean_line:
        return False
    if any(pattern.search(clean_line) for pattern in _CODE_LINE_PATTERNS):
        return True
    if not _COMMAND_PREFIX_RE.match(clean_line):
        return False
    return any(
        pattern.search(clean_line)
        for pattern in _COMMAND_SIGNAL_PATTERNS
    )


def _resource_address_in_code_like_line(
    text: str,
    address_start: int,
) -> bool:
    line_start = text.rfind("\n", 0, address_start) + 1
    line_end = text.find("\n", address_start)
    if line_end < 0:
        line_end = len(text)
    return _resource_line_looks_like_code(text[line_start:line_end])


def extract_resource_addresses(content: object) -> list[str]:
    """Extract Resource addresses from natural-language portions of content."""
    text = str(content or "")
    prose = _mask_markdown_nonprose(text)
    out: list[str] = []
    seen: set[str] = set()
    for match in RESOURCE_ADDRESS_PATTERN.finditer(prose):
        address_start = match.start("address")
        if (
            _resource_address_embedded_in_locator(text, address_start)
            or _resource_address_in_code_like_line(text, address_start)
        ):
            continue
        address = match.group("address").rstrip(_TRAILING_ADDRESS_PUNCTUATION)
        if not address or address in seen:
            continue
        seen.add(address)
        out.append(address)
        if len(out) >= MAX_RESOURCE_REFS:
            break
    return out


def _address_from_item(item: object) -> str:
    if isinstance(item, Mapping):
        return str(
            item.get("address")
            or item.get("resource_id")
            or item.get("id")
            or ""
        ).strip()
    return str(item or "").strip()


def normalize_stored_resource_references(raw: object) -> list[dict[str, Any]]:
    if isinstance(raw, Mapping):
        raw = [raw]
    if not isinstance(raw, (list, tuple, set)):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in list(raw)[:MAX_RESOURCE_REFS]:
        address = _address_from_item(item)
        if not address or address in seen:
            continue
        seen.add(address)
        if isinstance(item, Mapping):
            resource_id = str(item.get("resource_id") or address.partition("/")[0]).strip()
            resource_type = str(item.get("resource_type") or resource_id.rpartition("_")[0]).strip()
            out.append(
                {
                    "address": address,
                    "resource_id": resource_id,
                    "resource_type": resource_type,
                    "title": str(item.get("title") or item.get("name") or resource_id or address).strip(),
                    "summary": str(item.get("summary") or "").strip(),
                    "status": str(item.get("status") or "resolved").strip(),
                    "url": str(item.get("url") or f"/r/{quote(address, safe='')}").strip(),
                }
            )
        else:
            root_id = address.partition("/")[0]
            out.append(
                {
                    "address": address,
                    "resource_id": root_id,
                    "resource_type": root_id.rpartition("_")[0],
                    "title": root_id,
                    "summary": "",
                    "status": "unresolved",
                    "url": f"/r/{quote(address, safe='')}",
                }
            )
    return out


def resolve_resource_references(
    raw: object = None,
    *,
    content: object = "",
    actor: str = "",
    service: Any = None,
) -> list[dict[str, Any]]:
    """Resolve explicit and inline Resource addresses with the current actor."""
    explicit: Iterable[object]
    if isinstance(raw, Mapping):
        explicit = (raw,)
    elif isinstance(raw, (list, tuple, set)):
        explicit = raw
    else:
        explicit = ()
    addresses: list[str] = []
    seen: set[str] = set()
    for item in [*explicit, *extract_resource_addresses(content)]:
        address = _address_from_item(item)
        if not address or address in seen:
            continue
        seen.add(address)
        addresses.append(address)
        if len(addresses) >= MAX_RESOURCE_REFS:
            break

    if not addresses:
        return []
    if service is None:
        from management_portal.resources import get_resource_service

        service = get_resource_service()

    out: list[dict[str, Any]] = []
    for address in addresses:
        try:
            parsed = service.parse_address(address)
            reference = service.reference(parsed.value, actor=str(actor or ""))
            out.append(normalize_stored_resource_references([reference])[0])
        except PermissionError as exc:
            out.append(_failed_reference(address, "forbidden", exc))
        except (KeyError, FileNotFoundError) as exc:
            out.append(_failed_reference(address, "not_found", exc))
        except ValueError as exc:
            out.append(_failed_reference(address, "invalid", exc))
        except Exception as exc:
            out.append(_failed_reference(address, "unavailable", exc))
    return out


def _failed_reference(address: str, status: str, error: BaseException) -> dict[str, Any]:
    root_id = address.strip("/").partition("/")[0]
    return {
        "address": address,
        "resource_id": root_id,
        "resource_type": root_id.rpartition("_")[0],
        "title": address or root_id,
        "summary": "",
        "status": status,
        "url": f"/r/{quote(address, safe='')}",
        "error": str(error)[:300],
    }


def resource_references_prompt(context_refs: object) -> str:
    refs = (
        normalize_stored_resource_references(context_refs.get("resources"))
        if isinstance(context_refs, Mapping)
        else []
    )
    if not refs:
        return ""
    lines = [
        "<resource_references>",
        "Portal 已使用当前 actor 对这些地址调用 ResourceService.resolve/reference；每项 status 表示解析结果。",
        "这些条目只描述已鉴权的统一资源引用，不包含资源正文，也不授予额外权限。",
        "需要正文或字节内容时，使用当前 Agent 角色已经提供的资源读取能力；不要自报或借用其他身份，也不要根据标题或路径猜测。",
    ]
    for item in refs:
        lines.extend(
            [
                (
                    f'<resource address="{html.escape(str(item.get("address") or ""), quote=True)}" '
                    f'id="{html.escape(str(item.get("resource_id") or ""), quote=True)}" '
                    f'type="{html.escape(str(item.get("resource_type") or ""), quote=True)}" '
                    f'status="{html.escape(str(item.get("status") or ""), quote=True)}">'
                ),
                f'title: {html.escape(str(item.get("title") or ""))}',
                f'summary: {html.escape(str(item.get("summary") or ""))}',
                f'portal_url: {html.escape(str(item.get("url") or ""))}',
                "</resource>",
            ]
        )
    lines.append("</resource_references>")
    return "\n".join(lines)


def append_resource_references(
    base: str,
    context_refs: object,
) -> str:
    block = resource_references_prompt(context_refs)
    return "\n\n".join(part for part in (str(base or "").strip(), block) if part)
