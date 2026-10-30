"""Shared core of the Lineage operations.

The domain clock, Lineage folder paths, cross-Lineage scopes and access checks, definition
normalization, Resource projection publication, Program contract cleanup, and the state
transaction every Lineage write commits through.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping

from management_common.file_lock import lock_stream
from management_common.projection_receipts import project_committed
from management_portal.resources.workspace_files import _write_text

from .errors import LineageConflictError
from .lineage_program_queries import _compiled_program
from .lineage_event_conditions import _scheduled_time_reached as _event_scheduled_time_reached
from . import lineage_definition_normalization, lineage_read_cache
from . import lineage_definition_merge, lineage_subgraph
from .lineage_definition_normalization import LINEAGE_MEMBERSHIP_VALUES
from .lineage_values import _clone_json, _safe_actor, _text
from . import lineage_access_policy, lineage_view_projection
from .lineage_access_policy import actor_can_manage_lineage as _actor_can_manage_lineage
from .lineage_view_projection import node_resident_id as _node_resident_id
from .lineage_program_fields import _position
from . import (
    identity_contract,
    invariant_locks,
    lineage_definition_contract,
    lineage_operator_runtime,
    lineage_placement,
    lineage_program_event_scope,
    lineage_program_observations,
    lineage_program_occurrences,
    lineage_program_runtime,
    lineage_program_wire_queue,
    lineage_state_domains,
    lineage_storage,
    resident_catalog,
    resident_protocol,
)
from . import lineage_aggregate
from .domain_ports import resident_domain_port as resident_domain, resource_port
from .lineage_data_runtime import RuntimeCASConflict


LINEAGE_FOLDER_ROOT = lineage_storage.configured_lineage_folder_root()
SCHEMA_VERSION = 1


LINEAGE_FOLDER_SCHEMA = "lineage-folder-v3"


_LINEAGE_READ_CACHE = lineage_read_cache.LineageReadCache()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _lineages_root() -> Path:
    return LINEAGE_FOLDER_ROOT


def _lineage_dir(lineage_id: str) -> Path:
    value = _text(lineage_id, 80).lower()
    if not identity_contract.is_valid_id(value, "lineage"):
        raise ValueError("invalid Lineage id")
    return lineage_storage.canonical_lineage_folder(
        value,
        folder_root=_lineages_root(),
    )


def _node_workspace_path(lineage_id: str, node: dict[str, Any]) -> Path:
    resident_id = _node_resident_id(node)
    if not identity_contract.is_valid_id(resident_id, "resident"):
        raise ValueError("Agent 节点缺少规范的 Resident ID")
    return (
        _lineage_dir(lineage_id)
        / "residents"
        / resident_id
        / "workspace"
    ).resolve(strict=False)


def _lineage_node(lineage: dict[str, Any], resident_id: object) -> dict[str, Any]:
    candidate = _text(resident_id, 80)
    node = next(
        (
            item
            for item in lineage.get("nodes") or []
            if isinstance(item, dict) and str(item.get("id") or "") == candidate
        ),
        None,
    )
    if not isinstance(node, dict):
        raise ValueError("Lineage 中不存在对应的 Agent 节点")
    return node


def _seed_folder(lineage_id: str) -> Path:
    """Where a Seed's ``artifact_path`` points: the Lineage folder, not any Resident's Workspace.

    A Seed is a Lineage object, so its Markdown stays when the Resident that
    wrote it, or its candidate fork source, is deleted.  Resolved, because
    the configured folder is usually a link to the Lineage's real folder and
    callers check that a resolved artifact stays inside it.
    """

    return _lineage_dir(lineage_id).resolve(strict=False)


@contextmanager
def _lineage_scopes(
    lineage_ids: Iterable[str],
    *,
    operation: str = "",
) -> Iterator[None]:
    """One effect scope for an operation that spans several Lineages."""

    with invariant_locks.lineage_scopes(lineage_ids, operation=operation):
        yield


@contextmanager
def _catalog_mutation_lock(
    catalog_name: str,
    *,
    operation: str = "",
) -> Iterator[None]:
    """Protect one global catalog's read-modify-write invariant."""

    with invariant_locks.catalog_lock(catalog_name, operation=operation):
        yield


@contextmanager
def catalog_mutation_lock(
    catalog_name: str,
    *,
    operation: str = "",
) -> Iterator[None]:
    """Expose one named catalog mutation invariant to sibling domains."""

    with _catalog_mutation_lock(catalog_name, operation=operation):
        yield


@contextmanager
def _program_contract_authority(
    lineage_id: str,
    impact: Mapping[str, Iterable[str]],
) -> Iterator[None]:
    """Hold the Program authority while a contract change commits and fences old work.

    Program runtime makes its enqueue decisions under the same authority, so
    none of them sees the new definition before the obsolete work is fenced.
    An edit that leaves the execution contract unchanged commits without it.
    """

    if any(impact.values()):
        with lineage_aggregate.execution_authority(lineage_id):
            yield
    else:
        yield


