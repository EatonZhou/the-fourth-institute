"""Paper-owned collection orchestration; execution belongs to the bound Resident.

The caller's trusted request context stays in-process. Never use an old job's
created_by field to manufacture authentication or launch the standalone crawler.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json

from management_common.format_tags import schema_matches
from management_common.operation_identity import current_operation_identity, operation_identity_scope
from management_common.principal import OperationIdentity, authenticated_principal

from . import paper_projection


CONTRACT = "tfi.paper.collector.v2"
RESULT_SCHEMA = "tfi.paper.collector.result.v1"
MAX_DIRECTION_CHARS = 20000


@contextmanager
def authenticated_request(auth: dict):
    """Only the HTTP boundary may supply this server-authenticated auth record."""
    principal = authenticated_principal(auth)
    current = current_operation_identity()
    if current is not None and current.principal != principal:
        raise PermissionError("Collector request identity disagrees with authenticated caller")
    with operation_identity_scope(current or OperationIdentity(principal, principal)):
        yield


def require_actor(actor: str) -> str:
    identity = current_operation_identity()
    if (identity is None or identity.principal.kind not in {"user", "resident"}
            or identity.principal.id != str(actor or "").strip()):
        raise PermissionError("Collector requires the original authenticated User or Resident request context")
    return identity.principal.id


def _prompt(job: dict) -> str:
    return """执行本次 Paper Collect 请求。你是 Paper canonical Lineage 已绑定的 paper:collector。
用户 direction 是完整的本次业务要求，不是生成宽泛搜索关键词的种子。
先通过已授权 Paper 资源查询核对现有库；已存在的论文直接作为 existing 返回，严禁重复登记。
严格遵守用户对来源、联网、只查现有库、禁止登记和数量的约束。若仅要求现有论文，
不要启动 Reference/arXiv 或其他外部收集。不得自报身份、切换 provider 或绕过鉴权；授权/配置/正式保存失败立即如实失败。
确实需要新论文且用户允许时，仅用已授权业务接口正式登记，取得并核实保存结果后返回 registered。
生成文本不等于正式登记；不得仅给出登记建议就声称成功。最多返回 max_papers 篇论文。
最终仅返回 JSON（无 markdown），协议如下：
{"schema":"tfi.paper.collector.result.v1","job_id":"本次 job_id",
 "status":"completed 或 error","papers":[{"paper_id":"实际 paper Resource ID",
 "action":"existing 或 registered"}],"summary":"如实说明结果","error":"失败原因，成功时空串"}
