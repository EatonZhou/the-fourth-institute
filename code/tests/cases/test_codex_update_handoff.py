"""Codex selection leaves live processes alone; terminal recovery survives handover."""
from dataclasses import replace
from unittest.mock import Mock, patch

import pytest

from management_portal.execution import provider_updates as updates
from management_portal.execution.backend_contracts import BackendContext
from management_portal.execution.codex_backend import CodexBackend
from management_portal.execution.contracts import ExecutionRecoveryDeferred
from management_portal.codex_app_server_protocol import CodexTurnResult, CodexTurnSafetyBlocked
from tests.cases.test_codex_recovery_budget import _invocation


def test_codex_selects_new_binary_without_touching_live_or_stopped_accounts(monkeypatch, tmp_path):
    old = tmp_path / 'old'
    old.write_text('old immutable program')
    new = tmp_path / 'new'
    new.write_text('new immutable program')
    link = tmp_path / 'current'
    link.symlink_to(old)
    runtime = updates.CodexRuntime()
    monkeypatch.setattr(runtime, 'activation_link', lambda: link)
    monkeypatch.setattr(runtime, 'link_target', lambda root: new)
    # Discovery, health checks and controller calls must not touch live daemons.
    monkeypatch.setattr(updates.CodexRuntime, 'services', property(lambda _: pytest.fail('service discovery')))
    monkeypatch.setattr(runtime, 'account_processes', lambda: pytest.fail('account discovery'))
    monkeypatch.setattr(runtime, 'ensure_ready', lambda: pytest.fail('live health check'))
    monkeypatch.setattr(updates, '_prepare_execution_recovery', lambda _: pytest.fail('recovery intent'))
    assert updates._activate('codex', runtime, tmp_path, prepare=True)
    assert link.resolve() == new
    assert old.read_text() == 'old immutable program'


@pytest.mark.parametrize('status', ['interrupted', 'failed'])
def test_terminal_event_survives_worker_handover_without_following_old_turn(monkeypatch, status):
    invocation = _invocation()
    thread = invocation.session.backend_session_id
    saved = []
    client = Mock()
    terminal = {'id': 'old-turn', 'status': status}
    if status == 'failed':
        terminal['error'] = {'message': 'The selected model is at capacity', 'codexErrorInfo': 'serverOverloaded'}

    def lost_worker(*args, **kwargs):
        kwargs['turn_started_callback']('old-turn')
        kwargs['event_callback']({'method': 'turn/completed', 'params': {'threadId': thread, 'turn': terminal}})
        raise ExecutionRecoveryDeferred('worker handover', retry_after_seconds=5)

    client.run_turn.side_effect = lost_worker
    backend = CodexBackend()
    backend._resolve_client = Mock(return_value=(Mock(account_id='one'), client, thread, False))
    context = BackendContext(emit_event=lambda *_: None, cancellation_requested=lambda: False,
                             set_interrupt_target=lambda *_: None,
                             set_provider_recovery_state=lambda state: saved.append(dict(state)))
    with pytest.raises(ExecutionRecoveryDeferred, match='handover'):
        backend.execute(invocation, context)
    assert saved[-1]['kind'] == 'terminal_turn'
    state = {**saved[-1], 'original_input_recorded': True}
    recovered = replace(invocation, recovery_checkpoint={'turn_id': 'old-turn', 'provider_recovery_state': state})
    client.run_turn.side_effect = None
    client.run_turn.return_value = CodexTurnResult(thread_id=thread, turn_id='new-turn', response='done',
                                                  turn={'id': 'new-turn', 'status': 'completed'}, token_usage={})
    with patch('management_portal.execution.provider_continuation.wait_before_continuation', return_value=True):
        assert backend.execute(recovered, context).output['message'] == 'done'
    client.resume_turn.assert_not_called()
    assert client.run_turn.call_args.args[1] != invocation.user_prompt
    assert saved[-1]['recovery_budget']['attempts'] == 1


def test_terminal_safety_rejection_is_not_continued():
    invocation = _invocation()
    thread = invocation.session.backend_session_id
    terminal = {'id': 'blocked-turn', 'status': 'failed',
                'error': {'message': 'blocked by our safety systems', 'codexErrorInfo': 'cyberPolicy'}}
    recovered = replace(invocation, recovery_checkpoint={'turn_id': 'blocked-turn', 'provider_recovery_state': {
        'kind': 'terminal_turn', 'turn_id': 'blocked-turn', 'turn': terminal}})
    client = Mock()
    backend = CodexBackend()
    backend._resolve_client = Mock(return_value=(Mock(account_id='one'), client, thread, False))
    with pytest.raises(CodexTurnSafetyBlocked):
        backend.execute(recovered, BackendContext(emit_event=lambda *_: None, cancellation_requested=lambda: False,
                                                set_interrupt_target=lambda *_: None))
    client.resume_turn.assert_not_called()
    client.run_turn.assert_not_called()
