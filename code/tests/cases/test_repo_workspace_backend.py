from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from management_portal import (
    repo_credentials_backend, repo_workspace_auto_commit, repo_workspace_checkout, repo_workspace_files,
    repo_workspace_git, repo_workspace_history, repo_workspace_network, repo_workspace_operations,
    repo_workspace_overview, repo_workspace_state, repo_workspace_statistics,
)
from management_portal import resource_identity_backend


@pytest.fixture
def ssh_key(tmp_path, monkeypatch):
    root = tmp_path / "ssh"
    root.mkdir()
    key = root / "id_ed25519_test"
    key.write_text("test fixture; not a usable private key")
    key.with_suffix(".pub").write_text("test public key fixture")
    monkeypatch.setattr(resource_identity_backend, "DEFAULT_SSH_KEY_ROOT", root)
    monkeypatch.setattr(repo_workspace_network, "_SSH_KEY_ROOT", root)
    return key


def _git(path: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(path), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode:
        raise AssertionError(proc.stderr or proc.stdout)
    return proc.stdout.strip()


def _repo(tmp_path: Path, *, with_remote: bool = False) -> tuple[str, Path]:
    checkout = tmp_path / "sample"
    checkout.mkdir()
    _git(checkout, "init", "-q", "-b", "main")
    _git(checkout, "config", "user.name", "Portal Test")
    _git(checkout, "config", "user.email", "test-actor@example.com")
    (checkout / "src").mkdir()
    (checkout / "src" / "app.py").write_text(
        "def hello():\n    return 'hello'\n",
        encoding="utf-8",
    )
    (checkout / "src" / "theme.css").write_text(
        ":root {\n  color-scheme: light;\n}\n",
        encoding="utf-8",
    )
    (checkout / "README.md").write_text(
        "# Sample\n\nA repository workspace fixture.\n",
        encoding="utf-8",
    )
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-qm", "Initial workspace")
    _git(checkout, "branch", "feature/navigation")
    if with_remote:
        remote = tmp_path / "remote.git"
        subprocess.run(
            ["git", "init", "--bare", "-q", str(remote)],
            check=True,
        )
        _git(checkout, "remote", "add", "origin", str(remote))
        _git(checkout, "push", "-qu", "origin", "main")
    record = resource_identity_backend.register_repo_checkout(
        checkout,
        project_ids=["Portal"],
    )
    return record["id"], checkout


def test_no_remote_does_not_treat_current_branch_as_default(tmp_path: Path) -> None:
    """仓库没有远端时，默认分支留空而不取当前分支"""
    repo_id, checkout = _repo(tmp_path)
    assert repo_workspace_overview.repo_overview(repo_id)["default_branch"] == ""
    _git(checkout, "switch", "feature/navigation")
    overview = repo_workspace_overview.repo_overview(repo_id)
    assert overview["current_branch"] == "feature/navigation"
    assert overview["default_branch"] == ""


def test_remote_records_include_sanitized_web_url_contracts(tmp_path, subtests, isolated_portal_case):
    """仓库远端记录隐去凭证并附网页地址，禁用 Push 时自动提交只留在本地"""
    with subtests.test(contract='test_remote_records_include_sanitized_web_url'), isolated_portal_case() as case_path:
        tmp_path = case_path
        _repo_id, checkout = _repo(tmp_path)
        _git(checkout, 'remote', 'add', 'origin', 'https://user:token@gitee.com/org/repo.git')
        records = repo_workspace_state._remote_records(checkout)
        assert records == [{'name': 'origin', 'url': 'https://gitee.com/org/repo.git', 'push_url': 'https://gitee.com/org/repo.git', 'web_url': 'https://gitee.com/org/repo', 'credentials_redacted': True}]
        assert 'user' not in repr(records)
        assert 'token' not in repr(records)
    with subtests.test(contract='test_workspace_fetch_and_push_use_registered_remotes'), isolated_portal_case() as case_path:
        tmp_path = case_path
        repo_id, checkout = _repo(tmp_path, with_remote=True)
        fetched = repo_workspace_operations.sync_repo(repo_id, 'fetch', remote='origin')
        assert fetched['operation'] == 'fetch'
        assert fetched['workspace']['upstream'] == 'origin/main'
        (checkout / 'sync.txt').write_text('push through Portal\n', encoding='utf-8')
        repo_workspace_operations.commit_repo_changes(repo_id, 'Add sync fixture', actor='portal')
        pushed = repo_workspace_operations.sync_repo(repo_id, 'push', remote='origin')
        assert pushed['operation'] == 'push'
        assert pushed['workspace']['status']['ahead'] == 0
    with subtests.test(contract='test_repo_push_policy_blocks_push_and_keeps_auto_commit_local'), isolated_portal_case() as case_path:
        tmp_path = case_path
        repo_id, checkout = _repo(tmp_path, with_remote=True)
        updated = resource_identity_backend.update_repo_policy(repo_id, push_enabled=False)
        assert updated['push_enabled'] is False
        assert repo_workspace_overview.repo_overview(repo_id)['push_enabled'] is False
        with pytest.raises(repo_workspace_checkout.RepoWorkspaceConflict, match='禁用 Push'):
            repo_workspace_operations.sync_repo(repo_id, 'push')
        (checkout / 'src' / 'app.py').write_text("def hello():\n    return 'local only'\n", encoding='utf-8')
        result = repo_workspace_auto_commit.auto_commit_repo(repo_id, actor='portal', message_generator=lambda _context: {'subject': 'Keep third-party changes local', 'reason': 'This checkout is configured as pull-only.'})
        assert result['outcome'] == 'committed'
        assert result['pushed'] is False
        assert result['push']['attempted'] is False
        assert result['automation_status']['will_push'] is False
    with subtests.test(contract='test_discard_repo_changes_restores_tracked_and_removes_untracked'), isolated_portal_case() as case_path:
        tmp_path = case_path
        repo_id, checkout = _repo(tmp_path)
        original = (checkout / 'src' / 'app.py').read_text(encoding='utf-8')
        (checkout / 'src' / 'app.py').write_text('changed\n', encoding='utf-8')
        (checkout / 'scratch').mkdir()
        (checkout / 'scratch' / 'note.txt').write_text('remove me\n', encoding='utf-8')
        result = repo_workspace_operations.discard_repo_changes(repo_id)
        assert result['discarded_count'] == 2
        assert result['workspace']['status']['dirty'] is False
        assert (checkout / 'src' / 'app.py').read_text(encoding='utf-8') == original
        assert not (checkout / 'scratch').exists()
    with subtests.test(contract='test_auto_commit_blocks_likely_secret_files_before_ai'), isolated_portal_case() as case_path:
        tmp_path = case_path
        repo_id, checkout = _repo(tmp_path)
        (checkout / '.env.local').write_text('TOKEN=do-not-send\n', encoding='utf-8')
        with pytest.raises(repo_workspace_checkout.RepoWorkspaceConflict, match='可能包含凭据'):
            repo_workspace_auto_commit.auto_commit_repo(repo_id, message_generator=lambda _context: pytest.fail('sensitive content should not be sent to AI'))
        assert _git(checkout, 'status', '--porcelain') == '?? .env.local'


def test_workspace_reads_tree_files_history_search_and_blame(tmp_path: Path, ssh_key) -> None:
    """仓库工作区读取目录树、文件、统计、历史、搜索与 blame，并识别重命名"""
    repo_id, checkout = _repo(tmp_path)
    (checkout / ".git" / "info" / "exclude").write_text(
        ".cache/\n",
        encoding="utf-8",
    )
    (checkout / ".cache").mkdir()
    (checkout / ".cache" / "ignored.txt").write_text(
        "not part of the repository tree\n",
        encoding="utf-8",
    )

    overview = repo_workspace_overview.repo_overview(
        repo_id,
    )
    assert overview["name"] == "sample"
    assert overview["current_branch"] == "main"
    assert overview["branch_count"] == 2
    assert overview["head"]["subject"] == "Initial workspace"
    assert overview["status"]["dirty"] is False
    assert overview["push_enabled"] is True
    assert overview["ssh_key"] == ""
    assert ssh_key.name in overview["available_ssh_keys"]
    assert "code_line_count" not in overview
    assert "repository_size_bytes" not in overview
    statistics = repo_workspace_statistics.repo_statistics(repo_id)
    assert {item["name"] for item in statistics["languages"]} == {"Python", "CSS"}
    assert round(sum(item["percent"] for item in statistics["languages"])) == 100
    assert statistics["code_line_count"] == 5
    assert statistics["repository_size_bytes"] == sum(
        path.stat().st_size
        for path in (
            checkout / "README.md",
            checkout / "src" / "app.py",
            checkout / "src" / "theme.css",
        )
    )

    root = repo_workspace_files.list_repo_tree(
        repo_id,
    )
    assert [item["name"] for item in root["entries"]] == ["src", "README.md"]
    assert root["readme"]["content"].startswith("# Sample")
    assert root["entries"][0]["last_commit"]["subject"] == "Initial workspace"
    src_tree = repo_workspace_files.list_repo_tree(
        repo_id,
        "src",
    )
    assert "code_line_count" not in src_tree
    src_statistics = repo_workspace_statistics.repo_statistics(repo_id, "src")
    assert src_statistics["code_line_count"] == 5
    assert {item["name"] for item in src_statistics["languages"]} == {"Python", "CSS"}

    file_payload = repo_workspace_files.read_repo_file(
        repo_id,
        "src/app.py",
    )
    assert file_payload["editable"] is True
    assert "def hello" in file_payload["content"]
    assert len(file_payload["hash"]) == 64

    search = repo_workspace_files.search_repo_files(
        repo_id,
        "app",
    )
    assert search["results"] == [
        {"path": "src/app.py", "name": "app.py", "directory": "src"}
    ]

    history = repo_workspace_history.list_repo_commits(
        repo_id,
        path="src/app.py",
    )
    assert history["commits"][0]["subject"] == "Initial workspace"
    detail = repo_workspace_history.read_commit(
        repo_id,
        history["commits"][0]["sha"],
    )
    assert "src/app.py" in detail["patch"]
    assert detail["stats"]["files_changed"] == 3
    assert detail["stats"]["additions"] > 0
    assert {item["path"] for item in detail["files"]} == {
        "README.md",
        "src/app.py",
        "src/theme.css",
    }
    assert next(
        item for item in detail["files"] if item["path"] == "src/app.py"
    )["patch"].startswith("diff --git ")

    blame = repo_workspace_history.blame_repo_file(
        repo_id,
        "src/app.py",
    )
    assert [line["line"] for line in blame["lines"]] == [1, 2]
    assert all(line["subject"] == "Initial workspace" for line in blame["lines"])

    _git(checkout, "mv", "src/theme.css", "src/portal.css")
    _git(checkout, "commit", "-qm", "Rename theme")
    renamed = repo_workspace_history.read_commit(
        repo_id,
        _git(checkout, "rev-parse", "HEAD"),
    )
    assert len(renamed["files"]) == 1
    assert renamed["files"][0]["path"] == "src/portal.css"
    assert renamed["files"][0]["original_path"] == "src/theme.css"
    assert renamed["files"][0]["status_code"] == "R"
    assert "rename to src/portal.css" in renamed["files"][0]["patch"]
    assert checkout.is_dir()


def test_large_repo_file_preview_is_streamed_and_marked_truncated(tmp_path: Path) -> None:
    """超过文本上限的仓库文件不给预览内容，标为截断并给出大小与哈希"""
    repo_id, checkout = _repo(tmp_path)
    content = b"x" * (repo_workspace_files.MAX_TEXT_BYTES + 257)
    (checkout / "large.log").write_bytes(content)

    result = repo_workspace_files.read_repo_file(
        repo_id,
        "large.log",
    )

    assert result["binary"] is True
    assert result["truncated"] is True
    assert result["content"] == ""
    assert result["size"] == len(content)
    assert result["hash"] == hashlib.sha256(content).hexdigest()
    assert result["preview_unavailable_reason"]


def test_workspace_edit_move_delete_diff_commit_and_branches(tmp_path: Path) -> None:
    """仓库工作区支持编辑、移动、删除、查看差异、提交与切换分支，过期哈希的写入被拒绝"""
    repo_id, _checkout = _repo(tmp_path)
    original = repo_workspace_files.read_repo_file(
        repo_id,
        "src/app.py",
    )

    saved = repo_workspace_files.write_repo_file(
        repo_id,
        "src/app.py",
        "def hello():\n    return 'updated'\n",
        expected_hash=original["hash"],
    )
    assert saved["status"]["dirty"] is True
    assert saved["status"]["changes"][0]["label"] == "修改"
    assert saved["status"]["changes"][0]["modified_at"]
    assert datetime.fromisoformat(
        saved["status"]["changes"][0]["modified_at"]
    ).timestamp() == pytest.approx(
        (_checkout / "src" / "app.py").stat().st_mtime,
        abs=1,
    )

    with pytest.raises(
        repo_workspace_checkout.RepoWorkspaceConflict,
        match="其他操作修改",
    ):
        repo_workspace_files.write_repo_file(
            repo_id,
            "src/app.py",
            "stale",
            expected_hash=original["hash"],
        )

    created = repo_workspace_files.create_repo_entry(
        repo_id,
        "notes/plan.md",
        content="# Plan\n",
    )
    assert created["path"] == "notes/plan.md"
    created_change = next(
        item
        for item in created["status"]["changes"]
        if item["path"] == "notes/plan.md"
    )
    assert created_change["modified_at"]
    moved = repo_workspace_files.move_repo_entry(
        repo_id,
        "notes/plan.md",
        "notes/roadmap.md",
    )
    assert moved["destination"] == "notes/roadmap.md"

    diff = repo_workspace_history.working_diff(
        repo_id,
        "src/app.py",
    )
    assert "-    return 'hello'" in diff["diff"]
    assert "+    return 'updated'" in diff["diff"]

    committed = repo_workspace_operations.commit_repo_changes(
        repo_id,
        "Update workspace",
        description="Exercise the Portal commit flow.",
        actor="playwright",
    )
    assert committed["commit"]["subject"] == "Update workspace"
    assert committed["status"]["dirty"] is False

    created_branch = repo_workspace_operations.update_repo_branch(
        repo_id,
        "create",
        "feature/portal",
    )
    assert created_branch["current_branch"] == "feature/portal"
    switched = repo_workspace_operations.update_repo_branch(
        repo_id,
        "switch",
        "main",
    )
    assert switched["current_branch"] == "main"

    deleted = repo_workspace_files.delete_repo_file(
        repo_id,
        "notes/roadmap.md",
    )
    assert deleted["deleted"] is True
    assert deleted["status"]["dirty"] is True
    deleted_change = next(
        item
        for item in deleted["status"]["changes"]
        if item["path"] == "notes/roadmap.md"
    )
    assert deleted_change["modified_at"] == ""


def test_push_permission_denial_automatically_disables_repo_push(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """远端拒绝写入权限时自动关闭该仓库的 Push"""
    repo_id, _checkout = _repo(tmp_path, with_remote=True)

    def reject_push(*_args, **_kwargs):
        raise repo_workspace_git.GitOperationError(
            "remote: Write access to repository not granted."
        )

    monkeypatch.setattr(repo_workspace_operations, "_network_git", reject_push)

    with pytest.raises(
        repo_workspace_git.GitOperationError,
        match="自动关闭",
    ):
        repo_workspace_operations.sync_repo(
            repo_id,
            "push",
            remote="origin",
        )

    assert resource_identity_backend.get_repo_record(
        repo_id,
    )["push_enabled"] is False


def test_repo_ssh_key_is_applied_to_network_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ssh_key,
) -> None:
    """仓库设置的 SSH 密钥用于推送等网络 git 操作"""
    repo_id, _checkout = _repo(tmp_path, with_remote=True)
    resource_identity_backend.update_repo_ssh_key(
        repo_id,
        ssh_key=ssh_key.name,
    )
    captured: dict = {}

    def complete_push(*_args, **kwargs):
        captured.update(kwargs)
        return "done", {
            "used_saved": False,
            "saved": False,
            "credential_id": "",
        }

    monkeypatch.setattr(repo_workspace_operations, "_network_git", complete_push)
    repo_workspace_operations.sync_repo(
        repo_id,
        "push",
        remote="origin",
    )

    assert captured["ssh_identity_file"] == ssh_key


def test_auto_commit_uses_generated_reason_and_pushes_remote(
    tmp_path: Path,
) -> None:
    """自动提交用 AI 生成的标题和理由写提交信息并推送到远端"""
    repo_id, checkout = _repo(tmp_path, with_remote=True)
    (checkout / "src" / "app.py").write_text(
        "def hello(name='Portal'):\n    return f'hello {name}'\n",
        encoding="utf-8",
    )
    captured: list[dict] = []

    def generate(context: dict) -> dict:
        captured.append(context)
        return {
            "subject": "Improve Portal greeting",
            "reason": "Allow callers to provide the displayed name so the fixture exercises the automated commit flow.",
            "provider": "kimi_primary",
            "model": "kimi-test",
        }

    result = repo_workspace_auto_commit.auto_commit_repo(
        repo_id,
        actor="test-actor",
        message_generator=generate,
    )

    assert captured[0]["repo_id"] == repo_id
    assert "src/app.py" in captured[0]["status"]
    assert "+def hello(name='Portal')" in captured[0]["diff"]
    assert result["outcome"] == "committed_and_pushed"
    assert result["committed"] is True
    assert result["pushed"] is True
    assert result["message"] == "Improve Portal greeting"
    assert result["reason"].startswith("Allow callers")
    assert result["ai"]["provider"] == "kimi_primary"
    assert result["ai"]["model"] == "kimi-test"
    assert result["push"]["remote"] == "origin"
    assert _git(checkout, "status", "--porcelain") == ""
    assert _git(checkout, "rev-parse", "HEAD") == _git(
        checkout,
        "rev-parse",
        "origin/main",
    )
    assert _git(checkout, "log", "-1", "--format=%B") == (
        "Improve Portal greeting\n\n"
        "Allow callers to provide the displayed name so the fixture exercises "
        "the automated commit flow."
    )


def test_network_git_prompts_then_saves_and_reuses_valid_credential(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """网络 git 缺凭证时要求输入，验证通过后保存并复用，凭证失效后标为 invalid"""
    credential_root = tmp_path / "credentials"
    credential_key = tmp_path / "credential.master-key"
    context = repo_workspace_state._remote_auth_context_from_url(
        "https://github.com/example/portal.git"
    )
    supplied_material: list[dict] = []
    accept_valid_token = {"value": True}

    def fake_git(
        _checkout: Path,
        *_args: str,
        git_config=(),
        env_overrides=None,
        **_kwargs,
    ) -> str:
        environment = dict(env_overrides or {})
        assert "fixture-valid-token" not in repr(git_config)
        assert "fixture-valid-token" not in repr(environment)
        material_path = environment.get("PORTAL_GIT_CREDENTIAL_FILE")
        if not material_path:
            raise repo_workspace_git.GitOperationError(
                "fatal: could not read Username for 'https://github.com': "
                "terminal prompts disabled"
            )
        material = json.loads(Path(material_path).read_text(encoding="utf-8"))
        supplied_material.append(material)
        if (
            material["secret"] != "fixture-valid-token"
            or not accept_valid_token["value"]
        ):
            raise repo_workspace_git.GitOperationError(
                "fatal: Authentication failed for "
                "'https://github.com/example/portal.git'"
            )
        return "push completed"

    monkeypatch.setattr(repo_workspace_network, "_git", fake_git)

    with pytest.raises(repo_workspace_git.GitAuthenticationRequired) as missing:
        repo_workspace_network._network_git(
            tmp_path,
            "push",
            "origin",
            "main",
            repo_id="repo_0123456789ab",
            operation="push",
            remote="origin",
            branch="main",
            context=context,
            actor="sample_admin",
            credential_root=credential_root,
            credential_key_path=credential_key,
        )
    assert missing.value.challenge["provider"] == "github"
    assert missing.value.challenge["secret_label"] == "Personal access token"
    assert missing.value.challenge["rejected"] is False

    with pytest.raises(repo_workspace_git.GitAuthenticationRequired) as rejected:
        repo_workspace_network._network_git(
            tmp_path,
            "push",
            "origin",
            "main",
            repo_id="repo_0123456789ab",
            operation="push",
            remote="origin",
            branch="main",
            context=context,
            actor="sample_admin",
            credential={
                "username": "octocat",
                "secret": "fixture-wrong-token",
            },
            remember=True,
            credential_root=credential_root,
            credential_key_path=credential_key,
        )
    assert rejected.value.challenge["rejected"] is True
    assert repo_credentials_backend.list_credentials(
        "sample_admin",
        root=credential_root,
        key_path=credential_key,
    )["credentials"] == []

    output, credential_result = repo_workspace_network._network_git(
        tmp_path,
        "push",
        "origin",
        "main",
        repo_id="repo_0123456789ab",
        operation="push",
        remote="origin",
        branch="main",
        context=context,
        actor="sample_admin",
        credential={
            "username": "octocat",
            "secret": "fixture-valid-token",
        },
        remember=True,
        credential_root=credential_root,
        credential_key_path=credential_key,
    )
    assert output == "push completed"
    assert credential_result["saved"] is True

    output, reused = repo_workspace_network._network_git(
        tmp_path,
        "push",
        "origin",
        "main",
        repo_id="repo_0123456789ab",
        operation="push",
        remote="origin",
        branch="main",
        context=context,
        actor="sample_admin",
        credential_root=credential_root,
        credential_key_path=credential_key,
    )
    assert output == "push completed"
    assert reused["used_saved"] is True
    assert [item["secret"] for item in supplied_material] == [
        "fixture-wrong-token",
        "fixture-valid-token",
        "fixture-valid-token",
    ]

    accept_valid_token["value"] = False
    with pytest.raises(repo_workspace_git.GitAuthenticationRequired) as expired:
        repo_workspace_network._network_git(
            tmp_path,
            "push",
            "origin",
            "main",
            repo_id="repo_0123456789ab",
            operation="push",
            remote="origin",
            branch="main",
            context=context,
            actor="sample_admin",
            credential_root=credential_root,
            credential_key_path=credential_key,
        )
    assert expired.value.challenge["rejected"] is True
    stored = repo_credentials_backend.list_credentials(
        "sample_admin",
        root=credential_root,
        key_path=credential_key,
    )["credentials"]
    assert stored[0]["status"] == "invalid"
    assert repo_credentials_backend.find_credential(
        "sample_admin",
        context,
        root=credential_root,
        key_path=credential_key,
    ) is None


def test_workspace_rejects_paths_outside_the_checkout(tmp_path, subtests, isolated_portal_case):
    """仓库文件读取拒绝上级、绝对、.git 内与反斜杠路径"""
    for path in [
        "../outside",
        "/absolute",
        ".git/config",
        "src\\windows.txt",
    ]:
        with subtests.test(path=path), isolated_portal_case() as case_path:
            tmp_path = case_path
            repo_id, _checkout = _repo(tmp_path)
            with pytest.raises(ValueError):
                repo_workspace_files.read_repo_file(
                    repo_id,
                    path,
                )


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ


def test_repo_overview_without_ssh_directory(tmp_path, monkeypatch):
    """新实例尚无 SSH 目录时，仓库仍能打开且没有可选密钥"""
    monkeypatch.setattr(repo_workspace_network, "_SSH_KEY_ROOT", tmp_path / "missing-ssh")
    repo_id, _ = _repo(tmp_path)
    assert repo_workspace_overview.repo_overview(repo_id)["available_ssh_keys"] == []


def test_unreadable_ssh_directory_returns_no_keys(tmp_path, monkeypatch):
    """延迟遍历 SSH 目录失败时返回空列表，不中断仓库页面"""
    def denied():
        raise PermissionError("unreadable test directory")
        yield
    from types import SimpleNamespace
    monkeypatch.setattr(repo_workspace_network, "_SSH_KEY_ROOT", SimpleNamespace(iterdir=denied))
    assert repo_workspace_network._available_ssh_keys() == []
