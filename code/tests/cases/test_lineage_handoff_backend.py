from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from management_portal.society_service import lineage_handoff_backend


def test_handoff_scope_rejects_paths_before_normalization(subtests, isolated_portal_case):
    """交接范围在规范化前拒绝绝对路径、上级目录和含多余分隔符的路径"""
    for scope in [
    "/etc", "//server/share", "src//config", "src/./config", "src/../config",
    "./folder", "src/", "C:\\data", "C:folder", "\\server\\share",
]:
        with subtests.test(scope=scope), isolated_portal_case() as case_path:
            with pytest.raises(ValueError, match="安全的相对路径"):
                lineage_handoff_backend._scope(scope)


def test_handoff_scope_preserves_explicit_workspace_root_and_relative_paths(subtests, isolated_portal_case):
    """交接范围保留 Workspace 根和相对路径，反斜杠统一为斜杠"""
    for scope, expected in [
    (None, "."), ("", "."), (".", "."), ("data", "data"),
    ("data/sub", "data/sub"), ("data\\sub", "data/sub"),
]:
        with subtests.test(scope=scope, expected=expected), isolated_portal_case() as case_path:
            assert lineage_handoff_backend._scope(scope) == expected


def test_handoff_rejects_overflow_without_dropping_items_contracts(subtests, isolated_portal_case):
    """交接项超过 24 个时报错而不丢弃，尝试记录留在交接项内，说明可显式清空"""
    with subtests.test(contract='test_handoff_rejects_overflow_without_dropping_items'), isolated_portal_case() as case_path:
        with pytest.raises(ValueError, match="24"):
            lineage_handoff_backend.normalize_handoff_fields(
                {"items": [{}] * 25}, now="2026-09-12T00:00:00Z",
            )
    with subtests.test(contract='test_handoff_normalization_keeps_attempts_inside_the_handoff'), isolated_portal_case() as case_path:
        fields = lineage_handoff_backend.normalize_handoff_fields(
            {
                "purpose": "探索新的 Lineage UI",
                "acceptance": "可运行并有截图",
                "explanation": "portal 目录包含本次交接的 Lineage 界面实现。",
                "items": [{
                    "item_id": "portal",
                    "source": {"workspace_id": "workspace_111111111111", "path": "/repo"},
                    "transfer": {"mode": "copy", "method": "git_worktree"},
                    "target": {"path": "/target"},
                    "attempts": [{"number": 1, "status": "failed", "error": "branch exists"}],
                }],
                "status": "failed",
            },
            now="2026-07-26T00:00:00Z",
        )

        assert fields["status"] == "failed"
        assert fields["items"][0]["attempts"][0]["error"] == "branch exists"
        assert "operation_id" not in fields["items"][0]["attempts"][0]
        assert "purpose" not in fields
        assert "acceptance" not in fields
        assert fields["explanation"] == "portal 目录包含本次交接的 Lineage 界面实现。"
    with subtests.test(contract='test_handoff_explanation_can_be_explicitly_cleared'), isolated_portal_case() as case_path:
        fields = lineage_handoff_backend.normalize_handoff_fields(
            {"explanation": "", "items": []},
            {"explanation": "旧解释", "items": []},
            now="2026-07-26T00:00:00Z",
        )

        assert fields["explanation"] == ""


def test_handoff_copy_cannot_select_an_excluded_file_or_directory(tmp_path, subtests, isolated_portal_case):
    """交接复制的范围不能选中被排除的文件或目录，指向它们的符号链接也不行"""
    for scope in [
    ".env", "nested/.env.local", ".git/config", "node_modules", "alias", "custom.txt",
]:
        with subtests.test(scope=scope), isolated_portal_case() as case_path:
            tmp_path = case_path
            source = tmp_path / "source"
            source.mkdir()
            (source / ".env").write_text("SYNTHETIC_AUDIT_SECRET=not_real")
            (source / "nested").mkdir()
            (source / "nested/.env.local").write_text("synthetic fixture")
            (source / ".git").mkdir()
            (source / ".git/config").write_text("synthetic fixture")
            (source / "node_modules").mkdir()
            (source / "custom.txt").write_text("excluded fixture")
            (source / "alias").symlink_to(source / ".env")
            target = tmp_path / "copy"

            with pytest.raises(ValueError, match="复制排除项"):
                lineage_handoff_backend.materialize_handoff_item(
                    {
                        "source": {"path": str(source), "scope": scope},
                        "transfer": {"mode": "copy", "method": "filesystem_copy", "parameters": {"exclude": ["custom.txt"]}},
                        "target": {"path": str(target)},
                    },
                    allowed_target_roots=[tmp_path],
                    checked_at="2026-09-12T00:00:00Z",
                )

            assert not target.exists()


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def test_handoff_commands_use_a_configurable_long_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """交接命令默认使用可由环境变量配置的长超时"""
    observed: dict[str, object] = {}
    monkeypatch.setenv(
        lineage_handoff_backend.HANDOFF_COMMAND_TIMEOUT_ENV,
        "2700",
    )

    def fake_run(command: list[str], **kwargs: object):
        observed.update(kwargs)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(lineage_handoff_backend.subprocess, "run", fake_run)

    lineage_handoff_backend._run(["git", "status"])

    assert observed["timeout"] == 2700


