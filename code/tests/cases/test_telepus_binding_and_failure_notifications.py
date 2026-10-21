"""Telepus identity binding and automatic Lineage failure recipients."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal import portal_access_control
from management_common.operation_errors import OperationError
from management_portal.resources.service import ResourceService
from management_portal.society_service import lineage_failure_notifications as failures, trigger_paths
from management_portal.watchboards import telepus
from tests.cases.permission_test_support import Fact, permissions  # noqa: F401

R = 'resident_111111111111'
P = 'project-a'


@pytest.fixture
def contacts(monkeypatch):
    users = [dict(username=name, configured=True, status='active') for name in ('alice', 'bob')]
    access = SimpleNamespace(
        user=lambda name: next((user for user in users if user['username'].casefold() == name.casefold()),
                               dict(username=name, configured=False, status='active')),
        list_users=lambda: users,
        snapshot=lambda: {'users': {user['username']: user for user in users}},
        is_active=lambda name: any(user['username'] == name and user['status'] == 'active' for user in users),
    )
    monkeypatch.setattr(portal_access_control, 'get_portal_access_store', lambda: access)
    monkeypatch.setattr(telepus, 'get_portal_access_store', lambda: access)
    def execute(actor, **arguments):
        return telepus.act(SimpleNamespace(actor=actor), 'save', arguments)
    return SimpleNamespace(read=telepus._read_telepus_telegram_user_contacts, execute=execute, users=users,
                           service=ResourceService())


def test_binding_uses_authenticated_user_and_rejects_spoofed_username(contacts):
    """Telepus 绑定只用已认证用户身份，拒绝冒用他人用户名或由 Resident 绑定"""
    result = contacts.execute('bob', id_token='123456')
    assert result['contact']['portal_actor'] == 'bob'
    before = contacts.read()
    for field in ('name', 'username', 'portal_actor', 'previous_username'):
        with pytest.raises(ValueError, match='Unsupported'):
            contacts.execute('bob', id_token='654321', **{field: 'alice'})
    with pytest.raises(ValueError, match='User'):
        contacts.execute(R, id_token='999999')
    assert contacts.read() == before


def test_even_portal_admin_binds_only_the_authenticated_user(contacts):
    """portal_admin 也只能为自己绑定 Telepus 联系人"""
    # alice holds portal_admin in the authorization fixture. There is no
    # administrator override in the contact interface.
    with pytest.raises(ValueError, match='Unsupported'):
        contacts.execute('alice', id_token='123456', name='bob')
    assert contacts.read() == {}
    assert contacts.execute('alice', id_token='123456')['contact']['portal_actor'] == 'alice'


def test_binding_validates_old_owner_uniqueness_and_account_existence(contacts):
    """Telepus 绑定校验原联系人归属、ID 唯一和账号存在，失败时联系人不变"""
    contacts.execute('alice', id_token='123456')
    before = contacts.read()
    with pytest.raises(OperationError, match='原联系人'):
        contacts.execute('bob', id_token='654321', previous_id_token='123456')
    with pytest.raises(ValueError, match='CreateUser'):
        contacts.execute('missing', id_token='111111')
    with pytest.raises(ValueError, match='已存在'):
        contacts.execute('bob', id_token='123456')
    assert contacts.read() == before
    good = contacts.execute('alice', id_token='654321', previous_id_token='123456')
    assert telepus._read_telepus_telegram_user_contacts() == {'654321': good['contact']}
    with pytest.raises(OperationError, match='原联系人'):
        contacts.execute('alice', id_token='111111', previous_id_token='123456')


def test_notification_recipients_use_live_inherited_admin_and_active_users(contacts, permissions):
    """通知收件人是自己的表项在当前祖先链上放行 UpdateLineage 的用户，排除停用用户和 Resident

    分类节点上的表项代表该分类的每位 User：管理员和一般用户都是收件人；实习用户只因自己的表项成为收件人。
    """
    from management_portal import permission_system
    lineage = 'lineage_aaaaaaaaaaaa'
    contacts.users.append(dict(username='carol', configured=True, status='active'))
    permissions.place(Fact('project', P), Fact('lineage', lineage, {'project': P}))
    permissions.administrator('alice')
    permissions.users('bob', category='intern')
    entry = permissions.grant('bob', P, '@admin')
    permissions.grant(R, lineage, '@admin')
    recipients = lambda: permission_system.users_permitted('UpdateLineage', 'lineage_id', lineage)  # noqa: E731
    assert recipients() == ['alice', 'bob', 'carol']
    contacts.users[1]['status'] = 'disabled'
    assert recipients() == ['alice', 'carol']
    contacts.users[1]['status'] = 'active'
    store = permissions.store
    with store.transaction() as connection:
        store.delete(connection, where='entry_id = %s', parameters=(entry.entry_id,))
    assert recipients() == ['alice', 'carol']


FAILED = dict(status='failed', trigger_path_id='run_one', finished_at='2026-10-12T00:01:00Z', trigger_type='time.reached')


def _route_failures(monkeypatch, *, leader='resident_aaaaaaaaaaaa', unreached=(), handled=False):
    notices, written = [], []
    monkeypatch.setattr(failures.permission_system, 'users_permitted', lambda *_: ['CASE-A', 'bob'])
    monkeypatch.setattr(failures.lineage_human_notifications, 'create_notification', lambda *a, **k: notices.append(k))
    monkeypatch.setattr(failures, 'handled', lambda *_: handled)
    monkeypatch.setattr(failures, '_leader', lambda _: leader)
    from management_portal.society_service import outward_messages

    def post(resident_id, users, text, **kwargs):
        written.append((resident_id, users, text, kwargs))
        return {'unreached': list(unreached)}
    monkeypatch.setattr(outward_messages, 'post_platform_notice', post)
    return notices, written


def test_an_automatic_failure_is_written_to_the_admins_in_the_leaders_name(monkeypatch):
    """自动运行失败由 Leader 写信给管理员，同一主题每天只占一条消息"""
    notices, written = _route_failures(monkeypatch)
    failures.notify('lineage_aaaaaaaaaaaa', 'run_one', FAILED, subject='resident-failure:' + R,
                    title='自动触发的 Resident 运行失败', message='Resident 工作失败', watched=('resident', R))
    assert notices == []
    [(leader, users, text, kwargs)] = written
    assert (leader, users) == ('resident_aaaaaaaaaaaa', ['CASE-A', 'bob'])
    assert text.startswith('自动触发的 Resident 运行失败\n\n')
    again = _route_failures(monkeypatch)[1]
    failures.notify('lineage_aaaaaaaaaaaa', 'run_two', FAILED, subject='resident-failure:' + R,
                    title='自动触发的 Resident 运行失败', message='又失败了', watched=('resident', R))
    assert again[0][3]['message_id'] == kwargs['message_id']  # one message per subject and day


def test_admins_the_leader_cannot_reach_and_leaderless_lineages_get_a_notification(monkeypatch):
    """Leader 无法送达的管理员和无 Leader 的 Lineage 改为收到系统通知"""
    notices, _ = _route_failures(monkeypatch, unreached=['bob'])
    failures.notify('lineage_aaaaaaaaaaaa', 'run_one', FAILED, subject='s', title='t', message='m')
    assert [notice['recipients'] for notice in notices] == [['bob']]
    notices, written = _route_failures(monkeypatch, leader='')
    failures.notify('lineage_aaaaaaaaaaaa', 'run_one', FAILED, subject='s', title='t', message='m')
    assert [notice['recipients'] for notice in notices] == [['CASE-A', 'bob']] and written == []


def test_a_failure_handler_in_the_lineage_tells_the_admins_itself(monkeypatch):
    """Lineage 内有事件源监听该失败时，系统不再另行通知管理员"""
    notices, written = _route_failures(monkeypatch, handled=True)
    failures.notify('lineage_aaaaaaaaaaaa', 'run_one', FAILED, subject='s', title='t', message='m',
                    watched=('resident', R))
    assert notices == [] and written == []


@pytest.mark.parametrize('source, watched, expected', [
    ({'event_type': 'effect.failed', 'subject_effect_id': 'effect_a'}, ('effect', 'effect_a'), True),
    ({'event_type': 'effect.failed', 'subject_effect_ids': ['effect_b', 'effect_a']}, ('script', 'effect_a'), True),
    ({'event_type': 'script.finished', 'subject_effect_id': 'effect_a', 'outcome': 'failed'}, ('script', 'effect_a'), True),
    ({'event_type': 'script.finished', 'subject_effect_id': 'effect_a', 'outcome': 'succeeded'}, ('script', 'effect_a'), False),
    ({'event_type': 'script.finished', 'subject_effect_id': 'effect_a'}, ('effect', 'effect_a'), False),
    ({'event_type': 'effect.failed', 'subject_effect_id': 'effect_b'}, ('effect', 'effect_a'), False),
    ({'event_type': 'resident.work_failed', 'subject_resident_ids': [R]}, ('resident', R), True),
    ({'event_type': 'resident.work_finished', 'subject_resident_id': R}, ('resident', R), False),
])
def test_which_event_sources_watch_a_failure(source, watched, expected):
    """按事件类型和主题判断事件源是否监听某个 Effect、脚本或 Resident 的失败"""
    assert failures._watches_failure(source, watched) is expected


@pytest.mark.parametrize('kind', ['effect', 'invocation'])
@pytest.mark.parametrize('trigger', ['time.reached', 'file.changed', 'manual.triggered'])

def test_terminal_failures_include_pre_script_effect_and_resident_failures(monkeypatch, kind, trigger):
    """Effect 失败和自动触发的 Resident 失败发出通知，手动触发的 Resident 失败不通知"""
    notices, written = _route_failures(monkeypatch, leader="")
    from management_portal.society_service import effect_failure_notifications
    monkeypatch.setattr(effect_failure_notifications, 'recipients', lambda *_: ['CASE-A'])
    monkeypatch.setattr(trigger_paths, "get", lambda *_: {"input": {"type": trigger}})
    record = {"status": "failed", "finished_at": "2026-10-12T00:01:00Z"}
    failures.notify("lineage_aaaaaaaaaaaa", "path_one", record, subject=f"{kind}:failure",
                    title="执行失败", message="failure", watched=("effect" if kind == "effect" else "resident", R))
    assert bool(notices) == (kind == 'effect' or trigger != 'manual.triggered')
    if kind == 'effect':
        assert notices[0]['recipients'] == ['CASE-A']
    if notices:
        assert notices[0]["trigger_path_id"] == "path_one"
