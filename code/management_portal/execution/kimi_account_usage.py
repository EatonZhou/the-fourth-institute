"""Read subscription quota from the account's native Kimi OAuth service.

The native service owns regional endpoints and credentials. These observations
are quota windows, separate from the per-request token ledger and API balances.
"""
from datetime import datetime
import time

from .account_quota import MAX_SAMPLE_AGE_SECONDS, finite_number


WINDOWS = {
    "limit5h": ("5 小时", 300),
    "limit7d": ("每周", 10080),
    "monthTotal": ("每月总额度", None),
    "monthCode": ("每月编程额度", None),
}


def quota_sample(payload, observed_at):
    if not isinstance(payload, dict) or payload.get("kind") != "ok":
        # Native usage failures are in-band, even on HTTP 200. Never forward
        # their messages (which can include upstream request details).
        raise ValueError("Kimi quota unavailable")
    quota = payload.get("quota")
    usages = quota.get("usages") if isinstance(quota, dict) else None
    if not isinstance(usages, dict):
        raise ValueError("Missing Kimi quota windows")
    windows = []
    for key, (label, minutes) in WINDOWS.items():
        entry = usages.get(key)
        if entry is None:
            continue  # Optional windows depend on the subscription plan.
        ratio = entry.get("usedRatio") if isinstance(entry, dict) else None
        if not finite_number(ratio) or ratio < 0:
            raise ValueError("Invalid Kimi quota ratio")
        reset = None
        if entry.get("resetAt"):
            try:
                date = datetime.fromisoformat(entry["resetAt"].replace("Z", "+00:00"))
                reset = date.timestamp() if date.tzinfo is not None else None
            except (ValueError, TypeError, AttributeError, OverflowError):
                pass  # The percentage remains useful without a reset time.
        windows.append({"id": key, "label": label, "remaining_percent": round(max(0, 1 - ratio) * 100, 6),
                        "window_duration_mins": minutes, "resets_at": reset,
                        "observed_at": observed_at, "stale": False})
    if not windows:
        raise ValueError("No Kimi quota windows")
    # A reset boundary requires a new observation before admitting more work.
    future_resets = [w["resets_at"] for w in windows if w["resets_at"] and w["resets_at"] > observed_at]
    fresh_until = min([observed_at + MAX_SAMPLE_AGE_SECONDS, *future_resets])
    return {"status": "ok", "rate_limits": windows,
            "quota": {"status": "available", "complete": True, "observed_at": observed_at,
                      "max_age_seconds": MAX_SAMPLE_AGE_SECONDS, "valid_until": fresh_until,
                      "remaining_percent": min(w["remaining_percent"] for w in windows),
                      "windows": [{"window": w["id"], "remaining_percent": w["remaining_percent"],
                                   "resets_at": w["resets_at"]} for w in windows]}}


def observe(client):
    auth = client._request("GET", "auth")
    status = (auth.get("managed_provider") or {}).get("status")
    state = ("logged_in" if status == "authenticated" else
             "login_expired" if status in {"expired", "revoked"} else "logged_out")
    from .account_login import now
    sample = {"login_observation": state, "login_observed_at": now().isoformat()}
    if state != "logged_in":
        return {**sample, "status": "unavailable", "quota": {"status": "unknown"},
                "error_code": "provider_authentication_failed", "error": "登录凭据失效，请重新登录"}
    try:
        payload = client._request("GET", "oauth/usage")
        return {**sample, **quota_sample(payload, time.time())}
    except Exception:
        # A quota read error says nothing about login state. The monitor keeps
        # the last successful windows, marks them stale, and admits no new quota.
        return {**sample, "status": "unavailable", "quota": {"status": "unknown"},
                "error_code": "account_read_unavailable", "error": "账号用量暂不可读取"}
