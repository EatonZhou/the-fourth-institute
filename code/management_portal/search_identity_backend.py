from __future__ import annotations

from contextlib import ExitStack, contextmanager
from typing import Any, Iterable, Iterator
from urllib.parse import unquote
from management_portal.resources.portal import portal_url_for_search

from management_portal import semantic_search_store
from management_portal.semantic_search_surfaces import RESOURCE_SCOPES
from management_portal.resources import get_resource_service
from management_common.text_values import stripped_text


BOARD_SURFACES = frozenset({"wall"})


class _DomainJobs:
    """The Saved Searches of one store, under that store's lock."""

    def __init__(self, jobs: semantic_search_store.SearchStore) -> None:
        self._jobs = jobs
        self.label = f"semantic_search:{jobs.surface}"

    def locked(self):
        return self._jobs.locked()

    def load(self) -> list[dict[str, Any]]:
        return self._jobs.load(strict=True)

    def write(self, jobs: list[dict[str, Any]]) -> None:
        self._jobs.write(jobs)


def _search_stores(*, include_board: bool = True) -> dict[str, Any]:
    stores = {
        "paper": _DomainJobs(semantic_search_store.PAPER_SEARCHES),
        "knowledge": _DomainJobs(semantic_search_store.KNOWLEDGE_SEARCHES),
    }
    if include_board:
        stores["board"] = _DomainJobs(semantic_search_store.BOARD_SEARCHES)
    return stores


@contextmanager
def identity_lock() -> Iterator[None]:
    """Serialize Search ID allocation and identity rewrites across surfaces."""
    with semantic_search_store.identity_lock():
        yield


@contextmanager
def _job_store_locks(stores: dict[str, Any]) -> Iterator[None]:
    with ExitStack() as stack:
        for store in stores.values():
            stack.enter_context(store.locked())
        yield


def _job_surface(store_name: str, job: dict[str, Any]) -> str:
    """The surface of a stored search: the store's, or one of the surfaces the store holds."""
    surface = stripped_text(job.get("surface"))
    if store_name == "paper":
        return "paper"
    if store_name == "knowledge":
        return surface if surface in RESOURCE_SCOPES else "knowledge"
    return surface if surface in BOARD_SURFACES else ""


