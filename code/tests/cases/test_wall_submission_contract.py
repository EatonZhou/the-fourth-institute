"""WALL keeps durable submission identity across acceptance and recovery.

:module: workflow.wall
:importance: P1
"""
import pytest

from management_portal import lineage_workflows as workflows, resident_handoff, wall_backend as wall
from management_portal import wall_store
from tests.cases.test_wall import wall_project  # noqa: F401

BINDING = {'lineage_id': 'lineage_0123456789ab', 'resident_id': 'resident_0123456789ab',
           'event_source_id': 'event_source_request', 'effect_id': 'effect_request'}


@pytest.fixture
def submission(wall_project, monkeypatch):
    from types import SimpleNamespace

    env = SimpleNamespace(mode='submitted', triggers=[], waits=[])
    monkeypatch.setattr(workflows.WorkflowBindings, 'install', lambda *a, **k: dict(BINDING))
    monkeypatch.setattr(resident_handoff, 'ensure_invocation_resident', lambda **_: dict(BINDING))

    def trigger(self, binding, *, request_id, **kwargs):
        env.triggers.append(request_id)
        saved = wall._read_state()['jobs'][env.job['id']]
        # The real submit_invocation must commit this through WALL's real store
        # before the external workflow could accept any input.
        prepared = workflows.WorkflowBindings.prepare(BINDING, request_id=request_id)
        assert all(saved.get(key) == value for key, value in prepared.items())
        assert saved['status'] == 'preparing' and not saved.get('message_id')
        receipt = {**prepared, 'instruction_id': 'inst_lin_original',
                   'submission_reason': 'original receipt',
                   'submission_error': {'code': 'instruction.interrupted', 'commit_state': 'unknown'}}
        if env.mode == 'pending':
            raise workflows.WorkflowPending(binding, receipt)
        if env.mode == 'not_submitted':
            raise workflows.WorkflowFailed(binding, receipt)
        return {**prepared, 'instruction_id': 'inst_lin_original', 'message_id': 'msg_actual',
                'event_id': 'event_original', 'trigger_path_id': 'trigger_path_original'}

    def wait(**kwargs):
        env.waits.append(kwargs)
        return {'message': 'WALL_RESULT: {"status":"completed","summary":"Original job done"}'}

    monkeypatch.setattr(workflows.WorkflowBindings, 'trigger', trigger)
    monkeypatch.setattr(resident_handoff, 'resume_and_wait', wait)
    env.job = wall.action({'action': 'analyse'}, 'owner')['job']
    return env


@pytest.mark.parametrize('mode,expected', [('submitted', 'completed'), ('pending', 'preparing'), ('not_submitted', 'failed')])
def test_real_handoff_preparation_and_returned_receipt_survive_wall_storage(submission, mode, expected):
    submission.mode = mode
    wall._run_job(submission.job['id'])
    saved = wall._read_state()['jobs'][submission.job['id']]
    assert saved['status'] == expected
    assert len(submission.triggers) == 1
    assert saved['request_id'] == submission.triggers[0]
    assert saved['instruction_id'] == 'inst_lin_original'
    if mode == 'submitted':
        assert saved['message_id'] == submission.waits[0]['message_id'] == 'msg_actual'
        assert saved['message_id'] != saved['request_id']
        assert saved['trigger_path_id'] == 'trigger_path_original'
    else:
        assert not saved.get('message_id') and not submission.waits
        assert saved['submission_error']['code'] == 'instruction.interrupted'


@pytest.mark.parametrize('cancel', [False, True])
def test_restart_observes_original_pending_submission_without_redelivery(submission, monkeypatch, cancel):
    submission.mode = 'pending'
    wall._run_job(submission.job['id'])
    original = wall._read_state()['jobs'][submission.job['id']]
    observations, cancellations = [], []

    def resolve(*, actor, handle):
        assert actor == 'owner' and handle['request_id'] == original['request_id']
        observations.append(handle)
        return workflows.submission_handle(original, state='pending' if len(observations) == 1 else 'submitted',
                                           message_id='msg_recovered')

    monkeypatch.setattr(resident_handoff, 'resolve_invocation_submission', resolve)
    monkeypatch.setattr(resident_handoff, 'cancel_resident_invocation', lambda *args: cancellations.append(args))
    if cancel:
        wall.action({'action': 'stop', 'job_id': submission.job['id']}, 'owner')
    wall._run_job(submission.job['id'])
    assert wall._read_state()['jobs'][submission.job['id']]['status'] == 'preparing'
    assert not submission.waits and not cancellations
    wall._run_job(submission.job['id'])
    saved = wall._read_state()['jobs'][submission.job['id']]
    assert len(submission.triggers) == 1 and len(observations) == 2
    assert saved['status'] == ('cancelled' if cancel else 'completed')
    if cancel:
        assert cancellations == [(BINDING['resident_id'], 'owner', 'msg_recovered')]
        assert not submission.waits
    else:
        assert submission.waits[0]['message_id'] == 'msg_recovered'


def test_existing_wall_table_upgrades_without_rewriting_historical_job(wall_project):
    historical = {'id': 'old-completed', 'status': 'completed', 'summary': 'Keep original result',
                  'message_id': 'msg_historical', 'trigger_path_id': 'trigger_path_historical'}
    wall_store.WallState().replace({'jobs': {historical['id']: historical}})
    with wall_store.database().transaction() as c, c.raw.transaction(force_rollback=True):
        for column in (*wall_store.SUBMISSION_TEXT, 'submission_error'):
            c.execute('ALTER TABLE wall_jobs DROP COLUMN ' + wall_store.identifier(column))
        c.executescript(wall_store.DDL)
        c.executescript(wall_store.DDL)
        row = dict(c.execute("SELECT * FROM wall_jobs WHERE id='old-completed'").fetchone())
        assert {k: v for k, v in row.items() if v is not None} == historical
        handle = workflows.WorkflowBindings.prepare(BINDING, request_id='msg_prepared')
        wall_store.write(c, {'jobs': {historical['id']: {**historical, **handle,
                         'submission_error': {'code': 'instruction.interrupted'}}}})
        row = dict(c.execute("SELECT * FROM wall_jobs WHERE id='old-completed'").fetchone())
        assert row['request_id'] == 'msg_prepared'
        assert row['summary'] == historical['summary']
        assert row['submission_error'] == {'code': 'instruction.interrupted'}
