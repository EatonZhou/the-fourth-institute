"""Saved Semantic Searches: their records, their page operations and their runs.

A Saved Search belongs to one surface (``semantic_search_surfaces.SURFACES``).
Each surface declares where its searches are kept, which field identifies a
result and how a refresh treats the previous ranking; everything else is the
same for every surface.  ``shared_search_adapter`` runs a search against the
surface's authorized candidate catalog, and pages reach searches through
``list_searches``, ``start_search``, ``refresh_search``, ``delete_search``,
``get_search`` and ``search_view``.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from management_portal import search_identity_backend, semantic_search_store
from management_portal.notifications import publish_local_projection_change
from management_portal.semantic_search_runs import SearchRunSuperseded, require_current_run, run_generation
from management_portal.semantic_search_store import SearchStore
from management_portal.semantic_search_surfaces import RESOURCE_SCOPES, canonical_surface

ACTIVE = ("queued", "running")


@dataclass(frozen=True)
class SearchSurface:
    name: str
    store: SearchStore
    # The field that names a result (and a candidate) of this surface.
    result_key: str
    # Results a public search record carries.
    shown_results: int
    # A refresh keeps the previous results and ranks only the other
    # candidates; the new ranking is merged in (Paper, decision_e37f240e186d).
    keeps_results: bool = False
    # "required": every search belongs to one project the actor can open
    # (WALL); "optional": a search may name a project that narrows its
    # candidates (Paper); "": searches have no project.
    project: str = ""


SURFACES = {
    "paper": SearchSurface("paper", semantic_search_store.PAPER_SEARCHES, "paper_id", 80,
                           keeps_results=True, project="optional"),
    "knowledge": SearchSurface("knowledge", semantic_search_store.KNOWLEDGE_SEARCHES, "kb_id", 120),
    **{scope: SearchSurface(scope, semantic_search_store.KNOWLEDGE_SEARCHES, "resource_id", 120)
       for scope in RESOURCE_SCOPES if scope != "knowledge"},
    "wall": SearchSurface("wall", semantic_search_store.BOARD_SEARCHES, "item_id", 200, project="required"),
}


def surface(value: object) -> SearchSurface:
    return SURFACES[canonical_surface(value)]


def resource_scope(value: object) -> str:
    """A resource catalog scope; an empty scope is Knowledge."""
    scope = str(value or "knowledge").strip().lower()
    if scope not in RESOURCE_SCOPES:
        raise ValueError(f"unsupported semantic search resource scope: {scope}")
    return scope


def _now() -> int:
    return int(time.time())


def _project(declared: SearchSurface, value: object, actor: str) -> str:
    key = str(value or "").strip()
    if declared.project == "required":
        from management_portal import wall_backend

        if not key:
            raise ValueError("WALL project required")
        wall_backend.project(key, actor)
        return key
    return key if declared.project == "optional" else ""


def search_by_id(surface_name: str, search_id: str) -> dict | None:
    target = str(search_id or "").strip()
    if not target:
        return None
    declared = surface(surface_name)
    job = declared.store.get(target)
    return job if job and job['surface'] == declared.name else None


def set_search(surface_name: str, search_id: str, patch: dict, *,
               expected_generation: int | None = None, expected_status: tuple[str, ...] | None = None) -> dict:
    """Change one search; with ``expected_generation`` only while that run is still current."""
    declared = surface(surface_name)
    target = str(search_id or "").strip()
    if not target:
        raise ValueError("semantic search id required")
    with declared.store.locked():
        job = search_by_id(declared.name, target)
        if job is not None:
            if expected_generation is not None and (
                    job.get("deleted") or run_generation(job) != expected_generation
                    or (expected_status is not None and job.get("status") not in expected_status)):
                raise SearchRunSuperseded("semantic search run is no longer current")
            if job.get("deleted"):
                return dict(job)
            found = {**job, **patch, "updated_at": _now()}
        else:
            if expected_generation is not None:
                raise SearchRunSuperseded("semantic search run is no longer current")
            found = {"search_id": target, "surface": declared.name, **patch, "updated_at": _now()}
        if found['search_id'] != target or found['surface'] != declared.name:
            raise ValueError('Search identity cannot be changed by a status update')
        declared.store.save(found)
    publish_local_projection_change(
        "semantic-search", target,
        fields=[f"scope:{found.get('surface') or declared.name}"],
    )
    return found


def public_search(job: dict) -> dict:
    declared = surface(job.get("surface"))
    results = job.get("results") if isinstance(job.get("results"), list) else []
    return {
        "search_id": str(job.get("search_id") or ""),
        "search_kind": str(job.get("search_kind") or "semantic"),
        "surface": declared.name,
        "resource_scope": declared.name,
        "project": str(job.get("project") or ""),
        "query": str(job.get("query") or ""),
        "status": str(job.get("status") or "queued"),
        "step": str(job.get("step") or ""),
        "progress": int(job.get("progress") or 0),
        "created_by": str(job.get("created_by") or ""),
        "created_at": int(job.get("created_at") or 0),
        "updated_at": int(job.get("updated_at") or 0),
        "result_count": len(results),
        "query_interpretation": str(job.get("query_interpretation") or ""),
        "notes": str(job.get("notes") or ""),
        "error": str(job.get("error") or ""),
        "results": results[:declared.shown_results],
    }


def _named_search(surface_name: str, payload: dict, actor: str) -> tuple[SearchSurface, dict]:
    """The live search the payload names, on this surface (and project)."""
    declared = surface(surface_name)
    project = _project(declared, payload.get("project"), actor)
    search_id = str(payload.get("search_id") or "").strip()
    if not search_id:
        raise ValueError("semantic search id required")
    job = search_by_id(declared.name, search_id)
    if (not job or job.get("deleted") or job.get("surface") != declared.name
            or (declared.project == "required" and str(job.get("project") or "") != project)):
        raise KeyError("semantic search not found")
    return declared, job


def list_searches(surface_name: str, actor: str = "", *, project: str = "") -> dict:
    declared = surface(surface_name)
    actor = str(actor or "").strip()
    project_id = _project(declared, project, actor)
    jobs = [job for job in declared.store.load()
            if not job.get("deleted") and job.get("surface") == declared.name
            and (declared.project != "required" or str(job.get("project") or "") == project_id)]
    jobs.sort(key=lambda item: int(item.get("updated_at") or item.get("created_at") or 0), reverse=True)
    if actor:
        from management_portal import shared_search_adapter

        jobs = [shared_search_adapter.resume_pending_observation(job, start_task) for job in jobs[:80]]
    return {"searches": [public_search(job) for job in jobs[:80]]}


def get_search(surface_name: str, search_id: str, actor: str = "") -> dict:
    job = search_by_id(surface_name, search_id)
    if not job or job.get("deleted") or job.get("surface") != surface(surface_name).name:
        raise KeyError("semantic search not found")
    if actor:
        from management_portal import shared_search_adapter

        job = shared_search_adapter.resume_pending_observation(job, start_task)
    return public_search(job)


def start_search(surface_name: str, payload: dict, actor: str = "") -> dict:
    from management_portal import shared_search_adapter

    declared = surface(surface_name)
    actor = str(actor or "").strip()
    project = _project(declared, payload.get("project"), actor)
    query = str(payload.get("query") or "").strip()
    if not query:
        raise ValueError("query required")
    job = {
        "search_id": search_identity_backend.generate_search_id(),
        "search_kind": "semantic",
        "run_generation": 1,
        "execution_contract": shared_search_adapter.CONTRACT,
        "surface": declared.name,
        "query": query[:800],
        "status": "queued",
        "step": "queued",
        "progress": 0,
        "created_by": actor,
        "created_at": _now(),
        "updated_at": _now(),
        "result_count": 0,
        "results": [],
        "query_interpretation": "",
        "notes": "",
        "error": "",
    }
    if declared.project:
        job["project"] = project
    with declared.store.locked():
        declared.store.save(job)
    return public_search(start_task(job))


def refresh_search(surface_name: str, payload: dict, actor: str = "") -> dict:
    """Rank again under the same Search ID; an active run is only reported."""
    from management_portal import shared_search_adapter

    declared, job = _named_search(surface_name, payload, actor)
    if str(job.get("status") or "") in ACTIVE:
        return public_search(job)
    search_id = str(job["search_id"])
    generation = run_generation(job)
    patch = {
        "run_generation": generation + 1,
        "execution_contract": shared_search_adapter.CONTRACT,
        "message_id": "",
        "status": "queued",
        "step": "queued",
        "progress": 0,
        "error": "",
        "query_interpretation": "",
        "notes": "",
        "refreshed_at": _now(),
    }
    if declared.keeps_results:
        previous = job.get("results") if isinstance(job.get("results"), list) else []
        patch["refresh_skip_paper_ids"] = [
            str(item.get(declared.result_key) or "").strip() for item in previous
            if isinstance(item, dict) and str(item.get(declared.result_key) or "").strip()
        ]
    else:
        patch.update(results=[], result_count=0)
    try:
        queued = set_search(declared.name, search_id, patch, expected_generation=generation,
                            expected_status=(str(job.get("status") or ""),))
    except SearchRunSuperseded:
        current = search_by_id(declared.name, search_id)
        if not current or current.get("deleted"):
            raise KeyError("semantic search not found")
        return public_search(current)
    return public_search(shared_search_adapter.start_refreshed_run(job, queued, start_task))


def delete_search(surface_name: str, payload: dict, actor: str = "") -> dict:
    from management_portal import shared_search_adapter

    declared, job = _named_search(surface_name, payload, actor)
    search_id = str(job["search_id"])
    set_search(declared.name, search_id, {"deleted": True, "status": "deleted", "step": "deleted", "progress": 100})
    shared_search_adapter.cancel_run(job, deleted=True)
    return {"ok": True, "search_id": search_id}


def search_view(search_id: str, *, actor: str, surfaces: tuple[str, ...]) -> dict:
    """A search of one of ``surfaces`` as a filter of a list: rank, score and reason per result."""
    from management_portal import shared_search_adapter

    stores: dict[str, SearchStore] = {}
    for name in surfaces:
        selected = surface(name).store
        stores.setdefault(selected.surface, selected)
    job = next((found for found in (store.get(search_id) for store in stores.values()) if found), None)
    if not str(actor or "").strip() or not job or job.get("deleted") or job.get("surface") not in surfaces:
        raise KeyError("semantic search not found")
    declared = surface(job["surface"])
    job = shared_search_adapter.resume_pending_observation(job, start_task)
    result_map = {}
    for index, item in enumerate(job.get("results") if isinstance(job.get("results"), list) else []):
        result_id = str(item.get(declared.result_key) or "").strip() if isinstance(item, dict) else ""
        if not result_id or result_id in result_map:
            continue
        result_map[result_id] = {"rank": index + 1, "score": _score(item.get("score")),
                                 "reason": str(item.get("reason") or "").strip()}
    summary = {
        "search_id": str(job.get("search_id") or ""),
        "resource_scope": declared.name,
        "query": str(job.get("query") or ""),
        "status": str(job.get("status") or ""),
        "result_count": int(job.get("result_count") or 0),
        "query_interpretation": str(job.get("query_interpretation") or ""),
        "notes": str(job.get("notes") or ""),
        "error": str(job.get("error") or ""),
        "updated_at": int(job.get("updated_at") or 0),
    }
    return {"summary": summary, "result_map": result_map}


def _score(value: object) -> int:
    try:
        return max(0, min(100, int(float(value or 0))))
    except (TypeError, ValueError):
        return 0


def coerce_results(surface_name: str, parsed: dict, allowed_ids: set[str]) -> list[dict]:
    """The model's results that name an allowed candidate, once each, with a bounded score."""
    key = surface(surface_name).result_key
    results, seen = [], set()
    for item in parsed.get("results") if isinstance(parsed.get("results"), list) else []:
        result_id = str(item.get(key) or "").strip() if isinstance(item, dict) else ""
        if not result_id or result_id not in allowed_ids or result_id in seen:
            continue
        seen.add(result_id)
        results.append({key: result_id, "score": _score(item.get("score")),
                        "reason": str(item.get("reason") or "").strip()[:500]})
    return results


