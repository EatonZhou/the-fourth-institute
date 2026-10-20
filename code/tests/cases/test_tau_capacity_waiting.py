"""TAU exposes real host-slot contention without changing dispatch or cancellation.

:module: operations.test-framework
:design: decision_c953eab654cd
"""
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from management_portal.test_framework import tau_campaign as module


class Records:
    def __init__(self):
        self.state = {'status': 'queued', 'waiting': ''}
        self.waiting = threading.Event()

    def update_group(self, run_id, group_id, values):
        self.state.update(values)
        if values.get('waiting'):
            self.waiting.set()


class TestTauCapacityWaiting(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'PORTAL_TEST_SLOT_ROOT': self.tmp.name, 'PORTAL_TEST_MAX_CONCURRENCY': '4'})
        self.env.start()
        self.records = Records()
        self.executor = module.TauCampaignExecutor(self.records, Path(self.tmp.name), None)
        self.cancelled = threading.Event()
        self.executor._is_cancelled = lambda _: self.cancelled.is_set()
        self.dispatched = threading.Event()
        self.outcomes = []
        self.errors = []
        self.contended = threading.Event()
        original_try_lock = module.slots.try_lock_descriptor

        def observed_try_lock(descriptor):
            acquired = original_try_lock(descriptor)
            if not acquired:
                self.contended.set()
            return acquired
        self.lock_probe = patch.object(module.slots, 'try_lock_descriptor', side_effect=observed_try_lock)
        self.lock_probe.start()

        def execute(*args):
            self.dispatched.set()
            self.assertEqual(self.records.state['waiting'], '')
            return ('cancelled' if self.cancelled.is_set() else 'passed', '')
        self.executor._execute_group = execute

    def tearDown(self):
        self.lock_probe.stop()
        self.env.stop()
        self.tmp.cleanup()

    def run_group(self):
        try:
            self.outcomes.append(self.executor._execute_group_in_slot('run', {'group_id': 'module:example'}, [], Path(self.tmp.name), 'actor'))
        except BaseException as exc:
            self.errors.append(exc)

    def blocked(self, cancel):
        holder = module.slots.acquire('pytest', 4, stop=lambda: False)
        self.assertEqual(holder.count, 4)
        thread = threading.Thread(target=self.run_group)
        thread.start()
        try:
            self.assertTrue(self.contended.wait(2), 'fixture did not encounter real kernel lock contention')
            self.assertTrue(self.records.waiting.wait(2), 'missing durable capacity waiting reason')
            self.assertEqual(self.records.state['status'], 'queued')
            self.assertIn('4', self.records.state['waiting'])
            self.assertFalse(self.dispatched.is_set())
            if cancel:
                self.cancelled.set()
                thread.join(3)
                self.assertFalse(thread.is_alive())
                self.assertEqual(self.outcomes, [('cancelled', '')])
        finally:
            holder.release()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertFalse(self.errors, self.errors)
        if not cancel:
            self.assertEqual(self.outcomes, [('passed', '')])
        self.assertEqual(self.records.state['waiting'], '')
        lease = module.slots.acquire('pytest', 4, stop=lambda: True)
        try:
            self.assertEqual(lease.count, 4, 'candidate leaked capacity after dispatch')
        finally:
            lease.release()

    def test_capacity_wait_is_visible_without_early_dispatch(self):
        """真实容量锁阻塞时持久显示等待原因，取得名额后才派发。"""
        self.blocked(False)

    def test_cancel_preserves_existing_cancellation_path(self):
        """容量等待中取消保持原取消路径并释放全部名额。"""
        self.blocked(True)

    def test_acquire_error_propagates_and_clears_waiting(self):
        """名额获取异常如实传播并清除等待提示。"""
        with patch.object(module.slots, 'acquire', side_effect=OSError('fixture failure')):
            self.run_group()
        self.assertEqual(len(self.errors), 1)
        self.assertIsInstance(self.errors[0], OSError)
        self.assertFalse(self.dispatched.is_set())
        self.assertEqual(self.records.state['waiting'], '')


if __name__ == '__main__':
    unittest.main()
