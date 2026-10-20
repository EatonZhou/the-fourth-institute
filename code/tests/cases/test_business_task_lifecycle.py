""":importance: P2"""
import threading
from unittest import mock

import pytest

from management_portal import business_tasks


def test_failed_thread_launch_releases_business_task_identity(subtests, isolated_portal_case):
    """线程构造或启动失败后释放业务任务标识"""
    for failure_stage in ["construct", "start"]:
        with subtests.test(failure_stage=failure_stage), isolated_portal_case() as case_path:
            task_id = f"test-launch-failure-{failure_stage}"
            fake_thread = mock.Mock()
            failure = RuntimeError("cannot launch thread")
            if failure_stage == "start":
                fake_thread.start.side_effect = failure
            with mock.patch.object(
                business_tasks.threading, "Thread", return_value=fake_thread,
                side_effect=failure if failure_stage == "construct" else None,
            ):
                with pytest.raises(RuntimeError, match="cannot launch thread"):
                    business_tasks.start(task_id, lambda: None)
            try:
                assert not business_tasks.is_active(task_id)
                finished = threading.Event()
                assert business_tasks.start(task_id, finished.set)
                assert finished.wait(2)
            finally:
                with business_tasks._LOCK:
                    business_tasks._ACTIVE.discard(task_id)