def _lineage_readme_text(lineage: dict[str, Any]) -> str:
    return "\n".join([
        f"# {str(lineage.get('title') or lineage.get('lineage_id') or 'Lineage')}",
        "",
        f"- Lineage ID: `{str(lineage.get('lineage_id') or '')}`",
        "## 目录",
        "",
        "- Lineage 与 HATG 当前状态由 SocietyService 持久化。",
        "- `LINEAGE.md` 保存所有节点共同使用的目标、背景和协作说明。",
        "- `residents/<resident_id>/` 保存 Agent 节点各自的 Resident Folder。",
        "- `residents/<resident_id>/workspace/` 是该节点自有的 Workspace。",
        "",
    ])


def _ensure_lineage_folder_files(lineage: dict[str, Any]) -> None:
    lineage_id = str(lineage.get("lineage_id") or "")
    # Resolve the owned canonical alias before private writes. The security
    # primitive deliberately rejects reparse points in its parent chain.
    lineage_dir = _lineage_dir(lineage_id).resolve(strict=False)
    lineage_dir.mkdir(parents=True, exist_ok=True)
    (lineage_dir / "residents").mkdir(parents=True, exist_ok=True)
    (lineage_dir / "runtime").mkdir(parents=True, exist_ok=True)
    lineage_md = lineage_dir / "LINEAGE.md"
    if not lineage_md.exists():
        lineage_md.touch()
    readme = lineage_dir / "README.md"
    if not readme.exists():
        _write_text(readme, _lineage_readme_text(lineage))


def _lineage_membership(lineage: Mapping[str, Any]) -> str:
    membership = str(lineage.get("lineage_membership") or "").strip().lower()
    if membership not in LINEAGE_MEMBERSHIP_VALUES:
        raise ValueError("Lineage lineage_membership must be formal or personal")
    return membership


def _requested_lineage_project_id(
    payload: dict[str, Any],
    previous: dict[str, Any],
    actor: str,
) -> str:
    if "project_id" not in payload:
        return _text(previous.get("project_id"), 120)
    project_id = _text(payload.get("project_id"), 120)
    if not project_id:
        return ""
    if project_id == _text(previous.get("project_id"), 120):
        # Keeping the Project claims no new access: Portal re-expands Function Blocks as itself.
        return project_id
    if resource_port.project_root(project_id, actor=actor) is not None:
        return project_id
    raise ValueError(f"项目不存在或当前用户无权访问: {project_id}")


def require_lineage_manager(lineage_id: str, actor: str) -> str:
    """Authorize a change to the Lineage without exposing its storage details."""

    actor_id = _safe_actor(actor)
    lineage_aggregate.ensure_manager(lineage_aggregate.read_lineage(lineage_id, actor=actor_id), actor_id)
    return actor_id


def _ensure_visible(lineage: dict[str, Any], actor: str) -> None:
    """Authorize reading the full Lineage surface."""

    if not _actor_can_manage_lineage(lineage, actor, operation="read"):
        raise lineage_access_policy.lineage_refusal(lineage)


def lineage_access_projection(lineage_id: str) -> dict[str, Any]:
    """Return the bounded identity projection shared with Resident Domain."""

    target_id = str(lineage_id or "").strip()
    records = lineage_aggregate.lineage_catalog_store().get_access_records([target_id])
    if not records:
        raise KeyError("Lineage not found")
    return records[0]


def authorize_lineage_resident_access(
    lineage_id: str,
    resident_id: str,
) -> str:
    """Validate a Resident caller separately from its target Lineage access."""
    principal = _text(resident_id, 80).lower()
    try:
        member = resident_catalog.get_catalog().get(principal)
    except KeyError:
        member = {}
    source_lineage_id = lineage_access_policy.resident_caller_source(member)
    principal = authorize_lineage_resident_principal(source_lineage_id, principal)
    lineage_access_projection(lineage_id)
    return principal


def authorize_lineage_resident_principal(
    lineage_id: str,
    resident_id: str,
    *,
    require_leader: bool = False,
) -> str:
    """Validate current membership and retain the authenticated Resident ID."""

    principal = lineage_access_policy.canonical_resident_principal(resident_id)
    identity_contract.validate_id(lineage_id, "lineage")
    lineage = lineage_aggregate.read_lineage_state(lineage_id) if require_leader else {}
    try:
        member = resident_catalog.get_catalog().get(principal)
    except KeyError:
        member = {}
    return lineage_access_policy.authorize_resident_principal(
        lineage_id, principal, lineage, member, require_leader=require_leader,
    )


