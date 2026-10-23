from __future__ import annotations
from management_common.portal_paths import path as portal_path
from . import business_fact_views

from copy import deepcopy
from functools import partial

from .errors import LineageConflictError
from .lineage_program_queries import _compiled_program
from .lineage_event_conditions import _scheduled_time_reached as _event_scheduled_time_reached
from . import (
    lineage_branch_transitions,
    lineage_definition_normalization,
    lineage_graph_authoring,
    lineage_program_graph_mutation,
    lineage_read_cache,
)
from . import lineage_definition_merge, lineage_event_reconciliation, lineage_subgraph
from . import lineage_program_services
from . import lineage_program_execution, lineage_program_polling, lineage_program_terminal_reconciliation, lineage_program_event_dispatch, lineage_program_identity
from .lineage_program_runtime_projection import (
    _program_invoke_wire_runtime_view, _program_event_source_runtime_view,
    _program_effect_runtime_view,
)
from .lineage_program_contracts import (
    _program_invoke_wire_execution_configs,
    _program_execution_contract_change,
    _program_event_source_contract_fingerprint,
)
from .lineage_definition_normalization import LINEAGE_MEMBERSHIP_VALUES
from .lineage_values import _bool, _clone_json, _dedupe_strings, _item_id, _safe_actor, _text
from . import (
    lineage_access_policy,
    lineage_execution_authority,
    lineage_view_projection,
    lineage_handoff_preparation,
    lineage_handoff_execution,
    lineage_program_backlog,
    lineage_workspace_binding,
    lineage_node_workspaces,
    lineage_program_authoring,
)
from .lineage_access_policy import (
    actor_can_manage_lineage as _actor_can_manage_lineage,
    actor_can_manage_lineages as _actor_can_manage_lineages,
)
from .lineage_view_projection import (
    node_resident_id as _node_resident_id,
    node_status as _lineage_node_status,
)
from .lineage_program import _position
from .lineage_workspace_files import (
    MAX_WORKSPACE_COPY_BYTES,
    MAX_WORKSPACE_COPY_FILES,
    _write_text,
)


from management_common.file_lock import lock_stream
import functools
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import sys
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping

from management_common.limit_contract import normalize_optional_limit
from management_common.projection_receipts import CommittedProjectionError, project_committed
from management_portal.portal_access_control import get_portal_access_store
from . import (
    identity_contract,
    invariant_locks,
    lineage_execution_explanation,
    lineage_definition_contract,
    lineage_catalog,
    lineage_operator_runtime,
    lineage_placement,
    lineage_program,
    lineage_program_runtime,
    lineage_primitives,
    lineage_state_domains,
    lineage_storage,
    resident_catalog,
    resident_state,
    resident_protocol,
)
from . import resident_message_contract
from . import lineage_event_dispatch_rules
from .domain_ports import resident_domain_port as resident_domain, resource_port
from .state_store import RuntimeCASConflict


DEFAULT_DATA_ROOT = (portal_path("staging_root") / "shared" / "portal_lineages")
DATA_ROOT = Path(os.getenv(
    "PORTAL_LINEAGE_ROOT",
    str(DEFAULT_DATA_ROOT),
)).expanduser()
invariant_locks.configure_root_provider(lambda: DATA_ROOT)
LINEAGE_FOLDER_ROOT = lineage_storage.configured_lineage_folder_root()
SCHEMA_VERSION = 1
# The Lineage buttons a listing reports in ``allows``; each needs only ``lineage_id`` under the default scheme.
LINEAGE_ACTIONS = ("UpdateLineage", "DeleteLineage", "CreateResident", "UpdateResident", "CreateWorkspaceHandoff")
LINEAGE_FOLDER_SCHEMA = "lineage-folder-v3"
LINEAGE_DELETION_MARKER = ".portal-lineage-delete.json"
LINEAGE_GRAPH_PATCH_FIELDS = frozenset(
    {
        "title",
        "description",
        "nodes",
        "edges",
        "program",
        "fragments",
        "leader_resident_id",
    }
)
_LINEAGE_READ_CACHE = lineage_read_cache.LineageReadCache()

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _lineages_root() -> Path:
    return LINEAGE_FOLDER_ROOT


LOGGER = logging.getLogger(__name__)


def _lineage_catalog_store() -> lineage_catalog.LineageCatalog:
    return lineage_catalog.get_catalog()


def _deleted_root() -> Path:
    return DATA_ROOT / "deleted"


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
def lineage_state_scope(
    lineage_id: str,
    operation: str = "",
) -> Iterator[None]:
    """Attach request diagnostics without selecting a mutation lock."""

    with invariant_locks.diagnostic_scope(lineage_id, operation):
        yield


