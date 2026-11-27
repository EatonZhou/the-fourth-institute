"""Account selection configuration values, independent of routing and storage."""
from __future__ import annotations


def mode(value: object = "auto") -> str:
    if not isinstance(value, str) or value not in {"auto", "fixed"}:
        raise ValueError("account_selection must be auto or fixed")
    return str(value)



def quota_switch_strategy(value: object = 'legacy') -> str:
    if not isinstance(value,str) or value not in {'legacy','same_provider_model'}:
        raise ValueError('quota_switch_strategy must be legacy or same_provider_model')
    return value


def pinned_quota(record) -> bool:
    return quota_switch_strategy((record.get('execution_settings') or {}).get('quota_switch_strategy','legacy')) == 'same_provider_model'

# The first switch keeps its original key so explicit opt-ins survive upgrades.
QUOTA_POLICY_FIELDS = ("auto_switch_on_quota", "emergency_switch_on_quota", "emergency_switch_order")
QUOTA_SETTINGS = (*QUOTA_POLICY_FIELDS, "quota_switch_strategy")


def quota_updates(data):
    from .account_migration_types import validate_priority
    result = {}
    for key in QUOTA_POLICY_FIELDS[:2]:
        if key in data:
            if type(data[key]) is not bool:
                raise ValueError(f"{key} must be a boolean")
            result[key] = data[key]
    if "emergency_switch_order" in data:
        result["emergency_switch_order"] = validate_priority(data["emergency_switch_order"])
    if "quota_switch_strategy" in data:
        result["quota_switch_strategy"] = quota_switch_strategy(data["quota_switch_strategy"])
    return result


def initial_quota_policy():
    return {"auto_switch_on_quota": False, "emergency_switch_on_quota": False, "emergency_switch_order": []}
