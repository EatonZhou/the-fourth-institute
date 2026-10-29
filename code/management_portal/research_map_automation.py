"""The research map's daily digest, drawn into the Project's Project-layer Lineage.

Once a day a ``time.reached`` Event starts an Effect that asks
``GetResearchMapDigest`` what changed on the map since the last digest.  Only
when something did does it deliver the digest to the Project Resident, the
Lineage's Leader, whose duty is to tidy those directions; a quiet day costs
no model call.  The Effect runs as the Leader, so the Project Resident's own
edits are not news to itself.  The watermark is the Lineage shared state
``research_map.digest_until``, moved on only after the delivery.

The flow is installed the first time a map changes (``ensure``), as an
ordinary graph edit by whoever changed it; the components carry client keys,
so a flow someone deleted afterwards is not drawn again.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

STATE_KEY = "research_map.digest_until"
DIGEST_TIME = "07:30:00"
TITLE = "研究地图每日整理"
SCRIPT = r'''lineage=$(GetLineage lineage_id="${arguments.lineage_id}")
reviewed=$(GetSharedState lineage_id="${arguments.lineage_id}" key="research_map.digest_until")
digest=$(GetResearchMapDigest project_id="${lineage.lineage.project_id}" since="${reviewed.value}")
if [ "${digest.count}" -eq 0 ]; then
  UpdateSharedState lineage_id="${arguments.lineage_id}" key="research_map.digest_until" value="${digest.until}"
  output status="quiet"
  exit
fi
sent=$(DeliverMessage resident_id="${endpoint.target}" message="${digest.prompt}")
UpdateSharedState lineage_id="${arguments.lineage_id}" key="research_map.digest_until" value="${digest.until}"
output status="requested" count="${digest.count}" message_id="${sent.message_id}"'''


def key(role: str) -> str:
    return f"research-map:digest:{role}"


def local_timezone() -> str:
    """The host's IANA time zone, so the digest arrives in the people's morning."""
    if os.getenv("TZ", "").strip("/:"):
        return os.environ["TZ"].strip(":")
    try:
        target = os.readlink("/etc/localtime")
    except OSError:
        return "UTC"
    marker = "zoneinfo/"
    return target.split(marker, 1)[1] if marker in target else "UTC"


def operations(lineage_id: str, resident_id: str) -> list[dict[str, Any]]:
    """Event → Effect → Project Resident; ApplyLineageChanges allocates the IDs."""
    return [
        {"op": "create", "kind": "component", "ref": "digest_event", "value": {
            "kind": "event_source", "title": TITLE, "client_key": key("event"),
            "config": {"event_type": "time.reached", "schedule_mode": "recurring", "recurrence_kind": "calendar",
                       "timezone": local_timezone(), "calendar_frequency": "daily", "calendar_time": DIGEST_TIME,
                       "misfire_policy": "coalesce_latest", "overlap_policy": "queue_one"},
        }},
        {"op": "create", "kind": "component", "ref": "digest_effect", "value": {
            "kind": "effect", "title": TITLE + " · 有新动静时交给 Project Resident", "client_key": key("effect"),
            "config": {"script": SCRIPT, "endpoints": ["target"]},
        }},
        {"op": "create", "kind": "wire", "value": {
            "kind": "route", "client_key": key("invoke"),
            "source": "$ref:digest_event", "source_port": "output", "target": "$ref:digest_effect", "target_port": "invoke",
            "config": {"include_envelope": False, "invoke_profiles": {"effect": {
                "arguments": {"lineage_id": {"value": lineage_id}}}}},
        }},
        {"op": "create", "kind": "wire", "value": {
            "kind": "target", "client_key": key("target"),
            "source": "$ref:digest_effect", "source_port": "target", "target": resident_id,
        }},
    ]


def ensure(project_id: str, actor: str) -> str:
    """Draw the daily digest into the Project-layer Lineage once; never fails the map change that asked.

    Returns ``installed`` (already there or drawn now), ``unavailable`` (the
    Project has no Project Resident yet) or ``failed`` (tried again on the next
    map change, for example when this actor may not edit the Lineage).
    """
    from management_portal import isa_client, research_map_store as store
    from management_portal.society_service import get_society_service

    if store.automation(project_id):
        return "installed"
    try:
        team = get_society_service().project.project_teams([project_id], actor).get(project_id) or {}
        lineage_id, resident_id = team.get("project_lineage_id") or "", team.get("project_resident_id") or ""
        if not lineage_id or not resident_id:
            return "unavailable"
        graph = isa_client.call(actor, "GetLineage", {"lineage_id": lineage_id})["lineage"]
        components = (graph.get("program") or {}).get("components") or []
        if not any(item.get("client_key") == key("event") for item in components):
            isa_client.call(actor, "ApplyLineageChanges", {
                "lineage_id": lineage_id, "operations": operations(lineage_id, resident_id),
                "near_resident_id": resident_id,
            }, version=graph["revision"], key=f"{key('install')}:{lineage_id}:{graph['revision']}")
        store.record_automation(project_id, lineage_id, actor=actor)
        return "installed"
    except Exception as error:  # noqa: BLE001 - the map change itself already committed
        LOGGER.warning("Research map digest not installed for %s by %s: %s", project_id, actor, error)
        return "failed"


def skill_path() -> Path:
    from management_common.portal_paths import path as portal_path

    return portal_path("share_root") / "Skills" / "write-research-map" / "daily-tidy.md"
