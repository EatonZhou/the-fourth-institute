#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from management_portal.shared_state import atomic_replace_json
from management_common.portal_paths import path as portal_path


DEFAULT_OUTPUT_ROOT = portal_path("staging_root") / "shared/codex_limit_usage/daily"
DEFAULT_TIMEZONE = "Asia/Hong_Kong"


def _account_snapshot(
    account_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    rate_limits = [
        {
            key: window.get(key)
            for key in (
                "id",
                "label",
                "remaining_percent",
                "window_duration_mins",
                "resets_at",
                "observed_at",
                "stale",
            )
        }
        for window in payload.get("rate_limits") or []
        if isinstance(window, dict)
    ]
    observed_at = next(
        (
            str(window.get("observed_at") or "")
            for window in rate_limits
            if window.get("observed_at")
        ),
        "",
    )
    return {
        "account_id": account_id,
        "status": str(payload.get("status") or ""),
        "updated_at": observed_at,
        "stale": any(bool(window.get("stale")) for window in rate_limits),
        "error": str(payload.get("error") or ""),
        "rate_limits": rate_limits,
    }


def collect_codex_accounts(*, isa_call=None) -> tuple[str, dict[str, dict[str, Any]]]:
    if isa_call is None:
        from portal_script import isa as isa_call
    result = isa_call("ListProviderAccounts", refresh=True)
    default_account_id = str((result.get("default_routes") or {}).get("codex") or "")
    accounts = {}
    for payload in result.get("accounts") or []:
        if not isinstance(payload, dict) or payload.get("provider") != "codex":
            continue
        account_id = str(payload.get("backend_route_id") or "").strip()
        if not account_id:
            raise ValueError("Codex account is missing its backend route ID")
        accounts[account_id] = payload
    if not default_account_id or default_account_id not in accounts:
        raise ValueError("Default Codex account is missing from Execution Service")
    return default_account_id, accounts


def build_snapshot(
    default_account_id: str,
    accounts: dict[str, dict[str, Any]],
    *,
    observed_at: datetime,
    timezone_name: str,
) -> dict[str, Any]:
    local_time = observed_at.astimezone(ZoneInfo(timezone_name))
    return {
        "schema": "tfi.codex-daily-limit-snapshot.v2",
        "snapshot_date": local_time.date().isoformat(),
        "timezone": timezone_name,
        "observed_at": observed_at.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "observed_at_local": local_time.isoformat(timespec="seconds"),
        "default_account_id": default_account_id,
        "account_count": len(accounts),
        "accounts": [
            _account_snapshot(account_id, payload)
            for account_id, payload in sorted(accounts.items())
        ],
    }


def write_snapshot(
    snapshot: dict[str, Any],
    output_root: Path,
) -> Path:
    dated_path = output_root / f"{snapshot['snapshot_date']}.json"
    atomic_replace_json(dated_path, snapshot, sort_keys=False)
    atomic_replace_json(output_root / "latest.json", snapshot, sort_keys=False)
    return dated_path


def snapshot_is_healthy(snapshot: dict[str, Any]) -> bool:
    accounts = snapshot.get("accounts")
    return bool(accounts) and all(
        isinstance(account, dict)
        and account.get("status") == "ok"
        and not account.get("stale")
        and bool(account.get("rate_limits"))
        for account in accounts
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Query and record the daily Codex account limit snapshot."
    )
    parser.add_argument(
        "--timezone",
        default=os.getenv(
            "CODEX_DAILY_LIMIT_TIMEZONE",
            DEFAULT_TIMEZONE,
        ),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(
            os.getenv(
                "CODEX_DAILY_LIMIT_ROOT",
                str(DEFAULT_OUTPUT_ROOT),
            )
        ),
    )
    parser.add_argument("--stdout", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()

    # Runs under RunScript (Portal System Lineage); account data comes through ISA.
    default_account_id, accounts = collect_codex_accounts()
    snapshot = build_snapshot(
        default_account_id,
        accounts,
        observed_at=datetime.now(timezone.utc),
        timezone_name=args.timezone,
    )
    if not args.no_write:
        output_path = write_snapshot(
            snapshot,
            args.output_root.expanduser(),
        )
        print(f"wrote {output_path}")
    if args.stdout or args.no_write:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    return 0 if snapshot_is_healthy(snapshot) else 1


if __name__ == "__main__":
    raise SystemExit(main())
