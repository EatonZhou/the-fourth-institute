from __future__ import annotations

import copy
import threading
import time
from typing import Any


_lock = threading.Lock()
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_account_locks: dict[str, threading.Lock] = {}


def runtime_catalog(client: Any, account_id: str) -> dict[str, Any]:
    with _lock:
        account_lock = _account_locks.setdefault(account_id, threading.Lock())
    with account_lock:
        cached = _cache.get(account_id)
        if cached and time.monotonic() - cached[0] < 60:
            return copy.deepcopy(cached[1])
        models = []
        cursor = None
        for _ in range(20):
            result = client.request("model/list", {"limit": 100, "cursor": cursor}, timeout=30)
            models.extend(model for model in result.get("data", []) if not model.get("hidden"))
            next_cursor = result.get("nextCursor")
            if not next_cursor:
                break
            if next_cursor == cursor:
                raise ValueError("Provider model catalog repeated a cursor")
            cursor = next_cursor
        else:
            raise ValueError("Provider model catalog exceeds the page limit")
        if not models:
            raise ValueError("Provider returned no available models")
        defaults = next((model for model in models if model.get("isDefault")), models[0])
        value = {
            "source": "model/list", "account_id": account_id,
            "models": [model["model"] for model in models],
            "default_model": defaults["model"], "default_reasoning_effort": defaults.get("defaultReasoningEffort", ""),
            "efforts_by_model": {model["model"]: [option["reasoningEffort"] for option in model.get("supportedReasoningEfforts", [])] for model in models},
            "default_efforts_by_model": {model["model"]: model.get("defaultReasoningEffort", "") for model in models},
            "service_tiers_by_model": {model["model"]: model.get("serviceTiers", []) for model in models},
            "input_modalities_by_model": {model["model"]: model.get("inputModalities", []) for model in models},
        }
        _cache[account_id] = (time.monotonic(), value)
        return copy.deepcopy(value)
