""":importance: P2"""
import threading
from unittest import mock

import pytest

from management_portal import business_tasks


def test_business_tasks_preserve_each_authenticated_identity_through_failure():
    """后台工作与错误回调保持原操作身份，独立请求不串用身份。"""
    from management_common.operation_identity import current_operation_identity, operation_identity_scope
    from management_common.principal import OperationIdentity

    identities = [OperationIdentity.for_actor('resident_0123456789ab', initiated_by='alice'),
                  OperationIdentity.for_actor('resident_abcdef012345', initiated_by='bob')]
    release = threading.Event()
    complete = [threading.Event(), threading.Event()]
    observed = [[], []]

    def work(index):
        assert release.wait(3)
        observed[index].append(current_operation_identity())
        raise RuntimeError('controlled background failure')

    def failed(index, error):
        observed[index].append(current_operation_identity())
        complete[index].set()

    before = current_operation_identity()
    try:
        for index, identity in enumerate(identities):
            with operation_identity_scope(identity):
                assert business_tasks.start(f'test-identity-{index}', work, index,
                    on_error=lambda error, index=index: failed(index, error))
                assert current_operation_identity() is identity
            assert current_operation_identity() is before
    finally:
        release.set()
    assert all(event.wait(3) for event in complete)
    assert observed == [[identity, identity] for identity in identities]
    assert current_operation_identity() is before


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
