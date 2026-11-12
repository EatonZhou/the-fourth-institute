"""Deep Read and Exp: background jobs that turn a paper's PDF text into a checked Markdown artifact."""

from __future__ import annotations

from contextvars import copy_context
from pathlib import Path
import hashlib
import json
import re
import secrets
import yaml

from management_portal import business_tasks, paper_state_store, paper_job_ownership
from management_portal.paper_detail import get_paper_detail
from management_portal.paper_records import _now, _slugify
from management_portal.paper_resident import _resident_reply
from management_portal.paper_store import (
    DATA_ROOT, LOCK, PAPER_STORE, _all_papers, _analysis_text, _extract_pdf_text, _local_pdf_for_paper,
    _notify_paper_projection, _paper_by_id, _paper_public, _paper_store_file, _pdf_text_for_paper, _update_paper_fields,
)
from management_portal.resources import get_resource_service
from management_portal.shared_state import atomic_replace_json


ANALYSIS_ROOT = DATA_ROOT / "analysis"


EXP_STALE_SECONDS = 45 * 60


def _set_job_with_run_record(kind: str, job_id: str, patch: dict) -> dict:
    # The run record is written before the job's lock is released, so run
    # records of one job are written in the same order as the job itself.
    with LOCK, paper_state_store.locked(kind):
        values = {**patch, "updated_at": _now()}
        found = (paper_job_ownership.worker_write(job_id, values)
                 if kind == "deep_read_jobs" and paper_job_ownership.is_worker()
                 else paper_state_store.merge(kind, job_id, values))
        try:
            PAPER_STORE.write_run_record(found)
        except Exception as exc:
            print(f"[paper-run-record] {kind} {job_id} not recorded: {type(exc).__name__}: {exc}", flush=True)
    return found


def _set_deep_read_job(job_id: str, patch: dict) -> dict:
    found = _set_job_with_run_record("deep_read_jobs", job_id, patch)
    _notify_paper_projection(
        found.get("paper_id") or job_id,
        fields=["deep_read"],
    )
    return found


def _set_exp_job(job_id: str, patch: dict) -> dict:
    found = _set_job_with_run_record("exp_jobs", job_id, patch)
    _notify_paper_projection(
        found.get("paper_id") or job_id,
        fields=["exp"],
    )
    return found


def _latest_job(kind: str, *, paper_id: str = "", job_id: str = "") -> dict | None:
    if job_id:
        return paper_state_store.get(kind, job_id)
    return paper_state_store.latest(kind, paper_id=paper_id)


def _latest_deep_read_job(*, paper_id: str = "", job_id: str = "") -> dict | None:
    return _latest_job("deep_read_jobs", paper_id=paper_id, job_id=job_id)


def _latest_exp_job(*, paper_id: str = "", job_id: str = "") -> dict | None:
    return _latest_job("exp_jobs", paper_id=paper_id, job_id=job_id)


def _is_stale_exp_job(job: dict) -> bool:
    status = str(job.get("status") or "")
    if status not in {"queued", "running"}:
        return False
    touched = int(job.get("updated_at") or job.get("created_at") or 0)
    return touched > 0 and _now() - touched > EXP_STALE_SECONDS


def _mark_stale_exp_job(job: dict) -> dict:
    return _set_exp_job(
        str(job.get("job_id") or ""),
        {
            "status": "error",
            "step": "stale",
            "progress": 100,
            "error": "Exp task was interrupted or stopped updating. Please generate Exp again.",
        },
    )


def start_deep_read(payload: dict, actor: str) -> dict:
    paper_id = str(payload.get("paper_id") or "").strip()
    if not paper_id:
        raise ValueError("paper_id required")
    # Same resource-read contract as the existing HTTP route/status API.
    # Do not introduce a contributor requirement for this read-generation task.
    if not str(actor or "").strip():
        raise PermissionError("Paper requires an authenticated actor")
    get_resource_service().get(paper_id, actor=actor)
    job, created = paper_state_store.admit_deep_read({
        "job_id": f"deep-read-{secrets.token_hex(6)}", "paper_id": paper_id,
        "paper_title": paper_id, "status": "queued", "step": "queued", "progress": 0,
        "created_by": actor, "created_at": _now(), "updated_at": _now(),
        "analysis_path": "", "error": "", "usage": {},
    })
    if created:
        _launch_deep_read_business_task(job)
    return job