def _lineage_scoped(function: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(function)
    def scoped(lineage_id: str, *args: Any, **kwargs: Any) -> Any:
        with lineage_state_scope(lineage_id, function.__name__):
            return function(lineage_id, *args, **kwargs)

    return scoped


def state_lock_health() -> dict[str, Any]:
    return invariant_locks.health()


@contextmanager
def _lineage_scope(
    lineage_id: str,
    *,
    operation: str = "",
) -> Iterator[None]:
    """One Lineage operation; its deferred effects run after it ends (no lock)."""

    with invariant_locks.lineage_scope(lineage_id, operation=operation):
        yield


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
def _lineage_execution_authority(lineage_id: str) -> Iterator[None]:
    with lineage_execution_authority.execution_authority(lineage_id, root=DATA_ROOT):
        yield

@contextmanager
def _lineage_execution_authorities(
    lineage_ids: list[str] | set[str] | tuple[str, ...],
) -> Iterator[None]:
    with lineage_execution_authority.execution_authorities(lineage_ids, root=DATA_ROOT):
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
        with _lineage_execution_authority(lineage_id):
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
    lineage_dir = _lineage_dir(lineage_id)
    lineage_dir.mkdir(parents=True, exist_ok=True)
    (lineage_dir / "residents").mkdir(parents=True, exist_ok=True)
    (lineage_dir / "runtime").mkdir(parents=True, exist_ok=True)
    lineage_md = lineage_dir / "LINEAGE.md"
    if not lineage_md.exists():
        lineage_md.touch()
    readme = lineage_dir / "README.md"
    if not readme.exists():
        _write_text(readme, _lineage_readme_text(lineage))


def _read_lineage_state(lineage_id: str) -> dict[str, Any]:
    """Read the canonical aggregate without re-entering ResourceService."""

    catalog = _lineage_catalog_store()
    try:
        payload, runtime_state = catalog.get_state(lineage_id)
    except KeyError:
        raise KeyError("Lineage not found")
    try:
        runtime_state = lineage_state_domains.validate_runtime_state(
            runtime_state,
            definition=payload,
        )
    except ValueError as exc:
        raise ValueError("Lineage runtime state does not match current contract") from exc
    # The snapshot was read for this call alone, so it is merged in place.
    return lineage_state_domains.merge_validated_runtime_state(payload, runtime_state)


def _read_lineage(lineage_id: str, *, actor: str) -> dict[str, Any]:
    if _lineage_deletion_intent(lineage_id):
        raise LineageConflictError("Lineage deletion is in progress")
    payload = _read_lineage_state(lineage_id)
    payload = business_fact_views.canonical_view(
        payload,
        resource_id=lineage_id,
        actor=_text(actor, 120),
    )
    return payload


def _lineage_deletion_intent(lineage_id: str) -> dict[str, Any]:
    return _lineage_catalog_store().state_store.lineage_deletion_operations.get(lineage_id)


def _read_lineage_for_deletion(lineage_id: str, actor: str) -> dict[str, Any]:
    # An interrupted deletion may already have tombstoned its descriptor.
    # The still-existing domain aggregate and revision remain the recovery truth.
    from .business_fact_views import canonical_view
    return canonical_view(_read_lineage_state(lineage_id), resource_id=lineage_id, actor=_text(actor, 120))


def _stage_empty_lineage_deletion(
    lineage: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    lineage_id = str(lineage["lineage_id"])
    store = _lineage_catalog_store().state_store
    from . import lineage_effect_script_journal
    lineage_effect_script_journal.ensure_deletion_can_settle(lineage_id)
    existing = _lineage_deletion_intent(lineage_id)
    if existing:
        if int(existing.get("definition_revision") or -1) != int(
            lineage.get("revision") or 0
        ):
            raise LineageConflictError("Lineage changed after deletion was prepared")
        return existing

    canonical = lineage_storage.canonical_lineage_folder(
        lineage_id,
        folder_root=_lineages_root(),
    )
    source = canonical.resolve(strict=True)
    operation_id = f"lineage-delete_{secrets.token_hex(12)}"
    deleted_at = _now_iso()
    intent = {
        "operation_id": operation_id,
        "lineage_id": lineage_id,
        "definition_revision": int(lineage.get("revision") or 0),
        "source_path": str(source),
        "canonical_path": str(canonical),
        "deleted_at": deleted_at,
        "deleted_by": actor,
        "phase": "prepared",
        "last_error": "",
    }
    with store.lineage_deletion_operations.transaction(lineage_id) as state:
        if state:
            raise LineageConflictError("Another Lineage deletion is in progress")
        state.update(intent)
    return intent


def _purge_deleted_lineage_folder(intent: Mapping[str, Any]) -> None:
    """Clean in place so surviving Workspace paths never change.

    Previously staged, already moved journals are drained at their recorded
    target; new deletion intents never create a target or move the folder.
    """
    from .resident_workspace_retirement import remove_retired_folder
    source = Path(str(intent["source_path"]))
    if not source.is_absolute():
        raise ValueError("Invalid Lineage deletion path")
    target_path = intent.get("target_path")
    if target_path and Path(target_path).exists():
        target = Path(target_path)
        proof = json.dumps({key: str(intent.get(key) or "") for key in (
            "operation_id", "lineage_id", "source_path", "target_path",
        )}, sort_keys=True, separators=(",", ":"))
        marker = target / LINEAGE_DELETION_MARKER
        if (target.parent.resolve() != _deleted_root().resolve()
                or not target.name.startswith(f"{intent['lineage_id']}_lineage-delete_")
                or not marker.is_file() or marker.is_symlink()
                or marker.read_text(encoding="utf-8") != proof):
            raise ValueError("Invalid staged Lineage deletion")
        source = target
    _lineage_catalog_store().state_store.retire_workspaces_under(
        [source], deleted_at=intent["deleted_at"], deleted_by=intent["deleted_by"],
    )
    remove_retired_folder(source)


def _record_lineage_deletion_error(
    lineage_id: str,
    operation_id: str,
    error: BaseException,
) -> None:
    with _lineage_scope(lineage_id):
        current = _lineage_deletion_intent(lineage_id)
        if str(current.get("operation_id") or "") != operation_id:
            return
        with _lineage_catalog_store().state_store.lineage_deletion_operations.transaction(lineage_id) as state:
            if str(state.get("operation_id") or "") == operation_id:
                state["last_error"] = f"{type(error).__name__}: {error}"[:2000]
                state["phase"] = "retryable"


def _lineage_runtime_actor(lineage: dict[str, Any]) -> str:
    """Resolve an optional automation User, otherwise observe as Portal."""
    from .lineage_automation_identity import runtime_actor
    return runtime_actor(lineage)


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


def _lineage_project_folder(
    lineage_id: str,
    project_id: str,
    actor: str,
) -> Path:
    if not project_id:
        return lineage_storage.canonical_lineage_folder(
            lineage_id,
            folder_root=_lineages_root(),
        )
    from .project_domain import project_root_for_id
    project_root = project_root_for_id(project_id)
    return project_root / "knowledge_base" / "agents" / lineage_id


def _lineage_relocation_blockers(lineage_id: str) -> list[str]:
    records = resident_catalog.get_catalog().for_lineage(
        lineage_id
    )
    resident_ids = [
        str(record.get("resident_id") or "")
        for record in records
        if str(record.get("resident_id") or "")
    ]
    states = resident_protocol.read_resident_states(resident_ids)
    return [
        resident_id
        for resident_id in resident_ids
        if str((states.get(resident_id) or {}).get("work_state") or "idle")
        in {"queued", "running", "stopping"}
    ]


def _relocate_lineage_for_project(
    lineage_id: str,
    project_id: str,
    actor: str,
) -> Path:
    blockers = _lineage_relocation_blockers(lineage_id)
    if blockers:
        raise ValueError(
            "Lineage 仍有任务正在排队或处理，停止或完成后再修改所属项目: "
            + ", ".join(blockers)
        )
    target = _lineage_project_folder(lineage_id, project_id, actor)
    lineage_storage.relocate_lineage_folder(
        lineage_id,
        target,
        folder_root=_lineages_root(),
    )
    return target


def _ensure_manager(lineage: dict[str, Any], actor: str) -> None:
    if not _actor_can_manage_lineage(lineage, actor):
        raise PermissionError(lineage_access_policy.lineage_denial(lineage, "修改"))


def require_lineage_manager(lineage_id: str, actor: str) -> str:
    """Authorize a change to the Lineage without exposing its storage details."""

    actor_id = _safe_actor(actor)
    _ensure_manager(_read_lineage(lineage_id, actor=actor_id), actor_id)
    return actor_id


def _ensure_visible(lineage: dict[str, Any], actor: str) -> None:
    """Authorize reading the full Lineage surface."""

    if not _actor_can_manage_lineage(lineage, actor, operation="read"):
        raise PermissionError(lineage_access_policy.lineage_denial(lineage, "访问"))


def lineage_access_projection(lineage_id: str) -> dict[str, Any]:
    """Return the bounded identity projection shared with Resident Domain."""

    target_id = str(lineage_id or "").strip()
    records = _lineage_catalog_store().get_access_records([target_id])
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
    lineage = _read_lineage_state(lineage_id) if require_leader else {}
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


def _project_canonical_resident_titles(
    lineage: dict[str, Any],
    snapshot: dict[str, Any] | None = None,
) -> None:
    """Project each active Resident's canonical title and Provider into its node."""

    del snapshot
    nodes = [
        node
        for node in lineage.get("nodes") or []
        if isinstance(node, dict)
    ]
    records = _resident_targets([_node_resident_id(node) for node in nodes])
    for node in nodes:
        record = records.get(_node_resident_id(node), {})
        title = _text(record.get("title"), 120)
        if title:
            node["title"] = title
            node["resident_title"] = title
        backend = _text(record.get("backend"), 40)
        if backend:
            node["backend"] = backend


def commit_resident_title_projection(
    resident_id: str,
    title: str,
    actor: str,
) -> dict[str, Any]:
    """Commit one canonical Resident title into its owning HATG definition."""

    actor_id = _safe_actor(actor)
    exact_resident_id = _text(resident_id, 80).lower()
    next_title = _text(title, 120)
    if not exact_resident_id or not next_title:
        raise ValueError("Resident title projection requires identity and title")
    lineage_ids = _lineage_catalog_store().lineage_ids_for_resident(exact_resident_id)
    if len(lineage_ids) != 1:
        raise ValueError("Resident must belong to exactly one Lineage")
    lineage_id = lineage_ids[0]
    with _lineage_scope(lineage_id):
        previous = _read_lineage(lineage_id, actor=actor)
        _ensure_manager(previous, actor_id)
        lineage = _clone_json(previous)
        node = next(
            (
                item
                for item in lineage.get("nodes") or []
                if isinstance(item, dict)
                and _node_resident_id(item) == exact_resident_id
            ),
            None,
        )
        if node is None:
            raise KeyError("Resident is not attached to its Lineage")
        if _text(node.get("title"), 120) == next_title:
            return lineage
        node["title"] = next_title
        node["resident_title"] = next_title
        _persist_lineage_state(
            previous,
            lineage,
            "重命名 Resident",
        )
        return lineage


def _synchronize_canonical_resident_titles(
    previous: dict[str, Any],
    lineage: dict[str, Any],
    actor: str,
) -> None:
    """Commit HATG title edits through the exact Resident authority."""

    nodes = [node for node in lineage.get("nodes") or [] if isinstance(node, dict)]
    previous_nodes = {
        _node_resident_id(node): node
        for node in previous.get("nodes") or []
        if isinstance(node, dict) and _node_resident_id(node)
    }
    records = _resident_targets([_node_resident_id(node) for node in nodes])
    for node in nodes:
        resident_id = _node_resident_id(node)
        record = records.get(resident_id)
        title = _text(node.get("title"), 120)
        previous_node = previous_nodes.get(resident_id)
        previous_title = _text(
            previous_node.get("title") if previous_node else "",
            120,
        )
        if (
            not record
            or not title
            or not previous_node
            or title == previous_title
        ):
            continue
        canonical_title = _text(record.get("title"), 120)
        if title != canonical_title:
            renamed = _rename_resident_lineage_node(node, title, actor)
            if isinstance(renamed, dict):
                title = _text(renamed.get("title"), 120) or title
        node["title"] = title
        node["resident_title"] = title


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
    store = _lineage_catalog_store().state_store
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
            projection = _read_lineage_state(lineage_id)
            if committed_revision is None and covered >= int(projection["revision"]):
                return
            if actor:
                _ensure_manager(_read_lineage(lineage_id, actor=actor), actor)
        _sync_lineage_resource_identity(projection)
        with store.lineage_resource_projection.transaction(lineage_id) as record:
            record["revision"] = max(int(record.get("revision") or 0), int(projection["revision"]))



def retry_lineage_projections(
    failures: list[dict[str, Any]], actor: str
) -> None:
    from . import resource_client

    for failure in failures:
        if failure.get("projection") == "lineage.system_workflow_switches":
            from . import system_workflow_switches
            system_workflow_switches.project_after_commit(
                str(failure.get("lineage_id") or ""),
                known_keys=tuple(failure.get("known_keys") or ()),
            )
            continue
        if failure.get("projection") == "project.principal":
            provision_project_principal(str(failure.get("project_id") or ""), actor)
            continue
        if failure.get("projection") == "resident.resource_bundle":
            resident_domain.confirm_resident_registration(
                str(failure.get("resident_id") or ""), actor,
            )
            continue
        if failure.get("projection") == "resident.resource_tombstone":
            resident_domain.confirm_resident_deletion_projection(
                str(failure.get("resident_id") or "")
            )
            continue
        if failure.get("projection") != "lineage.resource_identity":
            raise ValueError("未知 Lineage 投影")
        lineage_id = str(failure.get("lineage_id") or "")
        _ensure_manager(_read_lineage(lineage_id, actor=actor), actor)
        project_committed(
            failure,
            # Execution receipts keep references only, so the revision may be
            # gone; then the retry covers whatever is committed now.
            lambda lineage_id=lineage_id, revision=failure.get("committed_revision"): (
                _publish_lineage_resource_projection(
                    lineage_id, None if revision is None else int(revision), actor=actor,
                )
            ),
        )


def _sync_deleted_lineage_resource_identity(
    lineage: dict[str, Any],
    actor: str,
    deleted_at: str,
) -> None:
    tombstone = {
        **lineage,
        "deleted_at": deleted_at,
        "deleted_by": actor,
        "updated_at": deleted_at,
        "updated_by": actor,
    }
    _sync_lineage_resource_identity(tombstone)


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
    from . import lineage_automation_identity
    lineage_automation_identity.requested(payload, previous, actor)
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
        lineage_program.resident_completion_subscriptions(plan, nodes)
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
            lineage_program_runtime
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
                lineage_program_runtime
                .fence_event_source_contract_preserving_fire_budget(
                    lineage_id,
                    event_source_id,
                )
            )
        else:
            lineage_program_runtime.forget_event_source(
                lineage_id,
                event_source_id,
                preserve_pause=event_source_id in {
                    str(component.get("id") or "")
                    for component in ((current_lineage or {}).get("program") or {}).get("components") or []
                },
            )
    for wire_id in sorted(impact.get("invoke_wire_ids") or set()):
        lineage_program_runtime.forget_event_wire(
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
    _lineage_catalog_store().state_store.transaction(
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
        persisted, merged = _committed_definition(
            previous, lineage, domain, resident_references=resident_references,
        )
        _lineage_catalog_store().upsert(persisted)
        if merged:
            _adopt_committed_definition(lineage, persisted)
        else:
            for field in lineage_definition_merge.STAMP_FIELDS:
                lineage[field] = persisted[field]
        if (lineage.get("principal_hierarchy") or {}).get("level") == "project":
            store = _lineage_catalog_store().state_store
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
    if runtime_state_changed:
        if domain == lineage_state_domains.RUNTIME and previous is not None:
            try:
                merged_runtime = _lineage_catalog_store().put_runtime_cas(
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
            merged_runtime = _lineage_catalog_store().state_store.put_lineage_runtime_changes(
                lineage_id,
                lineage_state_domains.persisted_runtime_state(previous),
                runtime_candidate,
            )
            lineage["runtime_revision"] = int(merged_runtime.get("runtime_revision") or 0)
            lineage["runtime_updated_at"] = str(merged_runtime.get("updated_at") or "")
        else:
            _lineage_catalog_store().put_runtime(
                lineage_id,
                runtime_candidate,
            )
    if definition_changed:
        from . import system_workflow_switches
        system_workflow_switches.defer_definition_projection(lineage_id, previous, lineage)
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
        path=_lineage_catalog_store().storage_identity,
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
    store = _lineage_catalog_store().state_store
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


def _rename_resident_lineage_node(node: dict[str, Any], title: str, actor: str) -> object:
    return resident_domain.rename_resident_identity(
        _node_resident_id(node),
        actor,
        title,
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


def _lineage_summary(
    lineage: dict[str, Any],
    *,
    graph_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if graph_summary is None:
        statuses: dict[str, int] = {}
        for node in lineage.get("nodes") or []:
            status = str(node.get("status") or "idle")
            statuses[status] = statuses.get(status, 0) + 1
        graph_summary = {
            "lineage_node_ids": [
                _node_resident_id(node)
                for node in lineage.get("nodes") or []
                if isinstance(node, dict) and _node_resident_id(node)
            ],
            "node_count": len(lineage.get("nodes") or []),
            "edge_count": len(lineage.get("edges") or []),
            "active_seed_count": sum(
                1
                for seed in (lineage.get("program") or {}).get("components") or []
                if isinstance(seed, dict)
                and str(seed.get("kind") or "") == "seed"
                and str((seed.get("config") or {}).get("status") or "active") == "active"
            ),
            "statuses": statuses,
        }
    return {
        "lineage_id": str(lineage.get("lineage_id") or ""),
        **({"principal_hierarchy": _clone_json(lineage["principal_hierarchy"])} if "principal_hierarchy" in lineage else {}),
        "lineage_membership": _lineage_membership(lineage),
        "project_id": _text(lineage.get("project_id"), 120),
        "title": str(lineage.get("title") or ""),
        "description": str(lineage.get("description") or ""),
        "leader_resident_id": str(lineage.get("leader_resident_id") or ""),
        **graph_summary,
        "revision": int(lineage.get("revision") or 0),
        "layout_revision": int(lineage.get("layout_revision") or 0),
        "runtime_revision": int(lineage.get("runtime_revision") or 0),
        "created_at": str(lineage.get("created_at") or ""),
        "created_by": str(lineage.get("created_by") or ""),
        "updated_at": str(lineage.get("updated_at") or ""),
        "graph_updated_at": str(
            lineage.get("graph_updated_at")
            or lineage.get("updated_at")
            or ""
        ),
        "layout_updated_at": str(lineage.get("layout_updated_at") or ""),
        "runtime_updated_at": str(lineage.get("runtime_updated_at") or ""),
    }


def get_lineage_program_resource_projection(
    lineage_id: str, suffix: str,
) -> dict[str, Any]:
    namespace, item_id = lineage_program.parse_program_resource_suffix(suffix)
    definition, _runtime = _lineage_catalog_store().get_state(lineage_id)
    _, _, item = lineage_program.find_program_resource(
        definition, suffix,
    )
    return {
        "schema": "tfi.lineage-program-resource.v1",
        "resource_id": lineage_id,
        "namespace": namespace,
        "item_id": item_id,
        "revision": definition.get("revision", 0),
        "item": item,
    }


def get_family_resource_projection(resource_id: str) -> dict[str, Any]:
    identity_contract.validate_id(resource_id, "family")
    from . import family_storage
    with _lineage_catalog_store().state_store.database.snapshot() as connection:
        records = family_storage.resource_records(connection, resource_ids=[resource_id])
    if not records or records[0].get("deleted_at"):
        raise KeyError(resource_id)
    return {"schema": "tfi.family-resource.v1", **records[0]}


def get_lineage_resource_projection(lineage_id: str) -> dict[str, Any]:
    clean_id = str(lineage_id or "").strip()
    lineage = _read_lineage_state(clean_id)
    return lineage_view_projection.resource_projection(
        clean_id, lineage, _lineage_membership(lineage)
    )


def _lineage_read_fingerprint(lineage: dict[str, Any]) -> tuple[tuple[str, int, int], ...]:
    return lineage_read_cache.definition_fingerprint(
        lineage,
        seed_folder=_seed_folder,
        node_resident_id=_node_resident_id,
    )


def _lineage_read_cache_key(lineage_id: str, actor: str) -> tuple[str, str, str]:
    return lineage_read_cache.cache_key(DATA_ROOT, lineage_id, actor)


def invalidate_lineage_read_cache(lineage_id: str) -> None:
    """Invalidate projections whose canonical Resident facts may have changed."""

    target = str(lineage_id or "").strip()
    if not target:
        return
    resident_domain.invalidate_lineage_access_projection(target)
    _LINEAGE_READ_CACHE.invalidate(target)


def _lineage_snapshot_for_read(
    lineage_id: str,
    actor: str,
) -> dict[str, Any]:
    """Capture canonical Lineage facts and add the authenticated read projection."""

    actor_id = _safe_actor(actor)
    lineage = _clone_json(_read_lineage_state(lineage_id))
    lineage = business_fact_views.canonical_view(
        lineage,
        resource_id=lineage_id,
        actor=actor_id,
    )
    _ensure_visible(lineage, actor_id)
    return lineage


def get_lineage_definition_for_analysis(lineage_id: str, actor: str) -> dict[str, Any]:
    """Return an authorized definition without building the runtime UI view."""

    return _lineage_snapshot_for_read(lineage_id, actor)


def _read_projection_ports() -> lineage_read_cache.ReadProjectionPorts:
    return lineage_read_cache.ReadProjectionPorts(
        safe_actor=_safe_actor,
        snapshot_for_read=_lineage_snapshot_for_read,
        cache_key=_lineage_read_cache_key,
        fingerprint=_lineage_read_fingerprint,
        cache=_LINEAGE_READ_CACHE,
        clone_json=_clone_json,
        project_titles=_project_canonical_resident_titles,
        verified_view=_verified_lineage_view,
    )


def _current_lineage_view_for_read(lineage_id: str, actor: str) -> tuple[dict[str, Any], dict[str, Any]]:
    return lineage_read_cache.current_lineage_view_for_read(lineage_id, actor, ports=_read_projection_ports())


def list_lineages(actor: str) -> dict[str, Any]:
    actor_id = _safe_actor(actor)
    can_manage = _actor_can_manage_lineages(actor_id)
    catalog = _lineage_catalog_store()
    hydrated = business_fact_views.canonical_views(
        catalog.listing_records(),
        id_field="lineage_id",
        actor=actor_id,
    )
    visible = [
        payload for payload in hydrated
        if _actor_can_manage_lineage(payload, actor_id, operation="read")
    ] if can_manage else []
    graph_summaries = catalog.listing_graph_summaries([
        str(payload["lineage_id"]) for payload in visible
    ])
    owners = {
        str(payload["lineage_id"]): payload.get("created_by") if lineage_access_policy.is_personal_lineage(payload) else ""
        for payload in visible
    }
    # Each button asks only its own instruction (decision_b820faf42218 §3.1).
    passing = {name: lineage_access_policy.changeable_ids(actor_id, owners, name, "lineage_id") for name in LINEAGE_ACTIONS}
    lineages = [
        {**_lineage_summary(payload, graph_summary=graph_summaries[str(payload["lineage_id"])]),
         "allows": {name: str(payload["lineage_id"]) in ids for name, ids in passing.items()}}
        for payload in visible
    ]
    lineages.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return {"schema_version": SCHEMA_VERSION, "lineages": lineages}


# Read by the Resident Context service over the SocietyService socket.


def list_resident_discovery_catalog(actor: str) -> dict[str, Any]:
    """Return active registered Residents that can take work to an indexer."""

    actor_id = _safe_actor(actor)
    if not get_portal_access_store().is_service_actor(actor_id):
        raise PermissionError("Resident Discovery catalog requires a service actor")
    records: dict[str, dict[str, Any]] = {}
    for catalog_item in _lineage_catalog_store().snapshot():
        lineage_id = str(catalog_item.get("lineage_id") or "").strip()
        if not lineage_id:
            continue
        try:
            lineage = _read_lineage_state(lineage_id)
        except (KeyError, ValueError):
            continue
        lineage_title = str(lineage.get("title") or lineage_id)
        for node in lineage.get("nodes") or []:
            if not isinstance(node, Mapping):
                continue
            resident_id = _node_resident_id(node)
            if not resident_id:
                continue
            records[resident_id] = {
                "resident_id": resident_id,
                "lineage_id": lineage_id,
                "lineage_title": lineage_title,
                "lineage_description": str(lineage.get("description") or ""),
                "project_id": str(lineage.get("project_id") or ""),
                "title": str(node.get("title") or resident_id),
                "origin_seed_id": str(node.get("origin_seed_id") or ""),
                "status": str(node.get("status") or ""),
                "backend": str(node.get("backend") or ""),
                "created_at": str(node.get("created_at") or ""),
                "updated_at": str(node.get("updated_at") or ""),
                "lineage_revision": int(lineage.get("revision") or 0),
                "lineage_refresh_signature": str(lineage.get("updated_at") or ""),
            }
    active_ids = {
        record["resident_id"]
        for record in resident_catalog.get_catalog().get_many(records)
        if resident_catalog.registration_ready(record)
    }
    return {
        "schema": "tfi.resident-discovery-catalog.v1",
        "residents": [records[key] for key in sorted(records) if key in active_ids],
    }


def list_removed_discovery_residents(actor: str, resident_ids: list[str]) -> dict[str, Any]:
    """Of ``resident_ids``, those whose removal is recorded.

    A Resident is removed once it or its Lineage is deleted, or no record of it
    remains. One only missing from the catalog because its Lineage could not
    be read is not removed.
    """

    actor_id = _safe_actor(actor)
    if not get_portal_access_store().is_service_actor(actor_id):
        raise PermissionError("Resident Discovery catalog requires a service actor")
    requested = sorted({str(item or "").strip() for item in resident_ids or [] if str(item or "").strip()})
    live = resident_catalog.get_catalog().state_store.live_resident_ids(requested)
    return {
        "schema": "tfi.resident-discovery-removed.v1",
        "resident_ids": [resident_id for resident_id in requested if resident_id not in live],
    }


def list_principal_hierarchy(actor: str) -> dict[str, Any]:
    from .principal_hierarchy_domain import navigation

    return navigation(actor)


def ensure_personal_ama(username: str, actor: str) -> dict[str, Any]:
    from .principal_hierarchy_domain import ensure_personal_ama as ensure

    return ensure(username, actor)


def delete_personal_ama(username: str, actor: str) -> dict[str, Any]:
    from .principal_hierarchy_domain import delete_personal_ama as delete

    return delete(username, actor)


def ensure_personal_assistant(username: str, ama_id: str, actor: str) -> dict[str, Any]:
    from .personal_assistant import ensure
    return ensure(username, ama_id, actor)


def provision_project_principal(project_id: str, actor: str) -> dict[str, Any]:
    from .principal_hierarchy_domain import ensure_project
    return ensure_project(project_id, actor)


def list_lineage_human_notifications(
    actor: str,
    lineage_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Return actor-visible Lineage notification facts without UI metadata."""

    actor_id = _safe_actor(actor)
    requested = (
        {
            _text(lineage_id, 80).lower()
            for lineage_id in lineage_ids
            if _text(lineage_id, 80)
        }
        if lineage_ids is not None
        else None
    )
    from .lineage_human_notifications import list_actor_notifications

    definitions = list_actor_notifications(actor_id, requested)
    portal_level = [item for item in definitions if not item.get("lineage_id")]
    visible = business_fact_views.canonical_views(
        [item for item in definitions if item.get("lineage_id")],
        id_field="lineage_id",
        actor=actor_id,
    )
    result: list[dict[str, Any]] = []
    for lineage in [*visible, *portal_level]:
        lineage_id = str(lineage.get("lineage_id") or "").strip()
        notifications = lineage["notifications"]
        if notifications:
            result.append({
                "lineage_id": lineage_id,
                "title": str(lineage.get("title") or ""),
                "notifications": notifications,
            })
    return {"lineages": result}


def actor_lineage_access_records(
    actor: str,
    lineage_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return the Lineage membership facts authorized for one actor."""

    actor_id = _safe_actor(actor)
    catalog = _lineage_catalog_store()
    definitions = (
        catalog.snapshot()
        if lineage_ids is None
        else catalog.get_access_records(lineage_ids)
    )
    records: list[dict[str, Any]] = []
    for lineage in definitions:
        lineage_id = str(lineage.get("lineage_id") or "")
        if not _actor_can_manage_lineage(lineage, actor_id, operation="read"):
            continue
        node_ids = (
            list(dict.fromkeys(
                str(item or "").strip()
                for item in lineage.get("lineage_node_ids") or []
                if str(item or "").strip()
            ))
            if lineage_ids is not None
            else list(dict.fromkeys(
                _node_resident_id(node)
                for node in lineage.get("nodes") or []
                if isinstance(node, Mapping) and _node_resident_id(node)
            ))
        )
        records.append({
            "lineage_id": lineage_id,
            "lineage_membership": _lineage_membership(lineage),
            "project_id": _text(lineage.get("project_id"), 120),
            "lineage_node_ids": node_ids,
            "leader_resident_id": str(
                lineage.get("leader_resident_id") or ""
            ).strip(),
        })
    return records


def get_lineage_monitor_definition(lineage_id: str, actor: str) -> dict[str, Any]:
    """Read authorized flow definitions without assembling the workbench view."""

    actor_id = _safe_actor(actor)
    lineage = _read_lineage(lineage_id, actor=actor_id)
    _ensure_visible(lineage, actor_id)
    program = lineage.get("program") or {}
    return {
        "lineage_id": lineage["lineage_id"],
        "title": lineage.get("title"),
        "revision": lineage.get("revision"),
        "nodes": [
            {"id": node.get("id"), "title": node.get("title")}
            for node in lineage.get("nodes") or [] if isinstance(node, dict)
        ],
        "program": deepcopy({
            "components": program.get("components") or [],
            "wires": program.get("wires") or [],
        }),
    }


def get_lineage_monitor_definitions(lineage_ids: list[str], actor: str) -> dict[str, Any]:
    """Read independent monitor definitions in one RPC/read-only snapshot."""

    actor_id = _safe_actor(actor)
    requested = list(dict.fromkeys(str(value).strip() for value in lineage_ids if str(value).strip()))
    definitions: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    if requested:
        # Existing canonical readers join this snapshot. Keep each Lineage's
        # access/deletion checks and isolate its domain validation errors.
        with _lineage_catalog_store().state_store.database.snapshot():
            for lineage_id in requested:
                try:
                    definitions[lineage_id] = get_lineage_monitor_definition(lineage_id, actor_id)
                except Exception as exc:  # noqa: BLE001 - independent read results
                    errors[lineage_id] = str(exc)
    return {"definitions": definitions, "lineage_errors": errors}


def get_lineage(lineage_id: str, actor: str) -> dict[str, Any]:
    _lineage, view = _current_lineage_view_for_read(
        lineage_id,
        actor,
    )
    invoke_runtime = _program_invoke_wire_runtime_view(view)
    view["program_invoke_wire_runtime"] = invoke_runtime
    event_runtime = _program_event_source_runtime_view(view)
    view["program_event_source_runtime"] = event_runtime
    # Live User availability is independent of the cached definition revision.
    from .lineage_program_runtime_projection import event_source_diagnostics
    view.setdefault("program_diagnostics", []).extend(event_source_diagnostics(event_runtime))
    view["program_effect_runtime"] = _program_effect_runtime_view(view)
    view["program_execution_explanations"] = (
        lineage_execution_explanation.explain_program(
            view,
            event_source_runtime=event_runtime,
            invoke_wire_runtime=invoke_runtime,
        )
    )
    view["refresh_signature"] = lineage_read_cache.definition_refresh_signature(view)
    return view


def _lineage_node_session_key(
    node: dict[str, Any],
) -> str:
    return str(node.get("id") or "")


def _node_user_message_count(node: dict[str, Any], actor: str) -> int:
    return max(0, int(resident_domain.get_resident_user_message_count(
        _lineage_node_session_key(node),
        actor,
    )))


def get_node_message_counts(lineage_id: str, actor: str) -> dict[str, Any]:
    actor_id = _safe_actor(actor)
    lineage = get_lineage(lineage_id, actor_id)
    session_keys = tuple(
        str(node.get("id") or "")
        for node in lineage.get("nodes") or []
        if isinstance(node, dict) and str(node.get("id") or "")
    )
    state_snapshots = resident_protocol.read_resident_state_snapshots(
        session_keys
    )
    fingerprint = (
        int(lineage.get("revision") or 0),
        tuple(
            (
                resident_id,
                int((state_snapshots.get(resident_id) or {}).get("cursor") or 0),
            )
            for resident_id in session_keys
        ),
    )
    cache_key = _lineage_read_cache_key(lineage_id, actor_id)
    with _LINEAGE_READ_CACHE.build_lock("message_counts", cache_key):
        cached = _LINEAGE_READ_CACHE.get("message_counts", cache_key, fingerprint)
        if cached:
            return _clone_json(cached["payload"])
        payload = _build_node_message_counts_payload(lineage_id, actor_id, lineage)
        _LINEAGE_READ_CACHE.put("message_counts", cache_key, {
            "fingerprint": fingerprint,
            "payload": _clone_json(payload),
        })
        return payload


def _build_node_message_counts_payload(
    lineage_id: str,
    actor_id: str,
    lineage: dict[str, Any],
) -> dict[str, Any]:
    nodes = [node for node in lineage.get("nodes") or [] if isinstance(node, dict)]
    session_keys = {_lineage_node_session_key(node) for node in nodes}
    resolved: dict[str, tuple[bool, int]] = {}
    context_pressures: dict[str, dict[str, Any]] = {}
    counts = resident_domain.get_resident_user_message_counts(list(session_keys), actor_id)
    context_pressures = resident_domain.get_resident_context_pressures(
        list(session_keys),
        actor_id,
    )
    resolved = {
        key: (key in counts, max(0, int(counts.get(key) or 0)))
        for key in session_keys
    }
    sessions: list[dict[str, Any]] = []
    for node in nodes:
        key = _lineage_node_session_key(node)
        if key not in resolved:
            try:
                resolved[key] = (True, _node_user_message_count(node, actor_id))
            except (KeyError, PermissionError, ValueError):
                resolved[key] = (False, 0)
        available, user_message_count = resolved[key]
        sessions.append({
            "resident_id": str(node.get("id") or ""),
            "user_message_count": user_message_count,
            "available": available,
            "context_pressure": _clone_json(context_pressures.get(key) or {}),
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "lineage_id": str(lineage.get("lineage_id") or lineage_id),
        "sessions": sessions,
    }


def get_lineage_node_metadata(
    lineage_id: str,
    resident_id: str,
    actor: str,
) -> dict[str, Any]:
    """Authorize and return the lightweight session projection for one node."""

    actor_id = _safe_actor(actor)
    target_id = _text(resident_id, 80).lower()
    try:
        # Node membership belongs to the immutable Lineage definition.  A
        # committed PostgreSQL read is sufficient and must not queue behind an
        # unrelated aggregate writer or materialize runtime state.
        lineage = _lineage_catalog_store().get(lineage_id)
    except KeyError:
        raise KeyError("Lineage not found") from None
    node = next(
        (
            item
            for item in lineage.get("nodes") or []
            if isinstance(item, dict)
            and str(item.get("id") or "") == target_id
        ),
        None,
    )
    if node is None:
        raise KeyError("Lineage node not found")
    if not target_id:
        raise ValueError("Lineage node is not bound to a Resident session")

    # The visibility check may call ResourceService; its socket must not
    # extend the lifetime of the Lineage aggregate lock.
    _ensure_visible(lineage, actor_id)


    detail = resident_domain.get_lineage_node_metadata(
        target_id,
        actor_id,
    )
    detail["lineage_id"] = lineage_id
    detail["lineage_resident_id"] = target_id
    return detail


def _new_lineage_base(
    lineage_id: str,
    title: str,
    description: str,
    actor: str,
) -> dict[str, Any]:
    return lineage_graph_authoring.new_lineage_base(
        lineage_id, title, description, actor,
        schema=LINEAGE_FOLDER_SCHEMA,
        schema_version=SCHEMA_VERSION,
        now=_now_iso(),
    )


def _create_lineage_in_scope(
    payload: dict[str, Any],
    actor_id: str,
    lineage_id: str,
) -> dict[str, Any]:
    if not identity_contract.is_valid_id(lineage_id, "lineage"):
        raise ValueError("invalid Lineage id")
    title = _text(payload.get("title"), 140) or "未命名 Lineage"
    lineage_dir = _lineage_dir(lineage_id)
    base = _new_lineage_base(
        lineage_id,
        title,
        _text(payload.get("description"), 3000),
        actor_id,
    )
    lineage = _normalize_lineage_payload(payload, base, actor_id)
    project_id = _text(lineage.get("project_id"), 120)
    if _lineage_membership(lineage) == "formal" and not project_id:
        raise ValueError("Lineage 必须属于 Project")
    pass
    physical_dir = _lineage_project_folder(
        lineage_id,
        project_id,
        actor_id,
    )
    physical_dir.mkdir(parents=True, exist_ok=False)
    try:
        if physical_dir != lineage_dir:
            lineage_storage.bind_lineage_folder(
                lineage_id,
                physical_dir,
                folder_root=_lineages_root(),
            )
        _persist_lineage_state(None, lineage, "创建 Lineage")
    except Exception:
        if physical_dir != lineage_dir:
            lineage_storage.unbind_lineage_folder(
                lineage_id,
                folder_root=_lineages_root(),
                expected_target=physical_dir,
            )
        shutil.rmtree(physical_dir, ignore_errors=True)
        raise
    return lineage


def create_lineage(payload: dict[str, Any], actor: str) -> dict[str, Any]:
    actor_id = _safe_actor(actor)
    for _ in range(20):
        lineage_id = identity_contract.new_id("lineage")
        with _lineage_execution_authority(lineage_id):
            with _lineage_scope(lineage_id):
                if _lineage_dir(lineage_id).exists():
                    continue
                lineage = _create_lineage_in_scope(
                    payload,
                    actor_id,
                    lineage_id,
                )
                plan = _compiled_program(lineage)
                completion_baselines = (
                    _resident_completion_baseline_requests(
                        lineage,
                        {
                            "event_source_ids": set(
                                (plan.get("event_sources") or {}).keys()
                            ),
                            "invoke_wire_ids": set(
                                _program_invoke_wire_execution_configs(
                                    plan
                                )
                            ),
                        },
                    )
                )
            _establish_resident_completion_baselines(
                lineage_id,
                actor_id,
                completion_baselines,
            )
            return lineage
    raise RuntimeError("无法生成 Lineage id")


def _node_from_resident(
    record: dict[str, Any],
    *,
    initial_work_state: object = None,
    x: object = 20,
    y: object = 35,
) -> dict[str, Any]:
    resident_id = _text(record.get("resident_id"), 80).lower()
    if not identity_contract.is_valid_id(resident_id, "resident"):
        raise ValueError("Resident 注册结果缺少规范的 resident_id")
    title = _text(record.get("title"), 120) or resident_id
    work_state = initial_work_state
    if work_state is None:
        work_state = resident_protocol.read_resident_states([resident_id]).get(
            resident_id,
            {},
        ).get("work_state", "idle")
    return {
        "id": resident_id,
        "title": title,
        "status": _lineage_node_status(work_state),
        "x": _position(x, 20),
        "y": _position(y, 35),
        "backend": _text(record.get("backend"), 40) or "codex",
        "created_at": _text(record.get("created_at"), 80) or _now_iso(),
        "updated_at": _text(record.get("updated_at"), 80) or _now_iso(),
        "last_execution": {},
    }


def _new_lineage_node_position(
    lineage: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[float, float]:
    """Resolve a spatial anchor only; creation does not imply a relationship."""
    near_id = _text(payload.get("near_resident_id"), 80).lower()
    nodes = lineage.get("nodes") or []
    if near_id:
        anchor = _lineage_node(lineage, near_id)
    else:
        anchor = next(
            (node for node in nodes if node.get("id") == lineage.get("leader_resident_id")),
            nodes[0] if nodes else {"x": 20, "y": 35},
        )
    return lineage_placement.node_position(lineage, anchor, payload, child=payload)


def initialize_resident_lineage(
    record: dict[str, Any], actor: str,
) -> dict[str, Any]:
    """Initialize one newly registered Resident as a complete HATG Lineage.

    The caller owns Resident registration serialization.  This helper is only
    valid for a newly allocated canonical Lineage identity that does not yet
    exist, so it does not acquire the Lineage mutation lock.
    """
    actor_id = _safe_actor(actor)
    lineage_id = _text(record.get("lineage_id"), 80).lower()
    if not identity_contract.is_valid_id(lineage_id, "lineage"):
        raise ValueError("Resident 缺少规范的 lineage_id")
    try:
        _lineage_catalog_store().get(lineage_id)
    except KeyError:
        pass
    else:
        raise FileExistsError("Lineage 已存在，不能作为新 Resident Lineage 重新初始化")

    node = _node_from_resident(
        record,
        initial_work_state="idle",
    )
    base = _new_lineage_base(
        lineage_id,
        _text(record.get("lineage_title") or record.get("title"), 140),
        f"由 Lineage Workbench 创建的 Resident Lineage：{node['title']}",
        actor_id,
    )
    lineage_membership = str(
        record.get("lineage_membership") or "formal"
    ).strip().lower()
    project_id = _text(
        record.get("lineage_project_id")
        or record.get("project_id"),
        120,
    )
    if (
        lineage_membership == "formal"
        and not project_id
    ):
        raise ValueError("Resident Lineage 必须属于 Project")
    payload = {
        **base,
        "lineage_membership": lineage_membership,
        "project_id": project_id,
        "leader_resident_id": node["id"],
        "nodes": [node],
    }
    lineage = _normalize_lineage_payload(
        payload,
        base,
        actor_id,
        resident_references={
            node["id"]: {
                "resident_id": node["id"],
            },
        },
    )
    hierarchy_level = (record.get("principal_hierarchy") or {}).get("level")
    if hierarchy_level == "user" and lineage_membership == "personal":
        lineage["principal_hierarchy"] = {"level": "user", "scope_id": lineage_id}
    if hierarchy_level == "project":
        lineage["principal_hierarchy"] = {"level": "project", "scope_id": project_id}
        from .principal_hierarchy import project_leaders
        from .lineage_member_projection import project_resident_nodes
        references = project_leaders(lineage, _lineage_catalog_store().listing_records())
        project_resident_nodes(lineage, [record, *resident_catalog.get_catalog().get_many(list(references))])
    _persist_lineage_state(None, lineage, "创建 Resident Lineage")
    return lineage


def _append_registered_resident_node_locked(
    lineage: dict[str, Any],
    record: dict[str, Any],
    actor: str,
    *,
    position: tuple[float, float] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    resident_id = _text(record.get("resident_id"), 80).lower()
    existing = next(
        (
            item for item in lineage.get("nodes") or []
            if isinstance(item, dict) and str(item.get("id") or "") == resident_id
        ),
        None,
    )
    previous = _clone_json(lineage)
    body = _clone_json(lineage)
    node_created = not isinstance(existing, dict)
    if node_created:
        node_x, node_y = position or _new_lineage_node_position(lineage, {})
        node = _node_from_resident(
            record,
            x=node_x,
            y=node_y,
        )
        body.setdefault("nodes", []).append(node)
    else:
        return lineage, existing
    # The Resident authority is publishing this identity in the same write
    # transaction as membership. Committed readers cannot see it yet; use its
    # exact bounded projection, as initial Lineage publication already does.
    # Every other node still resolves through the canonical catalog.
    normalized = _normalize_lineage_payload(
        body, previous, actor,
        resident_references={resident_id: dict(record)},
    )
    _persist_lineage_state(
        previous,
        normalized,
        (
            f"新增 Resident 节点：{node['title']}"
            if node_created
            else f"同步 Resident 节点：{node['title']}"
        ),
        resident_references={resident_id: dict(record)},
    )
    created = next(
        item for item in normalized.get("nodes") or []
        if isinstance(item, dict) and str(item.get("id") or "") == resident_id
    )
    return normalized, created


def add_registered_resident_node(
    record: dict[str, Any],
    actor: str,
    *,
    node_options: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Add an already-created Resident to its managed HATG when one exists."""
    lineage_id = _text(record.get("lineage_id"), 80).lower()
    if not identity_contract.is_valid_id(lineage_id, "lineage"):
        return None
    expected_folder = _lineage_dir(lineage_id).resolve(strict=False)
    actual_folder = Path(str(record.get("workspace_path") or "")).expanduser().resolve(strict=False)
    if actual_folder != expected_folder:
        return None
    try:
        _lineage_catalog_store().get(lineage_id)
    except KeyError:
        return None
    with _lineage_scope(lineage_id):
        lineage = _read_lineage(lineage_id, actor=actor)
        _ensure_manager(lineage, actor)
        if node_options is not None:
            if str(record.get("project_id") or "") != str(lineage.get("project_id") or ""):
                raise ValueError("Lineage Project changed during Resident preparation; retry")
        if not isinstance(lineage.get("nodes"), list) or not isinstance(lineage.get("edges"), list):
            return None
        updated, _node = _append_registered_resident_node_locked(
            lineage,
            record,
            actor,
            position=_new_lineage_node_position(lineage, node_options) if node_options is not None else None,
        )
        return updated


def _resident_move_patch(
    source: dict[str, Any], resident_id: str, *, allow_empty_source: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = _resident_move_plan(source, resident_id, allow_empty_source=allow_empty_source)
    return plan["node"], plan["patch"]


def _moving_family_member(source: dict[str, Any], resident_id: str) -> dict[str, Any] | None:
    """The Family a moving Resident leaves; refuses its facade, Router, steward and personal AMA.

    A member leaves its Family as it moves (decision_44cf587c1721): the Family
    keeps its roles and other members, and its generated structure follows
    the roster as when a member is deleted.
    """

    from .family_familize import _role, family_blocks, member_ids

    names = {"facade": "门面", "router": "Router", "steward": "steward"}
    for block in family_blocks(source):
        title = str(block["family"].get("title") or block.get("title") or block["id"])
        role = next((role for role in names if _role(block, role) == resident_id), "")
        if role:
            raise ValueError(
                f"这个 Resident 是 Family「{title}」的{names[role]}，随 Family 一起存在，不能单独迁移；"
                "可以迁移整个 Family，或先解除 Family。没有做任何修改"
            )
        if resident_id not in member_ids(block):
            continue
        if block["family"].get("personal_ama_id") == resident_id:
            raise ValueError("这是个人助手的默认 AMA，不能迁出私人 Lineage。没有做任何修改")
        return block
    return None


def _resident_move_plan(
    source: dict[str, Any], resident_id: str, *, allow_empty_source: bool = False,
) -> dict[str, Any]:
    """Check one Resident's move and describe it: node, source patch, Family left, relations detached."""

    if not isinstance(allow_empty_source, bool):
        raise ValueError("allow_empty_source must be a boolean")
    allow_empty_source = allow_empty_source and [node["id"] for node in source.get("nodes") or []] == [resident_id]
    if allow_empty_source:
        program = source.get("program") or {}
        if source.get("edges") or source.get("function_blocks") or any(program.get(key) for key in (
            "wires", "components",
        )):
            raise ValueError("allow_empty_source requires a source without HATG flows")
        runtime = lineage_program_runtime.read_runtime(source["lineage_id"])
        if any(item.get("status") not in {"completed", "failed", "cancelled"}
               for item in lineage_program_runtime.pending_resident_handoffs(source["lineage_id"])) or any(
            state.get("pending_events") for state in (runtime.get("event_wires") or {}).values()
        ) or any(state.get("pending_deliveries") or state.get("inbox")
                 for state in (runtime.get("operators") or {}).values()):
            raise ValueError("allow_empty_source requires a source without pending HATG work")
    family = _moving_family_member(source, resident_id)
    if not allow_empty_source and resident_id == source.get("leader_resident_id"):
        raise ValueError("这个 Resident 是源 Lineage 的 Leader，不能迁移；请先指定新的 Leader。没有做任何修改")

    def references(value: Any) -> bool:
        if isinstance(value, dict):
            return any(references(item) for item in value.values())
        if isinstance(value, list):
            return any(references(item) for item in value)
        return value == resident_id

    # The Family's own block and generated wiring follow its roster; a
    # workflow the Family collected still names the member and must be
    # migrated or disconnected first.
    owned: set[str] = set()
    if family is not None:
        from . import family_workflows

        owned = {family["id"]} | {
            element["id"] for element in family.get("elements") or [] if family_workflows.is_generated(element)
        }
    program = source.get("program") or {}
    collections = {"function_blocks": source.get("function_blocks") or []}
    collections.update({key: program.get(key) or [] for key in (
        "wires", "components",
    )})
    blockers = [
        f"{kind}/{item.get('id', '')}"
        for kind, items in collections.items()
        for item in items if item.get("id") not in owned and references(item)
    ]
    # Relations record where a Resident came from; like MoveLineageObjects,
    # the move detaches them without reconnecting the source graph.
    detached = [
        {"id": str(edge.get("id") or ""), "source": str(edge.get("source") or ""),
         "target": str(edge.get("target") or ""), "type": str(edge.get("type") or "")}
        for edge in source.get("edges") or []
        if resident_id in {edge.get("source"), edge.get("target")}
    ]
    node, patch = _resident_projection_removal_patch(source, resident_id)
    if node.get("operator_executor"):
        blockers.append(f"nodes/{resident_id}")
    if blockers:
        raise ValueError(
            "这个 Resident 仍被流程引用：" + "、".join(blockers)
            + "。请先迁移或断开这些流程；没有做任何修改"
        )
    return {"node": node, "patch": patch, "family": family, "detached": detached}


def move_lineage_subgraph(lineage_id: str, payload: dict[str, Any], actor: str) -> dict[str, Any]:
    target_id = str(payload.get("target_lineage_id") or "")
    if not target_id:
        source = _read_lineage(lineage_id, actor=actor)
        _ensure_manager(source, actor)
        # Validate the selection before publishing a destination identity.
        lineage_subgraph.plan(source, {"program": lineage_program.empty_program()}, payload.get("object_ids"), source_member_ids={
            record["resident_id"] for record in resident_catalog.get_catalog().for_lineage(lineage_id)
        })
        target = create_lineage({
            "title": f"{source.get('title') or lineage_id} · 迁出",
            "project_id": source.get("project_id"), "lineage_membership": source["lineage_membership"],
        }, actor)
        target_id = str(target["lineage_id"])
        try:
            return move_lineage_subgraph(lineage_id, {**payload, "target_lineage_id": target_id}, actor)
        except Exception:
            current_target = _read_lineage(target_id, actor=actor)
            if not current_target.get("nodes") and not (current_target.get("program") or {}).get("components"):
                delete_lineage(target_id, actor)
            raise
    identity_contract.validate_id(target_id, "lineage")
    if target_id == lineage_id:
        raise ValueError("请选择不同的目标 Lineage")
    # Match normal graph edits: execution fences precede graph scopes.
    with _lineage_execution_authorities([lineage_id, target_id]), _lineage_scopes(
        [lineage_id, target_id], operation="subgraph.move",
    ):
        source, target = _read_lineage(lineage_id, actor=actor), _read_lineage(target_id, actor=actor)
        _ensure_manager(source, actor)
        _ensure_manager(target, actor)
        plan = lineage_subgraph.plan(source, target, payload.get("object_ids"),
            source_member_ids={record["resident_id"]
                               for record in resident_catalog.get_catalog().for_lineage(lineage_id)})
        lineage_placement.place_moved_objects(target, plan["target"], set(plan["moved_ids"]))

        def commit_graphs(references):
            from . import graph_storage
            store = resident_catalog.get_catalog().state_store
            store.database.write(lambda c: graph_storage.transfer(c, lineage_id, target_id, list(plan["moved_ids"])))
            normalized_source = _normalize_lineage_payload(
                plan["source"], source, actor, reconnect_removed_inheritance=False,
            )
            normalized_target = _normalize_lineage_payload(plan["target"], target, actor, resident_references=references)
            lineage_program_runtime.migrate_objects(
                lineage_id, target_id, set(plan["moved_ids"]), dropped_wire_ids=set(plan["detached_wire_ids"]),
            )
            _persist_lineage_state(source, normalized_source, "迁出 HATG 子图")
            _persist_lineage_state(target, normalized_target, "迁入 HATG 子图")
            # Publishing Resident membership may have allocated initial layout
            # rows. The move owns the final positions, including hidden members;
            # commit them through the layout owner in this same transaction.
            destination = plan["target"]
            _lineage_catalog_store().update_layout(target_id, [
                {"object_id": item["id"], "x": item["x"], "y": item["y"]}
                for item in [*destination.get("nodes", []),
                             *destination["program"].get("components", []),
                             *destination.get("function_blocks", [])]
                if item["id"] in plan["moved_ids"] and "x" in item and "y" in item
            ], updated_at=_now_iso())
            invalidate_lineage_read_cache(target_id)

        operations = resident_domain.move_resident_identities(lineage_id, target_id, plan["resident_ids"], actor, commit_graphs)
        operation_id = _record_move_operation(lineage_id, target_id, plan, operations)
        return {key: value for key, value in plan.items() if key not in {"source", "target"}} | {
            "source_lineage_id": lineage_id, "target_lineage_id": target_id,
            "operations": operations, "workspace_pending": bool(operations),
            "operation_id": operation_id,
        }


def _record_move_operation(
    source_id: str, target_id: str, plan: dict[str, Any], operations: list[dict[str, Any]],
) -> str:
    """One trackable ID for a whole MoveLineageObjects batch, read by GetMoveOperation."""

    operation_id = "lineage-move_" + secrets.token_hex(12)
    with _lineage_catalog_store().state_store.lineage_move_operations.transaction(target_id, operation_id) as record:
        record.update({
            "operation_id": operation_id,
            "source_lineage_id": source_id,
            "target_lineage_id": target_id,
            "object_ids": list(plan.get("moved_ids") or []),
            "resident_operation_ids": [
                str(item.get("operation_id") or "") for item in operations if isinstance(item, dict)
            ],
            "committed_at": _now_iso(),
        })
    return operation_id


def move_lineage_resident(lineage_id: str, payload: dict[str, Any], actor: str) -> dict[str, Any]:
    resident_id = str(payload.get("resident_id") or "").strip()
    target_id = str(payload.get("target_lineage_id") or "").strip()
    identity_contract.validate_id(resident_id, "resident")
    identity_contract.validate_id(target_id, "lineage")
    if target_id == lineage_id:
        raise ValueError("MoveResident requires different source and target Lineages")
    baselines: list[dict[str, Any]] = []
    with _lineage_scopes([lineage_id, target_id], operation="resident.move"):
        source, target = _read_lineage(lineage_id, actor=actor), _read_lineage(target_id, actor=actor)
        _ensure_manager(source, actor)
        _ensure_manager(target, actor)
        allow_empty_source = payload.get("allow_empty_source", False)
        plan = _resident_move_plan(source, resident_id, allow_empty_source=allow_empty_source)
        family = plan["family"]
        # Leaving a Family changes its generated wiring; old Program work is
        # fenced as for any graph edit that changes the execution contract.
        with _lineage_execution_authority(lineage_id) if family is not None else nullcontext():
            result = resident_domain.update_resident(resident_id, actor, {
                "target_lineage_id": target_id,
                "allow_empty_source": allow_empty_source,
            })
            if family is not None:
                current = _read_lineage(lineage_id, actor=actor)
                baselines = _execute_program_contract_cleanup(
                    lineage_id, _program_execution_contract_change(source, current),
                    previous_lineage=source, current_lineage=current,
                )
    if family is not None:
        _establish_resident_completion_baselines(lineage_id, actor, baselines)
        from .templates.familize import member_moved_out
        try:
            member_moved_out(family, resident_id, target, actor)
        except Exception:  # The move has committed; a retry would fail on the moved Resident.
            LOGGER.exception("Family %s could not settle the move of member %s", family["id"], resident_id)
    return {
        "resident_id": resident_id,
        "source_lineage_id": lineage_id,
        "target_lineage_id": target_id,
        "operation_id": result["operation_id"],
        "workspace_pending": result["workspace_pending"],
        "family_id": family["id"] if family is not None else "",
        "detached": plan["detached"],
    }


def get_move_operation(operation_id: str, actor: str) -> dict[str, Any]:
    """Read one committed Resident move by its operation ID, without recovery or mutation."""
    from .resident_lifecycle import ResidentLifecycleOperations

    operation_id = str(operation_id or "").strip()
    if re.fullmatch(r"lineage-move_[0-9a-f]{24}", operation_id):
        return _get_batch_move_operation(operation_id, actor)
    if not re.fullmatch(r"resident-operation_[0-9a-f]{24}", operation_id):
        raise ValueError("operation_id must be a resident-operation or lineage-move ID")
    try:
        transfer = ResidentLifecycleOperations(_lineage_catalog_store().state_store).transfer(operation_id)
    except KeyError:
        raise KeyError("迁移操作不存在或已被回收") from None
    source_id = str(transfer.get("source_lineage_id") or "")
    target_id = str(transfer.get("target_lineage_id") or "")
    resident_id = str(transfer.get("resident_id") or "")
    identity_contract.validate_id(source_id, "lineage")
    identity_contract.validate_id(target_id, "lineage")
    identity_contract.validate_id(resident_id, "resident")
    _ensure_visible(_read_lineage(source_id, actor=actor), actor)
    _ensure_visible(_read_lineage(target_id, actor=actor), actor)
    return {
        **resident_domain.observe_committed_resident_transfer(operation_id, resident_id),
        "source_lineage_id": source_id,
        "target_lineage_id": target_id,
        "instruction_id": transfer["instruction_id"],
    }


def _get_batch_move_operation(operation_id: str, actor: str) -> dict[str, Any]:
    """A batch is complete once every Resident it moved has finished its workspace move."""

    store = _lineage_catalog_store().state_store
    target_id = store.lineage_move_operations.lineage_id(operation_id)
    if not target_id:
        raise KeyError("迁移操作不存在或已被回收")
    record = store.lineage_move_operations.get(target_id, operation_id) or {}
    source_id = str(record.get("source_lineage_id") or "")
    _ensure_visible(_read_lineage(source_id, actor=actor), actor)
    _ensure_visible(_read_lineage(target_id, actor=actor), actor)
    parts = [get_move_operation(item, actor) for item in record.get("resident_operation_ids") or [] if item]
    failed = next((part for part in parts if part.get("status") == "failed"), None)
    pending = next((part for part in parts if part.get("status") != "completed"), None)
    current = failed or pending
    return {
        "operation_id": operation_id,
        "source_lineage_id": source_id,
        "target_lineage_id": target_id,
        "object_ids": list(record.get("object_ids") or []),
        "phase": str(current.get("phase") or "") if current else "completed",
        "status": str(current.get("status") or "") if current else "completed",
        "error": (current or {}).get("error"),
        "resident_operations": parts,
    }


def move_resident_membership(
    source_lineage_id: str, record: dict[str, Any], actor: str, *, allow_empty_source: bool = False,
    publish_identity: Callable[[], None],
) -> None:
    """Commit both graph projections with Resident Domain's membership transaction.

    Resident Domain already holds both ordered graph scopes and the exact
    Resident scope. File transfer and external projections follow via outbox.
    """
    target_lineage_id = str(record["lineage_id"])
    source, target = _read_lineage(source_lineage_id, actor=actor), _read_lineage(target_lineage_id, actor=actor)
    _ensure_manager(source, actor)
    _ensure_manager(target, actor)
    node, patch = _resident_move_patch(
        source, str(record["resident_id"]), allow_empty_source=allow_empty_source,
    )
    # Capture and validate both aggregates before publishing the new home.
    # Afterwards the source's current membership already excludes this node.
    body = lineage_graph_authoring.graph_patch_candidate(source, patch)
    from .lineage_definition_scope import check_before_membership_write
    check_before_membership_write(_lineage_catalog_store().state_store, source_lineage_id)
    publish_identity()
    normalized = _normalize_lineage_payload(
        body, source, actor, reconnect_removed_inheritance=False,
    )
    _persist_lineage_state(source, normalized, "迁出 Resident")
    position = lineage_placement.node_position(target, node, {}, child=node)
    _append_registered_resident_node_locked(
        target, record, actor,
        position=position,
    )
    _lineage_catalog_store().update_layout(target_lineage_id, [
        {"object_id": record["resident_id"], "x": position[0], "y": position[1]},
    ], updated_at=_now_iso())
    invalidate_lineage_read_cache(target_lineage_id)


def create_lineage_resident(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Create a Resident Folder and HATG node under one canonical Lineage.

    ``creation_key`` makes a Portal service's frozen creation request
    idempotent: repeating it returns the Resident it already created.
    """
    creation_key = _text(payload.get("creation_key"), 160)
    if not creation_key:
        return _create_lineage_resident(lineage_id, payload, actor)
    store = _lineage_catalog_store().state_store
    # A session lock serializes the frozen request without delaying the domain
    # commit until after external Resource publication.
    lock_key = int.from_bytes(hashlib.sha256(
        f"{store.database.schema}:resident_creation:{lineage_id}:{creation_key}".encode()
    ).digest()[:8], "big", signed=True)
    with store.database.advisory_lock(lock_key):
        receipt = store.resident_creation_operations.get(lineage_id, creation_key)
        resident_id = str(receipt.get("resident_id") or "")
        if resident_id:
            updated = _read_lineage(lineage_id, actor=actor)
            _ensure_manager(updated, actor)
            if resident_id not in {str(node.get("id") or "") for node in updated.get("nodes") or []}:
                raise ValueError("该创建请求建立的 Resident 已移走或删除，请重新发起")
            try:
                resident = resident_domain.confirm_resident_registration(resident_id, actor)
                project_committed(
                    {"projection": "lineage.resource_identity", "lineage_id": lineage_id,
                     "committed_revision": int(updated["revision"])},
                    lambda: _publish_lineage_resource_projection(lineage_id, None, actor=actor),
                )
            except CommittedProjectionError as exc:
                exc.details["resident_creation"] = {
                    "lineage_id": lineage_id, "request_id": creation_key, "resident_id": resident_id,
                }
                raise
            return {"lineage": updated, "resident": resident,
                    "node": _lineage_node(updated, resident_id)}
        try:
            result = _create_lineage_resident(lineage_id, payload, actor)
        except CommittedProjectionError as exc:
            committed = store.resident_creation_operations.get(lineage_id, creation_key)
            exc.details["resident_creation"] = {
                "lineage_id": lineage_id, "request_id": creation_key,
                **({"resident_id": committed["resident_id"]} if committed.get("resident_id") else {}),
            }
            raise
        return result


def _create_lineage_resident(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    actor_id = _safe_actor(actor)
    with _lineage_scope(lineage_id):
        lineage = _read_lineage(lineage_id, actor=actor)
        _ensure_manager(lineage, actor_id)


        lineage_project_id = _text(lineage.get("project_id"), 120)
        if not lineage_project_id and _lineage_membership(lineage) != "personal":
            raise ValueError("Lineage 必须属于 Project")
        requested_project_id = _text(payload.get("project_id"), 120)
        if requested_project_id and requested_project_id != lineage_project_id:
            raise ValueError("Resident Project 必须与 Lineage 一致")

        _new_lineage_node_position(lineage, payload)
        registration = {
            key: value
            for key, value in payload.items()
            if key not in {
                "x",
                "y",
                "near_resident_id",
                "placement",
                "workspace_root",
                "creation_key",
            }
        }
        registration.update({
            # Identity and membership are published by registration's final
            # transaction; no temporary identity escapes this operation.
            "lineage_id": lineage_id,
            "project_id": lineage_project_id,
            "lineage_folder_path": str(_lineage_dir(lineage_id).resolve(strict=False)),
            "lineage_title": str(lineage.get("title") or ""),
        })
    record = resident_domain.register_resident(
        registration, actor_id,
        **({"creation_key": _text(payload.get("creation_key"), 160)} if payload.get("creation_key") else {}),
        lineage_node={key: payload[key] for key in (
            "x", "y",
            "near_resident_id", "placement",
        ) if key in payload},
    )
    with _lineage_scope(lineage_id):
        updated = _read_lineage(lineage_id, actor=actor)
        node = _lineage_node(updated, record["resident_id"])
    return {"lineage": updated, "resident": record, "node": node}


def update_lineage_graph_state(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Update one runtime State Cell without advancing graph revision; ``expected_value`` makes it a compare-and-set."""

    actor_id = _safe_actor(actor)
    lineage = _read_lineage(lineage_id, actor=actor_id)
    _ensure_manager(lineage, actor_id)
    from . import lineage_graph_state

    guard = {"expected_value": payload["expected_value"]} if "expected_value" in payload else {}
    return lineage_graph_state.update_cell(
        lineage_id,
        payload.get("key"),
        payload.get("value"),
        **guard,
    )


def get_lineage_graph_state(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Read the bounded runtime State snapshot or one exact Cell."""

    actor_id = _safe_actor(actor)
    lineage = _read_lineage(lineage_id, actor=actor_id)
    _ensure_visible(lineage, actor_id)
    from . import lineage_graph_state

    if payload.get("key") not in (None, ""):
        return lineage_graph_state.read_cell(
            lineage_id,
            payload.get("key"),
        )
    return lineage_graph_state.read_state(lineage_id)


def delete_lineage_graph_state(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Delete one runtime State Cell without advancing graph revision."""

    actor_id = _safe_actor(actor)
    lineage = _read_lineage(lineage_id, actor=actor_id)
    _ensure_manager(lineage, actor_id)
    from . import lineage_graph_state

    arguments: list[Any] = [
        lineage_id,
        payload.get("key"),
    ]
    if "expected_value" in payload:
        arguments.append(payload.get("expected_value"))
    return lineage_graph_state.delete_cell(*arguments)


def _notification_recipients(initiator: str) -> list[str]:
    """Follow work provenance, falling back only to a Resident's human creator."""
    from management_common.principal import actor_principal
    from management_common.operation_identity import current_operation_identity
    from .resident_work_origin import human_initiator
    if not initiator:
        return []
    principal = actor_principal(initiator)
    if principal.kind == 'user':
        return [principal.id]
    if principal.kind != 'resident':
        return []
    identity = current_operation_identity()
    if identity and identity.principal == principal and identity.initiated_by.kind == 'user':
        return [identity.initiated_by.id]
    human = human_initiator(principal.id)
    if human:
        return [human]
    try:
        creator = str(resident_catalog.get_catalog().get(principal.id).get('created_by') or '')
    except KeyError:
        return []
    return [creator] if creator and actor_principal(creator).kind == 'user' else []


def create_lineage_human_notification(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Store a durable notification; default recipients follow the initiator."""

    actor_id = _safe_actor(actor)
    recipients = [str(item) for item in payload.get("recipients") or [] if str(item or "").strip()]
    if lineage_id:
        lineage = _read_lineage(lineage_id, actor=actor_id)
        _ensure_visible(lineage, actor_id)
        if not recipients:
            recipients = _notification_recipients(actor_id)
    if not recipients:
        raise ValueError("通知需要明确的 recipients 或可解析的人类发起人")
    from . import lineage_human_notifications

    return lineage_human_notifications.create_notification(
        lineage_id,
        title=payload.get("title") or "通知",
        message=payload.get("message"),
        recipients=recipients,
        severity=payload.get("severity") or "info",
        trigger_path_id=payload.get("trigger_path_id") or "",
        created_by=actor_id,
        notification_id=payload.get("notification_id"),
    )


def _mutate_lineage_program(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
    mutate: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    reason: str,
    *,
    full_view: bool = True,
) -> dict[str, Any]:
    """Apply one edit to the program graph; it merges with concurrent edits at commit."""

    actor_id = _safe_actor(actor)
    contract_impact: dict[str, set[str]] = {}
    completion_baselines: list[dict[str, Any]] = []
    with _lineage_scope(lineage_id):
        previous = _read_lineage(lineage_id, actor=actor)
        _ensure_manager(previous, actor_id)
        body = _clone_json(previous)
        program = (
            body.get("program")
            if isinstance(body.get("program"), dict)
            else lineage_program.empty_program()
        )
        result = mutate(body, program)
        body["program"] = program
        _require_unchanged_membership(previous, body)
        normalized = _normalize_lineage_payload(
            body,
            previous,
            actor_id,
        )
        contract_impact = _program_execution_contract_change(
            previous,
            normalized,
        )
        # Contract fence, persistence, and transient cleanup are one atomic
        # state transition. A newer start must never be able to enqueue a job
        # between the generation bump and cleanup of the old generation.
        with _program_contract_authority(lineage_id, contract_impact):
            _persist_lineage_state(previous, normalized, reason, placement_request=payload)
            completion_baselines = _execute_program_contract_cleanup(
                lineage_id,
                contract_impact,
                previous_lineage=previous,
                current_lineage=normalized,
            )
    _establish_resident_completion_baselines(
        lineage_id,
        actor_id,
        completion_baselines,
    )
    response = {"lineage": get_lineage(lineage_id, actor_id) if full_view else normalized}
    response.update(result)
    return response


def _create_packet_operator_executor(
    lineage_id: str,
    *,
    component_id: str,
    kind: str,
    title: str,
    config: dict[str, Any],
    actor: str,
    x: object = None,
    y: object = None,
) -> dict[str, Any]:
    return create_lineage_resident(
        lineage_id,
        {
            "title": f"{title} · Resident 执行器",
            "x": x,
            "y": y,
        },
        actor,
    )


def create_lineage_program_component(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
    *,
    full_view: bool = True,
) -> dict[str, Any]:
    kind, component_id, title, requested_config = (
        lineage_program_authoring.new_component_details(payload)
    )
    effective_payload = dict(payload)
    executor_resident_id = ""
    if kind in lineage_program.PACKET_OPERATOR_KINDS:
        if requested_config["operator_implementation"] == "resident":
            created_executor = _create_packet_operator_executor(
                lineage_id,
                component_id=component_id,
                kind=kind,
                title=title,
                config=requested_config,
                actor=_safe_actor(actor),
                x=payload.get("x"),
                y=payload.get("y"),
            )
            executor_resident_id = str(
                (created_executor.get("node") or {}).get("id") or ""
            )
            requested_config["resident_executor_id"] = executor_resident_id

    mutate = partial(
        lineage_program_authoring.create_component,
        component_id=component_id,
        kind=kind,
        title=title,
        payload=payload,
        requested_config=requested_config,
        executor_resident_id=executor_resident_id,
    )

    response = _mutate_lineage_program(
        lineage_id,
        effective_payload,
        actor,
        mutate,
        "创建 HATG 程序组件",
        full_view=full_view,
    )
    component_id = response.pop("component_id")
    response["component"] = next(
        component
        for component in response["lineage"]["program"]["components"]
        if component["id"] == component_id
    )
    if executor_resident_id:
        response["executor_resident_id"] = executor_resident_id
    return response


def save_lineage_program_component(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
    *,
    full_view: bool = True,
) -> dict[str, Any]:
    component_id, requested = lineage_program_authoring.component_patch(payload)
    effective_payload = dict(payload)
    effect_changes = payload.get("effect_changes") if isinstance(payload.get("effect_changes"), dict) else {}
    with _lineage_scope(lineage_id):
        current = _read_lineage(lineage_id, actor=actor)
        _ensure_manager(current, _safe_actor(actor))
        current_component = next(
            (
                component
                for component in (current.get("program") or {}).get(
                    "components",
                    [],
                )
                if isinstance(component, dict)
                and str(component.get("id") or "") == component_id
            ),
            None,
        )
        if not current_component:
            raise KeyError("program component not found")
        current_kind = str(current_component.get("kind") or "")
        current_title = str(current_component.get("title") or "")
    if effect_changes:
        config = _clone_json(
            requested.get("config") if isinstance(requested.get("config"), dict)
            else current_component.get("config") or {}
        )
        config.update(_clone_json(effect_changes))
        requested["config"] = config
    if current_kind in lineage_program.PACKET_OPERATOR_KINDS:
        candidate_config = lineage_program_authoring.packet_update_config(
            current_component, requested
        )
        if (
            candidate_config["operator_implementation"] == "resident"
            and not candidate_config.get("resident_executor_id")
        ):
            created_executor = _create_packet_operator_executor(
                lineage_id,
                component_id=component_id,
                kind=current_kind,
                title=_text(requested.get("title"), 120) or current_title,
                config=candidate_config,
                actor=_safe_actor(actor),
                x=requested.get("x", current_component.get("x")),
                y=requested.get("y", current_component.get("y")),
            )
            candidate_config["resident_executor_id"] = str(
                (created_executor.get("node") or {}).get("id") or ""
            )
        requested["config"] = candidate_config
    requested_endpoint_reference_ids = (
        payload.get("endpoint_reference_ids")
        if isinstance(payload.get("endpoint_reference_ids"), dict)
        else None
    )
    requested_watch_subject = (
        payload.get("watch_subject")
        if isinstance(payload.get("watch_subject"), dict)
        else None
    )

    mutate = partial(
        lineage_program_authoring.update_component,
        component_id=component_id,
        requested=requested,
        requested_endpoint_reference_ids=requested_endpoint_reference_ids,
        requested_watch_subject=requested_watch_subject,
    )

    response = _mutate_lineage_program(
        lineage_id,
        effective_payload,
        actor,
        mutate,
        "更新 HATG 程序组件",
        full_view=full_view,
    )
    response.pop("component_id")
    response["component"] = next(
        component
        for component in response["lineage"]["program"]["components"]
        if component["id"] == component_id
    )
    return response


def delete_lineage_program_component(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
    *,
    full_view: bool = True,
) -> dict[str, Any]:
    component_id = _text(payload.get("component_id"), 110).lower()

    def mutate(body: dict[str, Any], program: dict[str, Any]) -> dict[str, Any]:
        existing = next((item for item in program.get("components") or [] if item.get("id") == component_id), {})
        removed = lineage_program_authoring.remove_program_components(body, program, {component_id})
        return {
            "deleted_component_id": component_id,
            "client_key": str(existing.get("client_key") or ""),
            "deleted_component_ids": removed["component_ids"],
            "deleted_wire_ids": removed["wire_ids"],
            "detached_executor_resident_id": next(iter(removed["executor_ids"]), ""),
        }

    response = _mutate_lineage_program(
        lineage_id,
        payload,
        actor,
        mutate,
        "删除 HATG 程序组件",
        full_view=full_view,
    )
    return response


def _retire_exhausted_event_sources(lineage_id: str, actor: str) -> list[str]:
    """Retire opted-in sources once graph cleanup can preserve all accepted work."""

    deleted: list[str] = []
    with _lineage_execution_authority(lineage_id), _lineage_scope(lineage_id):
        lineage = _read_lineage(lineage_id, actor=actor)
        _ensure_manager(lineage, actor)
        candidates = [
            str(component["id"])
            for component in (lineage.get("program") or {}).get("components") or []
            if component.get("kind") == "event_source"
            and (component.get("config") or {}).get("auto_delete_on_exhaustion")
        ]
        for source_id in candidates:
            lineage = _read_lineage(lineage_id, actor=actor)
            source = (_compiled_program(lineage).get("event_sources") or {}).get(source_id)
            if not source or not source.get("auto_delete_on_exhaustion"):
                continue
            maximum = normalize_optional_limit(source.get("max_fires"))
            runtime = lineage_program_runtime.read_runtime(lineage_id)
            source_state = (runtime.get("event_sources") or {}).get(source_id) or {}
            if maximum is None or int(source_state.get("fire_count") or 0) < maximum:
                continue
            if maximum and source_state.get("fanout_settled_generation") != int(
                source_state.get("fire_generation") or 0
            ):
                continue
            if source_state.get("pending_scheduled_at"):
                continue
            proposed = _clone_json(lineage)
            removed = lineage_program_authoring.remove_program_graph_objects(proposed["program"], {source_id}, set())
            impact = _program_execution_contract_change(lineage, proposed)
            if any(
                (runtime.get("event_wires", {}).get(wire_id) or {}).get("pending_events")
                for wire_id in impact["invoke_wire_ids"]
            ):
                continue
            owner_states = [
                lineage_operator_runtime.read_operator_runtime(lineage_id, owner_id)
                for owner_id in impact["operator_ids"]
            ]
            if any(state.get("pending_deliveries") or state.get("inbox") for state in owner_states):
                continue

            def mutate(body: dict[str, Any], program: dict[str, Any]) -> dict[str, Any]:
                return lineage_program_authoring.remove_program_graph_objects(program, {source_id}, set())

            _mutate_lineage_program(
                lineage_id, {}, actor, mutate,
                "Event 触发次数耗尽后自动删除",
            )
            deleted.extend(removed["component_ids"])
    return deleted


def save_lineage_program_wire(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
    *,
    operation: str = "upsert",
    full_view: bool = True,
) -> dict[str, Any]:
    requested, requested_id, normalized_operation = (
        lineage_program_authoring.wire_request(payload, operation)
    )

    mutate = partial(
        lineage_program_authoring.save_wire,
        requested=requested,
        requested_id=requested_id,
        normalized_operation=normalized_operation,
    )

    response = _mutate_lineage_program(
        lineage_id,
        payload,
        actor,
        mutate,
        "保存 HATG 程序连线",
        full_view=full_view,
    )
    wire_id = response.pop("wire_id")
    response["wire"] = next(
        wire
        for wire in response["lineage"]["program"]["wires"]
        if wire["id"] == wire_id
    )
    return response


def delete_lineage_program_wire(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
    *,
    full_view: bool = True,
) -> dict[str, Any]:
    wire_id = _text(payload.get("wire_id"), 110).lower()

    def mutate(
        _body: dict[str, Any],
        program: dict[str, Any],
    ) -> dict[str, Any]:
        before = program.get("wires") or []
        existing = next(
            (
                wire
                for wire in before
                if (
                    isinstance(wire, dict)
                    and str(wire.get("id") or "") == wire_id
                )
            ),
            None,
        )
        if not existing:
            raise KeyError("program wire not found")
        removed = lineage_program_authoring.delete_program_objects_with_observer_dependents(
            program,
            wire_ids=[wire_id],
        )
        return {
            "deleted_wire_id": wire_id,
            "client_key": str(existing.get("client_key") or ""),
            "deleted_component_ids": removed["component_ids"],
            "deleted_wire_ids": removed["wire_ids"],
        }

    return _mutate_lineage_program(
        lineage_id,
        payload,
        actor,
        mutate,
        "删除 HATG 程序连线",
        full_view=full_view,
    )


def rename_lineage_resident(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Validate graph concurrency before the canonical Resident rename."""

    with _lineage_scope(lineage_id):
        current = _read_lineage(lineage_id, actor=actor)
        _ensure_manager(current, _safe_actor(actor))
        node = _lineage_node(current, payload.get("resident_id"))
        identity = resident_domain.resolve_resident_identity(_node_resident_id(node), actor)
        if identity["lineage_id"] != lineage_id:
            raise ValueError("Resident does not belong to the target Lineage")
        return resident_domain.rename_resident_title(
            _node_resident_id(node), actor, payload.get("title"),
        )


def update_lineage_layout(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Merge canvas positions for UpdateLineageLayout under their own layout revision.

    Layout is last-write-wins per object id, so moving one object never
    invalidates another object's move or an open editing form.
    """

    actor_id = _safe_actor(actor)
    current = _read_lineage(lineage_id, actor=actor_id)
    _ensure_manager(current, actor_id)
    candidate = _clone_json(current)
    normalized = lineage_state_domains.apply_layout_patch(
        candidate,
        payload.get("positions"),
        normalize_position=_position,
    )
    result = _lineage_catalog_store().update_layout(
        lineage_id,
        normalized["applied"],
        updated_at=_now_iso(),
    )
    result["missing_object_ids"] = list(dict.fromkeys([
        *normalized["missing_object_ids"],
        *result["missing_object_ids"],
    ]))
    if result["applied"]:
        from management_portal.society_service.changes import lineage_changed

        lineage_changed(lineage_id, runtime=False)
    return result


def update_lineage_metadata(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Update Lineage-owned metadata and fragments without accepting a whole graph body."""

    actor_id = _safe_actor(actor)
    if "fragments" in payload and not isinstance(payload["fragments"], list):
        raise ValueError("fragments 必须是数组")
    with _lineage_scope(lineage_id):
        current = _read_lineage(lineage_id, actor=actor)
        _ensure_manager(current, actor_id)
        lineage = _clone_json(current)
        if "fragments" in payload:
            lineage["fragments"] = _normalize_lineage_payload(
                {**_clone_json(current), "fragments": payload["fragments"]}, current, actor_id,
            )["fragments"]
        previous_project_id = _text(current.get("project_id"), 120)
        from . import lineage_automation_identity
        run_as = lineage_automation_identity.requested(payload, current, actor_id)
        if run_as is None:
            lineage.pop("run_as_principal", None)
        else:
            lineage["run_as_principal"] = run_as
        project_id = _requested_lineage_project_id(
            payload,
            current,
            actor_id,
        )
        if _lineage_membership(current) == "formal" and not project_id:
            raise ValueError("Lineage 必须属于 Project")
        lineage["project_id"] = project_id
        if "principal_hierarchy" in current:
            from .principal_hierarchy import scope_after_project_change
            lineage["principal_hierarchy"] = scope_after_project_change(current["principal_hierarchy"], project_id)

        if "title" in payload:
            lineage["title"] = (
                _text(payload.get("title"), 140)
                or "未命名 Lineage"
            )
        if "description" in payload:
            lineage["description"] = _text(
                payload.get("description"),
                3000,
            )
        previous_leader_id = _text(
            current.get("leader_resident_id"),
            80,
        ).lower()
        leader_id = _text(
            (
                payload.get("leader_resident_id")
                if "leader_resident_id" in payload
                else previous_leader_id
            ),
            80,
        ).lower()
        nodes = [
            node
            for node in current.get("nodes") or []
            if isinstance(node, dict)
        ]
        resident_ids = {
            str(node.get("id") or "")
            for node in nodes
        }
        if leader_id and leader_id not in resident_ids:
            raise ValueError("Leader 必须是当前 HATG 中的节点")
        if "leader_resident_id" in payload:
            lineage["leader_resident_id"] = leader_id
        original_folder: Path | None = None
        folder_relocated = False
        previous_resident_records: list[dict[str, Any]] = []
        next_resident_records: list[dict[str, Any]] = []
        try:
            if (
                project_id != previous_project_id
                or "project_id" in payload
            ):
                original_folder = _lineage_dir(lineage_id).resolve(strict=True)

                canonical_folder = lineage_storage.canonical_lineage_folder(
                    lineage_id,
                    folder_root=_lineages_root(),
                )
                previous_resident_records, next_resident_records = (
                    resident_domain.prepare_lineage_project_member_updates(
                        lineage_id,
                        project_id,
                        canonical_folder=canonical_folder,
                        source_folder=original_folder,
                    )
                )
                if project_id != previous_project_id:
                    _relocate_lineage_for_project(
                        lineage_id,
                        project_id,
                        actor_id,
                    )
                    folder_relocated = True
            _persist_lineage_state(
                current,
                lineage,
                "更新 Lineage 元数据",
                    )
            if next_resident_records:
                resident_domain.persist_lineage_project_member_updates(
                    next_resident_records
                )
        except BaseException:
            if folder_relocated and original_folder is not None:
                lineage_storage.relocate_lineage_folder(
                    lineage_id,
                    original_folder,
                    folder_root=_lineages_root(),
                )
                # Revert this update's own change; concurrent writes stay.
                _persist_lineage_state(lineage, current, "撤回 Lineage 元数据更新")
                if previous_resident_records:
                    resident_domain.persist_lineage_project_member_updates(
                        previous_resident_records
                    )
            raise
        return _clone_json(lineage)


def apply_lineage_graph_patch(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
    *,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _apply_lineage_graph_patch(
        lineage_id,
        payload,
        actor,
        synchronize_external_projections=True,
        previous=previous,
    )


def _apply_lineage_graph_patch(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
    *,
    synchronize_external_projections: bool,
    membership_transition: bool = False,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically apply an authored HATG graph projection.

    The patch deliberately remains a state projection, not a second graph
    mutation language. Fine-grained Instructions use the component/wire
    authorities; macro expansion can submit the resulting program, nodes, and
    edges together through this one commit.
    """

    actor_id = _safe_actor(actor)
    raw_patch = lineage_graph_authoring.authored_graph_patch(
        payload, instruction="ApplyLineageChanges", fields=LINEAGE_GRAPH_PATCH_FIELDS,
    )
    with _lineage_scope(lineage_id):
        current = _read_lineage(lineage_id, actor=actor_id)
        _ensure_manager(current, actor_id)
        base = previous if previous is not None else current
        body = lineage_graph_authoring.graph_patch_candidate(base, raw_patch)
    return update_lineage(
        lineage_id,
        {
            "lineage": body,
            "history_reason": _text(
                payload.get("history_reason"),
                180,
            )
            or "应用 HATG Graph Patch",
            "placement": payload.get("placement", {}),
            "near_resident_id": payload.get("near_resident_id"),
        },
        actor_id,
        synchronize_external_projections=(
            synchronize_external_projections
        ),
        membership_transition=membership_transition,
        previous=base,
    )


def commit_lineage_edit(
    lineage_id: str,
    actor: str,
    edit: Any,
    *,
    reason: str,
    resident_ids: Iterable[str] = (),
    placement_request: dict[str, Any] | None = None,
    preserve_event_source_ids: Iterable[str] = (),
    full_view: bool = True,
) -> tuple[dict[str, Any], Any]:
    """Apply ``edit(body)`` to a copy of the definition and commit it once.

    ``resident_ids`` are deleted with the full ``DeleteResident`` semantics in
    the same transaction.  ``edit`` may raise to abandon the whole change.
    Returns the verified Lineage view (or the stored definition when
    ``full_view`` is false) and whatever ``edit`` returned.
    """

    actor_id = _safe_actor(actor)
    deleted = [str(item) for item in resident_ids]
    if len(set(deleted)) != len(deleted):
        raise ValueError("resident_ids must contain unique IDs")
    for resident_id in deleted:
        identity_contract.validate_id(resident_id, "resident")
    completion_baselines: list[dict[str, Any]] = []
    with _lineage_scope(lineage_id):
        previous = _read_lineage(lineage_id, actor=actor)
        _ensure_manager(previous, actor_id)
        body = _clone_json(previous)
        outcome = edit(body)
        if deleted:
            for record in resident_catalog.get_catalog().get_many(deleted):
                if record["lineage_id"] != lineage_id:
                    raise ValueError("Resident 不属于这个 Lineage")
            present = {str(node.get("id") or "") for node in body.get("nodes") or []}
            _nodes, patch = _resident_projections_removal_patch(body, set(deleted) & present)
            body.update(patch)
        lineage = _normalize_lineage_payload(body, previous, actor_id)
        impact = _program_execution_contract_change(previous, lineage)

        def commit() -> None:
            if deleted:
                removed_family_ids = frozenset(
                    item["id"] for item in previous.get("function_blocks", [])
                ) - frozenset(item["id"] for item in lineage.get("function_blocks", []))
                resident_domain.delete_resident_identities(
                    lineage_id, deleted, actor_id, removed_family_ids=removed_family_ids,
                )
            _persist_lineage_state(
                previous,
                lineage,
                reason,
                placement_request=placement_request or {},
            )

        with (
            _program_contract_authority(lineage_id, impact),
            invariant_locks.resident_locks(deleted, operation="lineage.edit"),
        ):
            _lineage_catalog_store().state_store.transaction(commit)
            completion_baselines = _execute_program_contract_cleanup(
                lineage_id,
                impact,
                previous_lineage=previous,
                current_lineage=lineage,
                preserve_event_source_fire_budget_ids={str(item) for item in preserve_event_source_ids},
            )
        verified = _verified_lineage_view(lineage, actor_id) if full_view else lineage
    _establish_resident_completion_baselines(lineage_id, actor, completion_baselines)
    return verified, outcome


def apply_lineage_changes(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
    *,
    include_lineage: bool = True,
) -> dict[str, Any]:
    """Commit Program operations and Resident deletions as one Lineage version.

    ``payload["program_operations"]`` uses the Program mutation language
    (set/add/clone/remove on components and wires) and
    ``payload["resident_ids"]`` lists Residents deleted with the full
    ``DeleteResident`` semantics.  Either every change commits under one new
    revision or none does.
    """

    raw_operations = payload.get("program_operations") or []
    operations = lineage_program.normalize_graph_mutations(raw_operations) if raw_operations else []
    resident_ids = [str(item) for item in payload.get("resident_ids") or []]
    if not operations and not resident_ids:
        raise ValueError("ApplyLineageChanges 需要至少一项修改")
    preserve: set[str] = set()
    previous_ids: set[str] = set()

    def graph_ids(body: dict[str, Any]) -> set[str]:
        program = body.get("program") or {}
        return {
            str(item.get("id") or "")
            for collection in (body.get("nodes"), body.get("edges"), program.get("components"),
                               program.get("wires"), body.get("function_blocks"))
            for item in collection or [] if isinstance(item, dict) and item.get("id")
        }

    def edit(body: dict[str, Any]) -> dict[str, set[tuple[str, str]]]:
        previous_ids.update(graph_ids(body))
        if not operations:
            return {"created": set(), "changed": set(), "removed": set()}
        mutation = lineage_program_graph_mutation.mutate_program_graph(body, operations, True)
        body.clear()
        body.update(_clone_json(mutation.candidate))
        preserve.update(mutation.preserve_event_source_ids)
        return {"created": set(mutation.created), "changed": set(mutation.changed), "removed": set(mutation.removed)}

    verified, outcome = commit_lineage_edit(
        lineage_id,
        actor,
        edit,
        reason="ApplyLineageChanges",
        resident_ids=resident_ids,
        placement_request=payload,
        preserve_event_source_ids=preserve,
        full_view=include_lineage,
    )
    created, changed, removed = outcome["created"], outcome["changed"], outcome["removed"]
    return {
        "lineage": verified,
        "revision": int(verified.get("revision") or 0),
        "created_ids": sorted(item_id for _kind, item_id in created),
        "updated_ids": sorted(item_id for _kind, item_id in changed - created),
        "deleted_ids": sorted({item_id for _kind, item_id in removed} | set(resident_ids) | (previous_ids - graph_ids(verified))),
        "leader_resident_id": str(verified.get("leader_resident_id") or ""),
    }


def _resident_projection_removal_patch(
    lineage: dict[str, Any],
    resident_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    nodes, patch = _resident_projections_removal_patch(lineage, {resident_id})
    return nodes[0], patch


def _resident_projections_removal_patch(
    lineage: dict[str, Any], resident_ids: set[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    removed = [item for item in lineage.get("nodes") or [] if item.get("id") in resident_ids]
    if len(removed) != len(resident_ids):
        raise KeyError("Lineage node not found")
    nodes = [
        _clone_json(item)
        for item in lineage.get("nodes") or []
        if (
            isinstance(item, dict)
            and str(item.get("id") or "") not in resident_ids
        )
    ]

    def references_resident(item: dict[str, Any]) -> bool:
        return (
            str(item.get("source") or "") in resident_ids
            or str(item.get("target") or "") in resident_ids
        )

    edges = [
        _clone_json(item)
        for item in lineage.get("edges") or []
        if isinstance(item, dict) and not references_resident(item)
    ]
    program = lineage_program_authoring.program_without_resident_references(
        lineage.get("program"),
        resident_ids,
    )
    member_ids = {record["resident_id"]
                  for record in resident_catalog.get_catalog().for_lineage(lineage["lineage_id"])}
    leader_id = lineage_subgraph.successor_coordinator(
        str(lineage.get("leader_resident_id") or ""), resident_ids, nodes, member_ids=member_ids,
    )
    if not leader_id and (lineage.get("principal_hierarchy") or {}).get("level") == "project":
        # A Project-level Lineage always has its own Leader; its only Resident is that Leader.
        raise ValueError(
            "Project 层 Lineage 必须有 Leader：要移除的是它最后的成员。"
            "先在这个 Lineage 中加入其他 Resident，或用 DeleteProject 删除整个 Project"
        )
    return removed, {
        "nodes": nodes,
        "edges": edges,
        "program": program,
        "leader_resident_id": leader_id,
    }


def _finalize_deleted_resident_projection(lineage_id: str) -> None:
    """Publish projections after the enclosing delete transaction commits."""

    lineage = _read_lineage_state(lineage_id)
    _publish_lineage_resource_projection(lineage_id, int(lineage["revision"]))
    resident_domain.sync_lineage_member_credentials(
        lineage,
        previous_lineage=None,
    )


def update_lineage(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
    *,
    synchronize_external_projections: bool = True,
    membership_transition: bool = False,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Commit a whole-graph body; ``previous`` is the state the body was built from.

    The commit merges the difference between ``previous`` and the body into
    the committed definition, so concurrent edits outside that difference stay.
    """

    completion_baselines: list[dict[str, Any]] = []
    with _lineage_scope(lineage_id):
        if previous is None:
            previous = _read_lineage(lineage_id, actor=actor)
            _ensure_manager(previous, actor)
        raw_body = (
            payload.get("lineage")
            if isinstance(payload.get("lineage"), dict)
            else payload
        )
        lineage = lineage_graph_authoring.normalized_graph_update(
            previous, raw_body, actor,
            membership_transition=membership_transition,
            require_unchanged_membership=_require_unchanged_membership,
            normalize=_normalize_lineage_payload,
        )
        program_contract_impact = (
            _program_execution_contract_change(
                previous,
                lineage,
            )
        )
        def commit_graph_authority() -> None:
            _synchronize_canonical_resident_titles(previous, lineage, actor)
            _persist_lineage_state(
                previous,
                lineage,
                _text(payload.get("history_reason"), 180) or "更新 HATG",
                placement_request=payload,
                synchronize_resource_identities=(
                    synchronize_external_projections
                ),
                synchronize_resident_identities=(
                    synchronize_external_projections
                ),
            )

        with _program_contract_authority(lineage_id, program_contract_impact):
            _lineage_catalog_store().state_store.transaction(
                commit_graph_authority
            )
            completion_baselines = _execute_program_contract_cleanup(
                lineage_id,
                program_contract_impact,
                previous_lineage=previous,
                current_lineage=lineage,
                preserve_event_source_fire_budget_ids={
                    str(item)
                    for item in payload.get(
                        "preserve_event_source_fire_budget_ids",
                        [],
                    )
                    if str(item)
                },
            )
        response = _clone_json(lineage)
    _establish_resident_completion_baselines(
        lineage_id,
        actor,
        completion_baselines,
    )
    return response


def delete_lineage_graph_objects(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
    *,
    include_lineage: bool = True,
) -> dict[str, Any]:
    """Delete one graph selection, its identities and durable effects in one commit."""
    def selected_ids(field: str) -> list[str]:
        values = payload.get(field, [])
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value or value != value.strip()
            for value in values
        ):
            raise ValueError(f"{field} must contain nonempty canonical IDs")
        if len(set(values)) != len(values):
            raise ValueError(f"{field} must contain unique IDs")
        return values

    resident_ids = selected_ids("resident_ids")
    component_ids = selected_ids("component_ids")
    if not resident_ids and not component_ids:
        raise ValueError("Graph deletion requires at least one object")
    for resident_id in resident_ids:
        identity_contract.validate_id(resident_id, "resident")

    completion_baselines: list[dict[str, Any]] = []
    with _lineage_scope(lineage_id):
        previous = _read_lineage(lineage_id, actor=actor)
        _ensure_manager(previous, actor)
        for record in resident_catalog.get_catalog().get_many(resident_ids):
            if record["lineage_id"] != lineage_id:
                raise ValueError("Project references cannot delete a child Lineage leader; manage it in its home Lineage")

        body = _clone_json(previous)
        if component_ids:
            lineage_program_authoring.remove_program_components(body, body["program"], set(component_ids))
        nodes, patch = _resident_projections_removal_patch(body, set(resident_ids))

        body.update(patch)
        removed_seed_count = len(
            lineage_program_authoring.program_seed_component_ids(previous.get("program"))
            - lineage_program_authoring.program_seed_component_ids(body.get("program"))
        )
        lineage = _normalize_lineage_payload(body, previous, actor)
        program_contract_impact = _program_execution_contract_change(previous, lineage)

        def commit_identity_and_graph() -> None:
            if resident_ids:
                resident_domain.delete_resident_identities(lineage_id, resident_ids, actor)
            _persist_lineage_state(previous, lineage, "批量删除 HATG 对象及 Resident 身份")

        with (
            _program_contract_authority(lineage_id, program_contract_impact),
            invariant_locks.resident_locks(resident_ids, operation="lineage.objects.delete"),
        ):
            _lineage_catalog_store().state_store.transaction(commit_identity_and_graph)
            completion_baselines = _execute_program_contract_cleanup(
                lineage_id,
                program_contract_impact,
                previous_lineage=previous,
                current_lineage=lineage,
            )

        old_edge_ids = {
            str(item.get("id") or "")
            for item in previous.get("edges") or []
            if isinstance(item, dict)
        }
        reconnected_count = sum(
            1 for item in lineage.get("edges") or []
            if isinstance(item, dict)
            and str(item.get("type") or "") == "inheritance"
            and str(item.get("id") or "") not in old_edge_ids
        )
        response = {
            "deleted_resident_ids": resident_ids,
            "leader_resident_id": str(lineage.get("leader_resident_id") or ""),
            "leader_changed": str(lineage.get("leader_resident_id") or "")
            != str(previous.get("leader_resident_id") or ""),
            "deleted_component_ids": sorted(
                {str(item["id"]) for item in previous["program"]["components"]}
                - {str(item["id"]) for item in lineage["program"]["components"]}
            ),
            "deleted_wire_ids": sorted(
                {str(item["id"]) for item in previous["program"]["wires"]}
                - {str(item["id"]) for item in lineage["program"]["wires"]}
            ),
            "reconnected_edge_count": reconnected_count,
            "removed_seed_count": removed_seed_count,
        }
        if include_lineage:
            response["lineage"] = _verified_lineage_view(lineage, actor)
    _establish_resident_completion_baselines(
        lineage_id,
        actor,
        completion_baselines,
    )
    return response


def _send_node_message(
    node: dict[str, Any],
    prompt: str,
    actor: str,
    *,
    input_media: object = None,
    message_id: str = "",
    instruction_context: Any = None,
) -> object:
    """Lower a Resident Invoke to the same ISA call used by an explicit Effect."""
    from management_portal.hatg import routing
    from management_portal.hatg.contracts import InstructionContext

    prompt = lineage_primitives.normalize_invocation_prompt(prompt)
    content_parts: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    if isinstance(input_media, list):
        content_parts.extend(
            dict(item) for item in input_media if isinstance(item, Mapping)
        )
    delivery_message_id = resident_message_contract.message_identity(message_id)
    return routing.execute_or_raise(
        {
            "instruction": "DeliverMessage",
            "idempotency_key": f"lineage-delivery:{delivery_message_id}",
            "arguments": {
                "resident_id": _node_resident_id(node),
                "message": {
                    "message_id": delivery_message_id,
                    "role": "user",
                    "content_parts": content_parts,
                },
                "activation_policy": "enqueue_and_trigger",
            },
        },
        instruction_context or InstructionContext(caller=actor, entry="lineage"),
    )


def change_lineage_resident_state(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
    *,
    allow_quota_override: bool = True,
) -> dict[str, Any]:
    """Open or close the Resident intake switch through its owner."""

    actor_id = _safe_actor(actor)
    resident_id = _text(
        payload.get("resident_id"),
        80,
    ).lower()
    state_domain = _text(
        payload.get("state_domain"),
        40,
    ).lower() or "intake"
    transition = _text(payload.get("transition"), 24).lower()
    lineage = _read_lineage(lineage_id, actor=actor_id)
    _ensure_manager(lineage, actor_id)
    node = _clone_json(_lineage_node(lineage, resident_id))
    if state_domain == "intake":
        return resident_protocol.change_resident_state(
            _node_resident_id(node),
            actor_id,
            state_domain=state_domain,
            transition=transition,
            allow_quota_override=allow_quota_override,
        )
    raise ValueError("未知的 Resident 状态域")


def cancel_lineage_resident_execution(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Stop one message: a running turn is cancelled, a waiting one leaves the
    queue, and a finished one keeps its result (decision_2a3ff353165c)."""

    actor_id = _safe_actor(actor)
    resident_id = _text(payload.get("resident_id"), 80).lower()
    message_id = _text(payload.get("message_id"), 100)
    # Stop only needs identity, membership and authority. Loading the graph's
    # component bodies and runtime work here delays cancellation as it grows.
    if _lineage_deletion_intent(lineage_id):
        raise LineageConflictError("Lineage deletion is in progress")
    lineage = lineage_access_projection(lineage_id)
    _ensure_manager(lineage, actor_id)
    if resident_id not in lineage.get("lineage_node_ids", ()):
        raise ValueError("Lineage 中不存在对应的 Agent 节点")
    return resident_protocol.cancel_resident_input(
        resident_id, actor_id, message_id,
    )


def _lineage_resident_queue_target(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> tuple[str, str, str]:
    """Resolve one HATG node to the Resident queue authority locator."""

    actor_id = _safe_actor(actor)
    resident_id = _text(payload.get("resident_id"), 80).lower()
    queue_item_id = str(payload.get("message_id") or "").strip()
    if not resident_id or not queue_item_id:
        raise ValueError(
            "Resident queue Instruction 需要 resident_id 和 message_id"
        )
    lineage = _read_lineage(lineage_id, actor=actor_id)
    _ensure_manager(lineage, actor_id)
    node = _clone_json(_lineage_node(lineage, resident_id))
    return (
        actor_id,
        _node_resident_id(node),
        queue_item_id,
    )


def inject_lineage_resident_queue_item_into_active_turn(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    actor_id, resident_id, message_id = _lineage_resident_queue_target(
        lineage_id, payload, actor,
    )
    return resident_protocol.inject_queued_input_into_active_turn(
        resident_id, actor_id, message_id,
    )


def clear_lineage_resident_queue(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    actor_id = _safe_actor(actor)
    resident_id = _text(payload.get("resident_id"), 80).lower()
    if not resident_id:
        raise ValueError("resident_id required")
    lineage = _read_lineage(lineage_id, actor=actor_id)
    _ensure_manager(lineage, actor_id)
    node = _lineage_node(lineage, resident_id)
    return resident_protocol.clear_resident_queue(_node_resident_id(node), actor_id)


def cancel_lineage_resident_queue_item(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Cancel one queued Resident turn without changing the active turn."""

    (
        actor_id,
        resident_id,
        queue_item_id,
    ) = _lineage_resident_queue_target(lineage_id, payload, actor)
    result = resident_protocol.cancel_resident_queue_item(
        resident_id,
        actor_id,
        queue_item_id,
    )
    return {
        **result,
        "resident_id": resident_id,
        "message_id": queue_item_id,
    }


def quiesce_lineage_resident(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Close intake, cancel current work and discard that session's queue."""

    actor_id = _safe_actor(actor)
    resident_id = _text(
        payload.get("target"),
        80,
    ).lower()
    with _lineage_scope(lineage_id):
        lineage = _read_lineage(lineage_id, actor=actor)
        _ensure_manager(lineage, actor_id)
        node = _clone_json(_lineage_node(lineage, resident_id))
    exact_resident_id = _node_resident_id(node)
    intake = resident_protocol.change_resident_state(
        exact_resident_id,
        actor_id,
        state_domain="intake",
        transition="close",
    )
    resident_status = resident_protocol.get_resident_state(
        exact_resident_id,
        actor_id,
    )["state"]
    active_message_id = str(
        resident_status.get("active_message_id") or ""
    )
    operation = (
        resident_protocol.cancel_resident_execution(
            exact_resident_id,
            actor_id,
            active_message_id,
            continue_queue=False,
        )
        if active_message_id
        else {
            "cancelled": False,
            **resident_protocol.clear_resident_queue(exact_resident_id, actor_id),
        }
    )
    return {
        "target_resident_id": resident_id,
        "intake_state": str(intake.get("state") or "closed"),
        "cleared_queue_count": int(
            operation.get("cleared_queue_count") or operation.get("cleared_count") or 0
        ),
        "cancelled": bool(operation.get("cancelled")),
        "state": dict(operation.get("state") or {}),
        "cursor": int(operation.get("cursor") or 0),
    }


def absorb_lineage_node_context(lineage_id: str, payload: dict[str, Any], actor: str) -> dict[str, Any]:
    """Send a normal Resident message asking the target to absorb a source."""
    actor_id = _safe_actor(actor)
    source_resident_id, target_resident_id = (
        lineage_branch_transitions.absorption_request(payload)
    )
    with _lineage_scope(lineage_id):
        baseline = _read_lineage(lineage_id, actor=actor)
        _ensure_manager(baseline, actor_id)
        source_snapshot, target_snapshot = (
            lineage_branch_transitions.absorption_nodes(
                baseline, source_resident_id, target_resident_id,
                resident_id_of=_node_resident_id,
            )
        )

    source_reference_id = _node_resident_id(source_snapshot)
    target_reference_id = _node_resident_id(target_snapshot)
    if not identity_contract.is_valid_id(source_reference_id, "resident"):
        raise ValueError("来源节点还没有统一 Resident ID，请先同步协作资源注册表")
    if not identity_contract.is_valid_id(target_reference_id, "resident"):
        raise ValueError("目标节点还没有统一 Resident ID，请先同步协作资源注册表")
    prompt = lineage_branch_transitions.absorption_prompt(
        source_snapshot, target_snapshot, source_reference_id,
    )
    now = _now_iso()

    with _lineage_scope(lineage_id):
        current = _read_lineage(lineage_id, actor=actor)
        _ensure_manager(current, actor_id)
        current_nodes = {str(node.get("id") or ""): node for node in current.get("nodes") or [] if isinstance(node, dict)}
        if source_resident_id not in current_nodes or target_resident_id not in current_nodes:
            raise LineageConflictError("来源节点或目标节点已改变，请重试拖放")
        prepared_lineage, absorption_edge_id = (
            lineage_branch_transitions.prepare_absorption(
                current,
                source_id=source_resident_id,
                target_id=target_resident_id,
                source=source_snapshot,
                target=target_snapshot,
                source_resident_id=source_reference_id,
                target_resident_id=target_reference_id,
                now=now,
                new_edge_id=lambda: _item_id("", "edge_absorption"),
                normalize_edge=_normalize_edge,
            )
        )
        _persist_lineage_runtime_state(
            current,
            prepared_lineage,
            "开始节点吸收",
        )

    delivery_status = "queued"
    delivery_error = ""
    target_message_id = ""
    try:
        response = _send_node_message(target_snapshot, prompt, actor_id)
        target_message_id = resident_message_contract.delivered_message_id(response)
    except Exception as exc:
        delivery_status = "failed"
        delivery_error = _text(exc, 2000) or "目标节点运行失败"

    delivered_at = _now_iso()
    with _lineage_scope(lineage_id):
        current = _read_lineage(lineage_id, actor=actor)
        finalized_lineage = lineage_branch_transitions.finish_absorption(
            current, absorption_edge_id,
            status=delivery_status,
            message_id=target_message_id,
            error=delivery_error,
            delivered_at=delivered_at,
        )
        if finalized_lineage is not None:
            _persist_lineage_runtime_state(
                current, finalized_lineage, "节点吸收投递结果",
            )
        else:
            finalized_lineage = current

    verified = _verified_lineage_view(finalized_lineage, actor_id)
    verified_edge = next(
        (edge for edge in verified.get("edges") or [] if str(edge.get("id") or "") == absorption_edge_id),
        None,
    )
    return {
        "lineage": verified,
        "edge": verified_edge,
        "reference": {
            "source_resident_id": source_reference_id,
        },
        "delivery": {
            "status": delivery_status,
            "error": delivery_error,
            "target_message_id": target_message_id,
        },
    }


def _created_resident_id(detail: dict[str, Any]) -> str:
    return _text(detail.get("resident_id"), 180)


def _create_derived_resident(
    source: dict[str, Any],
    payload: dict[str, Any],
    actor: str,
    lineage_source: str,
) -> dict[str, Any]:
    return lineage_branch_transitions.create_derived_resident(
        _node_resident_id(source), payload, actor, lineage_source,
        resident_authority=resident_domain,
    )


def _lineage_branch_response(
    lineage: dict[str, Any],
    created_resident_id: str,
    actor: str,
    *,
    created: bool,
    title_sync_status: str = "",
    title_sync_error: str = "",
) -> dict[str, Any]:
    # The child and inheritance edge have committed. Opening Chat immediately
    # also requires the Resource identity and creator credentials to be visible.
    # Confirm outside graph transactions, including an existing-edge replay;
    # failure carries a projection-only receipt and must not recreate the Fork.
    resident_domain.confirm_resident_registration(created_resident_id, actor)
    return lineage_branch_transitions.branch_response(
        _verified_lineage_view(lineage, actor), created_resident_id,
        created=created,
        title_sync_status=title_sync_status,
        title_sync_error=title_sync_error,
    )


def create_lineage_branch(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Prepare a Fork without locking; validate and attach against committed membership."""
    actor_id = _safe_actor(actor)
    source_resident_id, context_source_resident_id, edge_id, mode = (
        lineage_branch_transitions.branch_request(payload)
    )

    with _lineage_scope(lineage_id):
        previous = _read_lineage(lineage_id, actor=actor)
        _ensure_manager(previous, actor_id)

        # The graph and Resident membership must be checked in one short
        # transaction. Context/Provider preparation below holds no graph lock.
        def validate_source() -> dict[str, Any]:
            current = _locked_branch_definition(previous, actor_id)
            _normalize_lineage_payload(_clone_json(current), current, actor_id)
            return current

        previous = _lineage_catalog_store().state_store.transaction(validate_source)
        source, source_node_id, existing_edge = (
            lineage_branch_transitions.branch_source(
                previous, source_resident_id, edge_id,
                resident_id_of=_node_resident_id,
            )
        )
        if not _text(payload.get("context_source_resident_id"), 80):
            context_source_resident_id = _node_resident_id(source)

        if existing_edge is not None:
            return _lineage_branch_response(
                previous,
                str(existing_edge.get("target") or ""),
                actor_id,
                created=False,
                title_sync_status="already-created",
            )

        lineage_source = f"lineage:{lineage_id}:{source_node_id}"
        requested_title = _text(payload.get("title"), 120) or _text(
            f"{source.get('title') or source_resident_id} · 分支",
            120,
        )
        child_x, child_y = lineage_placement.node_position(
            previous, source, payload, child={"title": requested_title},
        )
        creation = _create_derived_resident(
            source,
            {**payload, "mode": mode, "title": requested_title},
            actor_id,
            lineage_source,
        )
        created_resident_id = _created_resident_id(creation)
        if not created_resident_id:
            raise RuntimeError("ForkResident 响应缺少 resident_id")
        if any(
            _node_resident_id(node) == created_resident_id
            for node in previous.get("nodes") or []
            if isinstance(node, dict)
        ):
            raise ValueError("新建 Resident 已存在于当前 HATG")
        try:
            lineage, title_sync_status, title_sync_error = _attach_lineage_branch(
                previous, payload, source, source_node_id, creation, created_resident_id,
                requested_title=requested_title, position=(child_x, child_y),
                edge_id=edge_id, context_source_resident_id=context_source_resident_id,
                mode=mode, actor_id=actor_id,
            )
        except CommittedProjectionError:
            raise  # the graph committed; only its projections are pending
        except Exception:
            # A failed Fork leaves nothing behind: the child Resident exists
            # already, so it is deleted again (TAU resident.fork-handoff).
            _discard_unattached_branch(lineage_id, created_resident_id, actor_id)
            raise
        return _lineage_branch_response(
            lineage,
            created_resident_id,
            actor_id,
            created=True,
            title_sync_status=title_sync_status,
            title_sync_error=title_sync_error,
        )


def _discard_unattached_branch(lineage_id: str, resident_id: str, actor: str) -> None:
    try:
        from . import resident_intake_domain, resident_execution_work
        from .resident_workspace_domain import _find_resident
        record = _find_resident(resident_id)
        job = resident_execution_work.read(record) if record else {}
        if job.get("operation") == "context_fork_create" and not resident_execution_work.submitted_execution_id(job) and not job.get("continuation_id"):
            # This unattached child has only preparation work, never a dispatched turn.
            resident_intake_domain._cancel_resident_execution_for_target(
                resident_id, actor, str(job.get("user_message_id") or ""), continue_queue=False,
            )
        delete_lineage_graph_objects(lineage_id, {"resident_ids": [resident_id]}, actor)
    except Exception:  # noqa: BLE001 - the Fork's own error is the one to report
        LOGGER.exception("Failed Fork could not delete its child Resident %s in %s", resident_id, lineage_id)


def _locked_branch_definition(previous: dict[str, Any], actor: str) -> dict[str, Any]:
    """Refresh a branch's definition inside its short validation/commit transaction."""
    current = _lineage_catalog_store().state_store.lock_lineage_definition(previous["lineage_id"])
    if current is None:
        raise LineageConflictError("Fork Lineage was deleted during preparation")
    refreshed = _clone_json(previous)
    _adopt_committed_definition(refreshed, current)
    _ensure_manager(refreshed, actor)
    return refreshed


def _attach_lineage_branch(
    previous: dict[str, Any],
    payload: dict[str, Any],
    source: dict[str, Any],
    source_node_id: str,
    creation: dict[str, Any],
    created_resident_id: str,
    *,
    requested_title: str,
    position: tuple[Any, Any],
    edge_id: str,
    context_source_resident_id: str,
    mode: str,
    actor_id: str,
) -> tuple[dict[str, Any], str, str]:
    """Add the created child and its inheritance edge to the graph; returns (lineage, title status, title error)."""
    child_x, child_y = position
    now = _now_iso()
    title = requested_title
    child = lineage_branch_transitions.branch_child(
        source, creation,
        resident_id=created_resident_id,
        title=title,
        position=(child_x, child_y),
        now=now,
    )

    title_sync_status = ""
    title_sync_error = ""
    try:
        if str(creation.get("label") or "").strip() != title:
            _rename_resident_lineage_node(child, title, actor_id)
        title_sync_status = "synced"
    except Exception as exc:
        title_sync_status = "failed"
        title_sync_error = _text(exc, 1000)

    edge = lineage_branch_transitions.branch_edge(
        edge_id=edge_id,
        source_id=source_node_id,
        target_id=created_resident_id,
        context_source_resident_id=context_source_resident_id,
        mode=mode,
        message=(payload.get("message") if isinstance(payload.get("message"), dict) else {}),
        now=now,
    )
    def commit_branch() -> dict[str, Any]:
        current = _locked_branch_definition(previous, actor_id)
        if source_node_id not in {node["id"] for node in current.get("nodes") or []}:
            raise LineageConflictError("Fork source was deleted during preparation")
        candidate = _clone_json(current)
        # Registering the child already establishes graph membership. Replace
        # that default node with the Fork placement and metadata exactly once.
        candidate["nodes"] = [node for node in candidate.get("nodes") or []
                              if node["id"] != created_resident_id] + [child]
        candidate.setdefault("edges", []).append(edge)
        lineage = _normalize_lineage_payload(candidate, current, actor_id)
        _persist_lineage_state(current, lineage, "创建轨迹分支")
        return lineage

    lineage = _lineage_catalog_store().state_store.transaction(commit_branch)
    return lineage, title_sync_status, title_sync_error


def fork_lineage_resident(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Execute the canonical ForkResident business operation."""

    source_resident_id = _text(
        payload.get("source"),
        80,
    ).lower()
    if not source_resident_id:
        raise ValueError("ForkResident 需要 source")
    if "operation" in payload:
        raise ValueError("ForkResident 不接受 operation；Function Block 使用独立协议")
    fork_mode = _text(
        payload.get("fork_mode"),
        24,
    ).lower() or "full"
    forbidden_fields = [
        field
        for field in ("launch_after_fork", "prompt")
        if field in payload
    ]
    if "message" in payload and fork_mode != "message":
        forbidden_fields.append("message")
    if forbidden_fields:
        raise ValueError(
            "ForkResident 只创建普通 Resident，不接受字段："
            + "、".join(forbidden_fields)
        )
    if fork_mode == "message":
        selector = payload.get("message")
        if (
            not isinstance(selector, dict)
            or set(selector) != {"message_id"}
            or not isinstance(selector.get("message_id"), str)
            or not selector["message_id"].strip()
        ):
            raise ValueError(
                "ForkResident 的 message 只接受历史截止点选择器："
                "{message_id: 非空字符串}；不接受新消息正文"
            )
    effect_id = _text(payload.get("effect_id"), 110).lower()
    trigger_path_id = _text(payload.get("trigger_path_id"), 180)
    edge_id = _text(payload.get("edge_id"), 80).lower()
    if not edge_id:
        idempotency_key = _text(payload.get("idempotency_key"), 240)
        if idempotency_key or trigger_path_id:
            operation_identity = (
                f"{lineage_id}\0{effect_id}\0"
                f"{trigger_path_id}\0{source_resident_id}"
            )
            if idempotency_key:
                operation_identity += f"\0{_safe_actor(actor)}\0{idempotency_key}"
            digest = hashlib.sha256(operation_identity.encode("utf-8")).hexdigest()[:16]
            edge_id = f"edge_instruction_fork_{digest}"
        else:
            edge_id = _item_id("", "edge")
    branch_payload = {**payload}
    branch_payload.pop("source", None)
    branch_payload.pop("fork_mode", None)
    branch_payload.pop("child_title", None)
    response = create_lineage_branch(
        lineage_id,
        {
            **branch_payload,
            "source_resident_id": source_resident_id,
            "edge_id": edge_id,
            "mode": fork_mode,
            "title": _text(payload.get("child_title"), 120),
        },
        actor,
    )
    return response


def _program_seed_component(
    lineage: dict[str, Any],
    seed_id: str,
) -> dict[str, Any] | None:
    program = lineage.get("program") if isinstance(lineage.get("program"), dict) else {}
    return next(
        (
            component
            for component in program.get("components") or []
            if isinstance(component, dict)
            and str(component.get("kind") or "") == "seed"
            and str(component.get("id") or "") == seed_id
        ),
        None,
    )


def promote_seed_node(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Promote a Seed component to an idle Resident; Program decides later work."""

    actor_id = _safe_actor(actor)
    seed_id = _text(payload.get("seed_id"), 80).lower()
    if not seed_id:
        raise ValueError("采纳 Seed 需要 seed_id")
    with _lineage_scope(lineage_id):
        previous = _read_lineage(lineage_id, actor=actor)
        _ensure_manager(previous, actor_id)
        seed = _program_seed_component(previous, seed_id)
        if not seed:
            raise ValueError("Seed 不在当前 HATG Program 中")
        config = seed.get("config") if isinstance(seed.get("config"), dict) else {}
        if str(config.get("status") or "active") != "active":
            raise ValueError("这个 Seed 已经被采纳或抛弃")
        candidate_fork = _candidate_fork(previous, seed_id)
        source_resident_id = str((candidate_fork or {}).get("source") or "")
        source = next(
            (node for node in previous.get("nodes") or [] if str(node.get("id") or "") == source_resident_id),
            None,
        )
        if not candidate_fork or not source:
            raise ValueError("先建立候选 Fork 才能采纳这个 Seed")
        creation = resident_domain.create_resident_full_context_fork(
            _node_resident_id(source),
            actor_id,
            f"lineage:{lineage_id}:{source_resident_id}",
            defer_backend_start=True,
        )
        created_resident_id = _created_resident_id(creation)
        if not created_resident_id:
            raise RuntimeError("创建 Seed Resident 的响应缺少 resident id")
        now = _now_iso()
        title = _text(payload.get("child_title"), 120) or _text(seed.get("title"), 120) or "Seed Resident"
        purpose = _text(payload.get("action_purpose"), 4000)
        child = {
            "id": created_resident_id,
            "title": title,
            "status": "idle",
            "backend": str(source.get("backend") or "codex"),
            "x": _position(seed.get("x"), 50),
            "y": _position(seed.get("y"), 50),
            "origin_seed_id": seed_id,
            "created_at": now,
            "updated_at": now,
        }
        candidate = _clone_json(previous)
        candidate.setdefault("nodes", []).append(child)
        # The candidate fork becomes the inheritance relation to the new Resident.
        candidate["edges"] = [
            {
                "id": candidate_fork["id"],
                "source": source_resident_id,
                "target": created_resident_id,
                "type": "inheritance",
                "label": "Seed 采纳",
                "created_at": now,
                "updated_at": now,
            }
            if str(edge.get("id") or "") == candidate_fork["id"] else edge
            for edge in candidate.get("edges") or []
        ]
        promoted = _program_seed_component(candidate, seed_id)
        promoted.setdefault("config", {}).update({
            "status": "promoted",
            "promoted_resident_id": created_resident_id,
            **({"promotion_purpose": purpose} if purpose else {}),
            "promoted_at": now,
            "updated_at": now,
        })
        lineage = _normalize_lineage_payload(candidate, previous, actor_id)
        _persist_lineage_state(previous, lineage, f"采纳 Seed：{title}")
    verified = _verified_lineage_view(lineage, actor_id)
    return {
        "lineage": verified,
        "seed": _program_seed_component(verified, seed_id),
        "resident": next((node for node in verified.get("nodes") or [] if str(node.get("id") or "") == created_resident_id), child),
        "started": False,
        # Where the candidate is written; the caller sends it to the new Resident with its task.
        "candidate_path": _seed_candidate_path(lineage_id, previous, {**seed, **config}),
    }


def _candidate_fork(lineage: dict[str, Any], seed_id: str) -> dict[str, Any] | None:
    return next((
        edge for edge in lineage.get("edges") or []
        if lineage_definition_normalization.is_candidate_fork(edge) and str(edge.get("target") or "") == seed_id
    ), None)


def set_candidate_fork(lineage_id: str, seed_id: str, source_resident_id: str, actor: str) -> dict[str, Any]:
    """Make ``source_resident_id`` the Resident a Seed's candidate starts from.

    A Seed has one candidate fork; a different source replaces it with a new
    relation.  Returns the candidate fork and the ID of the one it replaced.
    """

    seed_id = _text(seed_id, 80).lower()
    source_resident_id = _text(source_resident_id, 80).lower()

    def edit(body: dict[str, Any]) -> tuple[str, str]:
        seed = _program_seed_component(body, seed_id)
        if not seed:
            raise ValueError("Seed 不在当前 HATG Program 中")
        if str((seed.get("config") or {}).get("status") or "active") != "active":
            raise ValueError("这个 Seed 已经被采纳或抛弃，不能再建立候选 Fork")
        if not any(str(node.get("id") or "") == source_resident_id for node in body.get("nodes") or []):
            raise ValueError("候选 Fork 的来源必须是这个 Lineage 中的 Resident")
        existing = _candidate_fork(body, seed_id)
        if existing and existing.get("source") == source_resident_id:
            return str(existing["id"]), ""
        edge_id = f"edge_candidate_{secrets.token_hex(6)}"
        body["edges"] = [
            edge for edge in body.get("edges") or [] if edge is not existing
        ] + [{"id": edge_id, "source": source_resident_id, "target": seed_id, "type": "candidate_fork"}]
        return edge_id, str((existing or {}).get("id") or "")

    verified, (edge_id, replaced) = commit_lineage_edit(
        lineage_id, actor, edit, reason="建立候选 Fork", full_view=False,
    )
    edge = next((item for item in verified.get("edges") or [] if item.get("id") == edge_id), {})
    return {"edge": edge, "replaced_edge_id": replaced}


def delete_candidate_fork(lineage_id: str, seed_id: str, actor: str) -> str:
    """Remove a Seed's candidate fork; returns its ID.  The Seed stays, without a source."""

    seed_id = _text(seed_id, 80).lower()

    def edit(body: dict[str, Any]) -> str:
        existing = _candidate_fork(body, seed_id)
        if existing is None:
            raise KeyError(f"Seed 没有候选 Fork：{seed_id}")
        body["edges"] = [edge for edge in body.get("edges") or [] if edge is not existing]
        return str(existing["id"])

    _verified, edge_id = commit_lineage_edit(lineage_id, actor, edit, reason="删除候选 Fork", full_view=False)
    return edge_id


def _seed_candidate_path(lineage_id: str, lineage: dict[str, Any], seed: dict[str, Any]) -> str:
    try:
        root = _seed_folder(lineage_id)
        path = (root / _workspace_relative_path(seed.get("artifact_path"), suffix=".md")).resolve(strict=False)
        path.relative_to(root)
    except (OSError, ValueError):
        return ""
    return str(path) if path.is_file() else ""


def _completed_message_evidence(
    node: dict[str, Any],
    message_id: str,
    actor: str,
) -> dict[str, Any]:
    resident_id = _node_resident_id(node)
    return resident_protocol.get_completed_message_evidence(
        resident_id,
        actor,
        message_id,
    )


def _resident_input_reconciliation_state(
    node: dict[str, Any],
    message_id: str,
    actor: str,
) -> dict[str, Any]:
    """Resolve one exact Resident input without an evidence/idle TOCTOU."""

    return resident_protocol.get_input_reconciliation_state(
        _node_resident_id(node),
        actor,
        message_id,
    )


def _node_has_active_execution(node: dict[str, Any], actor: str) -> bool:
    """Read whether the canonical Resident is currently executing work."""
    state = resident_protocol.get_resident_state(
        _node_resident_id(node),
        actor,
    )["state"]
    return str(state.get("work_state") or "idle") != "idle"


def _register_program_agent_invocation(
    lineage_id: str,
    target_id: str,
    message_id: str,
    event: lineage_primitives.LineageEvent,
    source_wire_id: str,
    actor: str,
) -> dict[str, Any]:
    """Commit the authoritative HATG Run link after delivery acknowledgement."""

    trigger_path_id = lineage_program_identity.event_path_id(lineage_id, event)
    event_envelope = event.to_dict()
    event_envelope["payload"] = {
        **dict(event_envelope.get("payload") or {}),
        "trigger_path_id": trigger_path_id,
    }
    invocation = lineage_program_runtime.register_resident_handoff(
        lineage_id,
        message_id=message_id,
        resident_id=target_id,
        trigger_path_id=trigger_path_id,
        event=event_envelope,
        source_wire_id=source_wire_id,
    )
    return invocation


def _pending_event_has_registered_invocation(
    lineage_id: str,
    wire_id: str,
    target_id: str,
    pending: dict[str, Any],
) -> bool:
    event = pending.get("event") if isinstance(pending.get("event"), dict) else {}
    event_id = str(event.get("id") or "")
    if not event_id or not target_id:
        return False
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    trigger_path_id = str(payload.get("trigger_path_id") or "") or lineage_program_identity.trigger_path_id(lineage_id, event_id)
    return any(
        str(invocation.get("resident_id") or "") == target_id
        and str(invocation.get("source_wire_id") or "") == wire_id
        and str((invocation.get("event") or {}).get("id") or "") == event_id
        for invocation in lineage_program_runtime.path_resident_inputs(lineage_id, trigger_path_id)
        if isinstance(invocation, dict)
        and isinstance(invocation.get("event"), dict)
    )


@_lineage_scoped
def dispatch_lineage_program_event(
    lineage_id: str,
    event_source_id: str,
    event: lineage_primitives.LineageEvent | dict[str, Any],
    actor: str,
    *,
    cursor: str,
    expected_event_source_contract: str = "",
    event_source_override: dict[str, Any] | None = None,
    selected_wire_ids: set[str] | None = None,
    preclaimed_wire_ids: set[str] | None = None,
    bypass_unavailable_policy: bool = False,
    invocation_arguments: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return lineage_program_event_dispatch.dispatch_program_event(
        lineage_id, event_source_id, event, actor,
        cursor=cursor,
        expected_event_source_contract=expected_event_source_contract,
        event_source_override=event_source_override,
        selected_wire_ids=selected_wire_ids,
        preclaimed_wire_ids=preclaimed_wire_ids,
        bypass_unavailable_policy=bypass_unavailable_policy,
        invocation_arguments=invocation_arguments,
        authority=_program_event_authority(),
    )


def dispatch_resident_terminal_observers(
    resident_id: str,
    message_id: str,
    *,
    terminal_status: str = "completed",
    completed_at: str = "",
    exclude_lineage_ids: set[str] | None = None,
) -> dict[str, Any]:
    return lineage_program_terminal_reconciliation.dispatch_resident_terminal_observers(
        resident_id,
        message_id,
        terminal_status=terminal_status,
        completed_at=completed_at,
        exclude_lineage_ids=exclude_lineage_ids,
        ports=_program_terminal_ports(),
    )


def _notify_program_resident_invocation_terminal(
    lineage_id: str,
    resident_id: str,
    message_id: str,
    invocations: list[dict[str, Any]],
    *,
    status: str,
) -> bool:
    """Notify from terminal facts; node state is read from the invocation ledger."""

    if not invocations:
        return False
    latest = invocations[-1]
    terminal_status = _text(status, 40) or "failed"
    with _lineage_scope(lineage_id):
        lineage = _read_lineage_state(lineage_id)
        node = next(
            (
                item
                for item in lineage.get("nodes") or []
                if isinstance(item, dict)
                and str(item.get("id") or "") == resident_id
            ),
            None,
        )
        if node is None:
            return False
        current_last_run = (
            node.get("last_execution")
            if isinstance(node.get("last_execution"), dict)
            else {}
        )
        current_message_id = str(current_last_run.get("message_id") or "")
        if current_message_id and current_message_id != message_id:
            # A newer queued invocation owns the visible node projection.
            return False
    return True


def _resume_program_execution_principal(
    lineage_id: str,
    event: lineage_primitives.LineageEvent,
    executor_resident_id: str,
) -> str:
    from management_common.principal import OperationIdentity

    if not event.identity:
        raise PermissionError("Pending invocation has no verified identity; explicit reissue is required")
    identity = OperationIdentity.from_dict(event.identity)
    _ensure_manager(_read_lineage_state(lineage_id), identity.principal.id)
    return authorize_lineage_resident_principal(lineage_id, executor_resident_id)


def emit_program_manual_event(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """TriggerEvent: one manual Event through a source or one of its wires."""

    return lineage_program_backlog.emit_manual_event(lineage_id, payload, actor, authority=_program_control_authority())


def observe_trigger_path(lineage_id: str, payload: dict[str, Any], actor: str) -> dict[str, Any]:
    from . import trigger_path_observation
    with _lineage_scope(lineage_id):
        lineage = _read_lineage(lineage_id, actor=actor)
        _ensure_visible(lineage, _safe_actor(actor))
    return {"lineage_id": lineage_id, **trigger_path_observation.observe(lineage_id, payload)}


def get_pending_deliveries(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    with _lineage_scope(lineage_id):
        lineage = _read_lineage(lineage_id, actor=actor)
        _ensure_visible(lineage, _safe_actor(actor))
        wire_id = str(payload.get("wire_id") or "")
        contracts = _program_invoke_wire_execution_configs(
            _compiled_program(lineage),
        )
        if wire_id not in contracts:
            raise KeyError("Invoke 连线已不存在")
        return {
            "wire_id": wire_id,
            "deliveries": lineage_program_runtime.pending_wire_deliveries(
                lineage_id, wire_id,
            ),
            "runtime": lineage_program_runtime.event_wire_runtime_view(
                lineage_id, {wire_id: contracts[wire_id]["config"]},
            )[wire_id],
        }


def _mutate_pending_delivery(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
    *,
    delete: bool,
) -> dict[str, Any]:
    with lineage_state_scope(lineage_id, "mutate_pending_delivery"):
        lineage = _read_lineage(lineage_id, actor=actor)
        _ensure_manager(lineage, _safe_actor(actor))
        wire_id = str(payload.get("wire_id") or "")
        contracts = _program_invoke_wire_execution_configs(
            _compiled_program(lineage),
        )
        if wire_id not in contracts:
            raise KeyError("Invoke 连线已不存在")
        result = lineage_program_runtime.update_pending_wire_delivery(
            lineage_id,
            wire_id,
            str(payload.get("delivery_id") or ""),
            prompt=payload.get("prompt", ""),
            delete=delete,
        )
        return {**result, **get_pending_deliveries(lineage_id, payload, actor)}


def update_pending_delivery(
    lineage_id: str, payload: dict[str, Any], actor: str,
) -> dict[str, Any]:
    return _mutate_pending_delivery(lineage_id, payload, actor, delete=False)


def delete_pending_delivery(
    lineage_id: str, payload: dict[str, Any], actor: str,
) -> dict[str, Any]:
    return _mutate_pending_delivery(lineage_id, payload, actor, delete=True)


def _scheduled_time_reached(value: object) -> bool:
    return _event_scheduled_time_reached(
        value, now=datetime.now(timezone.utc),
    )


def _role_file_signature(
    lineage_id: str,
    target_node: dict[str, Any],
    relative_path: str,
) -> str:
    root = _node_workspace_path(lineage_id, target_node)
    try:
        path = lineage_primitives.resolve_workspace_relative_path(
            root,
            relative_path,
        )
    except ValueError as exc:
        raise ValueError("文件 Event 路径越出了目标 Workspace") from exc
    try:
        stat = path.stat()
    except FileNotFoundError:
        return "missing"
    except OSError as exc:
        return f"unreadable:{type(exc).__name__}"
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except FileNotFoundError:
        return "missing"
    except OSError as exc:
        return f"unreadable:{type(exc).__name__}"
    return f"sha256:{digest.hexdigest()}:{stat.st_size}"


def _program_resident_state_value(
    lineage: dict[str, Any],
    node: dict[str, Any],
    state_domain: str,
    actor: str,
    state_cache: dict[str, str],
) -> str:
    """Read one state through the module that owns that state domain."""

    if state_domain != resident_state.INTAKE_DOMAIN:
        raise ValueError("未知的 Resident 状态域")
    resident_id = _node_resident_id(node)
    if not resident_id:
        raise ValueError("Resident 节点缺少 resident_id")
    cache_key = resident_id
    if cache_key not in state_cache:
        state_view = resident_protocol.get_resident_state(
            resident_id, actor,
        )["state"]
        state_cache[cache_key] = str(
            state_view.get("intake_state") or "open"
        )
    return state_cache[cache_key]


def _read_resident_activity_snapshot(
    resident_ids: list[str] | None,
) -> dict[str, Any]:
    """Read Resident work state through its single owning subsystem."""

    snapshot = resident_protocol.get_resident_activity_snapshot(resident_ids)
    if not isinstance(snapshot, dict):
        raise RuntimeError(
            "Resident activity resolver returned an invalid snapshot"
        )
    residents = (
        snapshot.get("residents")
        if isinstance(snapshot.get("residents"), dict)
        else {}
    )
    return {
        **snapshot,
        "resident_ids": sorted(
            str(value)
            for value in snapshot.get("resident_ids") or residents
            if str(value)
        ),
        "active_resident_ids": sorted(
            str(value)
            for value in snapshot.get("active_resident_ids") or []
            if str(value)
        ),
        "residents": residents,
    }


@_lineage_scoped
def _reconcile_lineage_program_system_events(
    lineage_id: str,
    actor: str,
    *,
    resident_activity_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return lineage_event_reconciliation.reconcile_lineage_program_system_events(
        lineage_id,
        actor,
        resident_activity_snapshot=resident_activity_snapshot,
        ports=lineage_program_services.event_reconcile_ports(sys.modules[__name__]),
    )


def reconcile_lineage_system_events(
    lineage_id: str,
    actor: str,
    *,
    resident_activity_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Recognize Program-owned time, file, state, and activity Events."""

    actor_id = _safe_actor(actor)
    program_result = _reconcile_lineage_program_system_events(
        lineage_id,
        actor_id,
        resident_activity_snapshot=resident_activity_snapshot,
    )
    backlog = lineage_program_backlog.drain_event_backlog(
        lineage_id, actor_id, authority=_program_control_authority(),
    )
    return {
        "lineage_id": lineage_id,
        "baseline_count": int(
            program_result.get("baseline_count") or 0
        ),
        "event_count": int(program_result.get("event_count") or 0),
        "events": list(program_result.get("events") or []),
        "program_event_backlog": backlog,
        "retry_after_seconds": int(
            backlog.get("retry_after_seconds") or 0
        ),
        "next_wake_at": str(program_result.get("next_wake_at") or ""),
    }


def poll_all_lineage_system_events() -> dict[str, Any]:
    return lineage_program_polling.poll_all_system_events(ports=_program_polling_ports())


def recover_pending_program_work_as_service() -> dict[str, Any]:
    return lineage_program_polling.poll_all_system_events(
        ports=_program_polling_ports(), recovery_only=True,
    )


@_lineage_scoped
def reconcile_lineage_system_events_as_service(
    lineage_id: str,
) -> dict[str, Any]:
    return lineage_program_polling.reconcile_system_events_as_service(
        lineage_id, ports=_program_polling_ports(),
    )


@_lineage_scoped
def dispatch_message_arrival(
    lineage_id: str,
    resident_id: str,
    message_id: str,
) -> dict[str, Any]:
    """Produce ``resident.message_received`` for one accepted message now (decision_44cf587c1721)."""

    return lineage_program_polling.dispatch_arrival_as_service(
        lineage_id, resident_id, message_id,
        ports=_program_polling_ports(),
        dispatch=dispatch_lineage_program_event,
        contract=_program_event_source_contract_fingerprint,
    )


def handle_resident_message_terminal(
    resident_id: str,
    actor: str,
    message_id: str,
    *,
    completed_at: str = "",
    lineage_id: str = "",
    terminal_status: str = "completed",
    program_event_source_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Serialize one Resident terminal with Lineage runtime consumption."""

    from . import resource_client

    result = _handle_resident_message_terminal(
        resident_id, actor, message_id, completed_at=completed_at, lineage_id=lineage_id,
        terminal_status=terminal_status, program_event_source_ids=program_event_source_ids,
    )
    requested = str(lineage_id or "").strip()
    lineage_ids = [requested] if requested else sorted(_lineage_catalog_store().lineage_ids_for_resident(resident_id))
    # An explorer may have added or dismissed candidates in this turn.
    from .templates import explore

    explore.sync_after_turn(lineage_ids, resident_id, actor)
    return result


def _handle_resident_message_terminal(
    resident_id: str,
    actor: str,
    message_id: str,
    *,
    completed_at: str = "",
    lineage_id: str = "",
    terminal_status: str = "completed",
    program_event_source_ids: set[str] | None = None,
) -> dict[str, Any]:
    def matching_lineage_ids() -> set[str]:
        return set(_lineage_catalog_store().lineage_ids_for_resident(resident_id))

    requested_lineage_id = str(lineage_id or "").strip()
    if requested_lineage_id:
        with _lineage_execution_authority(requested_lineage_id):
            with _lineage_scope(requested_lineage_id):
                return lineage_program_terminal_reconciliation.handle_resident_message_terminal_locked(
                    resident_id,
                    actor,
                    message_id,
                    completed_at=completed_at,
                    lineage_id=requested_lineage_id,
                    terminal_status=terminal_status,
                    program_event_source_ids=program_event_source_ids,
                    ports=_program_terminal_ports(),
                )

    # A callback without lineage_id may fan out to every HATG containing the
    # Resident view. Acquire only those Lineage authorities and aggregate locks
    # in canonical order, then verify the committed membership snapshot again.
    while True:
        authority_ids = matching_lineage_ids()
        with _lineage_execution_authorities(authority_ids):
            with _lineage_scopes(
                authority_ids,
                operation="resident.message_terminal",
            ):
                if matching_lineage_ids() != authority_ids:
                    continue
                return lineage_program_terminal_reconciliation.handle_resident_message_terminal_locked(
                    resident_id,
                    actor,
                    message_id,
                    completed_at=completed_at,
                    lineage_id="",
                    terminal_status=terminal_status,
                    program_event_source_ids=program_event_source_ids,
                    ports=_program_terminal_ports(),
                )


def handle_resident_message_completion(
    resident_id: str,
    actor: str,
    message_id: str,
    *,
    completed_at: str = "",
    lineage_id: str = "",
    program_event_source_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Record the completed terminal fact for one Resident work turn."""

    return handle_resident_message_terminal(
        resident_id,
        actor,
        message_id,
        completed_at=completed_at,
        lineage_id=lineage_id,
        terminal_status="completed",
        program_event_source_ids=program_event_source_ids,
    )


def reconcile_lineage_runtime(
    lineage_id: str, actor: str, *, recovery_only: bool = False, drain: bool = True,
) -> dict[str, Any]:
    return lineage_program_terminal_reconciliation.reconcile_runtime_with_authority(
        lineage_id,
        actor,
        ports=_program_terminal_ports(),
        recovery_only=recovery_only,
        drain=drain,
    )


def delete_lineage(lineage_id: str, actor: str) -> dict[str, Any]:
    """Delete a Lineage and its Residents; their folders are removed, not archived."""
    # Resident deletion confirms Resource tombstones after its own commit. Do not
    # hold the Lineage lock while invoking that cross-domain operation.
    with _lineage_scope(lineage_id):
        lineage = _read_lineage_for_deletion(lineage_id, actor)
        _ensure_manager(lineage, actor)
        if (lineage.get("principal_hierarchy") or {}).get("level") == "project":
            from .principal_hierarchy import project_leaders
            if project_leaders(lineage, _lineage_catalog_store().listing_records()):
                raise ValueError("Project principal still manages child Lineages")
        preferred_resident_id = _text(lineage.get("leader_resident_id"), 80).lower()

    result = resident_domain.delete_registered_lineage(
        lineage_id, preferred_resident_id, actor,
    )
    if result is not None:
        from . import system_workflow_switches
        system_workflow_switches.defer_definition_projection(lineage_id, lineage)
        return {
            "lineage_id": lineage_id,
            "deleted_at": str(result.get("deleted_at") or _now_iso()),
            "deleted_resident_ids": result.get("deleted_resident_ids") or [],
        }

    with _lineage_scope(lineage_id):
        lineage = _read_lineage_for_deletion(lineage_id, actor)
        _ensure_manager(lineage, actor)
        if (lineage.get("principal_hierarchy") or {}).get("level") == "project":
            from .principal_hierarchy import project_leaders
            if project_leaders(lineage, _lineage_catalog_store().listing_records()):
                raise ValueError("Project principal still manages child Lineages")
        if resident_catalog.get_catalog().state_store.residents_for_lineage(lineage_id):
            raise LineageConflictError("Lineage membership changed during deletion")
        intent = _stage_empty_lineage_deletion(lineage, actor)

    operation_id = str(intent["operation_id"])
    try:
        _sync_deleted_lineage_resource_identity(
            lineage,
            str(intent["deleted_by"]),
            str(intent["deleted_at"]),
        )
        _purge_deleted_lineage_folder(intent)
        lineage_storage.unbind_lineage_folder(
            lineage_id,
            folder_root=_lineages_root(),
            expected_target=str(intent["source_path"]),
        )
    except BaseException as exc:
        _record_lineage_deletion_error(lineage_id, operation_id, exc)
        raise

    removed = False
    with _lineage_scope(lineage_id):
        current = _lineage_deletion_intent(lineage_id)
        if current:
            if str(current.get("operation_id") or "") != operation_id:
                raise LineageConflictError("Lineage deletion operation changed")
            latest = _lineage_catalog_store().get(lineage_id)
            if int(latest.get("revision") or 0) != int(
                intent["definition_revision"]
            ):
                raise LineageConflictError("Lineage changed after deletion was prepared")
            _lineage_catalog_store().remove(lineage_id, deleted_by=str(intent["deleted_by"]))
            removed = True

    if removed:
        from . import system_workflow_switches
        system_workflow_switches.defer_definition_projection(lineage_id, lineage)
        from management_portal.society_service.changes import lineage_changed

        lineage_changed(lineage_id)
    return {
        "lineage_id": lineage_id,
        "operation_id": operation_id,
        "deleted_at": str(intent["deleted_at"]),
    }


def _resident_workspace_roots(resident_id: str, actor: str) -> dict[str, Any]:
    return resident_domain.get_resident_workspace_roots(resident_id, actor)


def _mount_resident_reference_workspace(
    resident_id: str,
    actor: str,
    workspace_path: str,
    *,
    workspace_id: str = "",
    purpose: str = "",
) -> dict[str, Any]:
    return resident_domain.add_resident_reference_workspace(
        resident_id,
        workspace_path,
        actor,
        workspace_id=workspace_id,
        purpose=purpose,
    )


def _node_workspace_context(lineage_id: str, resident_id: str, actor: str) -> tuple[dict[str, Any], dict[str, Any]]:
    lineage = _read_lineage(lineage_id, actor=actor)
    _ensure_visible(lineage, actor)
    node = next(
        (
            item for item in lineage.get("nodes") or []
            if isinstance(item, dict) and str(item.get("id") or "") == str(resident_id or "")
        ),
        None,
    )
    if not node:
        raise KeyError("Lineage node not found")
    return _clone_json(lineage), _clone_json(node)


def bind_node_workspace(lineage_id: str, payload: dict[str, Any], actor: str) -> dict[str, Any]:
    """Add another Workspace without replacing the node cwd."""
    actor_id = _safe_actor(actor)
    resident_id = _text(payload.get("resident_id"), 80).lower()
    mounted = add_node_reference_workspace(
        lineage_id, resident_id, actor_id, str(payload.get("path") or ""), purpose=str(payload.get("purpose") or ""),
    )
    lineage = _read_lineage(lineage_id, actor=actor_id)
    return {
        "lineage": _verified_lineage_view(lineage, actor_id),
        "workspace": mounted["workspace"],
        "mount": mounted["mount"],
    }


def unbind_node_workspace(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Remove one Resident Workspace binding without deleting its files."""

    actor_id = _safe_actor(actor)
    resident_id, workspace_id, workspace_path = (
        lineage_workspace_binding.unbind_selector(payload)
    )
    _lineage, node = _node_workspace_context(
        lineage_id,
        resident_id,
        actor_id,
    )
    node_resident_id = _node_resident_id(node)
    if not node_resident_id:
        raise ValueError("节点尚未绑定 Resident")
    removed = resident_domain.remove_resident_reference_workspace(
        node_resident_id, actor_id,
        workspace_id=workspace_id,
        workspace_path=workspace_path,
    )
    lineage = _read_lineage(lineage_id, actor=actor_id)
    return {
        "lineage": _verified_lineage_view(lineage, actor_id),
        "workspace_binding": removed,
    }


def get_node_workspaces(lineage_id: str, resident_id: str, actor: str) -> dict[str, Any]:
    """The node Resident's authorized Workspace bindings."""
    _, node = _node_workspace_context(lineage_id, resident_id, actor)
    resident_id = _node_resident_id(node)
    return lineage_node_workspaces.build_node_workspaces(
        lineage_id, node, resident_id, _resident_workspace_roots(resident_id, actor),
    )


def add_node_reference_workspace(
    lineage_id: str,
    resident_id: str,
    actor: str,
    workspace_path: str,
    *,
    workspace_id: str = "",
    purpose: str = "",
) -> dict[str, Any]:
    """Mount a reference directory through the selected node's Resident binding."""
    lineage, node = _node_workspace_context(lineage_id, resident_id, actor)
    return lineage_workspace_binding.mount_reference(
        lineage_id=lineage_id,
        node=node,
        lineage_nodes=lineage.get("nodes") or [],
        node_resident_id=_node_resident_id(node),
        actor=actor,
        workspace_path=workspace_path,
        workspace_id=workspace_id,
        purpose=purpose,
        mount=_mount_resident_reference_workspace,
        workspaces=get_node_workspaces,
        node_resident=_node_resident_id,
    )


def _handoff_allowed_target_roots(lineage_id: str) -> list[Path]:
    roots = [
        _lineage_dir(lineage_id) / "handoff-workspaces",
        lineage_handoff_preparation.EXPLORATIONS_ROOT,
    ]
    configured = os.getenv("PORTAL_HANDOFF_TARGET_ROOTS", "")
    roots.extend(Path(value.strip()).expanduser() for value in configured.split(os.pathsep) if value.strip())
    return [root.resolve(strict=False) for root in roots]


def _handoff_workspace_root(
    lineage_id: str,
    resident_id: str,
    actor: str,
    selector: dict[str, Any],
) -> dict[str, Any]:
    workspace = get_node_workspaces(lineage_id, resident_id, actor)
    workspace_id = _text(selector.get("workspace_id"), 80).lower()
    raw_path = lineage_node_workspaces.root_path(selector.get("path"))
    roots = workspace["workspaces"]
    if not (workspace_id or raw_path):
        # An item that names no source hands over the Workspace the node works in.
        matches = [root for root in roots if root.get("is_default_cwd")]
    else:
        matches = [
            root for root in roots
            if (
                (workspace_id and str(root.get("workspace_id") or "").lower() == workspace_id)
                or (raw_path and lineage_node_workspaces.root_path(root.get("path")) == raw_path)
            )
        ]
    if not matches:
        raise ValueError("来源必须是该节点已经绑定的 Workspace；Resident Folder、Lineage Folder 和知识库不能直接交接")
    root = matches[0]
    if not Path(str(root.get("path") or "")).is_dir():
        raise FileNotFoundError("来源 Workspace 不存在")
    return root


def _handoff_edge(lineage: dict[str, Any], handoff_id: str) -> dict[str, Any]:
    edge = next(
        (
            item for item in lineage.get("edges") or []
            if isinstance(item, dict)
            and item.get("type") == "handoff"
            and str(item.get("id") or "") == handoff_id
        ),
        None,
    )
    if not edge:
        raise KeyError("Handoff not found")
    return edge


def _save_handoff_lineage(
    previous: dict[str, Any],
    lineage: dict[str, Any],
    actor: str,
    reason: str,
    *,
    runtime: bool = False,
) -> dict[str, Any]:
    if runtime:
        # Handoff execution mutates only runtime fields on an already
        # normalized edge. Revalidate the changed Handoff edge itself, not
        # unrelated Graph authoring objects such as Program components or
        # reusable Function Blocks.
        saved = _clone_json(lineage)
        resident_ids = {
            str(node.get("id") or "")
            for node in saved.get("nodes") or []
            if isinstance(node, dict) and str(node.get("id") or "")
        }
        previous_edges = {
            str(edge.get("id") or ""): edge
            for edge in previous.get("edges") or []
            if isinstance(edge, dict) and str(edge.get("id") or "")
        }
        saved["edges"] = [
            _normalize_edge(
                edge,
                resident_ids,
                previous_edges.get(str(edge.get("id") or "")),
            )
            if (
                isinstance(edge, dict)
                and str(edge.get("type") or "") == "handoff"
                and edge
                != previous_edges.get(str(edge.get("id") or ""))
            )
            else edge
            for edge in saved.get("edges") or []
        ]
        _persist_lineage_runtime_state(previous, saved, reason)
        return saved
    normalized = _normalize_lineage_payload(lineage, previous, actor)
    _persist_lineage_state(previous, normalized, reason)
    return normalized


def create_handoff(
    lineage_id: str, payload: dict[str, Any], actor: str, *,
    full_view: bool = True, idempotent: bool = False,
) -> dict[str, Any]:
    """Create one canonical Handoff edge and optionally prepare its Workspace items."""
    actor_id = _safe_actor(actor)
    source_resident_id = _text(payload.get("source_resident_id"), 80).lower()
    target_resident_id = _text(payload.get("target_resident_id"), 80).lower()
    if not source_resident_id or not target_resident_id:
        raise ValueError("Handoff 需要来源节点和目标节点")
    if source_resident_id == target_resident_id:
        raise ValueError("Handoff 不能交接给来源节点自身")
    handoff_id = _item_id(payload.get("handoff_id"), "handoff")
    with _lineage_scope(lineage_id):
        previous = _read_lineage(lineage_id, actor=actor)
        _ensure_manager(previous, actor_id)
        nodes = {
            str(node.get("id") or ""): node
            for node in previous.get("nodes") or []
            if isinstance(node, dict)
        }
        if source_resident_id not in nodes or target_resident_id not in nodes:
            raise KeyError("Handoff 的来源或目标 Lineage 节点不存在")
        if any(str(edge.get("id") or "") == handoff_id for edge in previous.get("edges") or []):
            raise ValueError("Handoff id 已存在")
        existing = next((edge for edge in previous.get("edges") or []
            if isinstance(edge, dict) and str(edge.get("type") or "") == "handoff"
            and str(edge.get("source") or "") == source_resident_id
            and str(edge.get("target") or "") == target_resident_id), None)
        if existing is not None:
            if not idempotent:
                raise ValueError("相同来源和接收方只能有一个 Workspace Handoff")
            result = {"handoff": existing, "created": False}
            if full_view:
                result["lineage"] = _verified_lineage_view(previous, actor_id)
            return result

    items = lineage_handoff_preparation.draft_items(
        lineage_id=lineage_id,
        handoff_id=handoff_id,
        source_id=source_resident_id,
        target_title=nodes[target_resident_id].get("title"),
        payload=payload,
        actor=actor_id,
        lineage_folder=_lineage_dir(lineage_id),
        source_workspace=_handoff_workspace_root,
        root_path=lineage_node_workspaces.root_path,
    )

    with _lineage_scope(lineage_id):
        previous = _read_lineage(lineage_id, actor=actor)
        _ensure_manager(previous, actor_id)
        current_nodes = {str(node.get("id") or "") for node in previous.get("nodes") or []}
        if source_resident_id not in current_nodes or target_resident_id not in current_nodes:
            raise LineageConflictError("Handoff 的来源或目标 Lineage 节点已变化")
        if any(str(edge.get("id") or "") == handoff_id for edge in previous.get("edges") or []):
            raise LineageConflictError("Handoff id 已存在")
        existing = next((edge for edge in previous.get("edges") or []
            if isinstance(edge, dict) and str(edge.get("type") or "") == "handoff"
            and str(edge.get("source") or "") == source_resident_id
            and str(edge.get("target") or "") == target_resident_id), None)
        if existing is not None:
            if not idempotent:
                raise LineageConflictError("相同来源和接收方只能有一个 Workspace Handoff")
            result = {"handoff": existing, "created": False}
            if full_view:
                result["lineage"] = _verified_lineage_view(previous, actor_id)
            return result
        edge = {
            "id": handoff_id,
            "source": source_resident_id,
            "target": target_resident_id,
            "type": "handoff",
            "label": _text(payload.get("label"), 120) or "工作区交接",
            "explanation": _text(payload.get("explanation"), 8000),
            "items": items,
            "status": "draft",
            "error": "",
            "created_at": _now_iso(),
        }
        lineage = _clone_json(previous)
        lineage.setdefault("edges", []).append(edge)
        saved = _save_handoff_lineage(previous, lineage, actor_id, "创建工作区 Handoff")

    if _bool(payload.get("execute_now"), False):
        return execute_handoff(
            lineage_id,
            handoff_id,
            actor_id,
        )
    result = {"handoff": _handoff_edge(saved, handoff_id), "created": True}
    if full_view:
        result["lineage"] = _verified_lineage_view(saved, actor_id)
    return result


def get_handoff(lineage_id: str, handoff_id: str, actor: str, *, full_view: bool = True) -> dict[str, Any]:
    actor_id = _safe_actor(actor)
    lineage = _read_lineage(lineage_id, actor=actor_id)
    _ensure_visible(lineage, actor_id)
    result = {"handoff": _handoff_edge(lineage, handoff_id)}
    if full_view:
        result["lineage"] = _verified_lineage_view(lineage, actor_id)
    return result


def update_handoff(
    lineage_id: str, handoff_id: str, changes: dict[str, Any], actor: str, *, full_view: bool = True,
) -> dict[str, Any]:
    """Change a handoff's items, explanation or label; prepared results stay as they are."""

    actor_id = _safe_actor(actor)
    unknown = sorted(set(changes) - {"items", "explanation", "label"})
    if unknown:
        raise ValueError("交接关系只能修改 items、explanation、label：" + "、".join(unknown))
    items = None
    if "items" in changes:
        current = _read_lineage(lineage_id, actor=actor_id)
        edge = _handoff_edge(current, handoff_id)
        target = next(
            (node for node in current.get("nodes") or [] if node.get("id") == edge.get("target")), {},
        )
        items = lineage_handoff_preparation.draft_items(
            lineage_id=lineage_id,
            handoff_id=handoff_id,
            source_id=str(edge.get("source") or ""),
            target_title=target.get("title"),
            payload={"items": changes.get("items")},
            actor=actor_id,
            lineage_folder=_lineage_dir(lineage_id),
            source_workspace=_handoff_workspace_root,
            root_path=lineage_node_workspaces.root_path,
        )
    with _lineage_scope(lineage_id):
        previous = _read_lineage(lineage_id, actor=actor)
        _ensure_manager(previous, actor_id)
        lineage = _clone_json(previous)
        edge = _handoff_edge(lineage, handoff_id)
        if items is not None and edge.get("status") in {"ready", "preparing"}:
            # Prepared results belong to the items they were prepared from;
            # changing items afterwards would show them as ready unprepared.
            raise ValueError(
                "交接已准备或正在准备，不能再修改交接项；需要交接其他内容时删除这条交接后重新建立"
            )
        if items is not None:
            edge["items"] = items
        if "explanation" in changes:
            edge["explanation"] = _text(changes.get("explanation"), 8000)
        if "label" in changes:
            edge["label"] = _text(changes.get("label"), 120) or "工作区交接"
        edge["updated_at"] = _now_iso()
        saved = _save_handoff_lineage(previous, lineage, actor_id, "修改工作区 Handoff")
    result = {"handoff": _handoff_edge(saved, handoff_id)}
    if full_view:
        result["lineage"] = _verified_lineage_view(saved, actor_id)
    return result


def delete_handoff(lineage_id: str, handoff_id: str, actor: str, *, full_view: bool = True) -> dict[str, Any]:
    """Remove a handoff relation; prepared files and bindings keep their own owners."""

    actor_id = _safe_actor(actor)
    with _lineage_scope(lineage_id):
        previous = _read_lineage(lineage_id, actor=actor)
        _ensure_manager(previous, actor_id)
        _handoff_edge(previous, handoff_id)
        lineage = _clone_json(previous)
        lineage["edges"] = [
            edge for edge in lineage.get("edges") or [] if str(edge.get("id") or "") != handoff_id
        ]
        saved = _save_handoff_lineage(previous, lineage, actor_id, "删除工作区 Handoff")
    result = {"handoff_id": handoff_id}
    if full_view:
        result["lineage"] = _verified_lineage_view(saved, actor_id)
    return result


def _set_handoff_failure(
    lineage_id: str,
    handoff_id: str,
    actor: str,
    error: str,
    prepared: list[tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    lineage_handoff_preparation.cleanup_items(prepared)
    with _lineage_scope(lineage_id):
        previous = _read_lineage(lineage_id, actor=actor)
        lineage = _clone_json(previous)
        handoff = _handoff_edge(lineage, handoff_id)
        lineage_handoff_execution.fail_attempt(handoff, error, _now_iso())
        saved = _save_handoff_lineage(
            previous,
            lineage,
            actor,
            "Handoff 准备失败",
            runtime=True,
        )
    return {
        "lineage": _verified_lineage_view(saved, actor),
        "handoff": _handoff_edge(saved, handoff_id),
        "created": False,
    }


def execute_handoff(
    lineage_id: str,
    handoff_id: str,
    actor: str,
) -> dict[str, Any]:
    """Prepare and bind Workspace items without dispatching a Resident task."""
    actor_id = _safe_actor(actor)
    target_handoff_id = _text(handoff_id, 80).lower()
    with _lineage_scope(lineage_id):
        previous = _read_lineage(lineage_id, actor=actor)
        _ensure_manager(previous, actor_id)
        handoff = _handoff_edge(previous, target_handoff_id)
        if handoff.get("status") == "preparing":
            raise LineageConflictError("Handoff 正在准备，请等待当前 attempt 完成")
        if handoff.get("status") == "ready":
            return {
                "lineage": _verified_lineage_view(previous, actor_id),
                "handoff": handoff,
                "created": False,
            }
        else:
            lineage = _clone_json(previous)
            preparing = _handoff_edge(lineage, target_handoff_id)
            lineage_handoff_execution.begin_attempt(preparing, _now_iso())
            previous = _save_handoff_lineage(
                previous,
                lineage,
                actor_id,
                "开始准备 Handoff",
                runtime=True,
            )
            handoff = _handoff_edge(previous, target_handoff_id)

    if handoff.get("status") != "ready":
        prepared: list[tuple[dict[str, Any], dict[str, Any]]] = []
        try:
            lineage_handoff_preparation.prepare_items(
                lineage_id, handoff, actor_id,
                lineage_handoff_preparation.HandoffWorkspaceAccess(
                    workspaces=get_node_workspaces,
                    bind=add_node_reference_workspace,
                    allowed_target_roots=_handoff_allowed_target_roots,
                    root_path=lineage_node_workspaces.root_path,
                    now=_now_iso,
                    max_files=MAX_WORKSPACE_COPY_FILES,
                    max_bytes=MAX_WORKSPACE_COPY_BYTES,
                ),
                prepared,
            )
        except Exception as exc:
            _set_handoff_failure(
                lineage_id,
                target_handoff_id,
                actor_id,
                _text(exc, 4000) or "Handoff 准备失败",
                prepared,
            )
            raise

        with _lineage_scope(lineage_id):
            previous = _read_lineage(lineage_id, actor=actor)
            lineage = _clone_json(previous)
            ready = _handoff_edge(lineage, target_handoff_id)
            lineage_handoff_execution.complete_attempt(ready, prepared, _now_iso())
            saved = _save_handoff_lineage(
                previous,
                lineage,
                actor_id,
                "Handoff Workspace 已就绪",
                runtime=True,
            )
            handoff = _handoff_edge(saved, target_handoff_id)
    else:
        saved = previous

    return {
        "lineage": _verified_lineage_view(saved, actor_id),
        "handoff": handoff,
        "created": False,
    }


def _program_execution_ports() -> lineage_program_execution.ProgramExecutionPorts:
    return lineage_program_services.execution_ports(sys.modules[__name__])


def _program_polling_ports() -> lineage_program_polling.ProgramPollingPorts:
    return lineage_program_services.polling_ports(sys.modules[__name__])


def _program_terminal_ports() -> lineage_program_terminal_reconciliation.ProgramTerminalPorts:
    return lineage_program_services.terminal_ports(sys.modules[__name__])


def _program_event_authority() -> lineage_program_event_dispatch.ProgramEventAuthority:
    return lineage_program_services.event_authority(sys.modules[__name__])


def _program_control_authority() -> lineage_program_backlog.ProgramControlAuthority:
    return lineage_program_services.control_authority(sys.modules[__name__])
