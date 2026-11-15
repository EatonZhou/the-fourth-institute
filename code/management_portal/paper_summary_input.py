"""Summary job input identity; caller holds the per-job execution lock.

New jobs opt in at creation. A legacy job is never upgraded to 'new' merely
because its receipt is missing. Recovery observes one original input only.
"""
from . import paper_projection as projection
from . import paper_store as store
from .paper_resident import normalize_task_result

SCHEMA = "tfi.paper-summary-input.v1"


def new_input() -> dict:
    return {"schema": SCHEMA, "phase": "new"}


def validate_job(job: dict, actor: str) -> dict:
    if not actor or job.get("actor") != actor:
        raise PermissionError("Summary recovery requires the original current caller")
    if job.get("kind") != "summary":
        raise ValueError("Summary job required")
    state = job.get("summary_input") or {}
    if state.get("schema") != SCHEMA or state.get("phase") not in {"new", "dispatching", "generated"}:
        raise projection.InputConfirmationPending("Legacy summary has no verified input locator; no input resubmitted")
    return dict(state)


def reply(*, paper_id: str, job_id: str, actor: str, prompt: str, metadata: dict) -> tuple[str, dict]:
    job = store._paper_run_record_by_job_id(paper_id, job_id)
    state = validate_job(job, actor)

    def save(**patch):
        nonlocal state
        updated = {**state, **patch}
        store._update_paper_run_record(paper_id, {"job_id": job_id, "summary_input": updated})
        state = updated  # A failed write must not advance the in-memory intent.

    def remember(receipt):
        current = projection.input_receipt(receipt)
        previous = state.get("receipt") or {}
        # Use the same identity merge as the shared Paper projection. In
        # particular an assignment cannot erase an accepted input identity.
        save(receipt=projection._merge_receipt(previous, current))

    if state.get("result"):
        result = state["result"]
    elif state["phase"] != "new":
        receipt = state.get("receipt") or {}
        if not receipt:
            raise projection.InputConfirmationPending("Summary submission locator unavailable; no input resubmitted")
        confirmed = projection.confirm_input(actor=actor, receipt=receipt)
        remember(confirmed)
        result = projection.resume(actor=actor, resident_id=confirmed["resident_id"],
            lineage_id=confirmed["lineage_id"], message_id=confirmed["input_message_id"],
            receipt=confirmed, on_submitted=remember)
    else:
        save(phase="dispatching", business_ref=job_id,
             material_version=str(metadata.get("material_version") or ""))
        result = projection.turn(actor=actor, prompt=prompt, resident_scope="paper:summary", cwd="",
            business_ref=job_id, metadata={**metadata, "material_attempt": job_id},
            on_assigned=remember, on_submitted=remember)
    # Cache output before artifact writes: a filesystem failure must not cause
    # another model call. Persist only this summary's content/usage/identity.
    remember(result["invocation"])
    result = {key: result.get(key) for key in ("message", "usage", "invocation")}
    save(phase="generated", result=result)
    content = str(result.get("message") or "").strip()
    if not content:
        raise ValueError("paper:summary returned an empty reply")
    usage = dict(result.get("usage") or {})
    usage["invocation"] = state["receipt"]
    usage["summary_source_version"] = state.get("material_version", "")
    return normalize_task_result(content, usage, markdown=False)
