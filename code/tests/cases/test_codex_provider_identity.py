"""Preserve native thread identity when the account default provider changes.

:module: execution.runtime
:design: decision_6ad0384e020d
"""
from unittest.mock import Mock
import pytest
from management_portal.codex_app_server_threads import resume_thread
from management_portal.codex_app_server_protocol import CodexAppServerError

ERROR = CodexAppServerError('Codex app-server request failed: This chat uses openai, but your current provider is openai_http. Switch to openai to continue.')


def test_rejected_load_pins_native_identity_without_replaying_a_turn():
    rpc = Mock(side_effect=[ERROR, {'thread': {'id': 'same', 'modelProvider': 'openai'}}, {'thread': {'id': 'same'}}])
    params = {'threadId': 'same', 'excludeTurns': True, 'config': {'unrelated': True}, 'cwd': '/work'}
    cancelled = lambda: False
    result = resume_thread(rpc, params, timeout=4, cancellation_requested=cancelled)
    assert result == {'thread': {'id': 'same'}}
    assert [call.args[0] for call in rpc.call_args_list] == ['thread/resume', 'thread/read', 'thread/resume']
    assert rpc.call_args_list[1].args[1] == {'threadId': 'same', 'includeTurns': False}
    assert rpc.call_args.args[1] == {**params, 'modelProvider': 'openai'}
    assert all(call.kwargs == {'timeout': 4, 'cancellation_requested': cancelled} for call in rpc.call_args_list)
    assert 'modelProvider' not in params


@pytest.mark.parametrize('params', [{'threadId': 'same', 'modelProvider': 'explicit'},
                                  {'threadId': 'same', 'config': {'model_provider': 'explicit'}}])
def test_explicit_provider_conflict_is_not_overridden(params):
    rpc = Mock(side_effect=ERROR)
    with pytest.raises(CodexAppServerError): resume_thread(rpc, params)
    assert rpc.call_count == 1


@pytest.mark.parametrize('thread', [{'id': 'different', 'modelProvider': 'openai'},
                                  {'id': 'same', 'modelProvider': 'other'}, {'id': 'same'}])
def test_only_matching_native_identity_can_recover(thread):
    rpc = Mock(side_effect=[ERROR, {'thread': thread}])
    with pytest.raises(CodexAppServerError): resume_thread(rpc, {'threadId': 'same'})
    assert rpc.call_count == 2


def test_other_errors_and_second_failure_are_not_retried():
    rpc = Mock(side_effect=CodexAppServerError('permission denied'))
    with pytest.raises(CodexAppServerError): resume_thread(rpc, {'threadId': 'same'})
    assert rpc.call_count == 1
    rpc = Mock(side_effect=[ERROR, {'thread': {'id': 'same', 'modelProvider': 'openai'}}, ERROR])
    with pytest.raises(CodexAppServerError): resume_thread(rpc, {'threadId': 'same'})
    assert rpc.call_count == 3


def test_normal_load_does_not_add_metadata_reads():
    rpc = Mock(return_value={'thread': {'id': 'same'}})
    resume_thread(rpc, {'threadId': 'same'})
    rpc.assert_called_once_with('thread/resume', {'threadId': 'same'})