def test_handoff_command_timeout_can_be_overridden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """交接命令的超时可以按调用单独指定"""
    observed: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object):
        observed.update(kwargs)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(lineage_handoff_backend.subprocess, "run", fake_run)

    lineage_handoff_backend._run(
        ["git", "status"],
        timeout_seconds=7200,
    )

    assert observed["timeout"] == 7200


def test_handoff_command_timeout_is_reported_as_preparation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """交接命令超时报为准备错误，提示超时秒数、命令和配置变量名"""
    monkeypatch.setenv(
        lineage_handoff_backend.HANDOFF_COMMAND_TIMEOUT_ENV,
        "3600",
    )

    def time_out(command: list[str], **kwargs: object):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(lineage_handoff_backend.subprocess, "run", time_out)

    with pytest.raises(
        lineage_handoff_backend.HandoffPreparationError,
        match=(
            r"超过 3600 秒.*git status.*"
            + lineage_handoff_backend.HANDOFF_COMMAND_TIMEOUT_ENV
        ),
    ):
        lineage_handoff_backend._run(["git", "status"])


def test_filesystem_copy_excludes_secrets_and_records_the_actual_result_contracts(tmp_path, subtests, isolated_portal_case):
    """交接复制排除密钥和依赖目录，git worktree 带走未提交改动，目标须在允许的根目录内"""
    with subtests.test(contract='test_filesystem_copy_excludes_secrets_and_records_the_actual_result'), isolated_portal_case() as case_path:
        tmp_path = case_path
        source = tmp_path / "source"
        source.mkdir()
        (source / "app.py").write_text("print('ok')\n", encoding="utf-8")
        (source / ".env").write_text("SECRET=hidden\n", encoding="utf-8")
        (source / "node_modules").mkdir()
        (source / "node_modules" / "ignored.js").write_text("ignored\n", encoding="utf-8")
        target_root = tmp_path / "targets"
        target = target_root / "copy"

        result = lineage_handoff_backend.materialize_handoff_item(
            {
                "source": {"path": str(source), "scope": "."},
                "transfer": {"mode": "copy", "method": "filesystem_copy", "parameters": {}},
                "target": {"path": str(target), "scope": "."},
            },
            allowed_target_roots=[target_root],
            checked_at="2026-07-26T00:00:00Z",
        )

        assert (target / "app.py").is_file()
        assert not (target / ".env").exists()
        assert not (target / "node_modules").exists()
        assert result["file_count"] == 1
        assert result["content_hash"].startswith("sha256:")
    with subtests.test(contract='test_git_worktree_can_transfer_the_current_uncommitted_dev_state'), isolated_portal_case() as case_path:
        tmp_path = case_path
        repo = tmp_path / "repo"
        repo.mkdir()
        _git(repo, "init", "-b", "main")
        _git(repo, "config", "user.email", "test@example.com")
        _git(repo, "config", "user.name", "Portal Test")
        (repo / "lineage.js").write_text("const version = 1;\n", encoding="utf-8")
        _git(repo, "add", "lineage.js")
        _git(repo, "commit", "-m", "base")
        base_commit = _git(repo, "rev-parse", "HEAD")
        (repo / "lineage.js").write_text("const version = 2;\n", encoding="utf-8")
        (repo / "new-ui.css").write_text(".handoff { color: green; }\n", encoding="utf-8")
        target_root = tmp_path / "targets"
        target = target_root / "explore-2"

        result = lineage_handoff_backend.materialize_handoff_item(
            {
                "source": {"path": str(repo), "scope": "."},
                "transfer": {
                    "mode": "copy",
                    "method": "git_worktree",
                    "parameters": {
                        "base_commit": base_commit,
                        "branch": "explore/2-creative-lineage",
                        "include_worktree_changes": True,
                    },
                },
                "target": {"path": str(target), "scope": "."},
            },
            allowed_target_roots=[target_root],
            checked_at="2026-07-26T00:00:00Z",
        )

        assert (target / "lineage.js").read_text(encoding="utf-8") == "const version = 2;\n"
        assert (target / "new-ui.css").is_file()
        assert _git(target, "branch", "--show-current") == "explore/2-creative-lineage"
        assert result["source_state"]["git"]["base_commit"] == base_commit
        assert result["git"] == {
            "branch": "explore/2-creative-lineage",
            "base_commit": base_commit,
            "include_worktree_changes": True,
        }
        assert result["source_state"]["git"]["patch_sha256"].startswith("sha256:")
        assert result["source_state"]["git"]["untracked_file_count"] == 1
    with subtests.test(contract='test_copy_rejects_a_target_outside_the_allowed_roots'), isolated_portal_case() as case_path:
        tmp_path = case_path
        source = tmp_path / "source"
        source.mkdir()
        (source / "file.txt").write_text("content", encoding="utf-8")

        with pytest.raises(PermissionError, match="允许的 Handoff 根目录"):
            lineage_handoff_backend.materialize_handoff_item(
                {
                    "source": {"path": str(source), "scope": "."},
                    "transfer": {"mode": "copy", "method": "filesystem_copy"},
                    "target": {"path": str(tmp_path / "outside"), "scope": "."},
                },
                allowed_target_roots=[tmp_path / "allowed"],
                checked_at="2026-07-26T00:00:00Z",
            )
