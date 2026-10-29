from __future__ import annotations
from management_common.portal_paths import path as portal_path
from management_common.portal_paths import resolve_directory
from management_common.site_config import read_site_config
from management_common.clock import utc_now_iso

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from . import pulse_publication_store
from .society_service.lineage_storage import canonical_lineage_folder


# The Pulse workflow in Portal System publishes into its Lineage Folder;
# the items users publish from them are rows of pulse_publication_store.
PULSE_MIGRATION_RECEIPT = ".portal-system-migration.json"
PERIODS = ("daily", "weekly", "monthly")
# Who may read or publish Pulse is decided by the permission scheme on GetPulse
# and PublishPulseItem; this module keeps no user lists.


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _compact(value: Any, limit: int = 500) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _safe_paths(value: Any, limit: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if not text.startswith((str(portal_path("share_root") / "Projects") + "/", str(portal_path("staging_root")) + "/")):
            continue
        if text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _pulse_root() -> Path:
    # The one-time migration copies summaries and index before publishing its
    # receipt. Shared-code reloads before that point keep the complete old view.
    # After cutover a missing/corrupt target index never resurrects stale data.
    if os.getenv("PORTAL_PULSE_ROOT"):
        return resolve_directory(os.environ["PORTAL_PULSE_ROOT"])
    deployment = read_site_config().get("deployment", {})
    if not isinstance(deployment, dict) or not isinstance(deployment.get("pulse", {}), dict):
        raise ValueError("deployment.pulse must be an object")
    config = deployment.get("pulse", {})
    for key in ("lineage_id", "legacy_lineage_id"):
        value = config.get(key, "")
        if not isinstance(value, str) or (value and not re.fullmatch(r"lineage_[0-9a-f]{12}", value)):
            raise ValueError(f"deployment.pulse.{key} must be a Lineage ID")
    if not config.get("lineage_id"):
        raise ValueError("deployment.pulse.lineage_id is not configured")
    root = canonical_lineage_folder(config["lineage_id"]) / "memory" / "pulse"
    if not config.get("legacy_lineage_id") or (root / PULSE_MIGRATION_RECEIPT).exists():
        return root
    legacy = canonical_lineage_folder(config["legacy_lineage_id"]) / "memory" / "pulse"
    return legacy if (legacy / "index.json").exists() else root


def _summary_path(period: str, key: str) -> Path:
    return _pulse_root() / "summaries" / period / f"{key}.json"


def _read_index() -> dict:
    payload = _read_json(_pulse_root() / "index.json", {})
    return payload if isinstance(payload, dict) else {}


def _read_publications() -> dict:
    with pulse_publication_store.database().read() as connection:
        return {"items": pulse_publication_store.items(connection)}


def _publication_id(owner: str, period: str, key: str, item_id: str) -> str:
    raw = f"{owner}:{period}:{key}:{item_id}"
    return "pub_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _published_key(item: dict) -> tuple[str, str, str, str]:
    return (
        str(item.get("owner") or ""),
        str(item.get("source_period") or ""),
        str(item.get("source_key") or ""),
        str(item.get("source_item_id") or ""),
    )


def _published_map(publications: dict) -> dict[tuple[str, str, str, str], dict]:
    out: dict[tuple[str, str, str, str], dict] = {}
    for item in publications.get("items") or []:
        if isinstance(item, dict):
            out[_published_key(item)] = item
    return out


def _public_summary(summary: dict) -> dict:
    content = summary.get("content") if isinstance(summary.get("content"), dict) else {}
    projects = []
    for item in content.get("project_updates") or []:
        if not isinstance(item, dict):
            continue
        projects.append(
            {
                "project": _compact(item.get("project"), 80),
                "summary": _compact(item.get("summary"), 420),
                "change_count": int(item.get("change_count") or 0),
                "route_changes": [_compact(x, 220) for x in item.get("route_changes") or [] if str(x).strip()][:8],
                "evidence_paths": _safe_paths(item.get("evidence_paths")),
            }
        )
    urgent = content.get("urgent_work") if isinstance(content.get("urgent_work"), dict) else {}
    return {
        "period": str(summary.get("period") or ""),
        "key": str(summary.get("key") or ""),
        "label": str(summary.get("label") or ""),
        "generated_at": str(summary.get("generated_at") or ""),
        "global_summary": _compact(content.get("global_summary"), 900),
        "urgent_work": {
            "label": _compact(urgent.get("label") if isinstance(urgent, dict) else "", 80),
            "summary": _compact(urgent.get("summary") if isinstance(urgent, dict) else "", 320),
            "evidence_paths": _safe_paths(urgent.get("evidence_paths") if isinstance(urgent, dict) else []),
        },
        "project_updates": projects[:12],
        "route_changes": [_compact(x, 220) for x in content.get("route_changes") or [] if str(x).strip()][:12],
        "agent_updates": [
            {
                "agent": _compact(item.get("agent"), 80),
                "summary": _compact(item.get("summary"), 260),
                "evidence_paths": _safe_paths(item.get("evidence_paths")),
            }
            for item in content.get("agent_updates") or []
            if isinstance(item, dict)
        ][:12],
        "change_stats": summary.get("change_stats") if isinstance(summary.get("change_stats"), dict) else {},
        "model": content.get("model") if isinstance(content.get("model"), dict) else {},
    }


def _private_summary(summary: dict, actor: str, publications: dict) -> dict:
    actor_id = str(actor or "").strip()
    period = str(summary.get("period") or "")
    key = str(summary.get("key") or "")
    content = summary.get("content") if isinstance(summary.get("content"), dict) else {}
    raw_private = content.get("user_private_updates") if isinstance(content.get("user_private_updates"), dict) else {}
    section = raw_private.get(actor_id) if actor_id else None
    if not isinstance(section, dict):
        section = {}
    published = _published_map(publications)
    items = []
    for item in section.get("items") or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "").strip()
        if not item_id:
            continue
        pub = published.get((actor_id, period, key, item_id))
        items.append(
            {
                "id": item_id,
                "source_period": period,
                "source_key": key,
                "text": _compact(item.get("text"), 260),
                "project": _compact(item.get("project"), 80),
                "evidence_paths": _safe_paths(item.get("evidence_paths")),
                "published": bool(pub),
                "publication_id": str(pub.get("publication_id") or "") if pub else "",
            }
        )
    return {
        "summary": _compact(section.get("summary"), 420),
        "items": items,
    }


