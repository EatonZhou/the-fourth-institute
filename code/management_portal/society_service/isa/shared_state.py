"""Lineage shared state instructions (decision_71ba0494b93e)."""
from __future__ import annotations

from typing import Any

from management_portal.hatg.contracts import invalid
from management_portal.hatg.kernel import Invocation

from .common import domain


def _payload(inv: Invocation, *names: str) -> dict[str, Any]:
    return {name: inv.argument(name) for name in names if name in inv.call.arguments}


def get_shared_state(inv: Invocation) -> dict[str, Any]:
    fields = [str(name) for name in inv.argument("fields") or [] if str(name or "").strip()]
    if fields and inv.argument("key") in (None, ""):
        raise invalid("fields 需要与 key 一起使用")
    result = domain().get_lineage_graph_state(inv.argument("lineage_id"), _payload(inv, "key"), inv.caller)
    if "cells" in result:
        return {"state": result["cells"]}
    if not result.get("exists"):
        return {"state": {}, "value": None}
    value = result["value"]
    if fields and isinstance(value, dict):
        # A reader that needs a few fields of a large cell, e.g. the Family Router and its guide.
        value = {name: value[name] for name in fields if name in value}
    # An Effect script cannot name a computed key in a path, so the one cell read also comes bare.
    return {"state": {result["key"]: value}, "value": value}


def _change(result: dict[str, Any]) -> dict[str, Any]:
    return dict(result)


def update_shared_state(inv: Invocation) -> dict[str, Any]:
    return _change(domain().update_lineage_graph_state(
        inv.argument("lineage_id"), _payload(inv, "key", "value", "expected_value"), inv.caller,
    ))


def delete_shared_state(inv: Invocation) -> dict[str, Any]:
    return _change(domain().delete_lineage_graph_state(
        inv.argument("lineage_id"), _payload(inv, "key", "expected_value"), inv.caller,
    ))


HANDLERS = {
    "GetSharedState": get_shared_state,
    "UpdateSharedState": update_shared_state,
    "DeleteSharedState": delete_shared_state,
}