def _launch_deep_read_business_task(job: dict) -> dict:
    job_id = str(job.get("job_id") or "")
    if not job_id:
        raise ValueError("Deep Read job id required")
    business_tasks.start(
        f"paper-deep-read:{job_id}", copy_context().run,
        paper_job_ownership.run, job_id, _run_deep_read_job,
        on_error=lambda exc: print(f"[paper-job-owner] {job_id}: {exc}", flush=True),
    )
    return job


def recover_deep_read(payload: dict, actor: str) -> dict:
    """Adopt an explicitly selected generated message; never submit an AI input.

    The contributor selects the source, including a forwarded message. Authorship
    is verified; this selection is recorded, not inferred to be a canonical reply.
    A separate successful job preserves the original failed job and its error.
    """
    from management_portal import isa_client, permission_system
    from management_portal.paper_projection import input_receipt

    names = ("paper_id", "job_id", "input_message_id", "source_resident_id", "source_message_id", "content_sha256")
    values = {name: str(payload.get(name) or "").strip() for name in names}
    if not all(values.values()):
        raise ValueError("paper_id, job_id, input_message_id, source_resident_id, source_message_id and content_sha256 required")
    paper_id, job_id = values["paper_id"], values["job_id"]

    def authorize():
        if not actor or paper_id not in permission_system.permitted_ids(actor, "UpdateResource", "resource_id", [paper_id]):
            raise PermissionError("Deep Read recovery requires Paper contributor permission")

    authorize()
    original = _latest_deep_read_job(job_id=job_id)
    if not original:
        raise KeyError("Deep Read job not found")
    if original.get("paper_id") != paper_id or original.get("status") != "error":
        raise ValueError("recovery requires the failed job of this Paper")
    receipt = input_receipt(original.get("resident_handoff") or {})
    resident_id = str(original.get("resident_id") or "")
    if (not resident_id or receipt.get("resident_id") != resident_id
            or receipt.get("input_identity_status") != "delivered"
            or receipt.get("input_message_id") != values["input_message_id"]
            or original.get("resident_input_message_id") != values["input_message_id"]):
        raise ValueError("recovery requires the original confirmed input")
    state = isa_client.call(actor, "GetInputState", {
        "resident_id": resident_id, "message_id": values["input_message_id"],
    }).get("input_state") or {}
    if state.get("state") != "completed" or state.get("message_id") != values["input_message_id"]:
        raise ValueError("original Resident input has not completed")
    message = isa_client.call(actor, "GetMessage", {
        "resident_id": values["source_resident_id"], "message_id": values["source_message_id"],
    }).get("message") or {}
    if message.get("message_id") != values["source_message_id"] or message.get("author_id") != resident_id:
        raise ValueError("source message must be authored by the original role Resident")
    content = "\n\n".join(str(part.get("text") or "") for part in message.get("content_parts") or []
                          if part.get("type") == "text").strip()
    if hashlib.sha256(content.encode()).hexdigest() != values["content_sha256"]:
        raise ValueError("source content changed; read and confirm the message again")
    from .paper_resident import normalize_task_result
    content, recovered_usage = normalize_task_result(content, dict(original.get("usage") or {}), markdown=True)
    _require_artifact_sections(content, DEEP_READ_SECTIONS, "Deep Read")
    identity = "\0".join(values[name] for name in names)
    recovery_id = "deep-read-recovered-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
    with LOCK, paper_state_store.locked("deep_read_jobs"):
        authorize()
        existing = _latest_deep_read_job(job_id=recovery_id)
        if existing:
            return existing
        latest = _latest_deep_read_job(paper_id=paper_id)
        if not latest or latest.get("job_id") != job_id or latest.get("status") != "error":
            raise ValueError("Deep Read job changed; reload before recovery")
        paper = get_paper_detail(paper_id, sections=[])["paper"]
        path = _paper_store_file(paper, "analysis")
        if not path:
            path = ANALYSIS_ROOT / f"{_slugify(str(paper.get('id') or paper.get('title') or 'paper'))}.md"
        current_hash = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""
        if current_hash != str(payload.get("expected_analysis_sha256") or ""):
            raise ValueError("Analysis changed; read and confirm the current artifact before recovery")
        # Provenance is an explicit contributor selection. No forwarded text is
        # relabelled as the canonical final reply, and no model usage is invented.
        usage = {**recovered_usage, "artifact_recovery": {
            **values, "selected_by": actor, "selection": "explicit_generated_message",
        }}
        path = _save_deep_read_analysis(paper, content, usage)
        recovered = {
            "job_id": recovery_id, "paper_id": paper_id, "paper_title": str(paper.get("title") or ""),
            "status": "done", "step": "recovered generated artifact", "progress": 100,
            "created_by": actor, "created_at": _now(), "updated_at": _now(),
            "analysis_path": str(path), "error": "", "usage": usage,
            "resident_id": resident_id, "resident_input_message_id": values["input_message_id"],
            "resident_handoff": receipt, "lineage_id": str(original.get("lineage_id") or ""),
        }
        paper_state_store.append("deep_read_jobs", recovered)
        return _set_deep_read_job(recovery_id, {})


