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
                "status", "metric_kind", "display", "amount", "currency", "updated_at", "billing_url", "error")}
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
