"""Finish the run's allocation scopes through normal product deletion owners."""
from uuid import uuid4

from management_common.resource_lifecycle import LifecycleStore


def delete_script_run(identifier):
    from management_portal.society_service.script_runs import store, service, runtime
    from management_common.directory_cleanup_store import get_directory_cleanup_store
    record = store.get(identifier)
    if record is None:
        return
    service.cancel(identifier, record['caller_id'], lineage_id=record['lineage_id'])
    queue = get_directory_cleanup_store()
    with store.domain().transaction() as connection:
        queue.lock(connection)
        row = connection.execute('SELECT status FROM runs WHERE script_run_id=%s FOR UPDATE', (identifier,)).fetchone()
        if row and row['status'] not in service.TERMINAL:
            raise RuntimeError('Test ScriptRun has not stopped')
        connection.execute('DELETE FROM runs WHERE script_run_id=%s', (identifier,))
        queue.enqueue(runtime.RUN_ROOT / identifier, owner_id=identifier, operation_id='test-end:' + identifier,
                      actor='directory-retention', connection=connection)


def finish_run(run_id):
    store = LifecycleStore()
    # Concurrent maintenance/UI retries must not mistake another finisher's
    # exclusive seal for a still-running request or repeat domain deletion.
    with store.db.advisory_lock(store.lock_key('finish-run:' + run_id)):
        return _finish_run(store, run_id)


def _finish_run(store, run_id):
    from .tau_fixtures import _release_resource, _delete_upload
    from management_common.directory_retention import RetentionStore
    from management_portal.resources.creation import finish_scope_creations

    released, errors, completed_scopes = [], [], []
    creation_errors = []
    def release_scope(scope):
        resources = sorted(store.pending(scope['id']), key=lambda rid:
                           {'project': 0, 'lineage': 1, 'resident': 2, 'workspace': 4}.get(rid.partition('_')[0], 3))
        failures = []
        for resource_id in resources:
            try:
                key = f'run-cleanup:{run_id}:{resource_id}:{uuid4().hex}'
                delete = _delete_upload if resource_id.startswith('upload_') else _release_resource
                delete(resource_id, key)
                store.released(scope['id'], resource_id)
                released.append(resource_id)
            except Exception as error:
                failures.append(f'{resource_id}: {error}')
        return failures

    for scope in store.for_run(run_id):
        store.request_close(scope['id'])
        # Stop owned work before asking for the exclusive seal. Otherwise a
        # long-running child can hold its allocation lease forever while cleanup
        # waits for that same child to finish by itself.
        initial_errors = release_scope(scope)
        try:
            store.seal(scope['id'], timeout=0)
        except Exception as error:
            errors.extend(initial_errors)
            errors.append(f"{scope['id']}: {error}")
            continue
        # Domain deletion terminates owned work before unregistering objects.
        # Fixed module Residents are members, not newly allocated objects.
        # Include allocations from in-flight calls that completed during stop.
        errors.extend(release_scope(scope))
        try:
            failures = finish_scope_creations(scope['id'])
        except Exception as error:
            failures = [f"{scope['id']}: {error}"]
        creation_errors.extend(failures)
        if not failures and not store.pending(scope['id']):
            completed_scopes.append(scope['id'])
    retention = RetentionStore()
    generated = 0
    if not errors and not creation_errors:
        generated = retention.release_run(run_id)
        retention.finish(run_id)
        for scope_id in completed_scopes:
            store.complete(scope_id)
    # Immediate temporary-file cleanup; other categories keep their deadlines.
    temporary = retention.sweep(owner_id=run_id, categories=['runtime_tmp'])
    outcomes = retention.queue.run_pending() if temporary['queued'] or released or generated else []
    return {'status': 'error' if errors else ('incomplete' if creation_errors else 'complete'),
            'resources': released, 'errors': errors + creation_errors,
            'files': outcomes}
