"""A case cleanup retry cannot end the other cases of a live Campaign.

:module: operations.test-framework
"""
from management_common.resource_lifecycle import LifecycleStore, lifecycle_scope
from management_common.resource_identity import get_resource_type_registry
from management_common.directory_retention import RetentionStore
from management_portal.test_framework import run_cleanup, tau_fixtures


def test_background_cleanup_retries_only_closing_cases(tmp_path, monkeypatch):
    """真实持久层中一例清理，不关闭同 Run 活跃例，也不提前删除 Run 临时文件。"""
    store = LifecycleStore()
    run_id = 'testrun_0123456789abcdef'
    closed = store.open('finished-case', actor='owner', run_id=run_id, case_id='finished')
    active = store.open('active-case', actor='owner', run_id=run_id, case_id='active')
    registry = get_resource_type_registry()
    with lifecycle_scope(closed, actor='owner'):
        closed_resource = registry.new_id('workspace')
    with lifecycle_scope(active, actor='owner'):
        active_resource = registry.new_id('workspace')
    store.request_close(closed)
    released = []
    monkeypatch.setattr(tau_fixtures, '_release_resource', lambda rid, key: released.append(rid))
    # Run-owned retention is an independent boundary. Closing one case must
    # not release generated artifacts or expire the writer's temporary files.
    for operation in ('release_run', 'finish', 'sweep'):
        monkeypatch.setattr(RetentionStore, operation,
                            lambda *a, **kw: (_ for _ in ()).throw(AssertionError('Run was finalized')))
    outcome = run_cleanup.finish_closing_scopes(run_id)
    assert outcome['status'] == 'complete'
    assert released == [closed_resource]
    assert store.scope(closed)['state'] == 'complete'
    assert store.scope(active)['state'] == 'open'
    assert store.pending(active) == [active_resource]
    with lifecycle_scope(active, actor='owner'):
        next_resource = registry.new_id('workspace')
    assert store.pending(active) == [next_resource, active_resource]
    run_cleanup.finish_closing_scopes(run_id)
    assert released == [closed_resource]


def test_terminal_run_cleanup_still_releases_every_case(monkeypatch):
    store = LifecycleStore()
    run_id = 'testrun_abcdef0123456789'
    scopes = [store.open(key, actor='owner', run_id=run_id, case_id=key) for key in ('one', 'two')]
    registry = get_resource_type_registry()
    expected = []
    for scope in scopes:
        with lifecycle_scope(scope, actor='owner'):
            expected.append(registry.new_id('workspace'))
    released = []
    monkeypatch.setattr(tau_fixtures, '_release_resource', lambda rid, key: released.append(rid))
    assert run_cleanup.finish_run(run_id)['status'] == 'complete'
    assert set(released) == set(expected)
    assert all(store.scope(scope)['state'] == 'complete' for scope in scopes)
