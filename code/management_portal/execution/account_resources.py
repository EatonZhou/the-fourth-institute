"""Present the balance observation attached to each execution account."""
from __future__ import annotations
from .account_exchange_rates import comparison


def with_account_resources(accounts, exchange_rates):
    result = []
    for account in accounts:
        source = account.get("balance")
        key = str(account.get("billing_account_id") or "")
        if isinstance(source, dict) and source.get("id"):
            balance = {name: source.get(name) for name in (
                "status", "metric_kind", "display", "amount", "currency", "updated_at", "billing_url", "error", "reason", "http_status")}
            balance.update(comparison(source, exchange_rates))
            # A balance goes stale only when its read fails, never with age.
            balance.update(source_account_id=source["id"], stale=bool(source.get("stale")))
        elif key:
            balance = {"status": "loading" if account.get("status") == "loading" else "unavailable",
                       "metric_kind": "balance", "source_account_id": key}
        else:
            result.append(dict(account))
            continue
        result.append({**account, "balance": balance})
    return result


def home_account_resources(accounts, exchange_rates):
    """Home shows billing resources, while Execution retains every route identity."""
    result, grouped = [], {}
    for account in with_account_resources(accounts, exchange_rates):
        api_id = str(account.get("billing_account_id") or "")
        if not api_id:
            if account.get("kind") in {"service", "api_profile"}:
                continue
            result.append(account)
            continue
        if api_id not in grouped:
            row = {"account_id": api_id, "billing_account_id": api_id, "kind": "api_profile",
                   "details_scope": "model_api", "execution_account_ids": [],
                   "capabilities": {"resident_details": True}}
            grouped[api_id] = row
            result.append(row)
        row = grouped[api_id]
        row["execution_account_ids"].append(account["account_id"])
        balance = account.get("balance") or {}
        previous = row.get("balance") or {}
        # One shared sample, never add balances. Prefer the latest successful
        # sample when a rolling refresh temporarily contains different copies.
        rank = lambda b: (b.get("status") == "ok" and not b.get("stale"), str(b.get("updated_at") or ""))
        if "balance" not in row or rank(balance) > rank(previous):
            row["balance"] = balance
    return result