def _identity_plan(stores: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    invalid: list[dict[str, Any]] = []
    collisions: list[dict[str, Any]] = []
    owners: dict[str, tuple[str, int]] = {}
    planned = {
        store_name: [dict(item) for item in stores[store_name]]
        for store_name in stores
    }

    for store_name, jobs in planned.items():
        for index, job in enumerate(jobs):
            surface = _job_surface(store_name, job)
            if not surface:
                invalid.append(
                    {
                        "surface": store_name,
                        "index": index,
                        "search_id": stripped_text(job.get("search_id")),
                        "error": "board search surface must be wall",
                    }
                )
                continue
            search_id = stripped_text(job.get("search_id"))
            if not get_resource_service().is_valid_id(search_id, "search"):
                invalid.append(
                    {
                        "surface": surface,
                        "index": index,
                        "search_id": search_id,
                        "error": "search_id must match search_<12 lowercase hex>",
                    }
                )
                continue
            if "resource_id" in job:
                invalid.append(
                    {
                        "surface": surface,
                        "index": index,
                        "search_id": search_id,
                        "error": "resource_id is forbidden; search_id is the only ID",
                    }
                )
                continue
            owner = owners.get(search_id)
            if owner:
                collisions.append(
                    {
                        "search_id": search_id,
                        "surface": surface,
                        "conflicts_with": {
                            "surface": owner[0],
                            "index": owner[1],
                        },
                    }
                )
                continue
            owners[search_id] = (surface, index)

    changed_count = 0
    unchanged_count = 0
    surface_counts: dict[str, int] = {}
    deleted_count = 0
    for store_name, jobs in planned.items():
        for index, job in enumerate(jobs):
            surface = _job_surface(store_name, job)
            if not surface:
                continue
            surface_counts[surface] = surface_counts.get(surface, 0) + 1
            search_id = stripped_text(job.get("search_id"))
            if not get_resource_service().is_valid_id(search_id, "search") or "resource_id" in job:
                continue
            next_job = {
                **job,
                "search_kind": "semantic",
                "surface": surface,
            }
            if next_job != job:
                planned[store_name][index] = next_job
                changed_count += 1
            else:
                unchanged_count += 1
            if bool(job.get("deleted")):
                deleted_count += 1

    return {
        "planned": planned,
        "invalid": invalid,
        "collisions": collisions,
        "changed_count": changed_count,
        "unchanged_count": unchanged_count,
        "surface_counts": surface_counts,
        "deleted_count": deleted_count,
    }


def scan_search_ids(*, apply: bool = False) -> dict[str, Any]:
    sources = _search_stores()
    with identity_lock(), _job_store_locks(sources):
        stores = {store_name: source.load() for store_name, source in sources.items()}
        plan = _identity_plan(stores)
        if apply and (plan["invalid"] or plan["collisions"]):
            raise ValueError(
                "Search identity update blocked: "
                f"{len(plan['invalid'])} invalid record(s), "
                f"{len(plan['collisions'])} collision(s)"
            )
        if apply:
            for store_name, source in sources.items():
                if plan["planned"][store_name] != stores[store_name]:
                    source.write(plan["planned"][store_name])

    return {
        "schema": "tfi.search_identity_scan.v3",
        "apply": bool(apply),
        "paths": {surface: source.label for surface, source in sources.items()},
        "search_count": sum(plan["surface_counts"].values()),
        "surface_counts": plan["surface_counts"],
        "deleted_count": plan["deleted_count"],
        "changed_count": plan["changed_count"],
        "unchanged_count": plan["unchanged_count"],
        "invalid_count": len(plan["invalid"]),
        "collision_count": len(plan["collisions"]),
        "invalid": plan["invalid"],
        "collisions": plan["collisions"],
    }


def generate_search_id(
    *,
    additional_ids: Iterable[object] = (),
    include_board: bool = True,
) -> str:
    sources = _search_stores(include_board=include_board)
    with identity_lock():
        with _job_store_locks(sources):
            stores = {store_name: source.load() for store_name, source in sources.items()}
            plan = _identity_plan(stores)
            if plan["invalid"] or plan["collisions"]:
                raise ValueError("cannot allocate a Search ID while search identity data is invalid")
            used = {
                stripped_text(job.get("search_id"))
                for jobs in plan["planned"].values()
                for job in jobs
                if get_resource_service().is_valid_id(job.get("search_id"), "search")
            }
        used.update(stripped_text(value) for value in additional_ids if stripped_text(value))
        return get_resource_service().new_id("search", used)


def load_search_resources(*, include_deleted: bool = True) -> list[dict[str, Any]]:
    stores = {store_name: source.load() for store_name, source in _search_stores().items()}
    plan = _identity_plan(stores)
    if plan["invalid"] or plan["collisions"]:
        raise ValueError("Search identity data contains invalid or duplicate IDs")
    records: list[dict[str, Any]] = []
    for store_name, jobs in plan["planned"].items():
        for job in jobs:
            surface = _job_surface(store_name, job)
            deleted = bool(job.get("deleted"))
            if deleted and not include_deleted:
                continue
            search_id = stripped_text(job.get("search_id"))
            records.append(
                {
                    "id": search_id,
                    "type": "search",
                    "search_id": search_id,
                    "search_kind": "semantic",
                    "surface": surface,
                    "project": stripped_text(job.get("project")),
                    "resource_scope": (
                        surface if surface in RESOURCE_SCOPES else ""
                    ),
                    "query": stripped_text(job.get("query")),
                    "status": "deleted" if deleted else stripped_text(job.get("status")),
                    "created_by": stripped_text(job.get("created_by")),
                    "created_at": int(job.get("created_at") or 0),
                    "updated_at": int(job.get("updated_at") or 0),
                    "result_count": int(job.get("result_count") or 0),
                    "deleted": deleted,
                }
            )
    records.sort(
        key=lambda item: (
            int(item.get("updated_at") or item.get("created_at") or 0),
            stripped_text(item.get("id")),
        ),
        reverse=True,
    )
    return records


def scan_search_resources() -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    sources = _search_stores()
    stores: dict[str, list[dict[str, Any]]] = {}
    errors: list[dict[str, str]] = []
    for store_name, source in sources.items():
        try:
            stores[store_name] = source.load()
        except Exception as exc:
            errors.append({"path": source.label, "error": str(exc)[:1000]})
    if not stores:
        return [], errors

    plan = _identity_plan(stores)
    rejected_ids = {
        stripped_text(item.get("search_id"))
        for item in [*plan["invalid"], *plan["collisions"]]
        if stripped_text(item.get("search_id"))
    }
    for item in [*plan["invalid"], *plan["collisions"]]:
        surface = stripped_text(item.get("surface"))
        source_name = surface if surface in {"paper", "knowledge"} else "board"
        errors.append(
            {
                "resource_id": stripped_text(item.get("search_id")),
                "path": sources[source_name].label if source_name in sources else "",
                "error": stripped_text(item.get("error")) or "duplicate Search resource ID",
            }
        )

    records: list[dict[str, Any]] = []
    for store_name, jobs in plan["planned"].items():
        for job in jobs:
            search_id = stripped_text(job.get("search_id"))
            if search_id in rejected_ids or bool(job.get("deleted")):
                continue
            try:
                surface = _job_surface(store_name, job)
                records.append(
                    {
                        "id": search_id,
                        "type": "search",
                        "search_id": search_id,
                        "search_kind": "semantic",
                        "surface": surface,
                        "project": stripped_text(job.get("project")),
                        "resource_scope": (
                            surface if surface in RESOURCE_SCOPES else ""
                        ),
                        "query": stripped_text(job.get("query")),
                        "status": stripped_text(job.get("status")),
                        "created_by": stripped_text(job.get("created_by")),
                        "created_at": int(job.get("created_at") or 0),
                        "updated_at": int(job.get("updated_at") or 0),
                        "result_count": int(job.get("result_count") or 0),
                        "deleted": False,
                    }
                )
            except Exception as exc:
                errors.append(
                    {
                        "resource_id": search_id,
                        "path": sources[store_name].label,
                        "error": str(exc)[:1000],
                    }
                )
    records.sort(
        key=lambda item: (
            int(item.get("updated_at") or item.get("created_at") or 0),
            stripped_text(item.get("id")),
        ),
        reverse=True,
    )
    return records, errors


def resolve_search_address(
    address: str,
    *,
    actor: str = "",
) -> dict[str, Any]:
    clean_address = unquote(stripped_text(address)).strip("/")
    root_id, separator, _suffix = clean_address.partition("/")
    if not get_resource_service().is_valid_id(root_id, "search"):
        raise ValueError(f"invalid Search address: {address}")
    if separator:
        raise ValueError(f"Search resources do not define nested addresses: {address}")
    records = load_search_resources(include_deleted=True)
    record = next((item for item in records if item["id"] == root_id), None)
    if not record:
        raise KeyError(root_id)
    search_id = stripped_text(record.get("search_id"))
    portal_url = portal_url_for_search(search_id, record)
    return {
        "schema": "tfi.resource_resolution.v2",
        "address": clean_address,
        "root_id": root_id,
        "resource_type": "search",
        "target_kind": "search",
        "record": record,
        "search_id": search_id,
        "portal_url": portal_url,
        "exists": not bool(record.get("deleted")),
    }
