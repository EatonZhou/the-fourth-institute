"""Native billing endpoints and normalization, below the account projection."""
from __future__ import annotations
import json
import math
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any
from management_portal.ai_provider_registry import shared_ai_provider_registry

PROVIDER_BALANCE_TIMEOUT_SECONDS = float(os.getenv("AGENT_STATUS_PROVIDER_TIMEOUT_SECONDS", "24"))

def _now_iso():
    return datetime.now(timezone.utc).isoformat()

BILLING = {
    "deepseek": ("balance", "https://platform.deepseek.com/usage"),
    "moonshot": ("balance", "https://platform.moonshot.cn/console/account"),
    "openrouter": ("balance", "https://openrouter.ai/settings/credits"),
    "google": ("availability", "https://aistudio.google.com/usage?tab=billing"),
    "typesafe": ("availability", ""),
}

def metadata(item):
    kind, url = BILLING.get(str(item.get("provider") or "").lower(), ("balance", ""))
    return {"id": item["id"], "provider": item["provider"], "metric_kind": kind, "billing_url": url}

def entries():
    return [item for item in shared_ai_provider_registry.entries()
            if item.get("enabled") is True]


class BalanceResponseError(ValueError):
    """The connection returned no usable billing observation."""

def _read_json_response(url: str, headers: dict[str, str], timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
    payload = json.loads(raw) if raw else {}
    return payload if isinstance(payload, dict) else {}


def _balance_endpoint(provider: str, base_url: str) -> str:
    base = base_url.rstrip("/")
    if provider == "deepseek":
        return f"{base}/user/balance"
    if provider == "moonshot":
        return f"{base}/users/me/balance"
    if provider == "openrouter":
        return f"{base}/credits"
    raise ValueError("unsupported provider")


def _google_models_endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/openai"):
        base = base[: -len("/openai")]
    return f"{base}/models?pageSize=1"


def _typesafe_models_endpoint(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/models"


def _normalize_deepseek_balance(payload: dict[str, Any]) -> dict[str, Any]:
    infos = payload.get("balance_infos")
    balances = []
    if isinstance(infos, list):
        for item in infos:
            if not isinstance(item, dict):
                continue
            balances.append({
                "currency": str(item.get("currency") or ""),
                "total_balance": str(item["total_balance"]) if item.get("total_balance") is not None else "",
                "granted_balance": str(item.get("granted_balance") or ""),
                "topped_up_balance": str(item.get("topped_up_balance") or ""),
            })
    return {
        "is_available": bool(payload.get("is_available")),
        "balances": balances,
        "display": _first_balance_display(balances),
        "amount": next((_float_or_none(item["total_balance"]) for item in balances if item["total_balance"]), None),
        "currency": next((item["currency"] for item in balances if item["total_balance"]), ""),
    }


def _normalize_kimi_balance(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    balances = []
    for key in ("available_balance", "voucher_balance", "cash_balance"):
        value = data.get(key) if isinstance(data, dict) else None
        if value is not None:
            balances.append({"label": key.replace("_", " "), "value": str(value)})
    value = (data or {}).get("available_balance")
    display = str(value) if value is not None else ""
    if display:
        display = f"{display} CNY"
    return {"balances": balances, "display": display, "amount": _float_or_none(value), "currency": "CNY"}


def _normalize_openrouter_credits(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    total_credits = _float_or_none(data.get("total_credits") if isinstance(data, dict) else None)
    total_usage = _float_or_none(data.get("total_usage") if isinstance(data, dict) else None)
    remaining = None
    if total_credits is not None and total_usage is not None:
        remaining = total_credits - total_usage
    return {
        "total_credits": total_credits,
        "total_usage": total_usage,
        "remaining": remaining,
        "amount": remaining,
        "currency": "USD",
        "display": f"${remaining:.2f}" if remaining is not None else "",
    }


def _float_or_none(value: object) -> float | None:
    try:
        number = float(value)
    except Exception:
        return None
    return number if math.isfinite(number) else None


def _first_balance_display(balances: list[dict[str, str]]) -> str:
    for item in balances:
        value = str(item.get("total_balance") or "").strip()
        currency = str(item.get("currency") or "").strip()
        if value:
            return f"{value} {currency}".strip()
    return ""


def _fetch_provider_balance(item: dict[str, Any]) -> dict[str, Any]:
    provider = str(item.get("provider") or "").strip().lower()
    base_url = str(item.get("base_url") or "").strip()
    if provider not in BILLING:
        return {**metadata(item), "status": "not_supported", "reason": "unsupported_provider",
                "error": "此连接未提供余额查询；请在实际服务商控制台查看。"}
    key_path = shared_ai_provider_registry.secret_path(item)
    if not provider or not base_url or not key_path:
        raise ValueError("missing provider metadata")
    key = shared_ai_provider_registry.read_secret(key_path)
    if provider == "google":
        headers = shared_ai_provider_registry.headers(provider, key, item)
        headers.pop("Authorization", None)
        headers["x-goog-api-key"] = key
        headers["x-goog-api-client"] = "tfi-portal/1.0"
        payload = _read_json_response(
            _google_models_endpoint(base_url),
            headers,
            PROVIDER_BALANCE_TIMEOUT_SECONDS,
        )
        models = payload.get("models")
        if not isinstance(models, list) or not models:
            raise ValueError("Gemini models endpoint returned no models")
        normalized = {
            "metric_kind": "availability",
            "is_available": True,
            "display": "ready",
        }
    elif provider == "typesafe":
        payload = _read_json_response(
            _typesafe_models_endpoint(base_url),
            shared_ai_provider_registry.headers(provider, key, item),
            PROVIDER_BALANCE_TIMEOUT_SECONDS,
        )
        models = payload.get("models")
        if not isinstance(models, list) or not models:
            raise ValueError("TypeSafe models endpoint returned no models")
        normalized = {
            "metric_kind": "availability",
            "is_available": True,
            "display": "ready",
        }
    else:
        payload = _read_json_response(
            _balance_endpoint(provider, base_url),
            shared_ai_provider_registry.headers(provider, key, item),
            PROVIDER_BALANCE_TIMEOUT_SECONDS,
        )
        normalized = {"metric_kind": "balance"}
    if provider == "deepseek":
        normalized = {
            "metric_kind": "balance",
            **_normalize_deepseek_balance(payload),
        }
    elif provider == "moonshot":
        normalized = {
            "metric_kind": "balance",
            **_normalize_kimi_balance(payload),
        }
    elif provider == "openrouter":
        normalized = {
            "metric_kind": "balance",
            **_normalize_openrouter_credits(payload),
        }
    elif provider not in {"google", "gemini", "typesafe"}:
        raise ValueError("unsupported provider")
    if normalized.get("metric_kind") == "balance" and (
            normalized.get("amount") is None or not normalized.get("currency")):
        raise BalanceResponseError("No recognized amount and currency")
    return {
        **metadata(item),
        "status": "ok",
        "updated_at": _now_iso(),
        **normalized,
    }




class BillingObservations:
    """Poll each linked billing account once per minute, including shared routes."""
    def __init__(self):
        import threading
        self._lock = threading.RLock()
        self._samples = {}
        self._locks = {}
        self._exchange_rates = {}
        self._rates_lock = threading.Lock()
        self._rates_collect_lock = threading.Lock()

    def observe(self, account_id):
        import threading
        import time
        with self._lock:
            lock = self._locks.setdefault(account_id, threading.Lock())
        with lock:
            previous, checked = self._samples.get(account_id, ({}, 0))
            if time.monotonic() - checked < 60:
                return dict(previous)
            try:
                entry = next(item for item in entries() if item["id"] == account_id)
                sample = _fetch_provider_balance(entry)
            except Exception as exc:
                http_status = exc.code if isinstance(exc, urllib.error.HTTPError) else None
                reason, message = "unavailable", "余额暂不可读取；这不表示余额为零或聊天接口不可用。"
                if http_status in {404, 405, 501}:
                    reason, message = "unsupported_endpoint", "此连接未提供余额查询；请在实际服务商控制台查看。"
                elif http_status in {401, 403}:
                    reason, message = "unauthorized", "余额查询未获授权；请在实际服务商控制台查看。"
                elif isinstance(exc, (BalanceResponseError, json.JSONDecodeError)):
                    reason, message = "invalid_response", "余额接口返回了无法识别的数据；请在实际服务商控制台查看。"
                sample = {**previous, "id": account_id, "status": "unavailable", "stale": True,
                          "reason": reason, "http_status": http_status, "error": message}
            from .account_exchange_rates import collect
            with self._rates_collect_lock:
                rates = collect({sample.get("currency")}, self.exchange_rates_snapshot())
                with self._rates_lock:
                    self._exchange_rates = rates
            self._samples[account_id] = (sample, time.monotonic())
            return dict(sample)

    def exchange_rates_snapshot(self):
        from copy import deepcopy
        with self._rates_lock:
            return deepcopy(self._exchange_rates)