def _authorize_paper_job_status(job: dict | None, *, actor: str, job_id: str, paper_id: str) -> None:
    if not str(actor or "").strip():
        raise PermissionError("Paper status requires an authenticated actor")
    if not job_id and not paper_id:
        raise ValueError("paper_id or job_id required")
    if job_id and not job:
        raise KeyError("Paper job not found")
    target_paper_id = str(job.get("paper_id") or "") if job else paper_id
    if not target_paper_id:
        raise KeyError("Paper job has no associated paper")
    get_resource_service().get(target_paper_id, actor=actor)
    if paper_id and target_paper_id != paper_id:
        raise ValueError("Paper job belongs to another paper")


def provenance_candidates(actor: str) -> dict:
    """Inventory visible Papers and their latest completed jobs, without resuming work."""
    from management_portal import permission_system

    ids = [str(paper["id"]) for paper in _all_papers()]
    visible = set(permission_system.permitted_ids(actor, "ReadResource", "address", ids))
    latest = paper_state_store.latest_per_paper("deep_read_jobs", paper_ids=visible)
    candidates = [{"paper_id": job["paper_id"], "job_id": job["job_id"]}
                  for job in latest if job.get("status") == "done"]
    return {"visible_papers": len(visible), "without_completed_job": len(visible) - len(candidates), "candidates": candidates}


def refresh_deep_read_provenance(payload: dict, actor: str) -> dict:
    """Refresh only the recorded generation metadata, with an explicit artifact CAS."""
    from management_portal import isa_client, permission_system
    from management_portal.paper_projection import input_receipt

    paper_id, job_id, expected = (str(payload.get(k) or "").strip()
                                  for k in ("paper_id", "job_id", "expected_analysis_sha256"))
    if not all((paper_id, job_id, expected)):
        raise ValueError("paper_id, job_id and expected_analysis_sha256 required")

    def authorize():
        if not actor or paper_id not in permission_system.permitted_ids(actor, "UpdateResource", "resource_id", [paper_id]):
            raise PermissionError("Analysis provenance requires Paper contributor permission")

    authorize()
    original = _latest_deep_read_job(job_id=job_id)
    if not original or original.get("paper_id") != paper_id or original.get("status") != "done":
        raise ValueError("provenance requires the completed job of this Paper")
    receipt = input_receipt(original.get("resident_handoff") or {})
    resident_id, input_id = str(original.get("resident_id") or ""), str(original.get("resident_input_message_id") or "")
    if (not resident_id or not input_id or receipt.get("resident_id") != resident_id
            or receipt.get("input_message_id") != input_id or receipt.get("input_identity_status") != "delivered"):
        raise ValueError("provenance requires a confirmed original input")
    state = isa_client.call(actor, "GetInputState", {"resident_id": resident_id, "message_id": input_id}).get("input_state") or {}
    if state.get("message_id") != input_id or state.get("state") != "completed":
        raise ValueError("original input has not completed")
    usage = dict(original.get("usage") or {})
    # Read only the original canonical reply, never current Resident settings.
    reply_id = str(receipt.get("usage_message_id") or "")
    if reply_id and reply_id in (state.get("reply_message_ids") or []):
        message = isa_client.call(actor, "GetMessage", {"resident_id": resident_id, "message_id": reply_id}).get("message") or {}
        if (message.get("message_id") != reply_id or message.get("author_id") != resident_id
                or message.get("reply_to_message_id") != input_id):
            raise ValueError("generation reply identity changed")
        # Preserve recovery selection and its provenance independently of usage.
        for key in ("api_id", "provider", "account_id", "model", "model_evidence", "input_tokens", "cached_input_tokens", "output_tokens", "total_tokens"):
            if (message.get("usage") or {}).get(key):
                usage[key] = message["usage"][key]
        for key in ("provider", "account_id", "model", "model_evidence"):
            if (message.get("generation_source") or {}).get(key):
                usage[key] = message["generation_source"][key]
    usage["generation_source"] = {
        "resident_id": resident_id, "input_message_id": input_id,
        "reply_message_id": reply_id, "execution_id": str((state.get("work") or {}).get("execution_id") or ""),
        "provider_turn_id": str((state.get("work") or {}).get("provider_turn_id") or ""),
        "evidence": "original_input_and_reply_usage",
    }
    with LOCK, paper_state_store.locked("deep_read_jobs"):
        authorize()
        latest = _latest_deep_read_job(paper_id=paper_id)
        if not latest or latest.get("job_id") != job_id or latest != original:
            raise ValueError("Deep Read job changed; reload before updating provenance")
        paper = get_paper_detail(paper_id, sections=[])["paper"]
        path = _paper_store_file(paper, "analysis")
        if not path or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Analysis changed; read and confirm the current artifact")
        document = path.read_text()
        match = re.match(r"\A---\n([\s\S]*?)\n---\n", document)
        metadata = yaml.safe_load(match.group(1)) if match else None
        if not isinstance(metadata, dict) or metadata.get("schema") != "tfi.paper_analysis.v1" or metadata.get("paper_id") != paper_id:
            raise ValueError("Analysis has no matching managed provenance header")
        metadata.update(_analysis_model_metadata(usage))
        updated = "---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + "---\n" + document[match.end():]
        path = _write_analysis_file(paper, updated, usage)
        _update_paper_fields(paper_id, {"analysis_path": str(path)})
        return _set_deep_read_job(job_id, {"usage": usage})


