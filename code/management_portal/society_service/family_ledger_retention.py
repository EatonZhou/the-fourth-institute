"""Retention of the generated Family recovery and review state cells."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256


# Shared by generated Effects and authorized maintenance. See decision_4adcc1ff2f2c.
RECOVERY_SETTLED_LIMIT = 32
RECOVERY_SETTLED_AGE = timedelta(days=7)
SETTLED = frozenset({"completed", "cancelled", "resolved", "escalated"})

_LEGACY = {
    "803cf4546c5265304eee26e8812549503d121af730c257c710fcfa461443d9ba": "recovery_check",
    "541524612c3640f52b4c2c0d1fbde9d08d3e39ec1de0421688575e2335e826dc": "review_activity",
    "fd803abfffa5f9ff454e87bba8ef0aaa430691520c38827be8b76e0121331496": "notify_periodic_review_task",
}


def legacy_role(source):
    return _LEGACY.get(sha256(source.encode("utf-8")).hexdigest())


def _instant(text):
    if isinstance(text, str):
        try:
            value = datetime.fromisoformat(text)
            if value.tzinfo is not None:
                return value.astimezone(timezone.utc)
        except ValueError:
            pass
    return None


def _retained_link(pointer, incidents, removed):
    if not isinstance(pointer, str):
        return pointer
    visited = set()
    while isinstance(pointer, str) and pointer in removed:
        if pointer in visited:
            return None
        visited.add(pointer)
        pointer = incidents[pointer].get("previous")
    return pointer


def compact_recovery(value, expected, occurred_at):
    if not isinstance(value, dict) or not isinstance(value.get("incidents"), dict):
        return value
    incidents = value["incidents"]
    previous_incidents = (expected or {}).get("incidents", {}) if isinstance(expected, dict) else {}
    previous_incidents = previous_incidents if isinstance(previous_incidents, dict) else {}
    now = _instant(occurred_at)
    if now is not None:
        incidents = {
            key: {**item, "settled_at": now.isoformat()}
            if isinstance(item, dict) and str(item.get("status")) in SETTLED
            and isinstance(previous_incidents.get(key), dict)
            and str(previous_incidents[key].get("status")) not in SETTLED else item
            for key, item in incidents.items()
        }
    notice = value.get("notice")
    if notice and (not isinstance(notice, dict) or not isinstance(notice.get("incident_keys"), list)):
        return {**value, "incidents": incidents}
    pinned = {key for key in [value.get("head"), *((notice or {}).get("incident_keys") or [])]
              if isinstance(key, str)}
    settled = {key: item for key, item in incidents.items()
               if isinstance(item, dict) and str(item.get("status")) in SETTLED and key not in pinned}
    floor = datetime.min.replace(tzinfo=timezone.utc)
    dates = {key: _instant(item.get("settled_at")) for key, item in settled.items()}
    recent = [key for key, stamp in dates.items()
              if stamp is None or now is None or now - stamp <= RECOVERY_SETTLED_AGE]
    recent.sort(key=lambda key: (dates[key] or floor, str(settled[key].get("created_at") or ""), key), reverse=True)
    removed = settled.keys() - set(recent[:RECOVERY_SETTLED_LIMIT])
    return {
        **value,
        "incidents": {
            key: {**item, "previous": _retained_link(item["previous"], incidents, removed)}
            if isinstance(item, dict) and "previous" in item else item
            for key, item in incidents.items() if key not in removed
        },
    }


def _position(value):
    return isinstance(value, list) and len(value) == 2 and type(value[0]) is int and isinstance(value[1], str)


def compact_review(value, members):
    """Keep current cursor watermarks and any activity covered by a pending notice."""
    if not isinstance(value, dict):
        return value
    activity, reviewed, pending = value.get("activity"), value.get("reviewed"), value.get("pending")
    if not isinstance(activity, dict) or not isinstance(reviewed, dict):
        return value
    pending_activity = pending.get("activity") if isinstance(pending, dict) else None
    if pending and not isinstance(pending_activity, dict):
        return value
    retired = {member for member, cursor in reviewed.items()
               if member not in members and member not in (pending_activity or {})
               and _position(cursor) and (member not in activity or activity[member] == cursor)}
    if not retired:
        return value
    return {**value, "activity": {k: v for k, v in activity.items() if k not in retired},
            "reviewed": {k: v for k, v in reviewed.items() if k not in retired}}
