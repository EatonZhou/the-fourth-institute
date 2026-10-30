"""AI reviews of a paper: review jobs, the three reviewer lenses and the stored reviews."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import secrets
import time

from management_common.principal import is_user_or_resident
from management_portal import paper_state_store
from management_portal.paper_records import _now
from management_portal.paper_resident import _extract_json_object, _resident_reply
from management_portal.paper_store import (
    LOCK, _all_papers, _analysis_text, _delete_bundle_record, _notify_paper_projection, _paper_by_id,
    _paper_store_file, _pdf_text_for_paper,
)
from management_portal.resources import get_resource_service


REVIEW_LENSES = [
    {
        "id": "A",
        "title": "Reviewer A",
        "subtitle": "Novelty & Contribution",
        "checks": ["related work", "real delta", "significance"],
    },
    {
        "id": "B",
        "title": "Reviewer B",
        "subtitle": "Method & Soundness",
        "checks": ["mechanism", "assumptions", "hidden cost"],
    },
    {
        "id": "C",
        "title": "Reviewer C",
        "subtitle": "Evaluation & Fairness",
        "checks": ["baselines", "metrics", "comparison"],
    },
]


def _all_reviews() -> list[dict]:
    return paper_state_store.records("reviews")


def _all_review_jobs() -> list[dict]:
    return paper_state_store.records("review_jobs")


def _announce_review_changes() -> None:
    """Ask the review index for a rescan after the review list changed.

    The index joins the stored reviews with each package's
    reviews/review_*.md; the review table has no file a watcher could see.
    """
    try:
        get_resource_service().notify_source_changed(resource_type="review", kind="updated")
    except Exception:
        logging.getLogger(__name__).warning("Paper review index notification failed", exc_info=True)


def _set_review_job(job_id: str, patch: dict) -> dict:
    with LOCK:
        found = paper_state_store.merge("review_jobs", job_id, {**patch, "updated_at": _now()})
    _notify_paper_projection(
        found.get("paper_id") or job_id,
        actors=[found.get("created_by")],
        fields=["review"],
    )
    return found


def _review_job_by_id(job_id: str) -> dict | None:
    return paper_state_store.get("review_jobs", str(job_id or "").strip())


def start_review(payload: dict, actor: str) -> dict:
    from management_portal import business_tasks

    paper_id = str(payload.get("paper_id") or "").strip()
    if not paper_id:
        raise ValueError("paper_id required")
    clean_actor = str(actor or "").strip()
    for previous in paper_state_store.records("review_jobs", paper_id=paper_id):
        if previous.get("created_by") == clean_actor:
            _settle_interrupted_review(previous)
    with LOCK:
        papers = _all_papers()
        paper = next((p for p in papers if p["id"] == paper_id), None)
        if not paper:
            raise KeyError("paper not found")
        with paper_state_store.locked("review_jobs"):
            active = next(
                (
                    item
                    for item in reversed(paper_state_store.records("review_jobs", paper_id=paper_id))
                    if str(item.get("created_by") or "") == clean_actor
                    and str(item.get("status") or "")
                    in {"queued", "running"}
                ),
                None,
            )
            if active is not None:
                return active
            known_ids = [
                item.get("review_id")
                for item in _all_reviews()
            ]
            known_ids.extend(
                item.get("review_id")
                for item in _all_review_jobs()
            )
            review_id = get_resource_service().new_id(
                "review",
                known_ids,
            )
            job = {
                "job_id": f"paper-review-{secrets.token_hex(6)}",
                "paper_id": paper_id,
                "paper_title": str(paper.get("title") or ""),
                "review_id": review_id,
                "title": str(payload.get("title") or ""),
                "status": "queued",
                "step": "queued",
                "process_owner": business_tasks.process_identity(),
                "created_by": clean_actor,
                "created_at": _now(),
                "updated_at": _now(),
                "error": "",
            }
            paper_state_store.append("review_jobs", job)
    return _start_review_task(job)


def _start_review_task(job: dict) -> dict:
    from management_portal import business_tasks

    job_id = str(job.get("job_id") or "")
    def fail(exc: BaseException) -> None:
        _set_review_job(job_id, {
            "status": "error",
            "step": "error",
            "error": str(exc)[:2000],
        })

    try:
        business_tasks.start(
            f"paper-review:{job_id}",
            _run_review_job,
            job_id,
            on_error=fail,
        )
    except Exception as exc:
        return _set_review_job(job_id, {
            "status": "error",
            "step": "launch error",
            "error": f"Failed to run Paper review business task: {exc}",
        })
    return _review_job_by_id(job_id) or job


def _run_review_job(job_id: str) -> dict:
    job = _review_job_by_id(job_id)
    if not job:
        raise KeyError("Paper review job not found")
    _set_review_job(job_id, {
        "status": "running",
        "step": "generating review",
        "error": "",
    })
    try:
        paper_id = str(job.get("paper_id") or "")
        papers = [dict(item) for item in _all_papers()]
        paper = next(
            (item for item in papers if item["id"] == paper_id),
            None,
        )
        if not paper:
            raise KeyError("paper not found")
        review = _generate_ai_review(
            dict(paper),
            papers,
            actor=str(job.get("created_by") or ""),
            title=str(job.get("title") or ""),
            review_id=str(job.get("review_id") or ""),
        )
        _review_markdown_path(review)
        with LOCK:
            with paper_state_store.locked("reviews"):
                if paper_state_store.get("reviews", str(review.get("review_id") or "")) is None:
                    paper_state_store.append("reviews", review)
            _write_review_markdown(review)
        _announce_review_changes()
        incomplete = review.get("status") == "ai_incomplete"
        _set_review_job(job_id, {
            "status": "error" if incomplete else "done",
            "step": "incomplete review" if incomplete else "done",
            "error": str(review.get("error") or "") if incomplete else "",
        })
        return review
    except Exception as exc:
        _set_review_job(job_id, {
            "status": "error",
            "step": "error",
            "error": str(exc)[:2000],
        })
        raise


def _settle_interrupted_review(job: dict) -> dict:
    from management_portal import business_tasks

    if job.get("status") not in {"queued", "running"} or business_tasks.process_is_alive(job.get("process_owner") or {}):
        return job
    # The owner may have saved the review just before it died. Recheck under
    # the same locks as completion and recover that result without another AI call.
    with LOCK, paper_state_store.locked("review_jobs", "reviews"):
        current = paper_state_store.get("review_jobs", job.get("job_id"))
        if current is None:
            return job
        if current.get("status") not in {"queued", "running"} or current.get("process_owner") != job.get("process_owner"):
            return current
        review = paper_state_store.get("reviews", current.get("review_id"))
        complete = review is not None and review.get("status") != "ai_incomplete"
        current = paper_state_store.merge("review_jobs", current["job_id"], {
            "status": "done" if complete else "error",
            "step": "done" if complete else "interrupted",
            "error": "" if complete else str((review or {}).get("error") or "Paper review task was interrupted by a process restart. Please generate the review again."),
            "updated_at": _now(),
        })
    _notify_paper_projection(current.get("paper_id"), actors=[current.get("created_by")], fields=["review"])
    return current


def review_job_paper_id(job_id: str) -> str:
    """The paper a review job belongs to, so a read by job_id is checked against that paper."""
    job = _review_job_by_id(job_id)
    if not job:
        raise KeyError("Paper review job not found")
    return str(job.get("paper_id") or "")


def get_review_status(*, actor: str, job_id: str = "", paper_id: str = "") -> dict:
    if job_id:
        job = _review_job_by_id(job_id)
        if not job:
            raise KeyError("Paper review job not found")
    elif paper_id:
        job = next((item for item in reversed(paper_state_store.records("review_jobs", paper_id=paper_id))
                    if str(item.get("created_by") or "") == str(actor or "")), None)
    else:
        raise ValueError("paper_id or job_id required")
    if job:
        if not is_user_or_resident(actor):
            raise PermissionError("Paper review job needs a User or Resident")
        if paper_id and str(job.get("paper_id") or "") != paper_id:
            raise ValueError("Paper review job belongs to another paper")
        job = _settle_interrupted_review(job)
    return {"job": job}


def wait_review(
    job_id: str,
    *,
    actor: str,
    timeout_seconds: float = 5400,
) -> dict:
    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    while time.monotonic() < deadline:
        job = _review_job_by_id(job_id)
        if not job:
            raise KeyError("Paper review job not found")
        if not is_user_or_resident(actor):
            raise PermissionError("Paper review job needs a User or Resident")
        job = _settle_interrupted_review(job)
        status = str(job.get("status") or "")
        if status == "done":
            result = job.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("Paper review job result is invalid")
            return dict(result)
        if status == "error":
            raise RuntimeError(
                str(job.get("error") or "Paper review failed")
            )
        time.sleep(0.2)
    raise TimeoutError(
        f"Paper review job did not finish: {job_id}"
    )


def create_review(payload: dict, actor: str) -> dict:
    job = start_review(payload, actor)
    if str(job.get("status") or "") == "done":
        result = job.get("result")
        return dict(result) if isinstance(result, dict) else {}
    return wait_review(
        str(job.get("job_id") or ""),
        actor=actor,
    )


def _review_markdown_path(review: dict) -> Path:
    paper = _paper_by_id(str(review.get("paper_id") or ""))
    reviews_dir = _paper_store_file(paper or {}, "reviews_dir") if paper else None
    if not reviews_dir:
        raise ValueError("review paper has no reviews directory")
    review_id = str(review.get("review_id") or "").strip()
    get_resource_service().validate_id(review_id, "review")
    return reviews_dir / f"{review_id}.md"


def _write_review_markdown(review: dict) -> None:
    path = _review_markdown_path(review)
    path.parent.mkdir(parents=True, exist_ok=True)
    body_parts = [
        "---",
        f"review_id: {review.get('review_id') or ''}",
        f"paper_id: {review.get('paper_id') or ''}",
        f"created_by: {review.get('created_by') or ''}",
        f"created_at: {review.get('created_at') or ''}",
        f"status: {review.get('status') or ''}",
        "---",
        "",
        f"# {review.get('title') or 'Review'}",
        "",
        str(review.get("summary") or ""),
        "",
    ]
    for reviewer in review.get("reviewers") or []:
        if isinstance(reviewer, dict):
            body_parts.extend([f"## {reviewer.get('title') or reviewer.get('id') or 'Reviewer'}", "", str(reviewer.get("body") or ""), ""])
    if review.get("author_action_memo"):
        body_parts.extend(["## Author Action Memo", "", str(review.get("author_action_memo") or ""), ""])
    if review.get("raw_response"):
        body_parts.extend(["## 未完成评审的原始输出", "", str(review["raw_response"]), ""])
    path.write_text("\n".join(body_parts), encoding="utf-8")


def _related_papers(paper: dict, papers: list[dict]) -> list[dict]:
    tags = set()
    for key in ("tags", "workload", "platform", "architecture", "optimization"):
        tags.update(str(x).casefold() for x in paper.get(key) or [])
    scored = []
    for other in papers:
        if other["id"] == paper["id"]:
            continue
        other_tags = set()
        for key in ("tags", "workload", "platform", "architecture", "optimization"):
            other_tags.update(str(x).casefold() for x in other.get(key) or [])
        score = len(tags & other_tags)
        if score:
            scored.append((score, other))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [{"id": p["id"], "title": p["title"], "venue": p.get("venue", ""), "year": p.get("year", "")} for _, p in scored[:5]]


def _generate_ai_review(
    paper: dict,
    papers: list[dict],
    *,
    actor: str,
    review_id: str,
    title: str = "",
) -> dict:
    analysis = _analysis_text(paper)
    pdf_text = _pdf_text_for_paper(paper)
    if not (analysis.strip() or pdf_text.strip() or str(paper.get("abstract") or "").strip()):
        raise ValueError("paper has no analysis, PDF text, or abstract for AI review")
    related = _related_papers(paper, papers)
    prompt = f"""请对下面论文生成 AI review。输出 JSON，字段固定为：
{{
  "title": "简短 review 标题",
  "overall_recommendation": "Strong Accept | Accept | Weak Accept | Borderline | Weak Reject | Reject",
  "confidence": "High | Medium | Low",
  "summary": "3-5 句中文总结",
  "reviewers": [
    {{"id": "A", "body": "Markdown，Reviewer A: Novelty & Contribution"}},
    {{"id": "B", "body": "Markdown，Reviewer B: Method & Soundness"}},
    {{"id": "C", "body": "Markdown，Reviewer C: Evaluation & Fairness"}}
  ],
  "author_action_memo": "Markdown，给作者/研究者的下一步行动建议"
}}

