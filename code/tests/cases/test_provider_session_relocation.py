"""Provider sessions follow a moved directory prefix."""
from __future__ import annotations

import json

import pytest

from management_portal import provider_session_relocation as relocation


def test_dsh_moves_session_metadata_without_rewriting_messages(tmp_path, monkeypatch):
    """DSH 工作目录搬迁保持会话消息字节不变"""
    from management_common import portal_paths
    monkeypatch.setattr(portal_paths, 'path', lambda name: tmp_path)
    old, new = '/projects/项目/old', '/projects/项目/new'
    session = tmp_path / 'dsh/dsh-123/sessions' / relocation.dsh_project_key(old) / 'session'
    session.mkdir(parents=True)
    body = json.dumps({'type': 'message', 'text': old}).encode() + b'\n'
    (session / 'session.jsonl').write_bytes(json.dumps({'type': 'session', 'cwd': old}).encode() + b'\n' + body)
    result = {'changed': [], 'errors': [], 'conflicts': []}
    relocation._relocate_dsh(relocation._Prefix(old, new), True, result)
    target = session.parent.parent / relocation.dsh_project_key(new) / session.name
    header, rest = (target / 'session.jsonl').read_bytes().split(b'\n', 1)
    assert json.loads(header)['cwd'] == new
    assert rest == body and not session.exists()
    assert not result['errors'] and not result['conflicts']


@pytest.fixture
def accounts(tmp_path, monkeypatch):
    codex = tmp_path / "accounts/codex"
    claude = tmp_path / "accounts/claude"
    kimi = tmp_path / "kimi"
    for directory in (codex, claude / "projects", kimi / "sessions/wd_a/session_1"):
        directory.mkdir(parents=True)
    homes = {"codex": [codex], "claude": [claude]}
    monkeypatch.setattr(relocation, "_registered_homes", lambda provider, kinds=None: homes.get(provider, []))
    monkeypatch.setattr(relocation, "kimi_home", lambda: kimi)
    return {"codex": codex, "claude": claude, "kimi": kimi}


def _claude_folder(accounts, cwd: str, session: str = "s1") -> object:
    folder = accounts["claude"] / "projects" / relocation.claude_project_key(cwd)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{session}.jsonl").write_text(
        json.dumps({"type": "summary"}) + "\n" + json.dumps({"type": "user", "cwd": cwd}) + "\n", encoding="utf-8")
    return folder


def test_sessions_under_the_prefix_follow_it_and_others_stay(accounts, tmp_path):
    """会话按旧路径前缀迁到新前缀，前缀之外的不动"""
    old, new = "/area/P/knowledge_base/agents/lineage_aaaaaaaaaaaa", "/area/P/agents/lineage_aaaaaaaaaaaa"
    workspace = old + "/residents/resident_x/workspace"
    sibling = "/area/P/knowledge_base/agents/lineage_aaaaaaaaaaab/residents/r/workspace"
    (accounts["codex"] / "config.toml").write_text(
        f'[projects."{workspace}"]\ntrust_level = "trusted"\n[projects."{old}b"]\ntrust_level = "trusted"\n')
    moved = _claude_folder(accounts, workspace)
    (moved / "memory").mkdir()
    untouched = _claude_folder(accounts, sibling)
    memory_only = accounts["claude"] / "projects" / relocation.claude_project_key(old) / "memory"
    memory_only.mkdir(parents=True)
    state = accounts["kimi"] / "sessions/wd_a/session_1/state.json"
    state.write_text(json.dumps({"cwd": workspace, "other": sibling}))
    (accounts["kimi"] / "workspaces.json").write_text(json.dumps({"workspaces": {"wd_a": {"root": workspace}}}))

    preview = relocation.relocate_provider_sessions(old, new, apply=False)
    assert moved.is_dir() and workspace in state.read_text()
    assert len(preview["changed"]) == 5 and not preview["errors"]

    result = relocation.relocate_provider_sessions(old, new)
    new_workspace = new + "/residents/resident_x/workspace"
    config = (accounts["codex"] / "config.toml").read_text()
    assert f'[projects."{new_workspace}"]' in config and f'[projects."{old}b"]' in config
    target = accounts["claude"] / "projects" / relocation.claude_project_key(new_workspace)
    assert not moved.exists() and (target / "s1.jsonl").is_file() and (target / "memory").is_dir()
    assert untouched.is_dir()
    assert (accounts["claude"] / "projects" / relocation.claude_project_key(new) / "memory").is_dir()
    assert json.loads(state.read_text()) == {"cwd": new_workspace, "other": sibling}
    assert result["conflicts"] == [] and result["errors"] == []
    # A second run finds nothing left to move.
    again = relocation.relocate_provider_sessions(old, new)
    assert again["changed"] == [] and again["conflicts"] == []


def test_existing_target_folder_is_merged_without_overwriting(accounts):
    """目标会话目录已存在时只移入不冲突的文件，冲突列出"""
    old, new = "/a/old", "/a/new"
    source = _claude_folder(accounts, old, "s1")
    (source / "shared.txt").write_text("old")
    target = _claude_folder(accounts, new, "s2")
    (target / "shared.txt").write_text("new")

    result = relocation.relocate_provider_sessions(old, new)

    assert (target / "s1.jsonl").is_file() and (target / "shared.txt").read_text() == "new"
    assert result["conflicts"] == [{"source": str(source / "shared.txt"), "target": str(target / "shared.txt")}]
    assert (source / "shared.txt").read_text() == "old"


def test_relative_prefixes_are_rejected(accounts):
    with pytest.raises(ValueError):
        relocation.relocate_provider_sessions("relative/old", "/new")
