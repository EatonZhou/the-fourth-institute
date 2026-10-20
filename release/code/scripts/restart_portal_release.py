#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __package__:
    from .portal_restart_notice_client import drain_realtime, publish_restart_notice
else:
    from portal_restart_notice_client import drain_realtime, publish_restart_notice

from scripts.portal_service_control import ServiceControl


SERVICE = "management-portal-release.service"
REALTIME_DRAIN_URL = "http://127.0.0.1:18085/api/internal/drain-realtime"
RESTART_NOTICE_URL = "http://127.0.0.1:18085/api/internal/restart-notice"
RESTART_NOTICE_SECONDS = 5


def _drain_realtime() -> None:
    result = drain_realtime(REALTIME_DRAIN_URL)
    print(
        "Portal realtime drain confirmed: "
        f"{result['closed_realtime_connections']} connection(s) closed in "
        f"{result['elapsed_seconds']:.3f}s.",
        flush=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Restart only the Portal Release with a browser countdown.")
    parser.add_argument(
        "--seconds",
        type=int,
        default=RESTART_NOTICE_SECONDS,
        choices=(RESTART_NOTICE_SECONDS,),
    )
    parser.add_argument("--message", default="Portal Release将在倒计时结束后重启。")
    parser.add_argument(
        "--commit",
        default="",
        help="Fast-forward the release branch to this Dev commit while Release Web is stopped.",
    )
    args = parser.parse_args()
    seconds = RESTART_NOTICE_SECONDS
    message = str(args.message or "")
    try:
        publish_restart_notice(RESTART_NOTICE_URL, seconds=seconds, message=message, services=[SERVICE])
        print(f"Release restart notice projected for {seconds}s.", flush=True)
        time.sleep(seconds)
        _drain_realtime()
    except Exception as exc:
        # Repairing an unavailable Web must not require that Web to answer its
        # own notification endpoint. The following restart remains observable.
        print(f"Release notice/drain unavailable: {exc}; continuing restart.", file=sys.stderr)
    if not args.commit:
        ServiceControl().restart(SERVICE)
        return 0
    # The running Web never sees a partly updated tree: it stops first, the
    # release branch advances, and Web starts again even if that failed.
    from scripts import portal_release

    controller = ServiceControl()
    controller.stop(SERVICE)
    try:
        portal_release.advance(portal_release.RELEASE_ROOT, args.commit)
    finally:
        controller.start(SERVICE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
