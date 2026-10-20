"""One leased coordinator for shared Dev/Release records and durable results."""
from copy import deepcopy
from datetime import datetime
import json
import logging
import os
import secrets
import shutil
import threading
import time

from . import runtime, service, store, events

LOGGER = logging.getLogger(__name__)
_WORKER = None


class Worker:
    def __init__(self):
        self.holder = f'{os.getpid()}:{secrets.token_hex(8)}'
        self.stop_event = threading.Event()
        self.thread = None
        self.after = ''
        self.next_cleanup = 0.0
        self.lease_lost = threading.Event()
        self.lease_until = 0.0

    def owns_lease(self):
        return not self.lease_lost.is_set() and time.monotonic() < self.lease_until

    def tick(self):
        started = time.monotonic()
        if not store.acquire_lease(self.holder):
            return
        self.lease_until = started + 30
        self.lease_lost.clear()
        finished = threading.Event()

        def heartbeat():
            while not finished.wait(10):
                try:
                    renewed = time.monotonic()
                    if store.renew_lease(self.holder):
                        self.lease_until = renewed + 30
                        continue
                except Exception:
                    LOGGER.warning('脚本协调器续租失败', exc_info=True)
                self.lease_lost.set()
                return

        renewal = threading.Thread(target=heartbeat, name='script-run-lease', daemon=True)
        renewal.start()
        try:
            records = store.pending(self.after)
            for record in records:
                if self.stop_event.is_set() or not self.owns_lease():
                    return
                identifier = record['script_run_id']
                self.after = identifier
                try:
                    self.reconcile(identifier, record)
                except Exception:
                    LOGGER.warning('脚本运行协调失败 %s', identifier, exc_info=True)
            if len(records) < 100:
                self.after = ''
            if time.monotonic() >= self.next_cleanup:
                self.next_cleanup = time.monotonic() + 60
                for identifier in store.expired():
                    if self.stop_event.is_set() or not self.owns_lease():
                        return
                    shutil.rmtree(runtime.RUN_ROOT / identifier, ignore_errors=True)
                    store.delete_expired(identifier)
        finally:
            finished.set()
            renewal.join(timeout=1)

    def reconcile(self, identifier, record):
        root = runtime.RUN_ROOT / identifier
        if record['status'] == 'running':
            result_file = root / 'result.json'
            update = None
            if result_file.is_file():
                update = json.loads(result_file.read_text())
            elif record.get('cancel_requested'):
                # A Society restart may occur after launch but before start()
                # observes an admitted cancellation. Reuse the original run.
                service.cancel(identifier, record['caller_id'], lineage_id=record['lineage_id'])
                record = store.get(identifier)
            else:
                age = time.time() - datetime.fromisoformat(record['started_at']).timestamp()
                if age > 20:
                    state = service.running_state(record)
                    if result_file.is_file():
                        update = json.loads(result_file.read_text())
                    elif state == 'stopped':
                        timeout = record.get('timeout_seconds')
                        update = {'status': 'timed_out' if timeout is not None and age >= timeout else 'lost',
                                  'finished_at': service.now(), 'duration_seconds': age, 'error_code': 'script.result_missing'}
            if update:
                if not self.owns_lease():
                    return
                with store.edit(identifier) as row:
                    if row['status'] == 'running':
                        if result_file.is_file():
                            update = json.loads(result_file.read_text())
                        row.update(update)
                    record = deepcopy(row)
        if record['status'] in service.TERMINAL and self.owns_lease():
            service.revoke(record)
            if not record.get('notified'):
                # The dispatch cursor and notification ID are durable idempotency
                # keys. A crash between dispatch and marking notified replays safely.
                events.publish(record)
                if self.owns_lease():
                    with store.edit(identifier) as row:
                        row['notified'] = True

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception:
                LOGGER.warning('脚本运行协调器异常', exc_info=True)
            self.stop_event.wait(2)

    def start(self):
        self.thread = threading.Thread(target=self.run, name='script-run-coordinator', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                return  # Keep the lease until this process ends or it expires.
        store.release_lease(self.holder)


def start_worker():
    global _WORKER
    if _WORKER is None:
        _WORKER = Worker()
        _WORKER.start()


def stop_worker():
    global _WORKER
    if _WORKER:
        _WORKER.stop()
        _WORKER = None
