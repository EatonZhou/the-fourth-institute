"""A readable analysis body and compact generation facts, without model calls."""
from pathlib import Path
import hashlib
import json
import re

import yaml


def analysis_view(content: str, path: Path | None, summary_path: Path | None) -> dict:
    raw = content or ""
    is_summary = not raw.strip()
    source_path = summary_path if is_summary else path
    if is_summary and summary_path and summary_path.is_file():
        raw = summary_path.read_text(encoding="utf-8", errors="replace")
    metadata = {}
    body = raw
    header = re.match(r"\A---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)", raw)
    if header:
        try:
            parsed = yaml.safe_load(header[1])
        except yaml.YAMLError:
            parsed = None
        if isinstance(parsed, dict) and parsed.get("schema") in {"tfi.paper_analysis.v1", "dirtybase.paper_analysis.v1"}:
            metadata = parsed
            body = raw[header.end():].lstrip()
    usage = {}
    if source_path:
        sidecar = source_path.with_suffix(".usage.json")
        if sidecar.is_file():
            try:
                usage = json.loads(sidecar.read_text())
            except (ValueError, OSError):
                pass
    if not isinstance(usage, dict):
        usage = {}
    source = usage.get("generation_source") or {}
    facts = {key: usage.get(key, metadata.get(key)) for key in ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens")}
    facts.update({
        "resident_id": source.get("resident_id") or usage.get("resident_id") or metadata.get("llm_resident_id") or "",
        "input_message_id": source.get("input_message_id") or usage.get("input_message_id") or metadata.get("llm_input_message_id") or "",
        "execution_id": source.get("execution_id") or metadata.get("llm_execution_id") or "",
        "model": usage.get("model") or metadata.get("llm_model") or "未记录",
        "model_evidence": usage.get("model_evidence") or metadata.get("llm_model_evidence") or "",
    })
    return {"body": body, "kind": "summary" if is_summary and body.strip() else "analysis" if body.strip() else "empty",
            "provenance": facts, "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path and path.is_file() else ""}