def _resident_targets(
    resident_ids: list[str],
) -> dict[str, dict[str, str]]:
    clean_ids = list(dict.fromkeys(
        _text(resident_id, 80).lower()
        for resident_id in resident_ids
        if _text(resident_id, 80)
    ))
    try:
        # Definition validation also runs under the Lineage row lock. It
        # needs identity facts, not Workspace/Watchboard projections whose
        # filesystem and resource reads can hold up unrelated graph commits.
        records = resident_catalog.get_catalog().get_identity_records(clean_ids)
    except (OSError, ValueError):
        return {}
    return {
        str(record.get("resident_id") or ""): {
            "resident_id": str(record.get("resident_id") or ""),
            "source_resident_id": str(record.get("source_resident_id") or ""),
            "lineage_id": str(record.get("lineage_id") or ""),
            "title": str(record.get("title") or ""),
            "backend": str(record.get("backend") or ""),
        }
        for record in records
        if isinstance(record, dict) and str(record.get("resident_id") or "")
    }


def _sync_lineage_resource_identity(lineage: dict[str, Any]) -> None:
    resource_port.sync_lineage(
        lineage,
        updated_by=lineage.get("updated_by") or lineage.get("created_by"),
    )


def _publish_lineage_resource_projection(
    lineage_id: str,
    committed_revision: int | None,
    *,
    actor: str = "",
    creation: dict[str, Any] | None = None,
) -> None:
    """Publish the Lineage's current state to ResourceService once it covers a commit.

    Publications for one Lineage run one at a time under a file lock, and
    each publishes the state current at that moment, so a later publication
    never replaces newer state with older.  A commit that a later
    publication already covered needs none of its own.  A new Lineage is not
    in ResourceService yet; its creation publishes the snapshot it created.
    ``committed_revision=None`` covers whatever is committed now (a retry
    whose receipt kept no revision). ResourceService locks and rereads the
    canonical lifecycle before its descriptor-only commit. Stale committed
    payloads fail without overwriting domain facts.
    """
    from . import resource_client

    if invariant_locks.lock_held():
        raise RuntimeError("Resource projection publication cannot hold Lineage state locks")
    store = lineage_aggregate.lineage_catalog_store().state_store
    lock_path = _lineage_dir(lineage_id) / "runtime" / "resource-projection.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as publication_lock, lock_stream(publication_lock):
        published = store.lineage_resource_projection.get(lineage_id)
        covered = int(published.get("revision") or 0)
        if committed_revision is not None and covered >= int(committed_revision):
            return
        if creation is not None:
            projection = _clone_json(creation)
        else:
            projection = lineage_aggregate.read_lineage_state(lineage_id)
            if committed_revision is None and covered >= int(projection["revision"]):
                return
            if actor:
                lineage_aggregate.ensure_manager(lineage_aggregate.read_lineage(lineage_id, actor=actor), actor)
        _sync_lineage_resource_identity(projection)
        with store.lineage_resource_projection.transaction(lineage_id) as record:
            record["revision"] = max(int(record.get("revision") or 0), int(projection["revision"]))


def _workspace_relative_path(value: object, *, suffix: str = "") -> str:
    raw = _text(value, 800).replace("\\", "/")
    rel = Path(raw)
    if not raw or rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
        raise ValueError("invalid Lineage workspace path")
    normalized = rel.as_posix()
    if any(part.startswith(".") for part in rel.parts):
        raise ValueError("workspace path cannot be hidden")
    if suffix and not normalized.casefold().endswith(suffix.casefold()):
        raise ValueError(f"workspace path must end with {suffix}")
    return normalized


def _definition_ports() -> lineage_definition_normalization.DefinitionPorts:
    return lineage_definition_normalization.DefinitionPorts(
        clock=_now_iso,
        resident_targets=_resident_targets,
        position=_position,
        requested_lineage_project_id=_requested_lineage_project_id,
        clone_json=_clone_json,
        schema_version=SCHEMA_VERSION,
        lineage_folder_schema=LINEAGE_FOLDER_SCHEMA,
    )


