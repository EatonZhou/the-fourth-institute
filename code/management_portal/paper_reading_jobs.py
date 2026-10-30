"""Deep Read and Exp: background jobs that turn a paper's PDF text into a checked Markdown artifact."""

from __future__ import annotations

from pathlib import Path
import re
import secrets
import threading

from management_portal import paper_state_store
from management_portal.paper_detail import get_paper_detail
from management_portal.paper_records import _now, _slugify
from management_portal.paper_resident import _resident_reply
from management_portal.paper_store import (
    DATA_ROOT, LOCK, PAPER_STORE, _all_papers, _analysis_text, _extract_pdf_text, _local_pdf_for_paper,
    _notify_paper_projection, _paper_public, _paper_store_file, _pdf_text_for_paper, _update_paper_fields,
)
from management_portal.resources import get_resource_service
from management_portal.shared_state import atomic_replace_json


ANALYSIS_ROOT = DATA_ROOT / "analysis"


EXP_STALE_SECONDS = 45 * 60


_DEEP_READ_TASK_LOCK = threading.RLock()
_ACTIVE_DEEP_READ_TASKS: set[str] = set()


def _set_job_with_run_record(kind: str, job_id: str, patch: dict) -> dict:
    # The run record is written before the job's lock is released, so run
    # records of one job are written in the same order as the job itself.
    with LOCK, paper_state_store.locked(kind):
        found = paper_state_store.merge(kind, job_id, {**patch, "updated_at": _now()})
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
        found = paper_state_store.get(kind, job_id)
        matches = [found] if found else []
    else:
        matches = paper_state_store.records(kind, paper_id=paper_id)
    if not matches:
        return None
    return sorted(matches, key=lambda item: int(item.get("created_at") or item.get("updated_at") or 0), reverse=True)[0]


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
    with LOCK:
        papers = _all_papers()
        paper = next((item for item in papers if str(item.get("id") or "") == paper_id), None)
        if not paper:
            raise KeyError("paper not found")
        public = _paper_public(paper)
        if not public.get("has_pdf"):
            raise ValueError("paper has no local or remote PDF")
        with paper_state_store.locked("deep_read_jobs"):
            latest = _latest_deep_read_job(paper_id=paper_id)
            if latest and str(latest.get("status") or "") in {"queued", "running"}:
                return latest
            job_id = f"deep-read-{secrets.token_hex(6)}"
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
                "analysis_path": "",
                "error": "",
                "usage": {},
            }
            paper_state_store.append("deep_read_jobs", job)
    return _launch_deep_read_business_task(job)


def _launch_deep_read_business_task(job: dict) -> dict:
    job_id = str(job.get("job_id") or "")
    if not job_id:
        raise ValueError("Deep Read job id required")
    with _DEEP_READ_TASK_LOCK:
        if job_id in _ACTIVE_DEEP_READ_TASKS:
            return _latest_deep_read_job(job_id=job_id) or job
        _ACTIVE_DEEP_READ_TASKS.add(job_id)

    def run() -> None:
        try:
            _run_deep_read_job(job_id)
        finally:
            with _DEEP_READ_TASK_LOCK:
                _ACTIVE_DEEP_READ_TASKS.discard(job_id)

    threading.Thread(
        target=run,
        name=f"paper-deep-read-{job_id}",
        daemon=True,
    ).start()
    return _latest_deep_read_job(job_id=job_id) or job


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


def get_deep_read_status(*, actor: str, job_id: str = "", paper_id: str = "") -> dict:
    with LOCK:
        job = _latest_deep_read_job(paper_id=paper_id, job_id=job_id)
    _authorize_paper_job_status(job, actor=actor, job_id=job_id, paper_id=paper_id)
    if job and str(job.get("status") or "") in {"queued", "running"}:
        job = _launch_deep_read_business_task(job)
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
        paper_id = str(job.get("paper_id") or "")
        paper = get_paper_detail(paper_id)["paper"]
        actor = str(job.get("created_by") or "portal")
        _set_deep_read_job(
            job_id,
            {
                "status": "running",
                "step": "extracting pdf text",
                "progress": 18,
                "error": "",
            },
        )
        print(
            f"[paper-deep-read] {job_id} extracting pdf text",
            flush=True,
        )
        pdf_path = _local_pdf_for_paper(paper)
        text = _extract_pdf_text(pdf_path, paper_id=paper_id)
        if len(text.strip()) < 1200:
            raise RuntimeError("PDF text extraction returned too little text")
        from management_portal.paper_projection import input_receipt, resume, turn

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

        if resident_id and attempt_handle.get("input_identity_status") == "unconfirmed":
            raise RuntimeError("Deep Read input delivery is unconfirmed; inspect the original workflow; no input was resubmitted")
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
                on_assigned=remember_input,
                on_submitted=remember_input,
            )
        content = str(result.get("message") or "").strip()
        if not content:
            raise RuntimeError("Deep Read AI execution returned empty output")
        _require_artifact_sections(content, DEEP_READ_SECTIONS, "Deep Read")
        usage = (
            dict(result.get("usage"))
            if isinstance(result.get("usage"), dict)
            else {}
        )
        analysis = _deep_read_analysis_document(paper, content, usage)
        print(f"[paper-deep-read] {job_id} writing analysis", flush=True)
        _set_deep_read_job(job_id, {"step": "writing analysis", "progress": 82})
        analysis_path = _write_analysis_file(paper, analysis, usage)
        _update_paper_fields(
            paper_id,
            {
                "analysis_path": str(analysis_path),
                "status": "analysis_ready",
            },
        )
        _set_deep_read_job(
            job_id,
            {
                "status": "done",
                "step": "done",
                "progress": 100,
                "analysis_path": str(analysis_path),
                "usage": usage,
                "error": "",
            },
        )
        print(f"[paper-deep-read] {job_id} done", flush=True)
    except Exception as exc:
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
        f"llm_api: {usage.get('api_id') or ''}\n"
        f"llm_model: {usage.get('model') or ''}\n"
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
        papers = _all_papers()
        paper = next((p for p in papers if p["id"] == paper_id), None)
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
        paper = get_paper_detail(paper_id)["paper"]
        print(f"[paper-exp] {job_id} calling llm", flush=True)
        _set_exp_job(job_id, {"step": "calling llm", "progress": 35})
        content, usage = _generate_exp_extraction(paper, actor=str(job.get("created_by") or ""))
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


def _generate_exp_extraction(paper: dict, *, actor: str) -> tuple[str, dict]:
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
    content, usage = _resident_reply(prompt, actor=actor, resident_scope="paper:exp")
    return str(content or "").strip(), usage
