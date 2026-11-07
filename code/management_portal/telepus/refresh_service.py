"""The ``telepus-portal-refresh`` service: push Portal replies back to Telegram."""
from __future__ import annotations


def main() -> int:
    from management_common import loaded_modules

    from .cli import run_telegram_refresh_daemon

    loaded_modules.track()
    return run_telegram_refresh_daemon(["--sleep-seconds", "3"])


if __name__ == "__main__":
    raise SystemExit(main())