找不到符合要求的论文可以 completed + 空 papers，并如实说明；无法执行不是零结果成功。
本次请求 JSON：
""" + json.dumps({"job_id": job["job_id"], "direction": job["direction"],
                    "max_papers": job["max_papers"]}, ensure_ascii=False)


def _validate_result(raw: str, job: dict, before: set[str], papers: list[dict]) -> tuple[dict, list[dict], int]:
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError("Collector did not return a structured business result") from exc
    if (not isinstance(parsed, dict) or not schema_matches(parsed.get("schema"), "tfi.paper.collector.result.v1")
            or parsed.get("job_id") != job["job_id"]):
        raise ValueError("Collector result schema/job identity mismatch")
    if parsed.get("status") != "completed" or parsed.get("error"):
        raise RuntimeError(str(parsed.get("error") or "Collector did not complete the requested work"))
    items = parsed.get("papers")
    if not isinstance(items, list) or len(items) > int(job["max_papers"]):
        raise ValueError("Collector result exceeds the requested limit or has no papers list")
    authorized = {str(paper["id"]): paper for paper in papers}
    results, seen, registered = [], set(), 0
    for item in items:
        if not isinstance(item, dict) or item.get("action") not in {"existing", "registered"}:
            raise ValueError("Collector result requires a verified paper and action")
        paper_id = str(item.get("paper_id") or "")
        if paper_id in seen:
            raise ValueError("Collector returned duplicate paper IDs")
        if paper_id not in authorized:
            raise PermissionError("Collector result is not a saved, currently authorized Paper resource")
        paper = authorized[paper_id]
        if item["action"] == "registered":
            if paper_id in before or int(paper.get("created_at") or 0) < int(job["created_at"]):
                raise ValueError("Collector claimed a pre-existing paper was newly registered")
            registered += 1
        seen.add(paper_id)
        results.append({"paper_id": paper_id, "title": str(paper.get("title") or paper_id),
                        "rank": len(results) + 1, "action": item["action"]})
    return parsed, results, registered


def run(job_id: str, *, backend) -> None:
    job = backend._paper_collection_job_by_id(job_id)
    if not job:
        raise KeyError("paper collection job not found")
    if not schema_matches(job.get("collector_contract"), "tfi.paper.collector.v2"):
        raise RuntimeError("Collector job does not satisfy the current execution contract")
    actor = require_actor(str(job.get("created_by") or ""))
    if job.get("status") in {"done", "error", "cancelled", "deleted"} or job.get("deleted"):
        return
    work_dir = backend.PAPER_COLLECTOR_WORK_ROOT / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    stdout_path, stderr_path = work_dir / "stdout.json", work_dir / "stderr.log"

    def cancelled() -> bool:
        latest = backend._paper_collection_job_by_id(job_id) or {}
        return not latest or latest.get("deleted") or latest.get("status") in {"cancelled", "deleted"}

    def remember(receipt: dict) -> None:
        if not cancelled():
            backend._set_paper_collection_job(job_id, {"invocation": deepcopy(receipt)})

    try:
        binding = job.get("invocation") or {}
        if binding and (not binding.get("input_message_id") or binding.get("input_identity_status") == "unconfirmed"):
            raise RuntimeError("Collector input delivery is unconfirmed; inspect the original workflow; no resubmission")
        if "existing_paper_ids" not in job:
            if binding:
                raise RuntimeError("Collector invocation has no original library snapshot; no resubmission")
            job["existing_paper_ids"] = [str(p["id"]) for p in backend.resource_index_papers(actor=actor)]
            backend._set_paper_collection_job(job_id, {"existing_paper_ids": job["existing_paper_ids"]})
        backend._set_paper_collection_job(job_id, {
            "status": "running", "step": "waiting for Paper Collector Resident", "progress": 30,
            "stdout_path": str(stdout_path), "stderr_path": str(stderr_path), "error": "",
        })
        if binding:
            result = paper_projection.resume(
                actor=actor, resident_id=binding["resident_id"], lineage_id=binding["lineage_id"],
                message_id=binding["input_message_id"], receipt=binding, on_submitted=remember,
                cancellation_requested=cancelled,
            )
        else:
            result = paper_projection.turn(
                actor=actor, prompt=_prompt(job), cwd=str(backend.WORKSPACE_ROOT),
                resident_scope="paper:collector",
                business_ref=f"paper-collector:{job_id}",
                message_id=str(job["requested_message_id"]),
                on_assigned=remember, on_submitted=remember, cancellation_requested=cancelled,
                metadata={"business_type": "paper_collector", "collector_job_id": job_id,
                          "conversation_index": False},
            )
        raw = str(result.get("message") or "")
        stdout_path.write_text(raw, encoding="utf-8")
        invocation = result.get("invocation") or {}
        if not invocation.get("input_message_id") or not invocation.get("reply_message_ids"):
            raise ValueError("Collector result lacks a confirmed Resident input and linked reply")
        remember(invocation)
        # Resident results contain all associated progress and final messages.
        # Keep that full transcript in stdout, but parse only the final verified
        # agent reply; a progress JSON object must never serve as a commit receipt.
        replies = result.get("messages") or []
        final_text = "\n".join(
            str(part.get("text") or "") for part in (replies[-1].get("content_parts") or [])
            if isinstance(part, dict) and part.get("type") == "text"
        ).strip() if replies else ""
        parsed, results, registered = _validate_result(
            final_text, job, set(job["existing_paper_ids"]), backend.resource_index_papers(actor=actor),
        )
        if cancelled():
            return
        backend._set_paper_collection_job(job_id, {
            "status": "done", "step": "done", "progress": 100, "registered_count": registered,
            "results": results, "summary": str(parsed.get("summary") or ""), "error": "",
            "result_path": str(stdout_path), "usage": deepcopy(result.get("usage") or {}),
        })
    except Exception as exc:
        stderr_path.write_text(str(exc), encoding="utf-8")
        if not cancelled():
            backend._set_paper_collection_job(job_id, {
                "status": "error", "step": "error", "progress": 100, "error": str(exc)[-2000:],
            })
        raise
