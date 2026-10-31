"""Kimi owns device authorization and credentials in its per-account server."""
from contextlib import contextmanager
from datetime import datetime
from urllib.parse import urlsplit

from management_common.file_lock import try_lock_descriptor
from management_portal.hatg.contracts import InstructionError
from management_portal.kimi_account_runtime import client_for_route
from .account_login import ACTIVE, _session_paths, login_store, now


def logged_in(account_id):
    from .account_registrations import registration
    row = registration(account_id)
    auth = client_for_route(row["route_id"])._request("GET", "auth")
    return (auth.get("managed_provider") or {}).get("status") == "authenticated"


@contextmanager
def _lock(account_id):
    path, _ = _session_paths(account_id)
    with path.open("a+") as stream:
        if not try_lock_descriptor(stream.fileno()):
            raise InstructionError("instruction.conflict", "登录状态正在更新，请稍后重试", retryable=True)
        yield


REGIONS = ({"id": "mainland-cn", "label": "中国区"}, {"id": "global", "label": "国际区"})


def _flow_region(flow):
    host = urlsplit(flow.get("verification_uri_complete") or flow.get("verification_uri") or "").hostname
    return {"www.kimi.com": "mainland-cn", "kimi.com": "mainland-cn",
            "www.kimi.ai": "global", "kimi.ai": "global"}.get(host)


def login(row, action, *, region=None):
    if region is not None and region not in {item["id"] for item in REGIONS}:
        raise InstructionError("instruction.invalid_arguments", "不支持的 Kimi 登录区域")
    account_id = row["account_id"]
    with _lock(account_id):
        client = client_for_route(row["route_id"])
        store = login_store()
        session = store.get(account_id)
        flow = client._request("GET", "oauth/login") or {}
        if action == "cancel":
            client._request("DELETE", "oauth/login")
            if session:
                store.transition(session, "failed", error="登录已取消")
        else:
            start = action == "open" and (flow.get("status") != "pending"
                                          or region is not None and _flow_region(flow) != region)
            if start:
                # The native server replaces its pending flow atomically. Never
                # rewrite a verification URL: the device code belongs to its region.
                flow = client._request("POST", "oauth/login", {"region": region} if region else {})
            if action == "open" and (start or not session or session["state"] not in ACTIVE):
                expires = datetime.fromisoformat(flow["expires_at"].replace("Z", "+00:00")) if flow.get("expires_at") else None
                seconds = max(1, min(1800, int((expires - now()).total_seconds()))) if expires else 900
                session = store.begin(account_id, expires_seconds=seconds)
            if session and session["state"] in ACTIVE:
                state = flow.get("status")
                if state == "pending":
                    url = flow.get("verification_uri_complete") or flow.get("verification_uri", "")
                    parsed = urlsplit(url)
                    if parsed.scheme != "https" or parsed.hostname not in {"www.kimi.com", "www.kimi.ai", "kimi.com", "kimi.ai"}:
                        raise ValueError("Kimi returned an invalid device authorization URL")
                    store.transition(session, "awaiting_user", url=url, code=flow.get("user_code", ""))
                elif state == "authenticated":
                    store.transition(session, "succeeded")
                elif state in {"denied", "expired", "cancelled"}:
                    store.transition(session, "expired" if state == "expired" else "failed", error="Kimi 登录未完成，请重新授权")
        auth = client._request("GET", "auth")
        state = (auth.get("managed_provider") or {}).get("status")
        store.observe(account_id, "logged_in" if state == "authenticated" else
                      "login_expired" if state in {"expired", "revoked"} else "logged_out")
        session = store.get(account_id)
        return {"session": {**session, "requires_code_input": False} if session else None,
                "login_state": store.login_state(account_id),
                "login_options": {"regions": list(REGIONS), "region": _flow_region(flow) or "mainland-cn"}}
