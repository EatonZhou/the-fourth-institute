#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from management_portal.portal_access_control import get_portal_access_store
from management_portal.portal_session_auth import session_cookie_name
from management_portal.portal_deployment_registry import (
    DEFAULT_DEPLOYMENT_REGISTRY_PATH,
    PortalDeployment,
)
from management_portal.serve_management_portal import _issue_session_token, _verify_session_token
from management_portal.test_framework.tau import DEFAULT_STORAGE_STATE
from management_portal.test_framework.dev_target import resolve_unique_dev


from management_portal.test_framework.compatibility import test_actor

ACCOUNT = test_actor()


def storage_state_payload(deployment: PortalDeployment, token: str) -> dict:
    if deployment.instance_id != "dev" or deployment.kind != "dev":
        raise ValueError("test-actor 运行配置只能为当前唯一 DEV 创建")
    parsed = urlsplit(deployment.origin)
    host = parsed.hostname or ""
    return {
        "cookies": [
            {
                "name": session_cookie_name(),
                "value": token,
                "domain": host,
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": parsed.scheme == "https",
                "sameSite": "Lax",
            }
        ],
        "origins": [{"origin": deployment.origin, "localStorage": []}],
    }


def provision(
    output: Path,
    *,
    registry_path: Path = DEFAULT_DEPLOYMENT_REGISTRY_PATH,
) -> dict[str, str]:
    deployment = resolve_unique_dev(registry_path)
    access = get_portal_access_store()
    user = access.user(ACCOUNT)
    # Every signed-in user may operate the Test Lab; the account only has to be active.
    if user.get("status") != "active":
        raise PermissionError("test-actor 必须处于启用状态")
    token = _issue_session_token(ACCOUNT)
    if _verify_session_token(token) != ACCOUNT:
        raise RuntimeError("Portal rejected the newly issued test session")
    payload = storage_state_payload(deployment, token)
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = output.with_name(f".{output.name}.{secrets.token_hex(6)}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    access.audit(
        "test-actor-profile-provisioner",
        "test_browser_session_provisioned",
        target=ACCOUNT,
        details={"instance": deployment.instance_id, "storage_state": str(output)},
    )
    return {"account": ACCOUNT, "instance": deployment.instance_id, "storage_state": str(output)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision a local Playwright profile for the test-actor account.")
    parser.add_argument("--output", type=Path, default=DEFAULT_STORAGE_STATE)
    args = parser.parse_args()
    try:
        result = provision(args.output)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
