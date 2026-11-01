"""Portal System's mechanical directory maintenance, invoked by RunScript."""
from pathlib import Path

from management_common.directory_retention import RetentionStore
from management_common.file_retention import CACHE_CAPACITIES
from management_common.instance_manifest import instance_runtime_root


def workflow_operations(script_path):
    """ISA edit operations for the existing System Lineage, never a new Lineage."""
    from management_portal.society_service.lineage_effect_script import literal_argument
    script = (f'run=$(RunScript {literal_argument("path", str(script_path))} runtime=python '
              'exclusive_key=directory-retention timeout_seconds:=540)\n'
              'output script_run_id="${run.script_run_id}" status="${run.status}"')
    return [
        {'op': 'create', 'kind': 'component', 'ref': 'retention_timer', 'value': {
            'kind': 'event_source', 'title': '目录到期清理（每小时）', 'client_key': 'system:directory-retention:timer',
            'config': {'event_type': 'time.reached', 'schedule_mode': 'recurring', 'recurrence_kind': 'interval',
                       'interval_seconds': 3600, 'jitter_seconds': 0, 'misfire_policy': 'coalesce_latest',
                       'overlap_policy': 'drop', 'max_fires': None}}},
        {'op': 'create', 'kind': 'component', 'ref': 'retention_effect', 'value': {
            'kind': 'effect', 'title': '注销到期文件并重试目录删除', 'client_key': 'system:directory-retention:effect',
            'config': {'script': script, 'endpoints': []}}},
        {'op': 'create', 'kind': 'wire', 'value': {'kind': 'route', 'source': '$ref:retention_timer',
            'source_port': 'output', 'target': '$ref:retention_effect', 'target_port': 'invoke',
            'client_key': 'system:directory-retention:invoke',
            'config': {'unavailable_policy': 'drop', 'expires_after_seconds': 0}}},
    ]


def run(*, now=None, test_roots=None):
    from management_portal.test_framework.records import RunRecords, RunCoordinator, TERMINAL
    from management_portal.society_service.script_runs import store as scripts
    from management_common.resource_lifecycle import LifecycleStore
    from management_portal.test_framework.run_cleanup import finish_run
    retention = RetentionStore()
    report = {'tests': [], 'scripts': [], 'errors': []}
    from management_common.owned_path_deletion import OwnedPathDeletionStore
    try:
        report['claimed_deletions'] = OwnedPathDeletionStore().recover()
        report['errors'].extend(report['claimed_deletions'])
    except Exception as error:
        report['errors'].append(f'claimed deletions: {error}')
    # Recover ownership transfers before any queued old path can be removed.
    from management_portal.directory_move_owner import recover_pending_moves
    from management_portal.resources.project_deletion_effects import run_pending as project_effects
    from management_portal.workbench_projections.resource_lifecycle import ResourceProjectionCleanup
    for name, perform in (('moves', recover_pending_moves), ('projects', project_effects)):
        try:
            report[name] = perform()
            for item in report[name]:
                if item.get('error') or item.get('phase') in {'prepared', 'files_ready', 'committed'} or item.get('effects', {}).get('status') == 'pending':
                    report['errors'].append(f'{name}: {item}')
        except Exception as error:
            report['errors'].append(f'{name}: {error}')
    try:
        projections = ResourceProjectionCleanup()
        report['projections'] = projections.run_pending()
        report['pending_projections'] = projections.pending()
    except Exception as error:
        report['errors'].append(f'projections: {error}')
    from management_portal.paper_ccf_enrichment import cleanup_interrupted_requests
    try:
        report['ccf_requests'] = cleanup_interrupted_requests()
    except Exception as error:
        report['errors'].append(f'ccf requests: {error}')
    # Allocation cleanup survives the seven-day evidence limit independently.
    for run_id in LifecycleStore().closing_runs():
        try:
            outcome = finish_run(run_id)
            report['errors'].extend(outcome['errors'])
        except Exception as error:
            report['errors'].append(f'{run_id}: {error}')
    roots = list(test_roots) if test_roots is not None else list((instance_runtime_root() / 'test-framework').glob('*/runs.json'))
    for index in roots:
        try:
            records = RunRecords(Path(index))
            for record in records.recent():
                if record['status'] in TERMINAL and record.get('resource_cleanup', {}).get('status') != 'complete':
                    updated = records.finish_cleanup(record['run_id'])
                    report['errors'].extend(updated['resource_cleanup'].get('errors', []))
            report['tests'].extend(records.expire(Path(index).parent / 'run-artifacts', now=now))
            RunCoordinator(records, Path(index).parent / 'run-artifacts').remove_orphan_artifacts()
        except Exception as error:
            report['errors'].append(f'tests: {error}')
    for identifier in scripts.expired():
        try:
            scripts.delete_expired(identifier)
            report['scripts'].append(identifier)
        except Exception as error:
            report['errors'].append(f'{identifier}: {error}')
    report.update(retention.sweep(now=now, cache_capacities=CACHE_CAPACITIES))
    report['cleanup'] = retention.queue.run_pending()
    report['pending_cleanup'] = [row for row in retention.queue.status() if row['status'] != 'completed']
    report['ok'] = not report['errors'] and not report['pending_cleanup'] and not report.get('pending_projections')
    return report
