from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml


PORTAL_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PORTAL_ROOT / "core_skills/tfi-unified-id/scripts/resource_id.py"


def _script_env() -> dict[str, str]:
    """Run the Skill script against the Portal code and interpreter under test."""
    # Subprocesses do not inherit the parent test's in-memory permission fixture.
    # Persist the same open-mode policy in this test's isolated database namespace.
    from management_portal.execution.service_settings import ExecutionSettings
    ExecutionSettings().update_permission_system(False)
    env = {**os.environ, "PORTAL_ROOT": str(PORTAL_ROOT), "PORTAL_PYTHON": sys.executable,
           "PORTAL_RESOURCE_ID_ACTOR": "test-actor"}
    # These scans exercise the human path in the isolated test namespace.
    # An explicitly empty Resident identity is invalid, not an absent identity.
    env.pop("PORTAL_RESIDENT_ID", None)
    return env


def _write_skill(path: Path, skill_id: str = "skill_0123456789ab") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"name: {path.parent.name}\n"
        "description: Test skill.\n"
        "metadata:\n"
        f"  id: {skill_id}\n"
        "---\n\n# Test\n",
        encoding="utf-8",
    )


def _write_knowledge_skill_article(
    path: Path,
    skill_id: str = "skill_111111111111",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"kb_id: {skill_id}\n"
        "type: skill\n"
        "project: Test\n"
        "---\n\n# Test\n",
        encoding="utf-8",
    )


def _write_todo(path: Path, todo_id: str = "todo_0123456789ab") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"id: {todo_id}\n"
        "type: todo\n"
        "project: Test\n"
        "---\n\nTask\n",
        encoding="utf-8",
    )


def _write_review(path: Path, review_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"kb_id: {review_id}\n"
        "type: review\n"
        "project: Test\n"
        "---\n\nReview\n",
        encoding="utf-8",
    )


def _make_git_checkout(path: Path) -> None:
    path.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(path)],
        check=True,
    )


def test_resident_scan_uses_catalogs_without_legacy_folder(monkeypatch, capsys):
    import argparse
    import runpy
    from types import SimpleNamespace
    from management_portal.society_service import lineage_catalog, resident_catalog
    from management_portal.resources import collaboration_registry

    namespace = runpy.run_path(str(SCRIPT))
    scan = namespace["command_scan_resident"]
    scan.__globals__["DEFAULT_PORTAL_ROOT"] = PORTAL_ROOT
    resident_id = "resident_0123456789ab"
    monkeypatch.setattr(lineage_catalog, "get_catalog", lambda: SimpleNamespace(snapshot=lambda: []))
    monkeypatch.setattr(resident_catalog, "get_catalog", lambda: SimpleNamespace(snapshot=lambda: [
        {"resident_id": resident_id, "lineage_id": "lineage_abcdef012345"}
    ]))
    monkeypatch.setattr(collaboration_registry, "CollaborationDescriptorStore", lambda: SimpleNamespace(
        list=lambda kind: [{"resource_id": resident_id}] if kind == "resident" else []
    ))
    assert scan(argparse.Namespace(), {}) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["fact_store"] == "PostgreSQL SocietyService catalogs"
    assert "lineage_root" not in result
    assert result["violation_count"] == 1
    assert result["violations"][0]["kind"] == "resident_lineage_missing"
    assert f"PostgreSQL:residents/{resident_id}" in result["violations"][0].values()


