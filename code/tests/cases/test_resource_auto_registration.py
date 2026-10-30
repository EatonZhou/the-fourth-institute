from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from management_portal import (
    resource_auto_registration,
    resource_identity_backend,
)
from management_portal import article_registry
from management_portal.resources import ResourceService
from management_portal.resources.providers import install_mutation_providers
from management_portal.resources.registry_store import get_registry_store
from tests.cases.registry_support import write_projects


def test_default_auto_registration_interval_is_one_day() -> None:
    """自动登记默认间隔一天且状态存于运行目录"""
    assert resource_auto_registration.DEFAULT_INTERVAL_SECONDS == 24 * 60 * 60
    # Run state is Portal runtime state, never a shared business folder.
    assert not str(resource_auto_registration.DEFAULT_RUN_STATE_PATH).startswith("/share/")


def test_auto_registration_reconcile_is_shared_and_rate_limited_contracts(tmp_path, subtests, isolated_portal_case):
    """自动登记共享运行状态并限频，资源错误允许重试"""
    with subtests.test(contract='test_auto_registration_reconcile_is_shared_and_rate_limited'), isolated_portal_case() as case_path:
        tmp_path = case_path
        registrar = mock.Mock()
        registrar.reconcile.return_value = {'registered': ['repo_0123456789ab']}
        state_path = tmp_path / 'auto-registration.last-run'
        first = resource_auto_registration.reconcile_if_due(registrar, state_path=state_path, interval_seconds=900, now=lambda: 1000)
        second = resource_auto_registration.reconcile_if_due(registrar, state_path=state_path, interval_seconds=900, now=lambda: 1100)
        third = resource_auto_registration.reconcile_if_due(registrar, state_path=state_path, interval_seconds=900, now=lambda: 2000)
        assert first['ran'] is True
        assert first['report'] == {'registered': ['repo_0123456789ab']}
        assert second == {'ran': False, 'reason': 'recent', 'retry_after': 800}
        assert third['ran'] is True
        assert registrar.reconcile.call_count == 2
    with subtests.test(contract='test_auto_registration_resource_errors_retry_without_poisoning_run_state'), isolated_portal_case() as case_path:
        tmp_path = case_path
        registrar = mock.Mock()
        registrar.reconcile.return_value = {'registered': ['repo_0123456789ab'], 'resource_errors': [{'resource_id': 'repo_abcdef012345', 'error': 'invalid registry'}]}
        state_path = tmp_path / 'auto-registration.last-run'
        first = resource_auto_registration.reconcile_if_due(registrar, state_path=state_path, interval_seconds=24 * 60 * 60, now=lambda: 1000)
        second = resource_auto_registration.reconcile_if_due(registrar, state_path=state_path, interval_seconds=24 * 60 * 60, now=lambda: 1010)
        assert first['retry_after'] == 15 * 60
        assert second['ran'] is True
        assert registrar.reconcile.call_count == 2


def _make_git(path: Path, *, article: bool = False) -> None:
    path.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(path)],
        check=True,
    )
    if article:
        (path / "main.tex").write_text(
            "\\documentclass{article}\n"
            "\\title{Auto registered article}\n"
            "\\begin{document}Body\\end{document}\n",
            encoding="utf-8",
        )


def test_existing_projects_are_reconciled_without_treating_articles_as_repos(
    tmp_path: Path,
) -> None:
    """项目扫描区分论文与仓库并遵循手工注销记录"""
    project = tmp_path / "ExistingProject"
    project.mkdir()
    code = project / "runtime"
    article = project / "article"
    _make_git(code)
    _make_git(article, article=True)
    write_projects({"projects": [{"id": "ExistingProject", "path": str(project)}]})
    misclassified_article_repo = resource_identity_backend.register_repo_checkout(
        article,
        project_ids=["ExistingProject"],
    )["id"]
    index = mock.Mock()
    index.change_targets.return_value = ["resource_registry"]
    index.queue_change.return_value = ["resource_registry"]
    service = ResourceService(
        index_provider=index,
        mutation_provider_loader=install_mutation_providers,
    )
    with pytest.raises(
        ValueError,
        match=r"Article storage uses article_\*",
    ):
        service.register(
            "repo",
            {"path": str(article)},
            actor="resource-auto-registration",
        )
    registrar = resource_auto_registration.ResourceAutoRegistrar(service=service)

    report = registrar.reconcile()

    assert not report["resource_errors"], report["resource_errors"]
    assert len(report["registered"]) == 2
    assert report["removed"] == [misclassified_article_repo]
    repo_records = resource_identity_backend.load_repo_records()
    article_records = article_registry.load_article_records()
    assert {
        item["path"]
        for record in repo_records
        for item in record["checkouts"]
    } == {str(code)}
    assert {
        item["path"]
        for record in article_records
        for item in record["roots"]
    } == {str(article)}
    assert str(article) not in resource_auto_registration.suppressed_paths("repo")

    repo_id = repo_records[0]["id"]
    service.unregister(repo_id, actor="resource-auto-registration")
    assert str(code) in resource_auto_registration.suppressed_paths("repo")

    repeated = registrar.reconcile()
    assert repo_id not in repeated["registered"]
    assert resource_identity_backend.load_repo_records() == []

    with pytest.raises(ValueError, match="project_ids"):
        service.register("repo", {"path": str(code)}, actor="resource-auto-registration")
    restored = service.register(
        "repo",
        {"path": str(code), "project_ids": ["@public"]},
        actor="resource-auto-registration",
    )
    assert restored["resource_id"].startswith("repo_")
    assert str(code) not in resource_auto_registration.suppressed_paths("repo")

    repeated_after_manual_registration = registrar.reconcile()
    assert (
        restored["resource_id"]
        not in repeated_after_manual_registration["registered"]
    )
    assert len(resource_identity_backend.load_repo_records()) == 1


