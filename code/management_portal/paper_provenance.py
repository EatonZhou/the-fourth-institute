"""Historical provenance for existing Paper artifacts. Never generates content."""
import hashlib
import json
import re
from datetime import datetime

import yaml

from management_portal import isa_client, permission_system
from management_portal.paper_store import LOCK, _all_papers, _paper_by_id, _paper_store_file
from management_portal.shared_state import atomic_replace_json

FIELDS = ("api_id", "provider", "account_id", "model", "model_evidence",
          "input_tokens", "cached_input_tokens", "output_tokens", "total_tokens")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def timestamp(value):
    if not value:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()


def read_artifact(paper, kind):
    path = _paper_store_file(paper, kind)
    if not path or not path.is_file() or not path.read_bytes().strip():
        return None
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    header = re.match(r"\A---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)", text)
    metadata = yaml.safe_load(header[1]) if header else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    usage_path = path.with_suffix(".usage.json")
    usage_bytes = usage_path.read_bytes() if usage_path.is_file() else b""
    usage = json.loads(usage_bytes) if usage_bytes else {}
    if not isinstance(usage, dict):
        raise ValueError("Artifact usage must be an object")
    hints = {key: metadata.get("llm_" + key) or metadata.get(key) for key in FIELDS
             if (metadata.get("llm_" + key) or metadata.get(key)) not in (None, "", "未记录")}
    for key, alias in (("api_id", "llm_api"), ("account_id", "llm_account")):
        if metadata.get(alias) not in (None, "", "未记录"):
            hints[key] = metadata[alias]
    # Explicit sidecar unknowns also override older, possibly inferred headers.
    hints.update(usage)
    source = dict(hints.get("generation_source") or {})
    for key in ("resident_id", "input_message_id", "reply_message_id", "execution_id"):
        source.setdefault(key, hints.get(key) or metadata.get("llm_" + key) or metadata.get(key) or "")
    source["reply_message_id"] = source.get("reply_message_id") or hints.get("usage_message_id") or ""
    generated_by = hints.get("generated_by") or metadata.get("generated_by") or ""
    if not source.get("resident_id") and str(generated_by).startswith("resident_"):
        source["resident_id"] = generated_by
    hints["generation_source"] = source
    hints.setdefault("generated_at", metadata.get("generated_at"))
    return path, usage_path, raw, usage_bytes, hints, text[header.end():].strip() if header else text.strip()


def local_status(usage):
    if usage.get("model_evidence") == "resident_current_settings":
        return ""
    if all(usage.get(key) not in (None, "", "未记录") for key in ("model", "provider", "account_id", "total_tokens")):
        return "complete"
    if not (usage.get("generation_source") or {}).get("resident_id"):
        return "missing_original_source"
    return ""