def get_deep_read_status(*, actor: str, job_id: str = "", paper_id: str = "") -> dict:
    job = _latest_deep_read_job(paper_id=paper_id, job_id=job_id)
    _authorize_paper_job_status(job, actor=actor, job_id=job_id, paper_id=paper_id)
    return job or {"status": "idle", "paper_id": paper_id, "job_id": job_id}


# The sections a Deep Read analysis and an Exp extraction must contain. A reply with fewer than a third of them is not
# the requested artifact (for example a model narrating tool calls it cannot run) and is not saved as done; a long
# analysis cut off after its first sections still is.
DEEP_READ_SECTIONS = (
    "一段话先看懂", "论文要解决的问题", "方法结构", "关键机制与算法流程", "Figure / Table / Experiment Evidence",
    "关键结果", "可迁移价值与适用边界", "局限与需要继续确认的问题", "可检索标签",
)
EXP_SECTIONS = (
    "实验目标", "数据集 / 环境 / 任务", "Baselines 与对比对象", "Metrics", "训练与实现设置",
    "关键实验结果", "Ablation / Sensitivity", "复现路径", "复现风险",
)
DEEP_READ_HEADINGS = "\n".join(f"## {name}" for name in DEEP_READ_SECTIONS)


def _require_artifact_sections(content: str, sections: tuple[str, ...], artifact: str) -> None:
    headings = [
        re.sub(r"\s+", " ", line.strip().lstrip("#")).strip().casefold()
        for line in str(content or "").splitlines()
        if line.strip().startswith("#")
    ]
    present = [name for name in sections if any(name.casefold() in heading for heading in headings)]
    if len(present) * 3 < len(sections):
        opening = re.sub(r"\s+", " ", str(content or "")).strip()[:120]
        raise RuntimeError(
            f"{artifact} 返回的不是规定产物：{len(sections)} 个必需小节只出现 {len(present)} 个；输出开头：{opening}"
        )