def _registrar() -> resource_auto_registration.ResourceAutoRegistrar:
    index = mock.Mock()
    index.change_targets.return_value = ["resource_registry"]
    index.queue_change.return_value = ["resource_registry"]
    return resource_auto_registration.ResourceAutoRegistrar(service=ResourceService(
        index_provider=index,
        mutation_provider_loader=install_mutation_providers,
    ))


def test_suppression_ends_when_its_directory_is_removed(tmp_path: Path) -> None:
    """自动登记的忽略记录随目录删除而失效

    注销资源后同一目录不再被自动登记；目录删除后下一次扫描清除忽略记录，之后在该路径出现的新目录重新自动登记；忽略时间写 UTC ISO。

    :design: decision_86aed2f0e8c7
    """
    project = tmp_path / "ExistingProject"
    article = project / "article"
    _make_git(article, article=True)
    write_projects({"projects": [{"id": "ExistingProject", "path": str(project)}]})
    registrar = _registrar()
    article_id = registrar.reconcile()["registered"][0]
    registrar.service.unregister(article_id, actor="resource-auto-registration")
    suppression = "article:" + str(article)
    ignored = get_registry_store().documents(resource_auto_registration.IGNORE_COLLECTION)[suppression]
    assert ignored["ignored_at"].endswith("Z")

    kept = registrar.reconcile()
    assert kept["registered"] == [] and kept["pruned_suppressions"] == []
    shutil.rmtree(article)
    pruned = registrar.reconcile()

    assert pruned["pruned_suppressions"] == [suppression]
    assert resource_auto_registration.suppressed_paths("article") == set()
    _make_git(article, article=True)
    assert len(registrar.reconcile()["registered"]) == 1


@pytest.mark.parametrize("failure", ["deleted_checkout", "removed_git", "broken_gitfile"])
def test_unavailable_repo_is_unregistered_without_deleting_files_and_can_return(
    tmp_path: Path, failure: str,
) -> None:
    """不可用仓库注销后保留文件且恢复后可重新登记"""
    project = tmp_path / "ExistingProject"
    checkout = project / "code"
    _make_git(checkout)
    (checkout / "keep.txt").write_text("preserve working files", encoding="utf-8")
    write_projects({"projects": [{"id": "ExistingProject", "path": str(project)}]})
    repo_id = resource_identity_backend.register_repo_checkout(checkout)["id"]
    if failure == "deleted_checkout":
        shutil.rmtree(checkout)
    else:
        shutil.rmtree(checkout / ".git")
        if failure == "broken_gitfile":
            (checkout / ".git").write_text("not a Git marker\n", encoding="utf-8")

    registrar = _registrar()
    report = registrar.reconcile()

    assert report["removed"] == [repo_id]
    assert not report["resource_errors"]
    assert resource_identity_backend.load_repo_records() == []
    assert str(checkout) not in resource_auto_registration.suppressed_paths("repo")
    if failure != "deleted_checkout":
        assert (checkout / "keep.txt").read_text(encoding="utf-8") == "preserve working files"
    if failure == "broken_gitfile":
        (checkout / ".git").unlink()
    subprocess.run(["git", "init", "-q", "-b", "main", str(checkout)], check=True)
    restored = registrar.reconcile()
    assert len(restored["registered"]) == 1
    assert restored["registered"][0] != repo_id
    assert not restored["resource_errors"]


