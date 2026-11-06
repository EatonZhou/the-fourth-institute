"""Stable account kinds exposed to migration policy editors and selectors."""
from __future__ import annotations

import re


def migration_type(account):
    provider = str(account.get("provider") or "")
    kind = str(account.get("kind") or "unknown")
    key = f"{provider}:{kind}"
    names = {"antigravity": "Google Antigravity", "claude": "Claude Code", "codex": "Codex", "kimi": "Kimi Code", "dsh": "DSH", "opencode": "OpenCode"}
    label = names.get(provider, provider)
    if kind == "api_profile":
        api = str(account.get("billing_account_id") or account.get("backend_route_id") or account.get("account_id"))
        key += ":" + api
        label += f" · API（{api}）"
    else:
        label += " · " + {"subscription_login": "订阅", "service": "服务账号"}.get(kind, "其他")
    rank = {"subscription_login": 0, "service": 100, "api_profile": 200}.get(kind, 300)
    rank += {"claude": 0, "codex": 1, "kimi": 2}.get(provider, 10)
    return {"id": key, "label": label, "default_priority": rank}


def subscription_priority(accounts, priority):
    """Complete a custom Family order without opting into new billing kinds."""
    if not priority:
        return []  # An absent site order does not authorize cross-kind selection.
    types = {item["id"]: item for account in accounts
             if account.get("kind") == "subscription_login"
             for item in (migration_type(account),)}
    defaults = sorted(types, key=lambda key: (types[key]["default_priority"], key))
    return list(dict.fromkeys([*priority, *defaults]))


def validate_priority(value):
    if (not isinstance(value, list) or len(value) > 128
            or any(not isinstance(key, str) or len(key) > 256
                   or not re.fullmatch(r"[a-zA-Z0-9_.-]+:(?:subscription_login|service|unknown|api_profile:[a-zA-Z0-9_.-]+)", key)
                   for key in value) or len(set(value)) != len(value)):
        raise ValueError("auto_migrate_priority 必须是不重复的账号种类 ID 数组")
    return list(value)
