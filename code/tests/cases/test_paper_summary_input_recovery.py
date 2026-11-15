from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from management_portal import paper_store, paper_summaries as summaries, paper_projection as projection, business_tasks
from management_portal.resident_handoff import ResidentInputTerminated

PAPER = 'paper_0123456789ab'
JOB = 'summary-original'
ACTOR = 'caller'
RECEIPT = projection.input_receipt({
    'schema': projection.PAPER_INPUT_RECEIPT_SCHEMA,
    'resident_id': 'summary-role', 'lineage_id': 'paper-lineage',
    'input_message_id': 'input-original', 'requested_message_id': 'planned-original',
    'event_id': 'event-original', 'reply_message_ids': ['reply-original'],
    'usage_message_id': 'reply-original',
})
ASSIGNED = {**RECEIPT, 'input_message_id': '', 'reply_message_ids': [], 'usage_message_id': ''}
RESULT = {'message': '这篇论文讨论机器人安全隔离及其适用边界。', 'usage': {'model_requests': 1}, 'invocation': RECEIPT}


@pytest.fixture
def env(tmp_path, monkeypatch):
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    monkeypatch.setattr(paper_store.PAPER_STORE, 'manifest_for_paper_id', lambda _: (bundle / 'manifest.json', {}))
    monkeypatch.setattr(paper_store.PAPER_STORE, 'paper_dir_from_manifest', lambda *_: bundle)
    monkeypatch.setattr(paper_store, '_notify_paper_projection', lambda *_a, **_kw: None)
    monkeypatch.setattr(summaries, 'PAPER_STORE_ROOT', tmp_path)
    monkeypatch.setattr(summaries, '_paper_by_id', lambda _: {'id': PAPER, 'title': 'Example', 'abstract': 'Evidence with limitations'})
    monkeypatch.setattr(summaries, '_paper_store_file', lambda *_: bundle / 'summary.md')
    monkeypatch.setattr(business_tasks, 'start', Mock())
    summaries._start_summary_task(PAPER, actor=ACTOR, job_id=JOB)
    sent = Mock(side_effect=lambda **options: send_result(options))
    monkeypatch.setattr(projection, 'turn', sent)
    monkeypatch.setattr(projection, 'resume', Mock(side_effect=lambda **options: send_result(options)))
    return sent, bundle


def send_result(options):
    if options.get('on_assigned'):
        options['on_assigned'](deepcopy(ASSIGNED))
    if options.get('on_submitted'):
        options['on_submitted'](deepcopy(RECEIPT))
    return deepcopy(RESULT)


def read_job():
    return summaries._paper_run_record_by_job_id(PAPER, JOB)


def run():
    summaries._run_summary_job(PAPER, actor=ACTOR, job_id=JOB)


def test_new_job_persists_canonical_receipt_and_material_identity(env):
    sent, _ = env
    run()
    job = read_job()
    assert job['summary_input']['receipt']['input_message_id'] == 'input-original'
    assert job['summary_input']['receipt']['resident_id'] == 'summary-role'
    assert job['summary_input']['receipt']['usage_message_id'] == 'reply-original'
    assert job['summary_input']['material_version']
    assert sent.call_args.kwargs['business_ref'] == JOB
    assert sent.call_args.kwargs['metadata']['material_attempt'] == JOB
    assert sent.call_count == 1 and job['status'] == 'done'


def test_legacy_running_never_delivers(env):
    sent, _ = env
    job = read_job()
    job.pop('summary_input', None)
    job['status'] = 'running'
    summaries._write_paper_run_record(PAPER, job)
    with pytest.raises(projection.InputConfirmationPending, match='Legacy'):
        run()
    assert read_job() == job
    sent.assert_not_called()


@pytest.mark.parametrize('status', ['queued', 'error'])
def test_legacy_without_locator_preserves_other_states(env, status):
    sent, _ = env
    job = read_job()
    job.pop('summary_input')
    job['status'] = status
    summaries._write_paper_run_record(PAPER, job)
    with pytest.raises(projection.InputConfirmationPending):
        run()
    assert read_job() == job
    sent.assert_not_called()


def test_assignment_timeout_confirms_then_resumes_original(env, monkeypatch):
    sent, _ = env
    def interrupted(**options):
        options['on_assigned'](ASSIGNED)
        raise TimeoutError('observation unavailable')
    sent.side_effect = interrupted
    with pytest.raises(TimeoutError):
        run()
    assert read_job()['status'] == 'running'
    confirm = Mock(return_value=RECEIPT)
    monkeypatch.setattr(projection, 'confirm_input', confirm)
    # Recovery is independent of changed/absent current paper material.
    monkeypatch.setattr(summaries, '_paper_by_id', lambda _: {'id': PAPER})
    run()
    assert confirm.call_args.kwargs['actor'] == ACTOR
    assert confirm.call_args.kwargs['receipt']['input_message_id'] == ''
    args = projection.resume.call_args.kwargs
    assert args['message_id'] == 'input-original' and args['resident_id'] == 'summary-role'
    assert args['actor'] == ACTOR
    assert 'prompt' not in args
    assert sent.call_count == 1 and read_job()['status'] == 'done'


