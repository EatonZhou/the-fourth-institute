"""Stopped notification watches discard process-local cadence, including remote settings changes.

:design: decision_c41e1310c463
"""
import pytest

from management_portal.telepus.notifications import Notifications
from tests.cases.test_telepus_notifications import reminder, enable  # noqa: F401
from tests.cases.test_telepus_weixin import bridge  # noqa: F401
from tests.cases.telepus_bridge_support import ACTOR, telepus_schema  # noqa: F401


@pytest.mark.parametrize('boundary', ['before_scan', 'successful_read', 'failed_read'])
def test_remote_disable_retires_scanner_cadence(reminder, monkeypatch, boundary):
    r = reminder
    enable(r, mention=True)
    assert ACTOR in r.notices._read_at
    r.notices._read_at['another-active-user'] = 123
    remote = Notifications(r.notices.router, r.portal, clock=lambda: r.now[0])

    def disable():
        # Another Web/router process has its own Notifications instance.
        with remote.store.lock(ACTOR):
            remote.update(ACTOR, mention=False)

    if boundary == 'before_scan':
        disable()
    else:
        original = r.notices.snapshot

        def read(actor):
            snapshot = original(actor)
            disable()
            if boundary == 'failed_read':
                raise OSError('isolated read failed after settings changed')
            return snapshot

        monkeypatch.setattr(r.notices, 'snapshot', read)
    assert r.notices.scan(ACTOR) is None
    assert ACTOR not in r.notices._read_at, 'stopped watch retained cadence for a departed actor'
    assert r.notices._read_at['another-active-user'] == 123
    assert not r.notices._snapshots
    assert not r.server.sent
