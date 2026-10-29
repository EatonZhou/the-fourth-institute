"""Script completion produces a new occurrence; failures notify Lineage admins."""
from pathlib import Path
from .. import lineage_domain, lineage_program_queries, lineage_primitives, lineage_failure_notifications
from ..lineage_program_contracts import _program_event_source_contract_fingerprint
from .. import lineage_aggregate


def publish(record):
    if not record.get('lineage_id') or not record.get('effect_id'):
        return
    try:
        lineage = lineage_aggregate.read_lineage_state(record['lineage_id'])
    except (KeyError, FileNotFoundError):
        return  # Deleted Lineages have no remaining subscribers or owners.
    plan = lineage_program_queries._compiled_program(lineage)
    payload = {key: record.get(key) for key in ('script_run_id', 'status', 'exit_code', 'outputs', 'path', 'duration_seconds', 'effect_id')}
    payload['cause'] = {'kind': 'external', 'id': record['script_run_id']}
    event = lineage_primitives.make_event('script.finished', payload=payload,
                                         correlation=record['script_run_id'], identity=record.get('identity') or None,
                                         occurred_at=record['finished_at'])
    for watcher_id, watcher in sorted(plan.get('event_sources', {}).items()):
        if watcher.get('event_type') != 'script.finished':
            continue
        subjects = watcher.get('subject_effect_ids') or [watcher.get('subject_effect_id')]
        if record['effect_id'] not in subjects:
            continue
        outcome = watcher.get('outcome', 'any')
        succeeded = record['status'] == 'succeeded'
        if outcome == 'succeeded' and not succeeded or outcome == 'failed' and succeeded:
            continue
        lineage_domain.dispatch_lineage_program_event(record['lineage_id'], watcher_id, event, record['caller_id'],
            cursor=f"script-finished:{record['script_run_id']}:{watcher_id}",
            expected_event_source_contract=_program_event_source_contract_fingerprint(watcher))
    lineage_failure_notifications.notify(record['lineage_id'], record['trigger_path_id'], record,
        subject=f"script-failure:{record['effect_id']}",
        title='自动触发的脚本失败：' + Path(record['path']).name,
        message=f"脚本 {record['path']} 运行{record['status']}，请查看运行记录 {record['script_run_id']}。",
        watched=('script', record['effect_id']))
