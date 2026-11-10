"""One independently backed-off iLink long poll per active binding."""
from __future__ import annotations

import signal
import threading

from .weixin_adapter import WeixinAdapter


def main():
    from management_common import loaded_modules
    loaded_modules.track()
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    workers = {}
    adapter = WeixinAdapter()

    def run(actor, token):
        local = WeixinAdapter()
        delay = 1
        account = None
        try:
            account = local.store.account(actor)
            if not account or account['bot_token'] != token:
                return
            try:
                local.client_factory(account).request('msg/notifystart')
            except Exception as exc:
                local._failure(actor, exc)
            while not stopped.is_set():
                account = local.store.account(actor)
                if not account or account['status'] != 'active' or account['bot_token'] != token:
                    return
                try:
                    local.poll_once(actor)
                    delay = 1
                except Exception:
                    delay = min(delay * 2, 60)
                stopped.wait(delay)
        finally:
            if account and account['bot_token'] == token:
                try:
                    local.client_factory(account).request('msg/notifystop')
                except Exception:
                    pass

    while not stopped.is_set():
        for account in adapter.store.accounts():
            actor = account['portal_actor']
            if actor not in workers or not workers[actor].is_alive():
                worker = threading.Thread(target=run, args=(actor, account['bot_token']), daemon=True)
                workers[actor] = worker
                worker.start()
        stopped.wait(3)
    for worker in workers.values():
        worker.join(timeout=45)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
