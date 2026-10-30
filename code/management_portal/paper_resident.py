"""One task to a Paper role Resident, and reading the reply it returns."""

from __future__ import annotations

import json
import re


def _parse_json_object_from_text(text: str) -> dict:
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("empty JSON text")
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        parsed = json.loads(match.group(0))
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("no JSON object found")


def _resident_reply(
    prompt: str,
    *,
    actor: str,
    resident_scope: str,
    cwd: str = "",
    metadata: dict | None = None,
) -> tuple[str, dict]:
    """One task to the Paper role Resident of ``resident_scope``: its reply and usage.

    The role Resident runs the task with its own Provider, model and settings.
    """
    from management_portal.paper_projection import turn

    result = turn(
        actor=str(actor or "portal"),
        prompt=prompt,
        resident_scope=resident_scope,
        cwd=cwd,
        metadata=dict(metadata or {"business_type": "paper"}),
    )
    content = str(result.get("message") or "").strip()
    if not content:
        raise ValueError(f"{resident_scope} returned an empty reply")
    usage = dict(result.get("usage")) if isinstance(result.get("usage"), dict) else {}
    return content, usage


def _clean_summary_paragraph(content: str) -> str:
    text = str(content or "").strip()
    text = re.sub(r"^```(?:markdown|md)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    text = re.sub(r"^#+\s*.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s+", " ", text).strip(" -\n\t")
    if len(text) > 700:
        text = text[:700].rstrip(" ，,。.;；") + "。"
    if not text:
        raise ValueError("empty paper summary")
    return text


def _extract_json_object(content: str) -> dict:
    text = str(content or "").strip()
    decoder = json.JSONDecoder()
    fenced = [
        match.group(1).strip()
        for match in re.finditer(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    ]
    for candidate in [*fenced, text]:
        for start, character in enumerate(candidate):
            if character != "{":
                continue
            try:
                value, _ = decoder.raw_decode(candidate, start)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    raise ValueError("AI review response was not a JSON object")