def _run_deep_read_job(job_id: str) -> None:
    try:
        print(f"[paper-deep-read] {job_id} start", flush=True)
        job = _latest_deep_read_job(job_id=job_id)
        if not job:
            raise KeyError("Deep Read job not found")
        if str(job.get("status") or "") in {"done", "error"}:
            return
        handoff_fields = set(job.get("resident_handoff") or {}) - {paper_job_ownership.CLAIM_KEY}
        generation = ((job.get("resident_handoff") or {}).get(paper_job_ownership.CLAIM_KEY) or {}).get("generation", 1)
        if job.get("status") == "running" and generation == 1 and not handoff_fields and not job.get("resident_input_message_id"):
            # Legacy in-flight rows have no durable submission boundary. Do
            # not infer "never submitted" from an absent receipt after restart.
            _set_deep_read_job(job_id, {"resident_handoff": {"_submission_started": True}})
        paper_id = str(job.get("paper_id") or "")
        paper = get_paper_detail(paper_id, sections=[])["paper"]
        actor = str(job.get("created_by") or "portal")
        _set_deep_read_job(
            job_id,
            {
                "status": "running",
                "paper_title": str(paper.get("title") or paper_id),
                "step": "waiting for Resident" if job.get("resident_input_message_id") else
                        "confirming submission" if handoff_fields else "extracting pdf text",
                "progress": 18,
                "error": "",
            },
        )
        from management_portal.paper_projection import input_receipt, resume, turn, confirm_input

        job = _latest_deep_read_job(job_id=job_id) or job
        resident_id = str(job.get("resident_id") or "")
        resident_input_message_id = str(job.get("resident_input_message_id") or "")
        attempt_handle = dict(job.get("resident_handoff") or {})

        def remember_input(handle: dict) -> None:
            nonlocal attempt_handle
            attempt_handle = input_receipt(handle)
            _set_deep_read_job(job_id, {
                "resident_id": attempt_handle["resident_id"],
                "resident_input_message_id": attempt_handle["input_message_id"],
                "resident_reply_message_ids": attempt_handle["reply_message_ids"],
                "resident_usage_message_id": attempt_handle["usage_message_id"],
                "resident_handoff": attempt_handle,
                "lineage_id": attempt_handle["lineage_id"],
            })

        if not resident_input_message_id and (attempt_handle.get("_submission_started") or resident_id):
            _set_deep_read_job(job_id, {"step": "confirming submission", "error": ""})
            confirmed = confirm_input(actor=actor, receipt=attempt_handle)
            remember_input(confirmed)
            resident_id = confirmed["resident_id"]
            resident_input_message_id = confirmed["input_message_id"]
        if resident_id and resident_input_message_id:
            result = resume(
                actor=actor,
                resident_id=resident_id,
                lineage_id=str(job.get("lineage_id") or ""),
                message_id=resident_input_message_id,
                receipt=attempt_handle,
                on_submitted=remember_input,
            )
        else:
            if not _paper_public(paper).get("has_pdf"):
                raise ValueError("Paper has no PDF (没有 PDF); attach a PDF before retrying Deep Read")
            pdf_path = _local_pdf_for_paper(paper)
            text = _extract_pdf_text(pdf_path, paper_id=paper_id)
            if len(text.strip()) < 1200:
                raise RuntimeError("PDF text extraction returned too little text")
            # Persist uncertainty BEFORE entering any submission path. A crash
            # without a full locator requires observation/manual diagnosis,
            # never a guessed second model invocation.
            _set_deep_read_job(job_id, {"resident_handoff": {"_submission_started": True}})
            _set_deep_read_job(job_id, {"status": "running", "step": "waiting for Resident", "progress": 42})
            result = turn(
                actor=actor,
                prompt=_deep_read_prompt(paper, text),
                resident_scope="paper:deep-read",
                business_ref=f"paper-deep-read:{job_id}",
                metadata={
                    "business_type": "paper_deep_read",
                    "paper_id": paper_id,

                },
                on_material_prepared=lambda material: _set_deep_read_job(job_id, {'usage': {**(job.get('usage') or {}), 'material_snapshot': material}}),
                on_assigned=remember_input,
                on_submitted=remember_input,
            )
        content = str(result.get("message") or "").strip()
        if not content:
            raise RuntimeError("Deep Read AI execution returned empty output")
        usage = (
            dict(result.get("usage"))
            if isinstance(result.get("usage"), dict)
            else {}
        )
        # Keep reported generation facts even if artifact validation fails.
        _set_deep_read_job(job_id, {"usage": usage})
        if (job.get('usage') or {}).get('material_snapshot'):
            usage.setdefault('material_snapshot', job['usage']['material_snapshot'])
        from .paper_resident import normalize_task_result
        content, usage = normalize_task_result(content, usage, markdown=True)
        _require_artifact_sections(content, DEEP_READ_SECTIONS, "Deep Read")
        print(f"[paper-deep-read] {job_id} writing analysis", flush=True)
        _set_deep_read_job(job_id, {"step": "writing analysis", "progress": 82})
        with LOCK, paper_job_ownership.fenced_write(job_id):
            analysis_path = _save_deep_read_analysis(paper, content, usage)
            _set_deep_read_job(
                job_id,
                {
                    "status": "done", "step": "done", "progress": 100,
                    "analysis_path": str(analysis_path), "usage": usage, "error": "",
                },
            )
        print(f"[paper-deep-read] {job_id} done", flush=True)
    except paper_job_ownership.OwnershipLost:
        raise
    except Exception as exc:
        from .lineage_workflows import WorkflowPending, WorkflowFailed
        from .paper_projection import InputConfirmationPending, PaperInputIdentityError
        current = _latest_deep_read_job(job_id=job_id) or {}
        receipt = current.get("resident_handoff") or {}
        uncertain = bool(receipt.get("resident_id") and not receipt.get("input_message_id"))
        transient_wait = isinstance(exc, TimeoutError) and bool(receipt.get("_submission_started") or receipt.get("input_message_id"))
        if (isinstance(exc, (WorkflowPending, InputConfirmationPending)) or transient_wait or
                (uncertain and not isinstance(exc, (WorkflowFailed, PaperInputIdentityError)))):
            patch = {"status": "running", "step": "confirming submission", "error": ""}
            if receipt.get("input_message_id"):
                patch["step"] = "waiting for Resident"
            if isinstance(exc, WorkflowPending):
                from .paper_projection import receipt_from_resident_handle
                patch["resident_handoff"] = receipt_from_resident_handle(exc.handle)
            _set_deep_read_job(job_id, patch)
            return
        print(f"[paper-deep-read] {job_id} error: {exc}", flush=True)
        _set_deep_read_job(
            job_id,
            {
                "status": "error",
                "step": "error",
                "error": str(exc),
                "progress": 100,
            },
        )


