from dataclasses import replace
import json
from unittest import mock

import pytest

from management_common.execution_context_file import execution_context
from management_portal.codex_app_server_client import CodexAppServerClient, CodexThreadWriterBusy
from management_portal.execution.backends import CodexBackend
from management_portal.execution.contracts import ExecutionRequest, SessionOptions


def test_native_plan_is_enabled_without_changing_other_tools_or_the_input_config():
    """默认开启原生计划工具，保留其他工具配置且不改写调用方配置。"""
    options = {"config_overrides": {"tools": {"web_search": {"enabled": False}}}}
    original = json.loads(json.dumps(options))
    config = CodexBackend._thread_config(options)
    assert config["tools"] == {
        "web_search": {"enabled": False}, "update_plan": {"enabled": True},
    }
    assert options == original
    assert CodexBackend._thread_config({})["tools"]["update_plan"]["enabled"] is True


@pytest.mark.parametrize("overrides", [
    {"tools": {"update_plan": {"enabled": False}}},
    {"tools.update_plan.enabled": False},
    {"tools.update_plan": {"enabled": False}},
])
def test_native_plan_respects_explicit_session_disable(overrides):
    """显式会话关闭不被默认原生计划配置覆盖。"""
    original = json.loads(json.dumps(overrides))
    config = CodexBackend._thread_config({"config_overrides": overrides})
    for key, value in overrides.items():
        assert config[key] == value
    assert overrides == original
    assert not any(config.get(key) for key in ("tools.update_plan.enabled",))
    if "tools" not in overrides:
        assert "tools" not in config


@pytest.mark.parametrize("overrides", [{"tools": []}, {"tools": {"update_plan": False}}])
def test_native_plan_rejects_malformed_config_without_overwriting_it(overrides):
    """错误工具配置直接报错，不用默认值悄悄覆盖。"""
    with pytest.raises(ValueError, match="config_overrides.tools"):
        CodexBackend._thread_config({"config_overrides": overrides})


def test_thread_configuration_stays_stable_while_the_execution_changes(tmp_path, monkeypatch):
    """执行变化时 codex 线程配置保持不变，执行上下文文件按每次执行写入且只含执行 ID"""
    monkeypatch.setenv('PORTAL_EXECUTION_STATE_ROOT', str(tmp_path))
    first = ExecutionRequest.new(service_type='agent_session', provider='codex', operation='turn',
                                 actor='sample_admin', agent_id='fixture', cwd=str(tmp_path), prompt='one',
                                 session=SessionOptions(mode='persistent', binding_id='fixture-thread'),
                                 runtime_options={'config_overrides': {'shell_environment_policy.set': {
                                     'PORTAL_EXECUTION_ID': 'old-id',
                                 }}})
    second = replace(first, execution_id='execution-'+'a'*24)
    config = CodexBackend._runtime_thread_config(first)
    assert config == CodexBackend._runtime_thread_config(second)
    env = config['shell_environment_policy.set']
    assert 'PORTAL_EXECUTION_ID' not in env
    monkeypatch.setenv('PORTAL_EXECUTION_CONTEXT_FILE', env['PORTAL_EXECUTION_CONTEXT_FILE'])
    CodexBackend._write_execution_context(first)
    assert execution_context() == {'execution_id': first.execution_id}
    CodexBackend._write_execution_context(second)
    assert execution_context() == {'execution_id': second.execution_id}


def test_new_input_cannot_adopt_another_active_turn():
    """新输入不能接管其他输入的进行中轮次，报 CodexThreadWriterBusy"""
    client = CodexAppServerClient()
    active = {'id': 'other-turn', 'status': 'inProgress', 'items': [
        {'type': 'userMessage', 'clientId': 'other-input'}]}
    with mock.patch.object(client, 'request', return_value={}) as rpc, \
            mock.patch.object(client, '_read_active_turn', return_value=active):
        with pytest.raises(CodexThreadWriterBusy):
            client.run_turn('thread', 'new input', cwd='/tmp', client_user_message_id='my-input')
    assert all(call.args[0] != 'turn/start' for call in rpc.call_args_list)
    assert not client._turn_waiters


