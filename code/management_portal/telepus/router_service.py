"""The ``telepus-telegram-router`` service: receive Telegram updates and hand them to Portal."""
from __future__ import annotations


def main() -> int:
    from management_common import loaded_modules

    from .cli import run_telegram_daemon

    loaded_modules.track()
    return run_telegram_daemon(["--poll-timeout", "10", "--sleep-seconds", "1"])


if __name__ == "__main__":
    raise SystemExit(main())
