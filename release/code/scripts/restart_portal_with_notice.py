#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

if __package__:
    from .portal_restart_notice_client import drain_realtime, publish_restart_notice
else:
    from portal_restart_notice_client import drain_realtime, publish_restart_notice


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from management_portal.shared_state import atomic_replace_json
from scripts.portal_service_control import ServiceControl

REQUEST_PATH = ROOT / "management_portal" / "restart_notice_request.json"
SERVICE = "management-portal.service"
DEFAULT_SERVICES = (SERVICE,)
REALTIME_DRAIN_URL = "http://127.0.0.1:18086/api/internal/drain-realtime"
RESTART_NOTICE_URL = "http://127.0.0.1:18086/api/internal/restart-notice"
RESTART_NOTICE_SECONDS = 5


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_restart_request(seconds: int, message: str, services: list[str]) -> None:
    now = time.time()
    atomic_replace_json(
        REQUEST_PATH,
        {
            "seconds": seconds,
            "message": str(message)[:240],
            "services": services,
            "created_at": now,
        },
        sort_keys=False,
    )


def _pop_restart_request(default_message: str, default_services: list[str]) -> tuple[int, str, list[str]]:
    payload = _read_json(REQUEST_PATH)
    try:
        REQUEST_PATH.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass
    created_at = float(payload.get("created_at") or 0)
    if not payload or time.time() - created_at > 120:
        return RESTART_NOTICE_SECONDS, default_message, default_services
    seconds = RESTART_NOTICE_SECONDS
    message = str(payload.get("message") or default_message).strip()[:240] or default_message
    services = [
        str(service)
        for service in payload.get("services") or []
        if str(service) in DEFAULT_SERVICES
    ]
    return seconds, message, services or default_services


def _drain_realtime() -> None:
    result = drain_realtime(REALTIME_DRAIN_URL)
    print(
        "Portal realtime drain confirmed: "
        f"{result['closed_realtime_connections']} connection(s) closed in "
        f"{float(result['elapsed_seconds']):.3f}s.",
        flush=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Restart Portal services. management-portal.service publishes the browser countdown from ExecStop.")
    parser.add_argument("--message", default="系统将在倒计时结束后重启。", help="Message shown to Portal users.")
    parser.add_argument(
        "--service",
        choices=(SERVICE,),
        help=(
            "Limit the restart to Portal Web. Domain services remain running."
        ),
    )
    parser.add_argument("--no-restart", action="store_true", help="Only publish the notice; do not restart.")
    parser.add_argument("--sleep", action="store_true", help="Sleep for the notice countdown after publishing it.")
    args = parser.parse_args()

    seconds = RESTART_NOTICE_SECONDS
    services = [args.service] if args.service else list(DEFAULT_SERVICES)
    if args.no_restart:
        seconds, message, services = _pop_restart_request(args.message, services)
        notice_projected = False
        try:
            publish_restart_notice(
                RESTART_NOTICE_URL,
                seconds=seconds,
                message=message,
                services=services,
            )
            notice_projected = True
            print(f"Restart notice projected for {seconds}s.")
        except Exception as exc:
            # ExecStop may run after the Web process has already become
            # unhealthy.  A best-effort notice must never make systemd mark
            # an otherwise recoverable restart as failed.
            print(f"Restart notice unavailable; continuing shutdown: {exc}", file=sys.stderr)
        if notice_projected and args.sleep:
            time.sleep(seconds)
        try:
            _drain_realtime()
        except Exception as exc:
            print(f"Realtime drain unavailable; continuing shutdown: {exc}", file=sys.stderr)
        return 0
    _write_restart_request(seconds, args.message, services)
    print(
        f"Restart request recorded for {seconds}s; {SERVICE} will "
        "publish the browser countdown before the requested services reload."
    )
    controller = ServiceControl()
    if controller.platform != "linux":
        # Linux's ExecStop owns this notice.  Native service managers do not
        # run that hook, so publish and drain before asking them to restart.
        try:
            publish_restart_notice(RESTART_NOTICE_URL, seconds=seconds,
                                   message=args.message, services=services)
            time.sleep(seconds)
        except Exception as exc:
            print(f"Restart notice unavailable; continuing restart: {exc}", file=sys.stderr)
        try:
            _drain_realtime()
        except Exception as exc:
            print(f"Realtime drain unavailable; continuing restart: {exc}", file=sys.stderr)
        REQUEST_PATH.unlink(missing_ok=True)
    controller.restart(SERVICE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
