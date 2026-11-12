"""Contributor-controlled adoption of an existing completed Exp, never generation."""
import hashlib
import json
import re
from pathlib import Path

from . import isa_client, paper_state_store as store, paper_job_ownership as owner


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def adopt_exp(payload, actor):
    from . import permission_system
    from .execution.agent_data_store import get_agent_data_store
    from .paper_store import DATA_ROOT, LOCK, PAPER_STORE, _paper_by_id, _paper_store_file
    from .paper_reading_jobs import EXP_SECTIONS, _write_exp_markdown, _exp_markdown_document, _set_exp_job
    from .paper_resident import normalize_task_result
    from .paper_records import _now
    names = ("paper_id", "job_id", "resident_id", "input_message_id", "reply_message_id", "execution_id",
             "content_sha256", "manifest_sha256")
    values = {key: str(payload.get(key) or "").strip() for key in names}
    if not all(values.values()) or "expected_target_sha256" not in payload:
        raise ValueError("Explicit job/input/reply/execution/content/manifest and target CAS required")
    def authorize():
        if not actor or values["paper_id"] not in permission_system.permitted_ids(
                actor, "UpdateResource", "resource_id", [values["paper_id"]]):
            raise PermissionError("Exp adoption requires Paper contributor permission")
    authorize()
    original = store.get("exp_jobs", values["job_id"])
    if not original or original.get("paper_id") != values["paper_id"]:
        raise ValueError("Original Exp job does not match Paper")
    if original.get("status") != "error":
        raise ValueError("Only a failed historical Exp may be adopted")
    attempt = "paper-exp:" + values["job_id"]
    args = {"resident_id": values["resident_id"]}
    # Authenticate both message reads before accessing their private correlation.
    input_message = isa_client.call(actor, "GetMessage", {**args, "message_id": values["input_message_id"]}).get("message") or {}
    reply = isa_client.call(actor, "GetMessage", {**args, "message_id": values["reply_message_id"]}).get("message") or {}
    state = isa_client.call(actor, "GetInputState", {**args, "message_id": values["input_message_id"]}).get("input_state") or {}
    if state.get("message_id") != values["input_message_id"] or state.get("state") != "completed":
        raise ValueError("Original input has not completed")
    if (input_message.get("message_id") != values["input_message_id"] or
            reply.get("message_id") != values["reply_message_id"] or reply.get("role") != "agent" or
            reply.get("author_id") != values["resident_id"] or reply.get("reply_to_message_id") != values["input_message_id"]):
        raise ValueError("Reply does not belong to the original input and Resident")
    work = state.get("work") or {}
    if (work.get("state") != "completed" or work.get("execution_id") != values["execution_id"] or
            work.get("primary_message_id") != values["input_message_id"] or
            values["reply_message_id"] not in (state.get("reply_message_ids") or [])):
        raise ValueError("Selected execution and reply are not the completed original work")
    initiator = (input_message.get("origin") or {}).get("initiated_by") or {}
    creator = str(original.get("created_by") or "")
    if (not creator or initiator.get("kind") != "user" or initiator.get("id") != creator or
            (input_message.get("role") == "user" and input_message.get("author_id") != creator)):
        raise ValueError("Original input initiator does not match the job creator")
    history = get_agent_data_store().read_chat_messages(values["resident_id"])
    matches = []
    for message in history:
        context = (message.get("metadata") or {}).get("business_context") or {}
        if (context.get("application") == "paper.exp" and context.get("attempt_ref") == attempt
                and context.get("subject_ref") == values["paper_id"]):
            matches.append(message)
    if len(matches) != 1 or matches[0].get("message_id") != values["input_message_id"]:
        raise ValueError("Business scope/attempt does not identify exactly one original input")
    usage = dict(reply.get("usage") or {})
    variant, source = usage.get("input_variant") or {}, reply.get("generation_source") or {}
    if (variant.get("business_ref") != attempt or variant.get("role") != values["resident_id"] or
            variant.get("execution_id") != values["execution_id"] or source.get("execution_id") != values["execution_id"]):
        raise ValueError("Execution/input-variant evidence does not match the attempt")
    key = "task-" + _digest(("paper:exp\0" + attempt).encode())
    directory = (DATA_ROOT / "job_materials").resolve() / key
    manifest_path = directory / "manifest.json"
    if directory.is_symlink() or manifest_path.is_symlink():
        raise PermissionError("Linked material reference refused")
    encoded = manifest_path.read_bytes()
    manifest = json.loads(encoded); meta = manifest.get("metadata") or {}
    if (_digest(encoded) != values["manifest_sha256"] or manifest.get("schema") != "tfi.paper-task-material.v1" or
            manifest.get("paper_id") != values["paper_id"] or meta.get("attempt") != attempt or
            meta.get("scope") != "paper:exp" or meta.get("actor") != original.get("created_by") or
            meta.get("target_resident_id") != values["resident_id"]):
        raise ValueError("Material manifest does not match the original job/input")
    material_hash = str(manifest.get("sha256") or "")
    if not re.fullmatch(r"[a-f0-9]{64}", material_hash):
        raise ValueError("Invalid material hash")
    material = directory / (material_hash + ".txt")
    if material.is_symlink() or _digest(material.read_bytes()) != material_hash:
        raise ValueError("Original task material has changed")
    content = "\n\n".join(str(p.get("text") or "") for p in reply.get("content_parts") or [] if p.get("type") == "text").strip()
    if _digest(content.encode()) != values["content_sha256"]:
        raise ValueError("Reply changed since approval")
    content, preserved_usage = normalize_task_result(content, usage, markdown=True)
    headings = [re.sub(r"\s+", "", line.casefold()) for line in content.splitlines() if line.startswith("## ")]
    missing = [name for name in EXP_SECTIONS if not any(re.sub(r"\s+", "", name.casefold()) in heading for heading in headings)]
    if missing:
        raise ValueError("Exp is incomplete: " + ", ".join(missing))
    recovery_id = "exp-adopted-" + _digest("\0".join(values[k] for k in
        ("job_id", "input_message_id", "reply_message_id", "content_sha256")).encode())[:24]
    with LOCK, store.locked("exp_jobs"):
        authorize()
        paper = _paper_by_id(values["paper_id"])
        if not paper:
            raise KeyError("Paper not found")
        path = _paper_store_file(paper, "exp")
        if not path:
            raise ValueError("Exp target is not managed by Paper")
        if path.is_symlink():
            raise PermissionError("Linked Exp target refused")
        current_original = store.get("exp_jobs", values["job_id"])
        if (not current_original or current_original.get("status") != original.get("status") or
                current_original.get("updated_at") != original.get("updated_at")):
            raise ValueError("Original job CAS conflict")
        if not payload.get("dry_run", True) and payload.get("expected_job_updated_at") != original.get("updated_at"):
            raise ValueError("Execute requires the original-job CAS from dry-run")
        current_hash = _digest(path.read_bytes()) if path.exists() else ""
        intent_path = path.with_name(path.name + "." + recovery_id + ".json")
        if intent_path.is_symlink():
            raise PermissionError("Linked adoption intent refused")
        intent = json.loads(intent_path.read_text()) if intent_path.exists() else {}
        if intent and (intent.get("source") != values or intent.get("expected_target_sha256") != payload["expected_target_sha256"]):
            raise ValueError("Adoption intent source/CAS conflict")
        existing = store.get("exp_jobs", recovery_id)
        if existing and existing.get("status") == "done":
            receipt = (existing.get("usage") or {}).get("adoption") or {}
            if current_hash != receipt.get("saved_sha256"):
                raise ValueError("Adopted artifact has since changed")
            return {"dry_run": bool(payload.get("dry_run", True)), "idempotent": True, "job": existing}
        generated_at = intent.get("generated_at", _now())
        document = _exp_markdown_document(paper, content, preserved_usage,
            actor=str(original.get("created_by") or ""), generated_at=generated_at)
        planned_sha = _digest(document.encode())
        reconcile = bool(intent and current_hash == planned_sha and intent.get("planned_sha256") == planned_sha)
        if not reconcile and current_hash != str(payload["expected_target_sha256"]):
            raise ValueError("Exp target CAS conflict")
        if not reconcile and path.exists() and path.stat().st_mtime > float(original.get("created_at") or 0):
            raise ValueError("A newer Exp artifact exists; cannot adopt")
        for job in store.records("exp_jobs", paper_id=values["paper_id"]):
            if job.get("job_id") == recovery_id:
                continue
            if job.get("status") in {"queued", "running"} or float(job.get("created_at") or 0) > float(original.get("created_at") or 0):
                raise ValueError("A newer or active Exp job exists; cannot adopt")
        adoption = {**values, "original_job_status": original.get("status"), "adopted_by": actor,
                    "source_reply_created_at": reply.get("created_at"), "generation_source": source,
                    "completed_work": work, "input_initiated_by": initiator,
                    "manifest_material_sha256": material_hash, "expected_target_sha256": payload["expected_target_sha256"],
                    "sections": len(EXP_SECTIONS), "adopted_at": _now()}
        audit = {"dry_run": bool(payload.get("dry_run", True)), "recovery_job_id": recovery_id,
                 "verified": adoption, "target": str(path), "unique_input": True,
                 "expected_job_updated_at": original.get("updated_at"), "reconcile_existing_save": reconcile}
        if audit["dry_run"]:
            return audit
        if not intent:
            from .shared_state import atomic_replace_json
            atomic_replace_json(intent_path, {"source": values, "expected_target_sha256": payload["expected_target_sha256"],
                "planned_sha256": planned_sha, "generated_at": generated_at})
        # A separate recovery record preserves the original failure and cannot
        # be launched by the model recovery scanner while this lock is held.
        if existing is None:
            store.append("exp_jobs", {"job_id": recovery_id, "paper_id": values["paper_id"], "status": "queued",
                "step": "saving result", "created_by": actor, "created_at": _now(), "updated_at": _now(),
                "usage": {**preserved_usage, "adoption": adoption}})
        def save(identity):
            with owner.fenced_write(identity):
                authorize()
                actual = _digest(path.read_bytes()) if path.exists() else ""
                if actual != current_hash:
                    raise ValueError("Exp changed immediately before fenced publication")
                saved = _write_exp_markdown(paper, content, preserved_usage,
                    actor=str(original.get("created_by") or ""), generated_at=generated_at)
                adoption["saved_sha256"] = _digest(saved.read_bytes())
                PAPER_STORE.update_paper(values["paper_id"], {})
                _set_exp_job(identity, {"status": "done", "step": "done", "progress": 100, "error": "",
                    "exp_path": str(saved), "usage": {**preserved_usage, "adoption": adoption}})
        if not owner.run(recovery_id, save, adapter=owner.TableAdapter("exp_jobs")):
            raise ValueError("Exp adoption is already owned; inspect the existing recovery")
        return {**audit, "job": store.get("exp_jobs", recovery_id)}