def test_cancel_during_start_interrupts_the_turn_from_buffered_notification():
    """turn/start 期间取消时，按已收到的 turn/started 通知中断该轮"""
    client = CodexAppServerClient()
    def request(method, params, **kwargs):
        if method == 'turn/start':
            client._dispatch_notification('turn/started', {'threadId': 'thread', 'turn': {'id': 'accepted'}})
            raise InterruptedError('cancelled before start response')
        return {}
    with mock.patch.object(client, 'request', side_effect=request), \
            mock.patch.object(client, '_read_active_turn', return_value=None), \
            mock.patch.object(client, 'interrupt_turn') as interrupt:
        with pytest.raises(InterruptedError):
            client.run_turn('thread', 'input', cwd='/tmp', client_user_message_id='my-input')
    interrupt.assert_called_once_with('thread', 'accepted')


def test_snapshot_rejoins_children_and_restores_cards_after_disconnect():
    """断线后快照重放重新跟踪子 agent 线程并恢复卡片，且只跟踪一次"""
    from management_portal.codex_app_server_client import _TurnWaiter
    from management_portal.codex_subagent_cards import CodexSubagentCards

    client = CodexAppServerClient()
    client._followed_children.add('child')
    client._child_threads['child'] = ('parent', 'activity')
    client._disconnect(RuntimeError('connection lost'))
    assert not client._followed_children and not client._child_threads
    cards = CodexSubagentCards('parent')
    projected = []
    waiter = _TurnWaiter('parent', turn_id='turn',
                         event_callback=lambda event: projected.extend(cards.observe(event)))
    client._turn_waiters['parent'] = waiter
    turn = {'id': 'turn', 'status': 'inProgress', 'items': [{
        'id': 'activity', 'type': 'subAgentActivity', 'kind': 'started',
        'agentThreadId': 'child', 'agentPath': '/root/worker', 'model': 'test-model',
    }]}
    with mock.patch('management_portal.codex_app_server_client.threading.Thread') as follow:
        client._replay_completed_items(waiter, turn)
        client._replay_completed_items(waiter, turn)
    assert client._followed_children == {'child'}
    follow.assert_called_once()
    assert follow.call_args.kwargs['args'] == ('child',)
    assert len(projected) == 1
    assert projected[0]['method'] == 'subagent/progress'


def test_completed_notification_preserves_real_final_items_for_checkpoint():
    """turn/completed 通知条目为空时补回已完成的 final_answer 条目供检查点读取"""
    from management_portal.codex_app_server_client import _TurnWaiter, codex_final_agent_response

    client = CodexAppServerClient()
    events = []
    client._turn_waiters['thread'] = _TurnWaiter('thread', turn_id='turn', event_callback=events.append)
    for item_id, phase, text in [('comment', 'commentary', 'working'), ('final', 'final_answer', 'answer')]:
        client._dispatch_notification('item/completed', {'threadId': 'thread', 'turnId': 'turn',
            'item': {'id': item_id, 'type': 'agentMessage', 'phase': phase, 'text': text}})
    client._dispatch_notification('turn/completed', {'threadId': 'thread',
        'turn': {'id': 'turn', 'status': 'completed', 'items': []}})
    assert codex_final_agent_response(events[-1]['params']['turn'], []) == 'answer'
    assert [item['id'] for item in events[-1]['params']['turn']['items']] == ['final']


@pytest.mark.parametrize('status', ['usageLimited', 'budgetLimited'])
def test_goal_limit_status_is_terminal(status):
    """goal 状态为 usageLimited 或 budgetLimited 时视为结束"""
    from management_portal.codex_app_server_client import _GoalWaiter

    client = CodexAppServerClient()
    waiter = _GoalWaiter('thread')
    client._goal_waiters['thread'] = waiter
    client._dispatch_notification('thread/goal/updated', {'threadId': 'thread', 'goal': {'status': status}})
    assert waiter.event.is_set()
    assert waiter.goal['status'] == status


def test_slow_account_catalog_does_not_block_another_account(monkeypatch):
    """一个账户读取模型目录慢时不阻塞其他账户"""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from management_portal import codex_capabilities

    monkeypatch.setattr(codex_capabilities, '_cache', {})
    monkeypatch.setattr(codex_capabilities, '_account_locks', {})
    entered, release = threading.Event(), threading.Event()
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return {'data': [{'model': 'slow'}]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        slow = pool.submit(codex_capabilities.runtime_catalog, mock.Mock(request=blocked), 'slow')
        assert entered.wait(1)
        try:
            fast = pool.submit(codex_capabilities.runtime_catalog,
                               mock.Mock(request=mock.Mock(return_value={'data': [{'model': 'fast'}]})), 'fast')
            assert fast.result(timeout=1)['models'] == ['fast']
        finally:
            release.set()
        assert slow.result(timeout=1)['models'] == ['slow']


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