每个 reviewer 的 Markdown 必须包含：
### Summary
### Strengths
### Major Concerns
### Questions
### Evidence To Check

每个 reviewer 控制在 600-900 个中文字。author_action_memo 控制在 400-700 个中文字。

Paper metadata:
- Title: {paper.get('title') or ''}
- Authors: {paper.get('authors') or ''}
- Venue: {paper.get('venue') or ''} {paper.get('year') or ''}
- Tags: {', '.join(paper.get('tags') or [])}
- Abstract: {paper.get('abstract') or ''}

Related papers in local library:
{json.dumps(related, ensure_ascii=False, indent=2)}

Analysis markdown:
{analysis}

Full extracted PDF text:
{pdf_text}
"""
    content, usage = _resident_reply(prompt, actor=actor, resident_scope="paper:review")
    try:
        parsed = _extract_json_object(content)
        return _coerce_ai_review(
            parsed,
            raw_content=content,
            paper=paper,
            related=related,
            usage=usage,
            actor=actor,
            review_id=review_id,
            title=title,
        )
    except ValueError as exc:
        return _fallback_ai_review_from_text(
            content,
            paper=paper,
            related=related,
            usage=usage,
            actor=actor,
            review_id=review_id,
            title=title,
            error=str(exc),
        )


def _coerce_ai_review(
    parsed: dict,
    *,
    raw_content: str,
    paper: dict,
    related: list[dict],
    usage: dict,
    actor: str,
    review_id: str,
    title: str,
) -> dict:
    for field in ("title", "overall_recommendation", "confidence", "summary", "author_action_memo"):
        if not isinstance(parsed.get(field), str) or not parsed[field].strip():
            raise ValueError(f"AI review response missing required text: {field}")
    by_id = {}
    reviewers_raw = parsed.get("reviewers")
    if not isinstance(reviewers_raw, list):
        raise ValueError("AI review response missing reviewers")
    expected_ids = {str(lens["id"]) for lens in REVIEW_LENSES}
    for item in reviewers_raw:
        if not isinstance(item, dict):
            raise ValueError("AI review reviewer must be an object")
        key = str(item.get("id") or "").strip()
        body = item.get("body")
        if key not in expected_ids or key in by_id or not isinstance(body, str) or not body.strip():
            raise ValueError("AI review reviewers must contain unique A/B/C with nonempty bodies")
        by_id[key] = body.strip()
    if set(by_id) != expected_ids:
        raise ValueError("AI review response missing one or more reviewers")
    reviewers = [{**lens, "body": by_id[str(lens["id"])]} for lens in REVIEW_LENSES]
    return {
        "review_id": review_id,
        "paper_id": paper["id"],
        "title": str(title or parsed.get("title") or f"{paper['title']} AI review").strip()[:180],
        "created_by": actor,
        "created_at": _now(),
        "status": "ai_generated",
        "overall_recommendation": str(parsed.get("overall_recommendation") or "").strip(),
        "confidence": str(parsed.get("confidence") or "").strip(),
        "summary": str(parsed.get("summary") or "").strip(),
        "library_check": {
            "closest_papers": related,
            "analysis_excerpt": _analysis_text(paper, limit=1600)[:900],
        },
        "reviewers": reviewers,
        "author_action_memo": str(parsed.get("author_action_memo") or "").strip(),
        "usage": usage,
        "source": "kimi_ai_review",
    }


def _fallback_ai_review_from_text(
    content: str,
    *,
    paper: dict,
    related: list[dict],
    usage: dict,
    actor: str,
    review_id: str,
    title: str,
    error: str = "AI review response was not a complete JSON review",
) -> dict:
    body = str(content or "")
    return {
        "review_id": review_id,
        "paper_id": paper["id"],
        "title": str(title or f"{paper['title']} AI review").strip()[:180],
        "created_by": actor,
        "created_at": _now(),
        "status": "ai_incomplete",
        "overall_recommendation": "",
        "confidence": "",
        "summary": "评审未完成：模型未返回完整的结构化评审。原始输出已保留，不作为三位 Reviewer 的有效评审。",
        "error": error,
        "raw_response": body,
        "library_check": {
            "closest_papers": related,
            "analysis_excerpt": _analysis_text(paper, limit=1600)[:900],
        },
        "reviewers": [],
        "author_action_memo": "",
        "usage": usage,
        "source": "kimi_ai_review_unstructured",
    }


def list_reviews(paper_id: str = "") -> dict:
    reviews = paper_state_store.records("reviews", paper_id=paper_id) if paper_id else _all_reviews()
    return {"reviews": sorted(reviews, key=lambda item: int(item.get("created_at") or 0), reverse=True)}


def delete_review(payload: dict, actor: str) -> dict:
    review_id = str(payload.get("review_id") or "").strip()
    if not review_id:
        raise ValueError("review_id required")
    with LOCK:
        target = paper_state_store.get("reviews", review_id)
        if not target:
            raise KeyError("review not found")
        from .workbench_projections.resource_lifecycle import release_resource
        release_resource(review_id, actor=actor)
        _delete_bundle_record("reviews", review_id, _review_markdown_path(target))
    _announce_review_changes()
    return {
        "deleted": True,
        "review_id": review_id,
        "paper_id": str(target.get("paper_id") or ""),
        "deleted_by": actor,
        "deleted_at": _now(),
    }
