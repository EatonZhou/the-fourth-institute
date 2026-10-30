"""The stored Lineage aggregate: its canonical reads, change scopes, Program authority and manager check.

Code that reads or guards a Lineage uses this module; the Lineage operation
modules (``lineage_state`` and those built on it) edit the aggregate, and
``lineage_domain`` owns the runtime entry points.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from management_common.portal_paths import path as portal_path

from . import (
    business_fact_views,
    invariant_locks,
    lineage_access_policy,
    lineage_catalog,
    lineage_execution_authority,
    lineage_state_domains,
)
from .errors import LineageConflictError
from .lineage_access_policy import actor_can_manage_lineage as _actor_can_manage_lineage
from .lineage_values import _text


DEFAULT_DATA_ROOT = (portal_path("staging_root") / "shared" / "portal_lineages")
DATA_ROOT = Path(os.getenv(
    "PORTAL_LINEAGE_ROOT",
    str(DEFAULT_DATA_ROOT),
)).expanduser()
invariant_locks.configure_root_provider(lambda: DATA_ROOT)


def lineage_catalog_store() -> lineage_catalog.LineageCatalog:
    return lineage_catalog.get_catalog()


@contextmanager
def lineage_scope(
    lineage_id: str,
    *,
    operation: str = "",
) -> Iterator[None]:
    """One Lineage operation; its deferred effects run after it ends (no lock)."""

    with invariant_locks.lineage_scope(lineage_id, operation=operation):
        yield


@contextmanager
def execution_authority(lineage_id: str) -> Iterator[None]:
    with lineage_execution_authority.execution_authority(lineage_id, root=DATA_ROOT):
        yield

@contextmanager
def execution_authorities(
    lineage_ids: list[str] | set[str] | tuple[str, ...],
) -> Iterator[None]:
    with lineage_execution_authority.execution_authorities(lineage_ids, root=DATA_ROOT):
        yield


def read_lineage_state(lineage_id: str) -> dict[str, Any]:
    """Read the canonical aggregate without re-entering ResourceService."""

    catalog = lineage_catalog_store()
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


def read_lineage(lineage_id: str, *, actor: str) -> dict[str, Any]:
    if lineage_deletion_intent(lineage_id):
        raise LineageConflictError("Lineage deletion is in progress")
    payload = read_lineage_state(lineage_id)
    payload = business_fact_views.canonical_view(
        payload,
        resource_id=lineage_id,
        actor=_text(actor, 120),
    )
    return payload


def lineage_deletion_intent(lineage_id: str) -> dict[str, Any]:
    return lineage_catalog_store().state_store.lineage_deletion_operations.get(lineage_id)


def ensure_manager(lineage: dict[str, Any], actor: str) -> None:
    if not _actor_can_manage_lineage(lineage, actor):
        raise lineage_access_policy.lineage_refusal(lineage)
