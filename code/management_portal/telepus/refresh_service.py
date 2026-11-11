"""The ``telepus-portal-refresh`` service: push Portal replies back to Telegram."""
from __future__ import annotations

from threading import Event, Thread


class NotificationScheduler:
    """Separate watch workers cannot delay ordinary reply refresh or hold send locks."""
    def __init__(self, adapter):
        self.adapter = adapter
        self.notifications = adapter.notifications
        self.store = adapter.store
        self.stopped = Event()
        self.workers = {}

    def start(self):
        self.manager = Thread(target=self._manage, name='telepus-notification-manager', daemon=True)
        self.manager.start()

    def close(self):
        self.stopped.set()

    def _manage(self):
        while not self.stopped.is_set():
            try:
                self.tick()
            except Exception as exc:
                self.notifications.diagnostic('', exc)
            self.stopped.wait(1)

    def _flush(self, actor):
        # The refresh process already owns a sender. Reuse its durable queue and
        # cross-process user lock, without waiting for the ordinary refresh loop.
        with self.store.lock(actor):
            self.adapter.flush(actor)

    def _watch(self, actor):
        while not self.stopped.is_set():
            try:
                delay = self.notifications.scan(actor, on_pending=self._flush)
                if delay is None:
                    return
            except Exception as exc:
                self.notifications.diagnostic(actor, exc)
                delay = 2
            self.stopped.wait(delay)

    def tick(self):
        self.workers = {actor: worker for actor, worker in self.workers.items() if worker.is_alive()}
        for account in self.store.accounts():
            actor = account['portal_actor']
            if account['status'] == 'active' and actor not in self.workers and any(self.notifications.preferences(actor).values()):
                worker = Thread(target=self._watch, args=(actor,), name='telepus-notification-watch', daemon=True)
                self.workers[actor] = worker
                worker.start()


def main() -> int:
    from management_common import loaded_modules

    from .cli import run_telegram_refresh_daemon

    loaded_modules.track()
    return run_telegram_refresh_daemon(["--sleep-seconds", "3"])


if __name__ == "__main__":
    raise SystemExit(main())
