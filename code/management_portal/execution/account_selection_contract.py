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
