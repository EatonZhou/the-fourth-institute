"""Bounded daily model-catalog synchronization through the account monitor."""
import time


def sync_catalogs(call, *, timeout=180, clock=time.time, monotonic=time.monotonic,
                  sleep=time.sleep):
    """Request one admitted observation, then verify per-account catalog receipts.

    The monitor owns Provider discovery and its durable ten-minute read gate.
    A recent observation is reused explicitly, never described as a new read.
    Only safe catalog fields enter the maintenance log.
    """
    started = clock()
    result = {"status": "failed", "started_at": started, "accounts": []}
    try:
        providers = {p["provider"] for p in call("ListProviders", include_runtime=False)["providers"]
                     if "model" in p.get("capabilities", {}).get("settings", [])}
        before = call("ListProviderAccounts", refresh=False)["accounts"]
        targets = {a["account_id"]: a for a in before
                   if a.get("enabled") and a.get("provider") in providers}
        if not targets:
            return {**result, "status": "skipped", "reason": "no_enabled_model_accounts"}
        # One request only: polling below never dispatches additional observations.
        snapshot = call("ListProviderAccounts", refresh=True)["accounts"]
        deadline = monotonic() + timeout
        pending = set(targets)
        completed = {}
        while pending:
            for account in snapshot:
                aid = account.get("account_id")
                if aid not in pending:
                    continue
                catalog = account.get("selection_catalog") or {}
                observed = catalog.get("checked_at") or 0
                if not isinstance(observed, (int, float)) or observed < started - 600:
                    continue
                models = catalog.get("models") or []
                valid = catalog.get("fresh") is True and bool(models)
                # An older failed receipt cannot settle a newly requested read.
                if not valid and observed < started:
                    continue
                row = {"account_id": aid, "provider": targets[aid]["provider"],
                       "status": ("refreshed" if observed >= started else "recent_observation")
                                 if valid else "failed",
                       "observed_at": observed}
                if valid:
                    previous = set((targets[aid].get("selection_catalog") or {}).get("models") or [])
                    row.update(models=models, default_model=catalog.get("default_model", ""),
                               added=sorted(set(models) - previous), removed=sorted(previous - set(models)))
                else:
                    row["error_code"] = "catalog_unavailable"
                completed[aid] = row
                pending.remove(aid)
            if not pending or monotonic() >= deadline:
                break
            sleep(min(2, max(0, deadline - monotonic())))
            snapshot = call("ListProviderAccounts", refresh=False)["accounts"]
        for aid in pending:
            completed[aid] = {"account_id": aid, "provider": targets[aid]["provider"],
                              "status": "failed", "error_code": "catalog_observation_timeout"}
        result["accounts"] = [completed[aid] for aid in sorted(completed)]
        result["status"] = "partial" if any(a["status"] == "failed" for a in completed.values()) else "ok"
    except Exception as error:
        # Permission/transport errors can contain private paths or credentials.
        result["error_code"] = type(error).__name__
    result["finished_at"] = clock()
    return result


def run():
    from portal_script import isa
    return sync_catalogs(isa)
