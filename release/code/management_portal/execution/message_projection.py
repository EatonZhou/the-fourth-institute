"""Portal's message envelope and Chat read policy.

Segments own content. Message metadata only describes its authorship, delivery
and attachments; it must never carry a second copy of the text or a display
decision. Consumers are free to apply a different read policy.
"""
from __future__ import annotations

from collections.abc import Mapping
from html import escape
from pathlib import PurePosixPath
from typing import Any
from xml.etree import ElementTree


OPEN = "<user-message>\n"
CLOSE = "\n</user-message>"
QUOTE_TAG = "quoted-message"


def quote_block(quote: Mapping[str, Any], *, author_title: str = "") -> str:
    """The model-input envelope for an accepted message quote."""
    attributes = [f'author="{escape(str(quote.get("author_id") or ""))}"']
    if author_title:
        attributes.append(f'author_title="{escape(author_title)}"')
    if quote.get("message_id"):
        attributes.append(f'message_id="{escape(str(quote["message_id"]))}"')
    excerpt = str(quote.get("text") or "").replace(f"</{QUOTE_TAG}>", f"<\\/{QUOTE_TAG}>")
    return f"<{QUOTE_TAG} {' '.join(attributes)}>\n{excerpt}\n</{QUOTE_TAG}>"


def _unwrap_quote(text: str, quote: Mapping[str, Any]) -> str:
    header, separator, _ = text.partition(">\n")
    if not separator or not header.startswith(f"<{QUOTE_TAG} "):
        return text
    try:
        element = ElementTree.fromstring(header + f"></{QUOTE_TAG}>")
    except ElementTree.ParseError:
        return text
    prefix = quote_block(quote, author_title=element.get("author_title", "")) + "\n\n"
    return text[len(prefix):] if text.startswith(prefix) else text


def _unwrap_file_references(text: str, templates: list[Mapping[str, Any]], original_length: int | None) -> str:
    """Remove only delivery annotations beyond the accepted text's boundary.

    File paths are added for the model's tools, while Chat already has the
    ordered file parts. Match every annotation to an accepted attachment;
    changed text, literal markers and unrelated paths must remain visible.
    """
    if original_length is None or len(text) <= original_length:
        return text
    body, suffix = text[:original_length], text[original_length:]
    if not suffix.startswith("\n\n[file: "):
        return text
    files = [part for part in templates if part.get("type") == "file"]
    for reference in suffix[2:].split("\n\n"):
        if not reference.startswith("[file: ") or not reference.endswith("]") or reference in body:
            return text
        source = reference[len("[file: "):-1]
        path = PurePosixPath(source)
        matched = next((part for part in files if (
            source and source == (part.get("source") or part.get("path") or part.get("url"))
        ) or (
            part.get("upload_id") and path.parent.name == part["upload_id"]
            and (not part.get("name") or path.name == part["name"])
        )), None)
        if matched is None:
            return text
        files.remove(matched)
    return body


def model_message_id(execution_id: str, item_id: str, phase: str) -> str:
    from management_common.message_identity import derived_message_identity
    key = "final" if phase in {"final", "final_answer"} else "provider:" + item_id
    return derived_message_identity("resident-execution-message", execution_id, key)


def wrap_message(content: str) -> str:
    return OPEN + content + CLOSE


def unwrap_message(content: object) -> str:
    """Remove one complete envelope, preserving its body's exact bytes.

    Provider prompts may have Portal context outside the envelope. Ambiguous,
    damaged or absent envelopes return the complete current text unchanged.
    """
    text = str(content or "")
    if text.startswith(OPEN) and text.endswith(CLOSE):
        return text[len(OPEN):-len(CLOSE)]
    if text.count(OPEN) == 1 and text.count(CLOSE) == 1:
        start = text.index(OPEN) + len(OPEN)
        end = text.index(CLOSE)
        if start <= end:
            return text[start:end]
    return text


def message_facts(message: Mapping[str, Any]) -> dict[str, Any]:
    facts = {key: value for key, value in message.items()
             if key not in {"content_parts", "segment_id", "usage", "provider_response"}}
    # Attachments retain their order relative to text without storing that text.
    facts["parts"] = [
        {"type": "text", "length": len(str(part.get("text") or ""))} if part.get("type") == "text" else dict(part)
        for part in message.get("content_parts", ()) if isinstance(part, Mapping)
    ]
    return facts


def content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, Mapping):
        parts = content.get("content")
        if isinstance(parts, list):
            return "\n".join(str(part.get("text", "")) for part in parts
                             if isinstance(part, Mapping) and part.get("type") in {"input_text", "text"})
    return str(content or "")


def project_message(segment_id: object, facts: Mapping[str, Any], content: Any) -> dict[str, Any]:
    message = {key: value for key, value in facts.items() if key != "parts"}
    text = content_text(content)
    if facts.get("role") == "user" or facts.get("harness_input"):
        text = unwrap_message(text)
    parts: list[dict[str, Any]] = []
    templates = facts.get("parts", ({"type": "text"},))
    texts = [part for part in templates if part.get("type") == "text"]
    original_length = (
        sum(part["length"] for part in texts) + len(texts) - 1
        if texts and all("length" in part for part in texts) else None
    )
    exact = original_length == len(text)
    quote = facts.get("quote")
    if facts.get("role") == "user" and isinstance(quote, Mapping) and quote and not exact:
        # Original message text is already the body. Composed input also carries
        # the accepted quote; remove only its complete matching envelope.
        text = _unwrap_quote(text, quote)
        exact = original_length == len(text)
    if facts.get("role") == "user" and not exact:
        text = _unwrap_file_references(text, templates, original_length)
        exact = original_length == len(text)
    inserted, offset = False, 0
    for part in templates:
        if part.get("type") == "text":
            if exact:
                length = part["length"]
                parts.append({"type": "text", "text": text[offset:offset + length]})
                offset += length + 1
            elif not inserted:
                parts.append({"type": "text", "text": text})
            inserted = True
        else:
            parts.append(dict(part))
    if text and not inserted:
        parts.insert(0, {"type": "text", "text": text})
    message.update(segment_id=str(segment_id), content_parts=parts)
    return message
