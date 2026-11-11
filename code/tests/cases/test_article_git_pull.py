"""Article pulls use a real remote and retain the workspace/identity boundary."""
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal import article_git, article_registry, repo_workspace_scoped_git as scoped
from management_portal.http_articles import ArticlesHttp
from management_portal.repo_workspace_git import GitAuthenticationRequired, GitOperationError


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.STDOUT, text=True).strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    remote, author, checkout = (tmp_path / name for name in ("remote.git", "author", "article"))
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    for location in (author, checkout):
        subprocess.run(["git", "clone", str(remote), str(location)], check=True, capture_output=True)
        git(location, "config", "user.name", "Fixture")
        git(location, "config", "user.email", "fixture@example.invalid")
    (author / "main.tex").write_text("initial")
    git(author, "add", "."); git(author, "commit", "-m", "initial"); git(author, "push", "origin", "main")
    git(checkout, "pull", "origin", "main")
    monkeypatch.setattr(article_registry, "_article_source_context", lambda _: ({}, {}, checkout))
    monkeypatch.setattr(article_git.permission_system, "permitted_ids", lambda actor, instruction, argument, ids: set(ids))
    return author, checkout


def advance(author):
    (author / "main.tex").write_text("remote revision")
    (author / "new.tex").write_text("new section")
    git(author, "add", "."); git(author, "commit", "-m", "remote update"); git(author, "push", "origin", "main")


def test_pull_updates_content_and_tracks_remote_branch(repo):
    author, checkout = repo
    git(checkout, "branch", "-m", "local-name")
    git(checkout, "branch", "--set-upstream-to", "origin/main")
    advance(author)
    result = article_git.pull_article_git("article_0123456789ab", actor="fixture")
    assert (checkout / "main.tex").read_text() == "remote revision"
    assert (checkout / "new.tex").exists()
    assert result["status"]["branch"] == "local-name"
    assert result["status"]["pull"] == {"available": True, "remote": "origin", "branch": "main", "reason": ""}
    assert git(checkout, "rev-parse", "HEAD") == git(author, "rev-parse", "HEAD")


def test_dirty_and_diverged_pulls_preserve_local_files(repo):
    author, checkout = repo
    advance(author)
    (checkout / "main.tex").write_text("local work")
    assert not scoped.scoped_git_status(checkout)["pull"]["available"]
    with pytest.raises(ValueError, match="提交"):
        article_git.pull_article_git("article_0123456789ab")
    git(checkout, "add", "."); git(checkout, "commit", "-m", "local change")
    head = git(checkout, "rev-parse", "HEAD")
    with pytest.raises(GitOperationError):
        article_git.pull_article_git("article_0123456789ab")
    assert git(checkout, "rev-parse", "HEAD") == head
    assert (checkout / "main.tex").read_text() == "local work"
    assert not (checkout / "new.tex").exists()


def test_no_remote_detached_and_nested_scope_are_explained(repo):
    _, checkout = repo
    nested = checkout / "sections"; nested.mkdir()
    with pytest.raises(ValueError, match="Repo"):
        scoped.pull_scoped_git_workspace(nested, article_id="article_0123456789ab")
    git(checkout, "checkout", "--detach")
    assert "分支" in scoped.scoped_git_status(checkout)["pull"]["reason"]
    git(checkout, "checkout", "main"); git(checkout, "remote", "remove", "origin")
    assert "远端" in scoped.scoped_git_status(checkout)["pull"]["reason"]


def test_network_uses_current_actor_credentials(repo, monkeypatch):
    _, checkout = repo
    network = Mock(return_value=("ok", {}))
    monkeypatch.setattr(scoped, "_network_git", network)
    article_git.pull_article_git("article_0123456789ab", actor="alice", credential={"username": "git", "secret": "fixture-only"}, remember=True)
    call = network.call_args
    assert call.kwargs["actor"] == "alice"
    assert call.kwargs["remember"] is True
    assert call.kwargs["credential"]["secret"] == "fixture-only"
    assert call.args[-2:] == ("origin", "refs/heads/main")


def test_http_pull_authentication_and_access_denial(monkeypatch):
    require = Mock()
    import inspect
    deps = {name: Mock() for name in inspect.signature(ArticlesHttp).parameters}
    deps.update(respond=lambda request, status, data: (status, data), session_actor=lambda _: "alice",
                require_resource=require, read_json=lambda _: {"credential": {"secret": "fixture-only"}})
    owner = ArticlesHttp(**deps)
    challenge = {"kind": "git_auth_required", "host": "example.invalid"}
    pull = Mock(side_effect=GitAuthenticationRequired("Authentication required", challenge=challenge))
    monkeypatch.setattr(article_git, "pull_article_git", pull)
    params = {"article_id": "article_0123456789ab", "action": "pull"}
    status, value = owner.git(SimpleNamespace(), None, "ignored", params)
    assert status == 409 and value["auth_challenge"] == challenge
    require.assert_called_with(params["article_id"], "alice")
    assert pull.call_args.kwargs["actor"] == "alice"
    require.side_effect = PermissionError("denied")
    pull.reset_mock()
    assert owner.git(SimpleNamespace(), None, "ignored", params)[0] == 403
    pull.assert_not_called()


def test_viewer_cannot_start_network_pull(repo, monkeypatch):
    monkeypatch.setattr(article_git.permission_system, "permitted_ids", lambda *args: set())
    network = Mock()
    monkeypatch.setattr(scoped, "_network_git", network)
    with pytest.raises(PermissionError):
        article_git.pull_article_git("article_0123456789ab", actor="viewer")
    network.assert_not_called()