def resolve_usage(usage, body, actor):
    """Exact invocation facts outrank IDs/configuration; a timestamp is a guard, not a model guess."""
    result = dict(usage)
    if result.get("model_evidence") == "resident_current_settings":
        result["model"] = "未记录"
        result["model_evidence"] = "historical_record_missing"
        result.pop("model_resolution", None)
    source = dict(result.get("generation_source") or {})
    resident = source.get("resident_id")
    status = local_status(result)
    if status:
        return result, status
    reply = source.get("reply_message_id")
    input_id = source.get("input_message_id")
    if not reply and input_id:
        state = isa_client.call(actor, "GetInputState", {"resident_id": resident, "message_id": input_id}).get("input_state") or {}
        replies = state.get("reply_message_ids") or []
        if len(replies) == 1:
            reply = replies[0]
    if not reply:
        # Other Agents may have written the artifact directly. Search their own
        # original text, requiring a unique full-body match and generation time.
        query = next((line.strip() for line in body.splitlines() if len(line.strip()) > 20), "")[:200]
        if query and result.get("generated_at"):
            found = []
            cursor = None
            while True:
                args = {"resident_ids": [resident], "query": query, "limit": 200}
                if cursor:
                    args["cursor"] = cursor
                page = isa_client.call(actor, "SearchMessages", args)
                for row in page.get("results", []):
                    m = row.get("message") or {}
                    text = "\n".join(p.get("text", "") for p in m.get("content_parts", []) if p.get("type") == "text").strip()
                    if (m.get("author_id") == resident and text == body.strip()
                            and timestamp(m.get("created_at")) is not None
                            and timestamp(m["created_at"]) <= timestamp(result["generated_at"])):
                        found.append(m["message_id"])
                cursor = page.get("next_cursor")
                if not cursor:
                    break
            if len(set(found)) == 1:
                reply = found[0]
    if not reply:
        return result, "missing_original_message"
    message = isa_client.call(actor, "GetMessage", {"resident_id": resident, "message_id": reply}).get("message") or {}
    if message.get("author_id") != resident or message.get("message_id") != reply:
        raise ValueError("Original message identity mismatch")
    if input_id and message.get("reply_to_message_id") != input_id:
        raise ValueError("Original input identity mismatch")
    if result.get("generated_at") and message.get("created_at") and timestamp(message["created_at"]) > timestamp(result["generated_at"]):
        raise ValueError("Source message is newer than artifact generation")
    if source.get("execution_id") and message.get("generation_source", {}).get("execution_id") not in (None, "", source["execution_id"]):
        raise ValueError("Original execution identity mismatch")
    facts = {**(message.get("usage") or {}), **(message.get("generation_source") or {})}
    execution_id = facts.get("execution_id") or source.get("execution_id")
    if not facts.get("model") and execution_id:
        from management_portal.execution.service import get_execution_service
        try:
            historical = get_execution_service().generation_source(execution_id, actor=actor)
        except KeyError:
            historical = {}  # Scheduler retention can expire before AgentData.
        if historical and (historical.get("resident_id") != resident or historical.get("execution_id") != execution_id):
            raise ValueError("Historical execution source identity mismatch")
        facts = {**{k: v for k, v in historical.items() if v not in (None, "")}, **facts}
        if not facts.get("model") and historical.get("model"):
            facts.update(model=historical["model"], model_evidence=historical["model_evidence"])
    for key in FIELDS:
        if (result.get(key) in (None, "", "未记录") or key == "model_evidence" and result.get(key) == "historical_record_missing") and facts.get(key) not in (None, "", "未记录"):
            result[key] = facts[key]
    result["generation_source"] = {**source, "reply_message_id": reply,
        "source_created_at": message.get("created_at"),
        "execution_id": facts.get("execution_id") or source.get("execution_id") or ""}
    return result, "resolved" if result != usage else "unchanged"


def inventory(actor, paper_ids=None):
    papers = [paper for pid in dict.fromkeys(paper_ids) if (paper := _paper_by_id(pid))] if paper_ids else _all_papers()
    visible = set(permission_system.permitted_ids(actor, "ReadResource", "address", [p["id"] for p in papers]))
    candidates = []
    for paper in papers:
        if paper["id"] not in visible or paper_ids and paper["id"] not in paper_ids:
            continue
        for kind in ("analysis", "summary"):
            try:
                artifact = read_artifact(paper, kind)
            except (ValueError, OSError, yaml.YAMLError) as error:
                candidates.append({"paper_id": paper["id"], "kind": kind, "error": str(error)})
                continue
            if artifact:
                _, _, raw, usage_bytes, usage, _ = artifact
                candidates.append({"paper_id": paper["id"], "kind": kind,
                    "expected_sha256": digest(raw), "expected_usage_sha256": digest(usage_bytes),
                    "status": local_status(usage), "known": {key: usage.get(key) for key in FIELDS},
                    "resident_id": usage["generation_source"].get("resident_id")})
    return {"visible_papers": len(visible), "candidates": candidates}


def refresh(payload, actor):
    paper_id, kind = payload.get("paper_id"), payload.get("kind")
    if kind not in ("analysis", "summary"):
        raise ValueError("kind must be analysis or summary")
    def authorize():
        if paper_id not in permission_system.permitted_ids(actor, "UpdateResource", "resource_id", [paper_id]):
            raise PermissionError("Paper contributor required")
    authorize()
    with LOCK:
        paper = _paper_by_id(paper_id)
        if not paper:
            raise ValueError("Paper no longer exists")
        artifact = read_artifact(paper, kind)
    if not artifact:
        raise ValueError("No existing content")
    path, usage_path, raw, usage_bytes, usage, body = artifact
    if digest(raw) != payload.get("expected_sha256") or digest(usage_bytes) != payload.get("expected_usage_sha256"):
        raise ValueError("Artifact changed; reload before refreshing")
    resolved, status = resolve_usage(usage, body, actor)
    if payload.get("apply") and resolved != usage:
        with LOCK:
            authorize()
            if path.read_bytes() != raw or (usage_path.read_bytes() if usage_path.is_file() else b"") != usage_bytes:
                raise ValueError("Artifact changed during refresh")
            atomic_replace_json(usage_path, resolved, indent=2, sort_keys=False)
    return {"status": status, "applied": bool(payload.get("apply") and resolved != usage), "usage": resolved}
