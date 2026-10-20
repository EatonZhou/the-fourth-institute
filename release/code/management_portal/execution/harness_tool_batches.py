from __future__ import annotations

from copy import deepcopy

from .harness import AgentLoopToolResult


def tool_result_text(item: AgentLoopToolResult) -> str:
    """Return a lossless text view only for plain successful tool results."""

    result = item.result if isinstance(item.result, dict) else {}
    if result.get("success") is not True:
        return ""
    content_items = result.get("contentItems")
    if not isinstance(content_items, list) or not content_items:
        return ""
    texts: list[str] = []
    for content_item in content_items:
        if (
            not isinstance(content_item, dict)
            or content_item.get("type") != "inputText"
            or not isinstance(content_item.get("text"), str)
        ):
            return ""
        texts.append(content_item["text"])
    return "\n".join(texts)


def replace_tool_result_text(
    item: AgentLoopToolResult,
    replacement: str,
) -> AgentLoopToolResult:
    """Replace an eligible plain-text result while preserving its envelope."""

    result = deepcopy(item.result)
    result["contentItems"] = [{
        "type": "inputText",
        "text": str(replacement or ""),
    }]
    return AgentLoopToolResult(
        call_id=item.call_id,
        tool_name=item.tool_name,
        arguments=dict(item.arguments),
        result=result,
        source=item.source,
    )


def utf8_bytes(value: object) -> int:
    return len(str(value or "").encode("utf-8"))


def numeric_delta(before: object, after: object) -> int:
    return max(0, utf8_bytes(before) - utf8_bytes(after))
