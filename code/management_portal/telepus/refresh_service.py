"""The ``telepus-portal-refresh`` service: push Portal replies back to Telegram."""
from __future__ import annotations


class NotificationScheduler:
    """Separate watch workers cannot delay ordinary reply refresh or hold send locks."""
    def __init__(self, adapter):
        from concurrent.futures import ThreadPoolExecutor
        self.notifications = adapter.notifications
        self.store = adapter.store
        self.pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix='telepus-notifications')
        self.pending = {}

    def tick(self):
        for account in self.store.accounts():
            actor = account['portal_actor']
            if any(self.notifications.preferences(actor).values()):
                if actor not in self.pending or self.pending[actor].done():
                    self.pending[actor] = self.pool.submit(self.notifications.scan, actor)


def main() -> int:
    from management_common import loaded_modules

    from .cli import run_telegram_refresh_daemon

    loaded_modules.track()
    return run_telegram_refresh_daemon(["--sleep-seconds", "3"])


if __name__ == "__main__":
    raise SystemExit(main())