def test_repo_with_a_live_checkout_is_retained(tmp_path: Path) -> None:
    """仓库仍有可用检出目录时保留登记"""
    project = tmp_path / "ExistingProject"
    missing = project / "clone-one"
    live = project / "clone-two"
    write_projects({"projects": [{"id": "ExistingProject", "path": str(project)}]})
    repo_ids = []
    for checkout in (missing, live):
        _make_git(checkout)
        subprocess.run([
            "git", "-C", str(checkout), "remote", "add", "origin",
            "https://example.invalid/org/code.git",
        ], check=True)
        repo_ids.append(resource_identity_backend.register_repo_checkout(checkout)["id"])
    assert repo_ids[0] == repo_ids[1]
    shutil.rmtree(missing)

    report = _registrar().reconcile()

    assert report["removed"] == []
    assert not report["resource_errors"]
    assert [record["id"] for record in resource_identity_backend.load_repo_records()] == [repo_ids[0]]


def test_unborn_and_detached_worktree_checkouts_are_available(tmp_path: Path) -> None:
    """未提交仓库和游离工作树均可通过检出检查"""
    checkout = tmp_path / "code"
    _make_git(checkout)
    assert resource_identity_backend.git_checkout_available(checkout)
    subprocess.run([
        "git", "-C", str(checkout), "-c", "user.name=Portal Test",
        "-c", "user.email=test-actor@example.invalid", "commit", "--allow-empty",
        "-qm", "Initial checkout",
    ], check=True)
    worktree = tmp_path / "detached"
    subprocess.run([
        "git", "-C", str(checkout), "worktree", "add", "--detach", "-q", str(worktree),
    ], check=True)

    assert (worktree / ".git").is_file()
    assert resource_identity_backend.git_checkout_available(worktree)


def test_unavailable_repos_are_cleaned_without_any_project_roots(tmp_path: Path) -> None:
    """未配置项目根目录时仍清理不可用仓库"""
    checkout = tmp_path / "code"
    _make_git(checkout)
    repo_id = resource_identity_backend.register_repo_checkout(checkout)["id"]
    shutil.rmtree(checkout)
    write_projects({"projects": []})

    report = _registrar().reconcile()

    assert report["project_count"] == 0
    assert report["removed"] == [repo_id]
    assert report["registered"] == []
    assert not report["resource_errors"]


@pytest.mark.parametrize("error", [PermissionError("Permission denied"), ValueError("Git command timed out after 15s")])
def test_checkout_verification_error_keeps_registration(tmp_path: Path, error: Exception) -> None:
    """检出验证报错时保留登记并记录资源错误"""
    project = tmp_path / "ExistingProject"
    checkout = project / "code"
    _make_git(checkout)
    write_projects({"projects": [{"id": "ExistingProject", "path": str(project)}]})
    repo_id = resource_identity_backend.register_repo_checkout(checkout)["id"]
    with mock.patch.object(resource_identity_backend, "_run_git", side_effect=error):
        report = _registrar().reconcile()

    assert report["removed"] == []
    assert report["resource_errors"][0]["resource_id"] == repo_id
    assert report["resource_errors"][0]["operation"] == "validate"
    assert [record["id"] for record in resource_identity_backend.load_repo_records()] == [repo_id]


def test_article_record_errors_keep_valid_roots_and_report_original_read_error(tmp_path: Path) -> None:
    """Article 登记来源保留有效记录，逐条报告格式错误的记录

    Article's optional source retains valid records while reporting malformed rows individually.
    """
    from management_portal import portal_features

    root = tmp_path / "manuscript"
    root.mkdir()
    valid = {
        "id": "article_0123456789ab",
        "title": "Registered manuscript",
        "roots": [{"path": str(root)}],
    }
    with get_registry_store().writer(article_registry.ARTICLE_COLLECTION) as writer:
        writer.put(article_registry.ARTICLE_COLLECTION, valid["id"], valid)
        writer.put(article_registry.ARTICLE_COLLECTION, "article_abcdef012345", {"id": "invalid"})

    source = portal_features.contribution("resource.article_registration")
    records, errors = source.records()
    assert records == [valid]
    assert errors == [{
        "resource_type": "article",
        "resource_id": "article_abcdef012345",
        "path": "",
        "operation": "read",
        "error": "invalid article id in registry: 'invalid'",
    }]
    report = _registrar().reconcile()
    assert report["resource_errors"] == errors
    assert report["registered"] == []
    assert report["removed"] == []