def _deep_read_prompt(paper: dict, pdf_text: str) -> str:
    full_text = str(pdf_text or "")
    return f"""请深读下面这篇论文，输出 Markdown。必须包含这些小节：

# {paper.get('title') or 'Paper analysis'}

{DEEP_READ_HEADINGS}

只根据论文证据讨论适用场景，不假定论文属于某个项目，不虚构项目需求。

Paper metadata:
- Title: {paper.get('title') or ''}
- Authors: {paper.get('authors') or ''}
- Venue: {paper.get('venue') or ''} {paper.get('year') or ''}
- Tags: {', '.join(paper.get('tags') or [])}
- Abstract: {paper.get('abstract') or ''}

Extracted PDF text:
{full_text}
"""


def _save_deep_read_analysis(paper: dict, content: str, usage: dict) -> Path:
    _require_artifact_sections(content, DEEP_READ_SECTIONS, "Deep Read")
    path = _write_analysis_file(paper, _deep_read_analysis_document(paper, content, usage), usage)
    _update_paper_fields(str(paper["id"]), {"analysis_path": str(path), "status": "analysis_ready"})
    return path


def _analysis_model_metadata(usage: dict) -> dict:
    source = usage.get("generation_source") or {}
    recovery = usage.get("artifact_recovery") or {}
    return {
        "llm_api": str(usage.get("api_id") or "未记录"),
        "llm_provider": str(usage.get("provider") or "未记录"),
        "llm_account": str(usage.get("account_id") or "未记录"),
        "llm_model": str(usage.get("model") or "未记录"),
        "llm_model_evidence": str(usage.get("model_evidence") or "原始执行用量记录") if usage.get("model") else "原始记录未包含型号；未按当前配置推断",
        "llm_resident_id": source.get("resident_id") or usage.get("resident_id") or "",
        "llm_input_message_id": source.get("input_message_id") or recovery.get("input_message_id") or usage.get("input_message_id") or "",
        "llm_reply_message_id": source.get("reply_message_id") or usage.get("usage_message_id") or "",
        "llm_execution_id": source.get("execution_id") or "",
        "llm_source_message_id": recovery.get("source_message_id") or "",
        "llm_source_selection": recovery.get("selection") or "canonical_reply",
        **{key: usage[key] for key in ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens") if isinstance(usage.get(key), int)},
    }


def _deep_read_analysis_document(
    paper: dict,
    content: str,
    usage: dict,
) -> str:
    header = (
        "---\n"
        "schema: tfi.paper_analysis.v1\n"
        f"paper_id: {paper.get('id') or ''}\n"
        f"generated_at: {_now()}\n"
        + "".join(f"{key}: {json.dumps(value, ensure_ascii=False)}\n" for key, value in _analysis_model_metadata(usage).items())
        +
        "---\n\n"
    )
    return header + _strip_outer_markdown_fence(content).strip() + "\n"


def _strip_outer_markdown_fence(content: str) -> str:
    text = str(content or "").strip()
    match = re.fullmatch(r"```(?:markdown|md)?\s*\n([\s\S]*?)\n```\s*", text, flags=re.I)
    if match:
        return match.group(1).strip()
    frontmatter = re.fullmatch(r"(---\n[\s\S]*?\n---\n*)([\s\S]*)", text)
    if frontmatter:
        body = str(frontmatter.group(2) or "").strip()
        body_match = re.fullmatch(r"```(?:markdown|md)?\s*\n([\s\S]*?)\n```\s*", body, flags=re.I)
        if body_match:
            return frontmatter.group(1).rstrip() + "\n\n" + body_match.group(1).strip()
    return text


def _write_analysis_file(paper: dict, content: str, usage: dict) -> Path:
    path = _paper_store_file(paper, "analysis")
    if not path:
        ANALYSIS_ROOT.mkdir(parents=True, exist_ok=True)
        path = ANALYSIS_ROOT / f"{_slugify(str(paper.get('id') or paper.get('title') or 'paper'))}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_strip_outer_markdown_fence(content).strip() + "\n", encoding="utf-8")
    usage_path = path.with_suffix(".usage.json")
    atomic_replace_json(usage_path, usage, indent=2, sort_keys=False)
    return path


def create_exp(payload: dict, actor: str) -> dict:
    return start_exp(payload, actor)


def start_exp(payload: dict, actor: str) -> dict:
    paper_id = str(payload.get("paper_id") or "").strip()
    if not paper_id:
        raise ValueError("paper_id required")
    with LOCK:
        paper = _paper_by_id(paper_id)
        if not paper:
            raise KeyError("paper not found")
        paper = dict(paper)
        with paper_state_store.locked("exp_jobs"):
            latest = _latest_exp_job(paper_id=paper_id)
            if latest and str(latest.get("status") or "") in {"queued", "running"}:
                if _is_stale_exp_job(latest):
                    paper_state_store.merge("exp_jobs", latest["job_id"], {
                        "status": "error",
                        "step": "stale",
                        "progress": 100,
                        "updated_at": _now(),
                        "error": (
                            "Exp task was interrupted or stopped updating. "
                            "Please generate Exp again."
                        ),
                    })
                    latest = None
                else:
                    return latest
            job_id = f"exp-{secrets.token_hex(6)}"
            job = {
                "job_id": job_id,
                "paper_id": paper_id,
                "paper_title": str(paper.get("title") or ""),
                "status": "queued",
                "step": "queued",
                "progress": 0,
                "created_by": actor,
                "created_at": _now(),
                "updated_at": _now(),
                "exp_path": "",
                "error": "",
                "usage": {},
            }
            paper_state_store.append("exp_jobs", job)
    return _start_exp_task(job)


def get_exp_status(*, actor: str, job_id: str = "", paper_id: str = "") -> dict:
    with LOCK:
        job = _latest_exp_job(paper_id=paper_id, job_id=job_id)
        _authorize_paper_job_status(job, actor=actor, job_id=job_id, paper_id=paper_id)
        if job and _is_stale_exp_job(job):
            job = _mark_stale_exp_job(job)
    return job or {"status": "idle", "paper_id": paper_id, "job_id": job_id}


def _start_exp_task(job: dict) -> dict:
    from management_portal import business_tasks

    job_id = str(job.get("job_id") or "")
    def fail(exc: BaseException) -> None:
        _set_exp_job(
            job_id,
            {
                "status": "error",
                "step": "error",
                "progress": 100,
                "error": str(exc)[:2000],
            },
        )

    try:
        business_tasks.start(
            f"paper-exp:{job_id}",
            _run_exp_job,
            job_id,
            on_error=fail,
        )
    except Exception as exc:
        return _set_exp_job(
            job_id,
            {
                "status": "error",
                "step": "launch error",
                "progress": 100,
                "error": f"Failed to run Exp business task: {exc}",
            },
        )
    return _latest_exp_job(job_id=job_id) or job


def _run_exp_job(job_id: str) -> None:
    try:
        print(f"[paper-exp] {job_id} start", flush=True)
        job = _set_exp_job(job_id, {"status": "running", "step": "loading paper", "progress": 8})
        paper_id = str(job.get("paper_id") or "")
        paper = get_paper_detail(paper_id, sections=[])["paper"]
        print(f"[paper-exp] {job_id} calling llm", flush=True)
        _set_exp_job(job_id, {"step": "calling llm", "progress": 35})
        content, usage = _generate_exp_extraction(paper, actor=str(job.get("created_by") or ""), job_id=job_id)
        _require_artifact_sections(content, EXP_SECTIONS, "Exp")
        print(f"[paper-exp] {job_id} writing exp", flush=True)
        _set_exp_job(job_id, {"step": "writing exp", "progress": 82})
        path = _write_exp_markdown(paper, content, usage, actor=str(job.get("created_by") or ""))
        PAPER_STORE.update_paper(paper_id, {})
        _set_exp_job(
            job_id,
            {
                "status": "done",
                "step": "done",
                "progress": 100,
                "exp_path": str(path),
                "usage": usage,
                "error": "",
            },
        )
        print(f"[paper-exp] {job_id} done", flush=True)
    except Exception as exc:
        print(f"[paper-exp] {job_id} error: {exc}", flush=True)
        _set_exp_job(job_id, {"status": "error", "step": "error", "error": str(exc), "progress": 100})


def _write_exp_markdown(paper: dict, content: str, usage: dict, *, actor: str = "") -> Path:
    exp_path = _paper_store_file(paper, "exp")
    if not exp_path:
        root_text = str(paper.get("_store_root") or "").strip()
        if not root_text:
            raise ValueError("paper store root unavailable")
        root = Path(root_text)
        exp_path = root / "exp.md"
    exp_path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "---\n"
        "schema: tfi.paper_exp.v1\n"
        f"paper_id: {paper.get('id') or ''}\n"
        f"generated_by: {actor or ''}\n"
        f"generated_at: {_now()}\n"
        f"llm_api: {usage.get('api_id') or ''}\n"
        f"llm_model: {usage.get('model') or ''}\n"
        "---\n\n"
    )
    exp_path.write_text(header + str(content or "").strip() + "\n", encoding="utf-8")
    return exp_path


