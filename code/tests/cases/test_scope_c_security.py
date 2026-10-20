"""Account, administrator and Git boundaries from the scope C repair."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from starlette.requests import Request

from management_portal import agent_token_auth, asgi, repo_workspace_backend as repo, web_isa
from management_portal import permission_system
from management_portal.hatg.contracts import InstructionCall, InstructionContext
from tests.cases.permission_test_support import permissions  # noqa: F401
from tests.cases.test_repo_workspace_backend import _git, _repo
from tests.cases.test_wall02_residual_20260919 import account_boundary, PASSWORD


def test_management_flag_follows_the_administrator_rule_only(permissions):
    """账号管理入口按 CreateUser 判定：管理员名单放行，资源上的 @admin 不算

    :module: spec.design-governance
    :importance: P2
    :design: decision_30528aff7035, decision_600ae27b0538
    """
    permissions.administrator("alice")
    assert permission_system.allows("alice", "CreateUser")
    # An @admin entry on one resource does not reach CreateUser's requirement on 共享.
    permissions.grant("bob", "article_111111111111", "@admin")
    assert not permission_system.allows("bob", "CreateUser")
    permissions.administrator("alice", False)
    assert not permission_system.allows("alice", "CreateUser")
    permissions.administrator("bob")
    assert permission_system.allows("bob", "CreateUser")


def test_password_reset_requires_an_administrator(permissions, monkeypatch):
    """ResetUserPassword 要求管理员，对账号本身的 admin 不足以重置他人密码"""
    from management_portal import serve_management_portal as server
    from tests.cases.permission_test_support import user_fact
    permissions.place(user_fact("alice"), user_fact("bob"))
    permissions.administrator("alice")
    action = Mock(return_value={"user": {"username": "alice"}})
    monkeypatch.setattr(server, "_admin_access_action", action)
    monkeypatch.setattr(server, "_require_authenticated_actor", lambda actor: None)
    kernel = web_isa.build_kernel()
    permissions.grant("bob", "alice", "@admin")
    arguments = {"user_id": "alice", "new_password": PASSWORD}
    denied = kernel.execute(InstructionCall("ResetUserPassword", arguments, idempotency_key="denied-reset"), InstructionContext(caller="bob", entry="web"))
    assert denied.error["code"] == "instruction.forbidden"
    action.assert_not_called()
    arguments["user_id"] = "bob"
    accepted = kernel.execute(InstructionCall("ResetUserPassword", arguments, idempotency_key="allowed-reset"), InstructionContext(caller="alice", entry="web"))
    assert accepted.status == "committed", accepted.error
    assert action.call_args.args[1]["action"] == "reset_password"
    action.reset_mock()
    arguments["user_id"] = "alice"
    own = kernel.execute(InstructionCall("ResetUserPassword", arguments, idempotency_key="own-reset"), InstructionContext(caller="alice", entry="web"))
    assert own.status != "committed"
    action.assert_not_called()


@pytest.mark.parametrize("status", ["active", "disabled", "deleted"])
def test_script_token_rechecks_user_state(account_boundary, monkeypatch, status):
    """脚本运行令牌每次使用都重新检查用户状态，停用或删除后失效

    :module: spec.design-governance
    :importance: P2
    :design: decision_fb910c43ab07
    """
    import time

    portal = asgi.portal
    monkeypatch.setattr(portal, "_get_session_username", lambda _: "")
    token = agent_token_auth.generate_token()
    lease = account_boundary.root / "script.lease"
    lease.touch()
    agent_token_auth.get_agent_token_store().put("script-test", {
        "enabled": True, "managed_by": "portal_script_run", "caller_id": "fixture-user",
        "required_scope": "instructions", "token_hash": agent_token_auth.hash_token(token),
        "token_lookup": agent_token_auth.token_lookup(token),
        "expires_at": time.time() + 60, "lease_path": str(lease),
    })
    if status == "deleted":
        account_boundary.access.retire_user("fixture-user", actor="CASE-A")
    else:
        account_boundary.access.upsert_user("fixture-user", actor="CASE-A", status=status)
    request = Request({"type": "http", "headers": [(b"authorization", ("Bearer " + token).encode())]})
    assert asgi._instruction_principal(request) == (("fixture-user", "cli") if status == "active" else ("", ""))


@pytest.mark.parametrize("operation,branch", [("pull", "--upload-pack=malicious"), ("push", "--receive-pack=malicious"), ("push", "main:other")])
def test_sync_rejects_git_option_and_refspec_injection(tmp_path, monkeypatch, operation, branch):
    """仓库同步拒绝以 Git 选项或 refspec 形式注入的分支名

    :module: spec.design-governance
    :importance: P2
    :design: decision_6476c69b33da
    """
    repo_id, _ = _repo(tmp_path, with_remote=True)
    network = Mock()
    monkeypatch.setattr(repo, "_network_git", network)
    with pytest.raises(ValueError, match="invalid branch name"):
        repo.sync_repo(repo_id, operation, remote="origin", branch=branch)
    network.assert_not_called()


@pytest.mark.parametrize("staged", [True, False])
def test_scoped_staging_treats_pathspec_magic_literally(tmp_path, staged):
    """限定目录的暂存把 :(top) 等 pathspec 魔法按字面路径处理

    :module: spec.design-governance
    :importance: P2
    :design: decision_956154cd02f6
    """
    _, checkout = _repo(tmp_path)
    workspace = checkout / "src"
    (checkout / "outside.txt").write_text("outside")
    literal = workspace / ":(top)"
    literal.write_text("inside")
    if not staged:
        _git(checkout, "add", "-A")
    repo.stage_scoped_git_paths(workspace, [":(top)"], staged=staged)
    paths = _git(checkout, "diff", "--cached", "--name-only").splitlines()
    assert ("outside.txt" in paths) is (not staged)
    assert ("src/:(top)" in paths) is staged


def test_authentication_windows_survive_new_challenges(account_boundary, monkeypatch):
    """登录与二次验证的失败计数跨新挑战和新实例保留，超限后正确密码也被拦截"""
    from management_portal import serve_management_portal as portal
    from management_portal.authentication_attempts import AuthenticationAttempts
    attempts = AuthenticationAttempts(limit=2, window=60, capacity=2)
    monkeypatch.setattr(portal, '_AUTH_ATTEMPTS', attempts)
    assert not portal._verify_credentials('fixture-user', 'wrong')
    assert not portal._verify_credentials('fixture-user', 'wrong')
    assert not portal._verify_credentials('fixture-user', PASSWORD)
    attempts.succeeded('password', 'fixture-user')
    assert portal._verify_credentials('fixture-user', PASSWORD)
    verifier = Mock(return_value=None)
    monkeypatch.setattr(portal._PORTAL_TWO_FACTOR, 'verify', verifier)
    for _ in range(3):
        portal._verify_second_factor('fixture-user', '000000')
    assert verifier.call_count == 2
    assert not AuthenticationAttempts(limit=2, window=60).allow('factor', 'fixture-user')


def test_authentication_windows_expire_and_bound_storage():
    """登录尝试窗口到期后恢复放行，记录的主体数受容量上限约束"""
    from management_portal.authentication_attempts import AuthenticationAttempts
    attempts = AuthenticationAttempts(limit=2, window=60, capacity=2)
    assert attempts.allow('password', 'one', now=10)
    assert attempts.allow('password', 'two', now=10)
    assert not attempts.allow('password', 'three', now=10)
    assert attempts.allow('password', 'three', now=71)
    assert len(attempts.state.read({})) == 1


def test_unknown_password_runs_same_hash_work(monkeypatch):
    """不存在的账号校验密码时也执行相同轮数的哈希计算"""
    from management_portal.portal_access_control import PortalCredentialStore
    import hashlib
    derive = Mock(wraps=hashlib.pbkdf2_hmac)
    monkeypatch.setattr(hashlib, 'pbkdf2_hmac', derive)
    assert not PortalCredentialStore.verify_password('', 'wrong')
    assert derive.call_args.args[3] == 260_000


def test_second_totp_setup_cannot_replace_enabled_secret(account_boundary):
    """已启用的 TOTP 密钥不能被第二次设置覆盖"""
    from management_portal.portal_two_factor import generate_totp_secret
    account_boundary.factors.enable('fixture-user', generate_totp_secret(), last_counter=1)
    with pytest.raises(ValueError, match='already enabled'):
        account_boundary.factors.enable('fixture-user', generate_totp_secret(), last_counter=2)


def test_repo_search_preserves_dotted_directory(tmp_path):
    """仓库文件搜索结果保留名称含点的目录"""
    repo_id, checkout = _repo(tmp_path)
    directory = checkout / 'version.1'
    directory.mkdir()
    (directory / 'example.txt').write_text('example')
    result = repo.search_repo_files(repo_id, query='example')
    assert result['results'][0]['directory'] == 'version.1'


def test_auto_commit_generation_does_not_hold_repo_lock(tmp_path):
    """自动提交生成提交信息时不持有仓库锁，期间文件变化则以冲突中止"""
    import threading
    repo_id, checkout = _repo(tmp_path)
    (checkout / 'notes.txt').write_text('change')
    acquired = threading.Event()
    def generator(context):
        def edit():
            with repo._repo_lock(repo_id):
                (checkout / 'notes.txt').write_text('new concurrent change')
                acquired.set()
        thread = threading.Thread(target=edit, daemon=True)
        thread.start()
        assert acquired.wait(2), 'generation held the repository write lock'
        thread.join()
        return {'subject': 'test', 'reason': 'test'}
    with pytest.raises(repo.RepoWorkspaceConflict, match='变化'):
        repo.auto_commit_repo(repo_id, message_generator=generator)
