"""Browser logout and security changes have distinct personal-token effects.

:importance: P2
"""
import hashlib
import secrets
from unittest.mock import Mock

import pytest

from management_portal import serve_management_portal as portal
from management_portal.portal_session_auth import PortalSessionAuthority
from management_portal.portal_two_factor import generate_totp_secret, totp_code
from management_portal.user_enrollment import UserEnrollment
from tests.cases.permission_test_support import permissions  # noqa: F401
from tests.cases.test_wall02_residual_20260919 import account_boundary, PASSWORD


def issue(enrollment, username):
    token = 'dbu_' + secrets.token_urlsafe(32)
    enrollment.create_token(username, hashlib.sha256(token.encode()).hexdigest())
    return token


def test_browser_logout_preserves_personal_tokens(account_boundary, monkeypatch, tmp_path):
    """浏览器登出只作废会话 cookie，个人访问令牌保持有效"""
    boundary = account_boundary
    enrollment = UserEnrollment(boundary.access, boundary.credentials)
    token = issue(enrollment, 'fixture-user')
    key = tmp_path / 'session.key'
    key.write_bytes(secrets.token_bytes(32))
    authority = PortalSessionAuthority(key, access_store=boundary.access)
    cookie = authority.issue('fixture-user', max_age=3600)
    monkeypatch.setattr(portal, '_same_origin_request', lambda request: True)
    monkeypatch.setattr(portal, '_get_session_token', lambda request: cookie)
    monkeypatch.setattr(portal, '_verify_session_token', authority.verify)
    monkeypatch.setattr(portal, '_clear_session_cookie', Mock())
    portal._route_logout_post(Mock(), None, 'fixture-user')
    assert not authority.verify(cookie)
    assert enrollment.authenticate(token) == 'fixture-user'
    assert enrollment.list_tokens('fixture-user')[0]['status'] == 'active'


@pytest.mark.parametrize('operation', ['change_password', 'admin_reset', 'admin_revoke', 'totp_enable', 'totp_disable', 'recovery_codes'])
def test_security_changes_revoke_all_personal_tokens_for_only_the_target(account_boundary, monkeypatch, operation):
    """改密码、管理员重置或撤销、启停 TOTP、换恢复码后撤销本人全部个人令牌，他人令牌不受影响

    :importance: P1"""
    boundary = account_boundary
    enrollment = UserEnrollment(boundary.access, boundary.credentials)
    tokens = [issue(enrollment, 'fixture-user') for _ in range(2)]
    peer = issue(enrollment, 'fixture-peer')
    if operation == 'change_password':
        portal._change_portal_password('fixture-user', {'current_password': PASSWORD,
            'new_password': PASSWORD + '-new', 'new_password_confirmation': PASSWORD + '-new'})
    elif operation.startswith('admin_'):
        portal._admin_access_action('CASE-A', {'username': 'fixture-user', 'action':
            'reset_password' if operation == 'admin_reset' else 'revoke_sessions', 'password': PASSWORD + '-new'})
    else:
        http = portal._ACCOUNT_HTTP
        monkeypatch.setattr(http, 'read_bounded_json', lambda *args, **kwargs: {})
        monkeypatch.setattr(http, 'require_reauthentication', Mock())
        monkeypatch.setattr(http, 'respond', lambda request, status, body, **kwargs: (status, body))
        if operation == 'totp_enable':
            setup = portal._begin_two_factor_setup('fixture-user')
            monkeypatch.setattr(http, 'read_bounded_json', lambda *args, **kwargs:
                {'setup_token': setup['setup_token'], 'code': totp_code(setup['secret'])})
            result = http.two_factor_confirm_post(Mock(), None, 'fixture-user')
        else:
            boundary.factors.enable('fixture-user', generate_totp_secret())
            method = http.two_factor_disable_post if operation == 'totp_disable' else http.two_factor_recovery_codes_post
            result = method(Mock(), None, 'fixture-user')
        assert result[0] == 200
    assert all(enrollment.authenticate(token) == '' for token in tokens)
    assert all(record['status'] == 'revoked' for record in enrollment.list_tokens('fixture-user'))
    assert enrollment.authenticate(peer) == 'fixture-peer'


def test_account_admin_label_follows_the_administrator_list_without_lifecycle_exemptions(account_boundary, permissions):
    """账户列表的 User 分类随修改变化，CASE-A 账户同样可被停用

    权限系统开启时，账户成为 Portal 管理员后列表中的 category 为 portal_admin、改回后为 member；CASE-A 账户没有生命周期豁免，可被其他账户停用。
    """
    boundary = account_boundary
    permissions.users('CASE-A', 'fixture-user')
    listed = lambda: {user['username']: user['category'] for user in boundary.access.list_users()}  # noqa: E731
    assert listed()['fixture-user'] == 'member'
    permissions.administrator('fixture-user')
    assert listed()['fixture-user'] == 'portal_admin'
    permissions.administrator('fixture-user', False)
    assert listed()['fixture-user'] == 'member'
    boundary.access.upsert_user('CASE-A', actor='fixture-user', status='disabled')
    assert boundary.access.user('CASE-A')['status'] == 'disabled'