def _generate_exp_extraction(paper: dict, *, actor: str, job_id: str = "") -> tuple[str, dict]:
    analysis = _analysis_text(paper, limit=120_000)
    pdf_text = _pdf_text_for_paper(paper)
    if not (analysis.strip() or pdf_text.strip() or str(paper.get("abstract") or "").strip()):
        raise ValueError("paper has no analysis, PDF text, or abstract for Exp extraction")
    prompt = f"""请为下面论文生成 Experiment Extraction，标题用：
# Exp: {paper.get('title') or 'Paper'}

必须包含这些小节：

## 实验目标
说明实验要验证什么 claim。

## 数据集 / 环境 / 任务
列出 dataset、simulator、robot、benchmark、task suite、train/test split。没有就写未说明。

## Baselines 与对比对象
列出 baseline、ablation、human/heuristic/oracle、previous SOTA。

## Metrics
列出评价指标、成功率定义、速度/延迟/成本指标、统计方式。

## 训练与实现设置
列出模型规模、训练步数、batch、optimizer、硬件、代码依赖、关键超参。没有就写未说明。

## 关键实验结果
用短表格或 bullet 记录最重要结果，标出结果支持了哪些 claim。

## Ablation / Sensitivity
提取消融、敏感性实验和反例。

## 复现路径
给出最小复现实验步骤，包含输入数据、核心代码模块、检查点、运行命令可能需要哪些信息。

## 复现风险
列出最可能卡住的地方，以及需要向作者或原文确认的信息。

Paper metadata:
- Title: {paper.get('title') or ''}
- Authors: {paper.get('authors') or ''}
- Venue: {paper.get('venue') or ''} {paper.get('year') or ''}
- Tags: {', '.join(paper.get('tags') or [])}
- Abstract: {paper.get('abstract') or ''}

Analysis markdown:
{analysis}

Full extracted PDF text:
{pdf_text}
"""
    content, usage = _resident_reply(prompt, actor=actor, resident_scope="paper:exp", metadata={"paper_id":paper["id"],"material_attempt":"paper-exp:"+job_id} if job_id else None)
    return str(content or "").strip(), usage
