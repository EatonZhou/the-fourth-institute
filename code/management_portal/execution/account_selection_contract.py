"""Account selection configuration values, independent of routing and storage."""
from __future__ import annotations


def mode(value: object = "auto") -> str:
    if not isinstance(value, str) or value not in {"auto", "fixed"}:
        raise ValueError("account_selection must be auto or fixed")
    return str(value)


# The first switch keeps its original key so explicit opt-ins survive upgrades.
QUOTA_SETTINGS = ("auto_switch_on_quota", "emergency_switch_on_quota", "emergency_switch_order")


def quota_updates(data):
    from .account_migration_types import validate_priority
    result = {}
    for key in QUOTA_SETTINGS[:2]:
        if key in data:
            if type(data[key]) is not bool:
                raise ValueError(f"{key} must be a boolean")
            result[key] = data[key]
    if "emergency_switch_order" in data:
        result["emergency_switch_order"] = validate_priority(data["emergency_switch_order"])
    return result


def initial_quota_policy():
    return {"auto_switch_on_quota": False, "emergency_switch_on_quota": False, "emergency_switch_order": []}
