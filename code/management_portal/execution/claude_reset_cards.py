"""Read reset grants following an admitted native /usage, without redeeming.

The CLI alone owns login/refresh. Only its registered account-local credentials
are reused after success; there is no second credential store or refresh flow.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
from urllib.request import Request

from .account_observation import check_cancelled, urlopen

SOURCE = "claude.oauth/usage?cedar_ember=1&skip_spend=1"
ENDPOINT = "https://api.anthropic.com/api/oauth/usage?cedar_ember=1&skip_spend=1"


def _instant(value):
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("Invalid grant time")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Grant time requires timezone")
    return result.astimezone(timezone.utc)


def parse_cards(payload, *, now=None):
    now = datetime.now(timezone.utc) if now is None else now
    if not isinstance(payload, dict) or payload.get("error") or payload.get("type") == "error":
        raise ValueError("Invalid Claude entitlement response")
    block = payload.get("cedar_ember")
    rows = []
    if block not in (None, "", {}):
        if not isinstance(block, dict) or type(block.get("eligible")) is not bool:
            raise ValueError("Invalid Claude entitlement block")
        grants = block.get("grants") or []
        if not isinstance(grants, list):
            raise ValueError("Invalid Claude grants")
        seen = set()
        for grant in grants:
            if not isinstance(grant, dict):
                raise ValueError("Invalid Claude grant")
            identity, count = grant.get("id"), grant.get("resets_left")
            if not isinstance(identity, str) or not identity or identity in seen or type(count) is not int or count < 0:
                raise ValueError("Invalid Claude grant identity/count")
            seen.add(identity)
            starts, ends = _instant(grant.get("starts_at")), _instant(grant.get("ends_at"))
            status = ("used" if count == 0 else "expired" if ends and ends <= now else
                      "pending" if starts and starts > now else "paused" if grant.get("paused") else
                      "unavailable" if not block["eligible"] else "available")
            rows.append({"id": identity, "title": str(grant.get("label") or "刷新卡"),
                         "status": status, "remaining_count": count, "resetType": grant.get("type"),
                         "startsAt": starts.isoformat() if starts else None,
                         "expiresAt": ends.isoformat() if ends else None})
    return {"status": "observed", "source": SOURCE, "observed_at": now.isoformat(),
            "available_count": sum(r["remaining_count"] for r in rows if r["status"] == "available"),
            "cards": rows, "stale": False}


def read_cards(config_dir):
    data = json.loads((Path(config_dir) / ".credentials.json").read_text())
    token = (data.get("claudeAiOauth") or {}).get("accessToken")
    if not isinstance(token, str) or not token:
        raise ValueError("Claude native credential unavailable")
    request = Request(ENDPOINT, headers={"Authorization": "Bearer " + token,
        "anthropic-beta": "oauth-2025-04-20", "Accept": "application/json"}, method="GET")
    check_cancelled()
    with urlopen(request, timeout=20, follow_redirects=False) as response:
        raw = response.read(1_000_001)
    check_cancelled()
    if len(raw) > 1_000_000:
        raise ValueError("Claude entitlement response too large")
    return parse_cards(json.loads(raw) if raw.strip() else {})


class ClaudeResetCards:
    def __init__(self):
        self._samples = {}
        self._revisions = {}
        self._locks = {}

    def sample(self, config_dir, snapshot):
        key = str(config_dir)
        with self._locks.setdefault(key, threading.RLock()):
            return self._sample(key, snapshot)

    def _sample(self, key, snapshot):
        previous = self._samples.get(key, {"status": "unknown", "available_count": None, "cards": None})
        if snapshot.get("quota_error"):
            return {**deepcopy(previous), "stale": True}
        if snapshot.get("quota_refreshing") or snapshot.get("quota_status") != "available":
            return deepcopy(previous)
        revision = max((w.get("observed_at") or "" for w in snapshot.get("rate_limits") or []
                        if w.get("source") == "claude_cli_usage" and not w.get("stale")), default="")
        if not revision or self._revisions.get(key) == revision:
            return deepcopy(previous)
        self._revisions[key] = revision
        try:
            result = read_cards(key)
        except InterruptedError:
            self._revisions.pop(key, None)
            raise
        except Exception as exc:
            result = {**previous, "stale": True, "error_code": type(exc).__name__}
            if previous.get("status") != "observed":
                result["status"] = "unavailable"
        self._samples[key] = deepcopy(result)
        return result


# Registration refresh rebuilds account adapters. The read owner must survive
# that projection lifecycle, like the native /usage observer it follows.
shared_reset_cards = ClaudeResetCards()