def test_repo_scan_excludes_article_git_roots_contracts(tmp_path, subtests, isolated_portal_case):
    """仓库扫描排除文章目录的 git 仓库，系统扫描只接受规范的 Skill 和 Todo ID"""
    with subtests.test(contract='test_repo_scan_excludes_article_git_roots'), isolated_portal_case() as case_path:
        tmp_path = case_path
        project = tmp_path / 'Project'
        code = project / 'runtime'
        article = project / 'article'
        _make_git_checkout(code)
        _make_git_checkout(article)
        (article / 'main.tex').write_text('\\documentclass{article}\n\\title{Article storage is not a Repo resource}\n', encoding='utf-8')
        result = subprocess.run([sys.executable, str(SCRIPT), 'scan', 'repo', str(project), '--apply'], env=_script_env(), text=True, capture_output=True, check=False)
        payload = json.loads(result.stdout)
        assert result.returncode == 0, result.stderr
        assert payload['checkout_count'] == 1
        assert payload['created_count'] == 1
        assert payload['created'][0]['canonical_checkout'] == str(code)
    with subtests.test(contract='test_system_scan_accepts_canonical_skill_and_todo_ids'), isolated_portal_case() as case_path:
        tmp_path = case_path
        skills = tmp_path / 'skills'
        projects = tmp_path / 'projects'
        _write_skill(skills / 'test-skill' / 'SKILL.md')
        _write_knowledge_skill_article(projects / 'Test' / 'knowledge_base' / 'skill' / 'skill-article.md')
        _write_todo(projects / 'Test' / 'knowledge_base' / 'todo' / 'task.md')
        result = subprocess.run([sys.executable, str(SCRIPT), 'scan', 'system', '--skip-index', '--skill-root', str(skills), '--projects-root', str(projects)], env=_script_env(), text=True, capture_output=True, check=False)
        payload = json.loads(result.stdout)
        assert result.returncode == 0
        assert payload['state'] == 'clean'
        assert payload['skill_documents_scanned'] == 1
        assert payload['todo_documents_scanned'] == 1
    with subtests.test(contract='test_system_scan_rejects_missing_or_noncanonical_ids'), isolated_portal_case() as case_path:
        tmp_path = case_path
        skills = tmp_path / 'skills'
        projects = tmp_path / 'projects'
        _write_skill(skills / 'test-skill' / 'SKILL.md', 'test-skill')
        _write_todo(projects / 'Test' / 'knowledge_base' / 'todo' / 'task.md', 'todo_NOT_CANONICAL')
        result = subprocess.run([sys.executable, str(SCRIPT), 'scan', 'system', '--skip-index', '--skill-root', str(skills), '--projects-root', str(projects)], env=_script_env(), text=True, capture_output=True, check=False)
        payload = json.loads(result.stdout)
        assert result.returncode == 2
        assert payload['state'] == 'violations'
        assert {item['kind'] for item in payload['violations']} == {'invalid_skill_id', 'invalid_todo_id'}




def test_review_scan_replaces_active_and_archived_legacy_ids(tmp_path: Path) -> None:
    """评审扫描把活动和归档评审的旧 ID 换成规范 ID，重复运行不再修改"""
    projects = tmp_path / "projects"
    papers = tmp_path / "papers"
    active = projects / "Test" / "knowledge_base" / "review" / "active.md"
    archived = (
        projects
        / "Test"
        / "knowledge_base"
        / "review"
        / ".archive"
        / "archived.md"
    )
    _write_review(active, "review_legacy_active")
    _write_review(archived, "review-legacy-archived")
    command = [
        sys.executable,
        str(SCRIPT),
        "scan",
        "review",
        "--projects-root",
        str(projects),
        "--paper-root",
        str(papers),
    ]

    dry_run = subprocess.run(
        command,
        env=_script_env(),
        text=True,
        capture_output=True,
        check=False,
    )
    dry_payload = json.loads(dry_run.stdout)
    assert dry_run.returncode == 0
    assert dry_payload["review_count"] == 2
    assert dry_payload["active_count"] == 1
    assert dry_payload["archived_count"] == 1
    assert dry_payload["changed_count"] == 2

    applied = subprocess.run(
        [*command, "--apply"],
        env=_script_env(),
        text=True,
        capture_output=True,
        check=False,
    )
    applied_payload = json.loads(applied.stdout)
    assert applied.returncode == 0
    assert applied_payload["changed_count"] == 2
    migrated_ids = {
        yaml.safe_load(path.read_text(encoding="utf-8").split("---", 2)[1])["kb_id"]
        for path in (active, archived)
    }
    assert len(migrated_ids) == 2
    assert all(
        len(review_id) == len("review_0123456789ab")
        and review_id.startswith("review_")
        for review_id in migrated_ids
    )

    repeated = subprocess.run(
        command,
        env=_script_env(),
        text=True,
        capture_output=True,
        check=False,
    )
    assert json.loads(repeated.stdout)["changed_count"] == 0


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
