"""Portal Web handlers of the Pulse instructions (``web.instructions``); declarations in ``pulse_instructions.py``."""
from __future__ import annotations

from typing import Any

from management_portal.hatg.kernel import Invocation


def _pulse():
    from management_portal import portal_pulse_backend

    return portal_pulse_backend


def get_pulse(inv: Invocation) -> dict[str, Any]:
    from management_portal.web_isa import _named_user

    return {"pulse": _pulse().get_home_payload(_named_user(inv))}


def publish_pulse_item(inv: Invocation) -> dict[str, Any]:
    published = bool(inv.argument("published"))
    result = _pulse().update_publication(inv.caller, {
        "action": "publish" if published else "unpublish",
        "source_period": inv.argument("source_period"),
        "source_key": inv.argument("source_key"),
        "item_id": inv.argument("item_id"),
    })
    payload = {"pulse": _pulse().get_home_payload(inv.caller)}
    if published:
        payload["publication"] = result.get("publication") or {}
    return payload


HANDLERS = {
    "GetPulse": get_pulse,
    "PublishPulseItem": publish_pulse_item,
}
