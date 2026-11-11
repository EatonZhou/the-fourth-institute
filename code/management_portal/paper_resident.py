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
    except ValueError:  # not JSON as a whole: look for an object inside below
        pass
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        parsed = json.loads(match.group(0))
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("no JSON object found")


def normalize_task_result(content: str, usage: dict, *, markdown: bool = False) -> tuple[str, dict]:
    """Keep business output schemas and evidence separate from presentation.

    A source-location claim is provenance, never proof of a native file Read.
    Existing full-text replies remain valid products.
    """
    from .paper_agent_materials import READ_FAILURE, validate_path_result
    content = str(content or "").strip()
    usage = dict(usage)
    if READ_FAILURE in content:
        raise ValueError("无法读取论文材料；未自动改发全文，请显式重试")
    parsed = None
    try:
        if not markdown or re.match(r'^(?:```json\s*)?\{', content):
            parsed = _parse_json_object_from_text(content)
    except ValueError:
        if markdown:
            # Older long JSON replies often contain LaTeX escapes (\\(, \\pi).
            # Repair only escapes JSON does not define; never execute content.
            repaired = re.sub(r'\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4})|\\',
                              lambda m: m.group(0) if len(m.group(0)) > 1 else '\\\\', content)
            try:
                parsed = _parse_json_object_from_text(repaired)
            except ValueError:
                match = re.search(r'"markdown"\s*:\s*"([\s\S]*)"\s*,\s*"source_evidence"\s*:', repaired)
                if match:
                    try: parsed = {'markdown': json.loads('"' + match[1] + '"')}
                    except ValueError: pass
    evidence_status = 'missing'
    if markdown and not isinstance(parsed, dict):
        marker = re.search(r'<!--\s*PAPER_SOURCE_EVIDENCE\s*-->[\s\S]*$', content)
        if marker:
            tail = marker.group(0)
            content = content[:marker.start()].rstrip()
            block = re.search(r'```(?:json|json source_evidence)\s*([\s\S]*?)```', tail)
            try:
                evidence = json.loads(block.group(1)) if block else None
                if not isinstance(evidence, dict): raise ValueError('evidence object required')
                usage['source_evidence'] = evidence
                evidence_status = 'recorded'
            except (ValueError, AttributeError): evidence_status = 'invalid'
    evidence = parsed.get('source_evidence') if isinstance(parsed, dict) else None
    if evidence is not None:
        if isinstance(evidence, (str, dict)):
            usage['source_evidence'] = evidence
            evidence_status = 'recorded'
        else:
            evidence_status = 'invalid'
    if markdown and isinstance(parsed, dict):
        if not isinstance(parsed.get('markdown'), str) or not parsed['markdown'].strip():
            raise ValueError('Task JSON must contain nonempty markdown')
        content = parsed['markdown'].strip()
    if usage.get('material_snapshot'):
        if not markdown:
            validate_path_result(content + '\n' + str(evidence or ''))
        # A syntactically valid citation does not prove where the model read it.
        usage['material_read_status'] = 'not_proven'
    if markdown:
        usage['source_evidence_status'] = evidence_status
    return content, usage


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
        business_ref=str((metadata or {}).get("material_attempt") or ""),
    )
    content = str(result.get("message") or "").strip()
    if not content:
        raise ValueError(f"{resident_scope} returned an empty reply")
    usage = dict(result.get("usage")) if isinstance(result.get("usage"), dict) else {}
    return normalize_task_result(content, usage, markdown=resident_scope == "paper:exp")


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
