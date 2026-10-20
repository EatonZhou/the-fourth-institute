from __future__ import annotations

import os
from pathlib import Path

from management_common.instance_paths import instance_root


def runs_lineage_timers(instance: str = "") -> bool:
    """Whether this Portal instance may wake Lineages for their due time sources.

    Dev and Release share Lineage data; a lease in that data lets one eligible
    instance at a time run the timers, and another takes over when it stops.
    ``PORTAL_LINEAGE_TIMER_INSTANCE`` restricts the timers to one instance.
    """

    current = str(instance or os.getenv("PORTAL_INSTANCE", "dev") or "dev").strip()
    only = str(os.getenv("PORTAL_LINEAGE_TIMER_INSTANCE", "") or "").strip()
    return not only or current == only


def syncs_template_structure(instance: str = "") -> bool:
    """Whether this instance regenerates template-owned structure when it starts.

    Dev may run unpublished template code against the shared Lineage data, so
    only one instance syncs: ``PORTAL_TEMPLATE_SYNC_INSTANCE``, Release by
    default. Every publish restarts it, so a published template reaches all
    existing Lineages.
    """

    current = str(instance or os.getenv("PORTAL_INSTANCE", "dev") or "dev").strip()
    return current == (str(os.getenv("PORTAL_TEMPLATE_SYNC_INSTANCE", "") or "").strip() or "release")


def society_service_mode() -> str:
    return str(os.getenv("PORTAL_SOCIETY_SERVICE_MODE", "local") or "local").strip()


def society_service_socket(instance: str = "") -> Path:
    clean_instance = str(
        instance or os.getenv("PORTAL_INSTANCE", "dev") or "dev"
    ).strip()
    configured = str(os.getenv("PORTAL_SOCIETY_SERVICE_SOCKET", "") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(
        f'{instance_root()}/{clean_instance}/society-service.sock'
    )