def _normalize_edge(
    raw: object,
    resident_ids: set[str],
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return lineage_definition_normalization._normalize_edge(
        raw, resident_ids, existing, _clock=_now_iso,
    )


def _seed_markdown_title(
    lineage_id: str,
    lineage: dict[str, Any],
    seed: dict[str, Any],
) -> tuple[str, bool, str]:
    title = _text(seed.get("title"), 120) or "待命名种子"
    try:
        rel_path = _workspace_relative_path(seed.get("artifact_path"), suffix=".md")
        root = _seed_folder(lineage_id)
        path = (root / rel_path).resolve(strict=False)
        path.relative_to(root)
        if not path.is_file():
            return title, False, "missing"
        stat = path.stat()
        content = path.read_text(encoding="utf-8", errors="replace")[:128 * 1024]
    except (OSError, ValueError):
        return title, False, "missing"
    heading = next(
        (_text(match.group(1), 120) for line in content.splitlines() if (match := re.match(r"^#\s+(.+?)\s*$", line))),
        "",
    )
    return heading or title, True, f"{stat.st_mtime_ns}:{stat.st_size}"


def _verified_lineage_view(
    lineage: dict[str, Any],
    actor: str,
    resident_references: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return lineage_view_projection.verified_lineage_view(
        lineage, actor,
        seed_title=_seed_markdown_title,
    )


def _normalize_lineage_payload(
    payload: dict[str, Any],
    previous: dict[str, Any],
    actor: str,
    *,
    resident_references: dict[str, dict[str, Any]] | None = None,
    reconnect_removed_inheritance: bool = True,
) -> dict[str, Any]:
    if "run_as_principal" in payload:
        raise ValueError("run_as_principal 已移除；自动运行使用 Lineage Leader 的身份")
    return _normalize_lineage_shape(
        payload, previous, actor, resident_references=resident_references,
        reconnect_removed_inheritance=reconnect_removed_inheritance,
    )


def _normalize_lineage_shape(
    payload: dict[str, Any],
    previous: dict[str, Any],
    actor: str,
    *,
    resident_references: dict[str, dict[str, Any]] | None = None,
    reconnect_removed_inheritance: bool = True,
) -> dict[str, Any]:
    # A Family loses the members this edit removes; its generated wiring follows.
    # The payload keeps its own keys: absent keys still mean "unchanged".
    nodes = payload["nodes"] if isinstance(payload.get("nodes"), list) else previous.get("nodes") or []
    member_ids = {n["id"] for n in nodes}
    blocks = payload["function_blocks"] if "function_blocks" in payload else previous.get("function_blocks") or []
    if any(
        rid not in member_ids
        for block in blocks if block.get("family") is not None
        for rid in block["family"].get("members") or []
    ):
        from .templates.familize import reconcile_in_body

        payload = _clone_json(payload)
        payload["function_blocks"] = _clone_json(blocks)
        if not isinstance(payload.get("program"), dict):
            payload["program"] = _clone_json(previous.get("program") or {})
        body = {"lineage_id": previous.get("lineage_id"), "program": payload["program"]}
        for block in payload["function_blocks"]:
            family = block.get("family")
            if family is None:
                continue
            kept = [rid for rid in family.get("members") or [] if rid in member_ids]
            if kept != family.get("members"):
                family["members"] = kept
                reconcile_in_body(body, block, actor)
    return lineage_definition_normalization._normalize_lineage_payload(
        payload, previous, actor,
        resident_references=resident_references,
        reconnect_removed_inheritance=reconnect_removed_inheritance,
        ports=_definition_ports(),
    )


def _resident_completion_baseline_requests(
    lineage: dict[str, Any] | None,
    impact: dict[str, set[str]],
) -> list[dict[str, Any]]:
    """Describe newly activated completion subscriptions after a Graph edit.

    Event Source contract changes discard the whole source runtime, so every
    surviving route needs the current completion baseline.  An Event wire
    change also baselines every current route: the occurrence cursor is
    source-wide, and an older coordinator could otherwise claim a sibling
    route whose baseline has no subscriber token.  The
    description is pure graph work; Resident rollout reads are deliberately
    performed after the global Lineage state lock is released.
    """

    if not isinstance(lineage, dict):
        return []
    changed_source_ids = {
        str(item)
        for item in impact.get("event_source_ids") or set()
        if str(item)
    }
    changed_wire_ids = {
        str(item)
        for item in impact.get("invoke_wire_ids") or set()
        if str(item)
    }
    if not changed_source_ids and not changed_wire_ids:
        return []
    plan = _compiled_program(lineage)
    nodes = {
        str(node.get("id") or ""): node
        for node in lineage.get("nodes") or []
        if isinstance(node, dict) and str(node.get("id") or "")
    }
    requests: list[dict[str, Any]] = []
    for event_source_id, watched, per_resident, subscriber_ids in (
        lineage_program_event_scope.resident_completion_subscriptions(plan, nodes)
    ):
        if not any(nodes.get(subject_id) for subject_id in watched):
            continue
        if event_source_id not in changed_source_ids and not changed_wire_ids.intersection(subscriber_ids):
            continue
        for subject_id in watched:
            subject = nodes.get(subject_id)
            if not subject:
                continue
            requests.append(
                {
                    "event_source_id": event_source_id,
                    "subject_resident_id": subject_id,
                    "resident_id": _node_resident_id(subject),
                    "locator": f"resident:{subject_id}",
                    "subscriber_ids": list(dict.fromkeys(subscriber_ids)),
                    "per_resident": per_resident,
                }
            )
    return requests


def _establish_resident_completion_baselines(
    lineage_id: str,
    actor: str,
    requests: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fence historical completions before releasing execution authority."""

    results: list[dict[str, Any]] = []
    for request in requests:
        try:
            completion = resident_protocol.get_latest_completed_message_reference(
                str(request.get("resident_id") or ""),
                actor,
            )
        except (KeyError, PermissionError, ValueError) as exc:
            results.append(
                {
                    "status": "unavailable",
                    "event_source_id": str(
                        request.get("event_source_id") or ""
                    ),
                    "error": str(exc),
                }
            )
            continue
        if not isinstance(completion, dict):
            completion = {}
        results.append(
            lineage_program_observations
            .establish_resident_completion_subscription_baseline(
                lineage_id,
                str(request.get("event_source_id") or ""),
                locator=str(request.get("locator") or ""),
                subject_resident_id=str(
                    request.get("subject_resident_id") or ""
                ),
                message_id=str(completion.get("message_id") or ""),
                completed_at=str(completion.get("completed_at") or ""),
                subscriber_ids=list(
                    request.get("subscriber_ids") or []
                ),
                per_resident=bool(request.get("per_resident")),
            )
        )
    return results


def _execute_program_contract_cleanup(
    lineage_id: str,
    impact: dict[str, set[str]],
    *,
    previous_lineage: dict[str, Any] | None = None,
    current_lineage: dict[str, Any] | None = None,
    preserve_event_source_fire_budget_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Fence obsolete work and return completion subscriptions to baseline."""

    completion_baselines = _resident_completion_baseline_requests(
        current_lineage,
        impact,
    )
    for loop_id in sorted(impact.get("loop_ids") or set()):
        lineage_program_runtime.delete_operator_state(lineage_id, loop_id)
    for operator_id in sorted(impact.get("operator_ids") or set()):
        lineage_operator_runtime.clear_operator_runtime(
            lineage_id,
            operator_id,
        )
    preserve_fire_budget = {
        str(item)
        for item in preserve_event_source_fire_budget_ids or set()
        if str(item)
    }
    recurring_source_ids = {
        str(component.get("id") or "")
        for lineage in (previous_lineage, current_lineage)
        for component in (
            ((lineage or {}).get("program") or {}).get("components") or []
        )
        if (
            isinstance(component, dict)
            and str(component.get("kind") or "") == "event_source"
            and str((component.get("config") or {}).get("event_type") or "")
            == "time.reached"
            and str((component.get("config") or {}).get("schedule_mode") or "")
            == "recurring"
            and str(component.get("id") or "")
        )
    }
    # A recurring schedule owns a monotonic generation across authoring
    # changes. Preserve its compact source state so the next reconcile can
    # detect the new contract and fence the old pending occurrence.
    preserve_fire_budget.update(
        recurring_source_ids & set(impact.get("event_source_ids") or set())
    )
    for event_source_id in sorted(
        impact.get("event_source_ids") or set()
    ):
        if event_source_id in preserve_fire_budget:
            (
                lineage_program_occurrences
                .fence_event_source_contract_preserving_fire_budget(
                    lineage_id,
                    event_source_id,
                )
            )
        else:
            lineage_program_occurrences.forget_event_source(
                lineage_id,
                event_source_id,
                preserve_pause=event_source_id in {
                    str(component.get("id") or "")
                    for component in ((current_lineage or {}).get("program") or {}).get("components") or []
                },
            )
    for wire_id in sorted(impact.get("invoke_wire_ids") or set()):
        lineage_program_wire_queue.forget_event_wire(
            lineage_id,
            wire_id,
        )
    from . import wire_delivery_budget
    live_wire_ids = {str(wire.get("id") or "") for wire in ((current_lineage or {}).get("program") or {}).get("wires") or []}
    wire_delivery_budget.forget(lineage_id, set(impact.get("invoke_wire_ids") or set()) - live_wire_ids)
    return completion_baselines


def _persist_lineage_state(
    previous: dict[str, Any] | None,
    lineage: dict[str, Any],
    reason: str,
    *,
    domain: str = lineage_state_domains.GRAPH,
    runtime_changed: bool = False,
    synchronize_resource_identities: bool = True,
    synchronize_resident_identities: bool = True,
    placement_request: dict[str, Any] | None = None,
    resident_references: dict[str, dict[str, Any]] | None = None,
) -> None:
    if domain == lineage_state_domains.GRAPH:
        lineage_placement.place_new_components(previous, lineage, placement_request)
    lineage_id = str(lineage.get("lineage_id") or "").strip()
    lineage_aggregate.lineage_catalog_store().state_store.transaction(
        lambda: _persist_lineage_state_transaction(
            previous,
            lineage,
            reason,
            domain=domain,
            runtime_changed=runtime_changed,
            synchronize_resource_identities=synchronize_resource_identities,
            synchronize_resident_identities=synchronize_resident_identities,
            resident_references=resident_references,
        )
    )
    invalidate_lineage_read_cache(lineage_id)


def _canonical_lineage_definition_projection(
    lineage: Mapping[str, Any],
) -> dict[str, Any]:
    return lineage_state_domains.definition_projection(_clone_json(lineage))


def _persist_lineage_state_transaction(
    previous: dict[str, Any] | None,
    lineage: dict[str, Any],
    reason: str,
    *,
    domain: str = lineage_state_domains.GRAPH,
    runtime_changed: bool = False,
    synchronize_resource_identities: bool = True,
    synchronize_resident_identities: bool = True,
    resident_references: dict[str, dict[str, Any]] | None = None,
) -> None:
    now = _now_iso()
    _cancel_removed_effects(previous, lineage, domain, now)
    if (
        domain == lineage_state_domains.GRAPH
        and previous is not None
        and lineage_state_domains.runtime_projection_changed(
            previous,
            lineage,
        )
    ):
        runtime_changed = True
    lineage_state_domains.stamp(
        lineage,
        previous,
        domain,
        now=now,
    )
    if runtime_changed and domain == lineage_state_domains.GRAPH:
        lineage_state_domains.stamp_additional_runtime_change(
            lineage,
            previous,
            now=now,
        )
    lineage_id = str(lineage.get("lineage_id") or "")
    _ensure_lineage_folder_files(lineage)
    definition_changed = (
        previous is None
        or domain in {lineage_state_domains.GRAPH, lineage_state_domains.LAYOUT}
    )
    runtime_state_changed = (
        previous is None
        or domain == lineage_state_domains.RUNTIME
        or runtime_changed
    )
    runtime_candidate = lineage_state_domains.persisted_runtime_state(lineage)
    if definition_changed:
        _write_definition_state(
            previous, lineage, lineage_id, domain, resident_references,
            synchronize_resource_identities, synchronize_resident_identities,
        )
    if runtime_state_changed:
        _write_runtime_state(previous, lineage, lineage_id, domain, runtime_candidate)
    _defer_change_notifications(
        previous, lineage, lineage_id, domain, definition_changed, runtime_changed,
    )


def _cancel_removed_effects(
    previous: dict[str, Any] | None,
    lineage: dict[str, Any],
    domain: str,
    now: str,
) -> None:
    """Cancel the script runs of the Effects a definition write removes."""
    if previous is not None and domain == lineage_state_domains.GRAPH:
        previous_effects = {str(component.get("id")) for component in (previous.get("program") or {}).get("components", [])
                            if component.get("kind") == "effect"}
        current_effects = {str(component.get("id")) for component in (lineage.get("program") or {}).get("components", [])
                           if component.get("kind") == "effect"}
        removed_effects = previous_effects - current_effects
        if removed_effects:
            from . import lineage_effect_script_journal
            lineage_effect_script_journal.cancel_removed_effects(str(lineage["lineage_id"]), removed_effects,
                reason="Effect node deleted", occurred_at=now)


def _write_definition_state(
    previous: dict[str, Any] | None,
    lineage: dict[str, Any],
    lineage_id: str,
    domain: str,
    resident_references: dict[str, dict[str, Any]] | None,
    synchronize_resource_identities: bool,
    synchronize_resident_identities: bool,
) -> None:
    """Commit the definition and defer the Resource and Resident identity projections it changes."""
    persisted, merged = _committed_definition(
        previous, lineage, domain, resident_references=resident_references,
    )
    lineage_aggregate.lineage_catalog_store().upsert(persisted)
    if merged:
        _adopt_committed_definition(lineage, persisted)
    else:
        for field in lineage_definition_merge.STAMP_FIELDS:
            lineage[field] = persisted[field]
    if (lineage.get("principal_hierarchy") or {}).get("level") == "project":
        store = lineage_aggregate.lineage_catalog_store().state_store
        committed = store.database.write(lambda connection: store._lineage_definition_from_connection(connection, lineage_id))
        lineage.update(committed)
    if (
        domain == lineage_state_domains.GRAPH
        and synchronize_resource_identities
    ):
        committed_revision = int(lineage["revision"])
        creation = None
        if previous is None:
            creation = _clone_json(lineage)
        invariant_locks.defer_until_state_unlock(
            lambda revision=committed_revision, creation=creation: (
                project_committed(
                    {
                        "projection": "lineage.resource_identity",
                        "lineage_id": lineage_id,
                        "committed_revision": revision,
                    },
                    lambda: _publish_lineage_resource_projection(lineage_id, revision, creation=creation),
                )
            ),
        )
    if (
        domain == lineage_state_domains.GRAPH
        and synchronize_resident_identities
        and (
            previous is None
            or _lineage_member_resident_ids(previous) != _lineage_member_resident_ids(lineage)
        )
    ):
        resident_projection = _clone_json(lineage)
        previous_projection = (
            _clone_json(previous)
            if isinstance(previous, dict)
            else None
        )

        def sync_resident_identities(
            projection: dict[str, Any] = resident_projection,
            previous_value: dict[str, Any] | None = previous_projection,
        ) -> None:

            resident_domain.sync_lineage_member_credentials(
                projection,
                previous_lineage=previous_value,
            )

        invariant_locks.defer_until_state_unlock(
            sync_resident_identities,
        )


def _write_runtime_state(
    previous: dict[str, Any] | None,
    lineage: dict[str, Any],
    lineage_id: str,
    domain: str,
    runtime_candidate: dict[str, Any],
) -> None:
    """Commit the runtime state and stamp the Lineage with the committed runtime revision."""
    if domain == lineage_state_domains.RUNTIME and previous is not None:
        try:
            merged_runtime = lineage_aggregate.lineage_catalog_store().put_runtime_cas(
                lineage_id,
                lineage_state_domains.persisted_runtime_state(previous),
                runtime_candidate,
            )
        except RuntimeCASConflict as exc:
            raise LineageConflictError(
                "Lineage runtime 已变化，请使用当前状态重试"
            ) from exc
        lineage["runtime_revision"] = int(
            merged_runtime.get("runtime_revision") or 0
        )
        lineage["runtime_updated_at"] = str(
            merged_runtime.get("updated_at") or ""
        )
    elif previous is not None:
        # A definition write carries the runtime objects it touched; the
        # rest of the runtime keeps its committed state.
        merged_runtime = lineage_aggregate.lineage_catalog_store().state_store.put_lineage_runtime_changes(
            lineage_id,
            lineage_state_domains.persisted_runtime_state(previous),
            runtime_candidate,
        )
        lineage["runtime_revision"] = int(merged_runtime.get("runtime_revision") or 0)
        lineage["runtime_updated_at"] = str(merged_runtime.get("updated_at") or "")
    else:
        lineage_aggregate.lineage_catalog_store().put_runtime(
            lineage_id,
            runtime_candidate,
        )


def _defer_change_notifications(
    previous: dict[str, Any] | None,
    lineage: dict[str, Any],
    lineage_id: str,
    domain: str,
    definition_changed: bool,
    runtime_changed: bool,
) -> None:
    """Defer the change notifications of a committed write until the state lock is released."""
    if definition_changed:
        from . import system_workflow_switches
        system_workflow_switches.defer_definition_projection(lineage_id, previous, lineage)
        if domain == lineage_state_domains.GRAPH:
            from . import family_facade
            from .changes import resident_changed
            for facade_id in family_facade.changed_roster_facades(previous, lineage):
                invariant_locks.defer_until_state_unlock(
                    lambda facade_id=facade_id: resident_changed(facade_id, scope="projection"),
                )
    from management_portal.society_service.changes import lineage_changed

    runtime_notification = (
        domain == lineage_state_domains.RUNTIME or runtime_changed
    )
    invariant_locks.defer_until_state_unlock(
        lambda: lineage_changed(
            lineage_id,
            runtime=runtime_notification,
        ),
    )


def _definition_record(value: Mapping[str, Any]) -> dict[str, Any]:
    persisted = _canonical_lineage_definition_projection(value)
    persisted["schema"] = LINEAGE_FOLDER_SCHEMA
    return lineage_definition_contract.validate_definition(
        persisted,
        path=lineage_aggregate.lineage_catalog_store().storage_identity,
    )


def _committed_definition(
    previous: dict[str, Any] | None,
    lineage: dict[str, Any],
    domain: str,
    *,
    resident_references: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], bool]:
    """This write's definition as it commits, and whether it was merged.

    The Lineage row is held for the rest of the commit.  When another write
    committed since this one read the Lineage, this write's own changes are
    merged into the committed definition (lineage_definition_merge).  The
    commit assigns the change counters.
    """

    lineage_id = str(lineage.get("lineage_id") or "")
    ours = _definition_record(lineage)
    store = lineage_aggregate.lineage_catalog_store().state_store
    current = store.lock_lineage_definition(lineage_id)
    from .lineage_definition_scope import check_definition_scope
    store.database.write(lambda connection: check_definition_scope(connection, current, lineage_id))
    from .lineage_definition_versions import check_definition_version
    store.database.write(lambda connection: check_definition_version(connection, lineage_id))
    if current is None:
        return ours, False
    merged = False
    if previous is not None:
        base = _definition_record(previous)
        if any(int(current.get(field) or 0) != int(base.get(field) or 0) for field in ("revision", "layout_revision")):
            try:
                result = lineage_definition_merge.merge_definition(
                    base, ours, current, successor=_merge_successor(lineage_id),
                )
            except lineage_definition_merge.ConcurrentChangeConflict as exc:
                raise LineageConflictError(str(exc)) from exc
            for field in lineage_definition_merge.STAMP_FIELDS:
                result[field] = current.get(field, ours.get(field))
            try:
                # Both changes were authorized at their mutation boundaries.
                # Revalidate the merged shape without treating historical
                # creator metadata as authority for a new run_as assignment.
                normalized = _normalize_lineage_shape(
                    result, current, str(lineage.get("created_by") or "portal"),
                    resident_references=resident_references,
                )
                ours = _definition_record(normalized)
            except ValueError as exc:
                raise LineageConflictError(f"并发合并后的 Lineage 定义无效：{exc}") from exc
            merged = True
    graph_revision = int(current.get("revision") or 0)
    layout_revision = int(current.get("layout_revision") or 0)
    if domain == lineage_state_domains.GRAPH:
        ours["revision"] = graph_revision + 1
        ours["layout_revision"] = layout_revision
        ours["layout_updated_at"] = str(current.get("layout_updated_at") or "")
        ours["graph_updated_at"] = ours["updated_at"] = str(lineage.get("graph_updated_at") or lineage.get("updated_at") or "")
    else:
        ours["revision"] = graph_revision
        ours["graph_updated_at"] = str(current.get("graph_updated_at") or "")
        ours["updated_at"] = str(current.get("updated_at") or "")
        ours["layout_revision"] = layout_revision + 1
        ours["layout_updated_at"] = str(lineage.get("layout_updated_at") or "")
    return ours, merged


def _merge_successor(lineage_id: str) -> Callable[[str, set[str], list[dict[str, Any]]], str]:
    """Leader succession for a Leader deleted by a concurrent write, as deletion does."""

    def successor(leader: str, removed: set[str], nodes: list[dict[str, Any]]) -> str:
        member_ids = {
            record["resident_id"]
            for record in resident_catalog.get_catalog().for_lineage(lineage_id)
        }
        return lineage_subgraph.successor_coordinator(leader, removed, nodes, member_ids=member_ids)

    return successor


def _adopt_committed_definition(lineage: dict[str, Any], committed: Mapping[str, Any]) -> None:
    """Make the in-memory aggregate the committed definition with this write's runtime.

    A later write in the same operation then starts from what actually
    committed instead of erasing the concurrent changes merged in.
    """

    runtime = lineage_state_domains.persisted_runtime_state(lineage)
    node_ids = {str(node.get("id") or "") for node in committed.get("nodes") or [] if isinstance(node, Mapping)}
    edge_ids = {str(edge.get("id") or "") for edge in committed.get("edges") or [] if isinstance(edge, Mapping)}
    if isinstance(runtime.get("nodes"), Mapping):
        runtime["nodes"] = {key: value for key, value in runtime["nodes"].items() if key in node_ids}
    if isinstance(runtime.get("edges"), Mapping):
        runtime["edges"] = {
            key: value for key, value in runtime["edges"].items()
            if key in edge_ids or isinstance(value.get("absorption"), Mapping)
        }
    rebuilt = lineage_state_domains.merge_runtime_state(_clone_json(dict(committed)), runtime)
    lineage.clear()
    lineage.update(rebuilt)


def _persist_lineage_runtime_state(
    previous: dict[str, Any],
    lineage: dict[str, Any],
    reason: str,
) -> None:
    _persist_lineage_state(
        previous,
        lineage,
        reason,
        domain=lineage_state_domains.RUNTIME,
    )


def _lineage_member_resident_ids(lineage: dict[str, Any]) -> set[str]:
    return {
        _node_resident_id(node)
        for node in lineage.get("nodes") or []
        if isinstance(node, dict) and _node_resident_id(node)
    }


def _require_unchanged_membership(previous: dict[str, Any], candidate: dict[str, Any]) -> None:
    if _lineage_member_resident_ids(previous) != _lineage_member_resident_ids(candidate):
        raise ValueError(
            "HATG 视图不能独立增加或移除 Resident 成员；请创建、转移或删除 Resident 身份"
        )


def invalidate_lineage_read_cache(lineage_id: str) -> None:
    """Invalidate projections whose canonical Resident facts may have changed."""

    target = str(lineage_id or "").strip()
    if not target:
        return
    resident_domain.invalidate_lineage_access_projection(target)
    _LINEAGE_READ_CACHE.invalidate(target)


def _scheduled_time_reached(value: object) -> bool:
    return _event_scheduled_time_reached(
        value, now=datetime.now(timezone.utc),
    )