def merge_results(surface_name: str, previous: list[dict], new: list[dict]) -> list[dict]:
    """Previous results first, then new ones for results not ranked before."""
    key = surface(surface_name).result_key
    merged, seen = [], set()
    for item in [*previous, *new]:
        result_id = str(item.get(key) or "").strip() if isinstance(item, dict) else ""
        if result_id and result_id not in seen:
            seen.add(result_id)
            merged.append(item)
    return merged


def run_job(surface_name: str, search_id: str, expected_generation: int | None = None) -> None:
    """The business task of one run: hand the current run to the shared search service."""
    from management_portal import shared_search_adapter

    job = search_by_id(surface_name, search_id)
    if not job:
        raise KeyError("semantic search not found")
    generation = run_generation(job) if expected_generation is None else expected_generation
    try:
        require_current_run(job, generation)
    except SearchRunSuperseded:
        return
    if job.get("execution_contract") != shared_search_adapter.CONTRACT:
        raise ValueError("unsupported semantic search execution contract")
    shared_search_adapter.run_search_job(search_id, str(job["surface"]), generation)


def start_task(job: dict) -> dict:
    """Start the business task of the search's current run (one per search and generation)."""
    from management_portal import business_tasks

    surface_name = str(job.get("surface") or "")
    search_id = str(job.get("search_id") or "")
    generation = run_generation(job)

    def update(patch: dict) -> dict:
        try:
            return set_search(surface_name, search_id, patch, expected_generation=generation, expected_status=ACTIVE)
        except SearchRunSuperseded:
            return search_by_id(surface_name, search_id) or {}

    try:
        require_current_run(search_by_id(surface_name, search_id), generation)
    except SearchRunSuperseded:
        return search_by_id(surface_name, search_id) or {}
    try:
        business_tasks.start(
            f"semantic-search:{search_id}:{generation}", run_job, surface_name, search_id, generation,
            on_error=lambda exc: update({"status": "error", "step": "error", "progress": 100,
                                         "error": str(exc)[:2000]}),
        )
    except Exception as exc:
        return update({"status": "error", "step": "launch error", "progress": 100,
                       "error": f"Failed to run semantic search business task: {exc}"})
    return search_by_id(surface_name, search_id) or job
