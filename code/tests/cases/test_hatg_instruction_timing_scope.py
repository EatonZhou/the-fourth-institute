"""Latency sampling surrounds the whole owning-service instruction execution.

:module: isa.instructions
:design: decision_898b8911a2ff
"""
from contextlib import contextmanager

import pytest

from management_portal.hatg import slow_call_profile
from management_portal.hatg.contracts import InstructionError
from tests.cases.test_hatg_isa_kernel import CONTEXT, fake, kernel, scheduler_call


@pytest.fixture
def tracking(monkeypatch):
    state = {'active': False, 'completed': []}

    @contextmanager
    def track(owner, instruction, *, scope='handler'):
        assert not state['active'], 'one sample per instruction, not duplicate handler samples'
        state['active'] = True
        try:
            yield
        finally:
            state['completed'].append((owner, instruction, scope))
            state['active'] = False

    monkeypatch.setattr(slow_call_profile, 'track', track)
    return state


@pytest.mark.parametrize('denied', [False, True])
def test_timing_includes_authorization_and_failures(kernel, tracking, denied):
    def authorize(*args):
        assert tracking['active']
        if denied:
            raise InstructionError('instruction.forbidden', 'fixture denial')

    kernel._authorize = authorize
    result = kernel.execute(scheduler_call(), CONTEXT)
    assert result.status == ('failed' if denied else 'committed')
    assert tracking['completed'] == [('execution', 'UpdateProviderAccount', 'instruction')]
    assert tracking['active'] is False


def test_timing_includes_record_and_replay_without_nested_samples(kernel, fake, tracking, monkeypatch):
    for name in ['begin', 'finish']:
        original = getattr(kernel.store, name)

        def checked(*args, _original=original, **kwargs):
            assert tracking['active']
            return _original(*args, **kwargs)

        monkeypatch.setattr(kernel.store, name, checked)
    first = kernel.execute(scheduler_call(idempotency_key='timing-replay'), CONTEXT)
    replay = kernel.execute(scheduler_call(idempotency_key='timing-replay'), CONTEXT)
    assert first.status == replay.status == 'committed'
    assert replay.replayed and len(fake.calls) == 1
    assert len(tracking['completed']) == 2
    assert all(row[2] == 'instruction' for row in tracking['completed'])


def test_stream_timing_finishes_before_subscription_iteration(tracking):
    from management_portal.hatg.catalog import instruction_catalog
    from management_portal.hatg.contracts import InstructionCall
    from management_portal.hatg.kernel import InstructionKernel, OwnerBinding

    name = 'WatchLineage'

    def events(invocation):
        assert tracking['active'] is False
        yield [{'cursor': 1}]
        assert tracking['active'] is False

    def authorize(*args):
        assert tracking['active']

    kernel = InstructionKernel(
        OwnerBinding('lineage', streams={name: events}), store=None,
        authorize=authorize, catalog={name: instruction_catalog()[name]},
    )
    subscription = kernel.stream(InstructionCall(name, {'lineage_id': 'lineage_aaaaaaaaaaaa'}), CONTEXT)
    assert tracking['completed'] == [('lineage', name, 'stream_setup')]
    assert list(subscription) == [[{'cursor': 1}]]
    assert len(tracking['completed']) == 1


def test_discovery_entrypoints_have_their_own_timing(tracking):
    from management_portal.hatg import routing
    from management_portal.hatg.contracts import InstructionCall

    result = routing.execute(InstructionCall('ListInstructions', {}), CONTEXT)
    assert result.status == 'committed'
    assert result.result['instructions']
    assert tracking['completed'] == [('entry', 'ListInstructions', 'instruction')]


def test_receipt_entry_timing_covers_lookup_and_keeps_identity(tracking, monkeypatch):
    from types import SimpleNamespace
    from management_portal.hatg import routing
    from management_portal.hatg.contracts import InstructionCall

    def receipt(instruction_id, context):
        assert tracking['active'] and context is CONTEXT
        return {'instruction_id': instruction_id}

    monkeypatch.setattr(routing, 'kernel_for', lambda *args, **kwargs: SimpleNamespace(execution_record=receipt))
    identifier = 'inst_exe_aaaaaaaaaaaa'
    result = routing.execute(InstructionCall('GetInstructionExecution', {'instruction_id': identifier}), CONTEXT)
    assert result.status == 'committed'
    assert result.result['instruction_id'] == identifier
    assert tracking['completed'] == [('entry', 'GetInstructionExecution', 'instruction')]