def _published_items(publications: dict, limit: int = 30) -> list[dict]:
    items = []
    for item in publications.get("items") or []:
        if not isinstance(item, dict):
            continue
        items.append(
            {
                "publication_id": str(item.get("publication_id") or ""),
                "owner": str(item.get("owner") or ""),
                "source_period": str(item.get("source_period") or ""),
                "source_key": str(item.get("source_key") or ""),
                "text": _compact(item.get("text"), 260),
                "project": _compact(item.get("project"), 80),
                "evidence_paths": _safe_paths(item.get("evidence_paths")),
                "published_at": str(item.get("published_at") or ""),
            }
        )
    items.sort(key=lambda item: item.get("published_at") or "", reverse=True)
    return items[:limit]


def _load_latest_summaries() -> dict[str, dict]:
    index = _read_index()
    latest = index.get("latest") if isinstance(index.get("latest"), dict) else {}
    out: dict[str, dict] = {}
    for period in PERIODS:
        key = str(latest.get(period) or "").strip()
        payload = _read_json(_summary_path(period, key), {}) if key else {}
        out[period] = payload if isinstance(payload, dict) else {}
    return out


def get_home_payload(actor: str) -> dict:
    actor_id = str(actor or "").strip()
    if not actor_id:
        return {
            "schema": "tfi.portal_pulse.home.v1",
            "actor": actor_id,
            "enabled": False,
            "generated_at": utc_now_iso(),
            "periods": {},
            "publications": [],
            "status": {},
        }
    publications = _read_publications()
    summaries = _load_latest_summaries()
    periods = {}
    for period, summary in summaries.items():
        periods[period] = {
            "public": _public_summary(summary) if summary else {},
            "mine": _private_summary(summary, actor_id, publications) if summary else {"summary": "", "items": []},
        }
    # The Pulse Resident publishes through index.json; it is the status.
    index = _read_index()
    status = {
        "latest": index.get("latest") if isinstance(index.get("latest"), dict) else {},
        "updated_at": str(index.get("updated_at") or ""),
    }
    return {
        "schema": "tfi.portal_pulse.home.v1",
        "actor": actor_id,
        "enabled": True,
        "generated_at": utc_now_iso(),
        "data_root": str(_pulse_root()),
        "periods": periods,
        "publications": _published_items(publications),
        "status": status if isinstance(status, dict) else {},
    }


def _find_private_item(actor: str, period: str, key: str, item_id: str) -> dict:
    payload = _read_json(_summary_path(period, key), {})
    if not isinstance(payload, dict):
        return {}
    private = _private_summary(payload, actor, _read_publications())
    for item in private.get("items") or []:
        if item.get("id") == item_id:
            return item
    return {}


def update_publication(actor: str, payload: dict) -> dict:
    actor_id = str(actor or "").strip()
    if not actor_id:
        raise PermissionError("需要登录后操作")
    action = str(payload.get("action") or "publish").strip().lower()
    with pulse_publication_store.database().transaction(pulse_publication_store.LOCK) as connection:
        items = pulse_publication_store.items(connection)
        if action == "publish":
            period = str(payload.get("source_period") or "").strip()
            key = str(payload.get("source_key") or "").strip()
            item_id = str(payload.get("item_id") or "").strip()
            if period not in PERIODS or not key or not item_id:
                raise ValueError("source_period、source_key 和 item_id 必填")
            source = _find_private_item(actor_id, period, key, item_id)
            if not source:
                raise PermissionError("只能发布自己的 Pulse 条目")
            pub_id = _publication_id(actor_id, period, key, item_id)
            publication = next((item for item in items if item["publication_id"] == pub_id), None)
            if publication is None:
                publication = {
                    "publication_id": pub_id,
                    "owner": actor_id,
                    "source_period": period,
                    "source_key": key,
                    "source_item_id": item_id,
                    "text": source.get("text") or "",
                    "project": source.get("project") or "",
                    "evidence_paths": source.get("evidence_paths") or [],
                    "published_at": utc_now_iso(),
                }
                pulse_publication_store.add(connection, publication)
        elif action == "unpublish":
            period = str(payload.get("source_period") or "").strip()
            key = str(payload.get("source_key") or "").strip()
            item_id = str(payload.get("item_id") or "").strip()
            if period not in PERIODS or not key or not item_id:
                raise ValueError("source_period、source_key 和 item_id 必填")
            # A publication's ID is its owner's item, so the item names it.
            pub_id = _publication_id(actor_id, period, key, item_id)
            target = next((item for item in items if item["publication_id"] == pub_id), None)
            if not target or target["owner"] != actor_id:
                raise PermissionError("只能撤回自己发布的 Pulse 条目")
            pulse_publication_store.remove(connection, pub_id)
            publication = None
        else:
            raise ValueError("unknown publication action")
    result = get_home_payload(actor_id)
    if publication is not None:
        result["publication"] = publication
    return result