def test_lost_pre_receipt_reply_never_replays(env):
    sent, _ = env
    sent.side_effect = TimeoutError('no receipt observed')
    with pytest.raises(TimeoutError):
        run()
    with pytest.raises(projection.InputConfirmationPending):
        run()
    assert sent.call_count == 1 and read_job()['status'] == 'running'
    projection.resume.assert_not_called()


def test_intent_write_failure_prevents_dispatch(env, monkeypatch):
    sent, _ = env
    original = paper_store._update_paper_run_record
    def fail_intent(paper_id, patch):
        if (patch.get('summary_input') or {}).get('phase') == 'dispatching':
            raise OSError('durable intent unavailable')
        original(paper_id, patch)
    monkeypatch.setattr(paper_store, '_update_paper_run_record', fail_intent)
    with pytest.raises(OSError):
        run()
    sent.assert_not_called()


def test_terminal_input_retains_receipt_and_observes_same_failure(env, monkeypatch):
    sent, _ = env
    error = ResidentInputTerminated({'state': 'failed', 'error': {'message': 'restricted profile rejected'}})
    def terminal(**options):
        options['on_submitted'](RECEIPT)
        raise error
    sent.side_effect = terminal
    with pytest.raises(ResidentInputTerminated):
        run()
    assert read_job()['status'] == 'error'
    assert read_job()['summary_input']['receipt']['input_message_id'] == 'input-original'
    monkeypatch.setattr(projection, 'confirm_input', Mock(return_value=RECEIPT))
    projection.resume.side_effect = error
    with pytest.raises(ResidentInputTerminated):
        run()
    assert sent.call_count == 1
    assert projection.resume.call_args.kwargs['message_id'] == 'input-original'


def test_result_save_failure_uses_cached_output_and_repairs_usage(env, monkeypatch):
    sent, bundle = env
    original = summaries.atomic_replace_json
    monkeypatch.setattr(summaries, 'atomic_replace_json', Mock(side_effect=OSError('disk unavailable')))
    with pytest.raises(OSError):
        run()
    assert read_job()['summary_input']['phase'] == 'generated'
    assert read_job()['status'] == 'running'
    assert (bundle / 'summary.md').is_file()
    monkeypatch.setattr(summaries, 'atomic_replace_json', original)
    run()
    assert (bundle / 'summary.usage.json').is_file()
    assert sent.call_count == 1 and read_job()['status'] == 'done'
    projection.resume.assert_not_called()


def test_parallel_same_job_dispatches_once(env):
    sent, _ = env
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: run(), range(2)))
    assert sent.call_count == 1 and read_job()['status'] == 'done'


def test_wrong_caller_rejected_without_receipt_or_job_mutation(env):
    sent, _ = env
    before = read_job()
    with pytest.raises(PermissionError):
        summaries._run_summary_job(PAPER, actor='other', job_id=JOB)
    assert read_job() == before
    sent.assert_not_called()


def test_changed_canonical_identity_rejected(env):
    sent, _ = env
    def changed(**options):
        options['on_submitted'](RECEIPT)
        options['on_submitted']({**RECEIPT, 'input_message_id': 'wrong-input'})
    sent.side_effect = changed
    with pytest.raises(projection.PaperInputIdentityError):
        run()
    assert read_job()['summary_input']['receipt']['input_message_id'] == 'input-original'
    assert read_job()['status'] == 'running'


def test_assignment_after_receipt_cannot_erase_identity(env):
    sent, _ = env
    def reordered(**options):
        options['on_submitted'](RECEIPT)
        options['on_assigned'](ASSIGNED)
        return deepcopy(RESULT)
    sent.side_effect = reordered
    run()
    assert read_job()['summary_input']['receipt']['input_message_id'] == 'input-original'


def test_repeated_create_cannot_reset_accepted_intent(env):
    sent, _ = env
    run()
    before = read_job()
    summaries._start_summary_task(PAPER, actor=ACTOR, job_id=JOB)
    assert not summaries._create_summary_job(PAPER, actor=ACTOR, job_id=JOB)
    assert read_job() == before
    assert sent.call_count == 1


def test_other_saved_summary_does_not_skip_original_input_confirmation(env, monkeypatch):
    sent, bundle = env
    def interrupted(**options):
        options['on_submitted'](RECEIPT)
        raise TimeoutError('original input still unobserved')
    sent.side_effect = interrupted
    with pytest.raises(TimeoutError):
        run()
    (bundle / 'summary.md').write_text('A different job saved this result.')
    monkeypatch.setattr(projection, 'confirm_input', Mock(return_value=RECEIPT))
    projection.resume.side_effect = ResidentInputTerminated({'state': 'failed'})
    with pytest.raises(ResidentInputTerminated):
        run()
    assert projection.resume.call_args.kwargs['message_id'] == 'input-original'
    assert read_job()['status'] == 'error' and sent.call_count == 1
    assert (bundle / 'summary.md').read_text() == 'A different job saved this result.'


def test_delayed_error_callback_cannot_downgrade_done(env):
    run()
    before = read_job()
    business_tasks.start.call_args.kwargs['on_error'](TimeoutError('older observation'))
    assert read_job() == before
