"""Article comparisons retain complete context from Git through the visible editor."""
import json
from pathlib import Path
import subprocess

import pytest

from management_portal import article_git, article_registry, repo_workspace_scoped_git as scoped
from management_portal.test_framework.slots import acquire


@pytest.fixture
def comparisons(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()

    git("init", "-q", "-b", "main")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    root = tmp_path / "article"
    root.mkdir()
    source = root / "main.tex"
    original = [f"Unchanged manuscript line {i}" for i in range(1, 241)]
    source.write_text("\n".join(original) + "\n")
    (tmp_path / "outside.txt").write_text("outside original\n")
    git("add", ".")
    git("commit", "-qm", "initial")
    monkeypatch.setattr(article_registry, "_article_source_context", lambda _: ({}, {}, root))
    staged = original.copy()
    staged[54] = "Staged revision at line 55"
    source.write_text("\n".join(staged) + "\n")
    git("add", "article/main.tex")
    working = staged.copy()
    working[154] = "Working revision at line 155"
    source.write_text("\n".join(working) + "\n")
    results = [
        {"kind": "working", "payload": article_git.article_git_diff("fixture", "main.tex"),
         "original": staged, "modified": working},
        {"kind": "staged", "payload": article_git.article_git_diff("fixture", "main.tex", staged=True),
         "original": original, "modified": staged},
    ]
    # Shared callers retain their existing compact-patch contract.
    assert "Unchanged manuscript line 1\n" not in scoped.scoped_git_diff(root, "main.tex")["diff"]
    (tmp_path / "outside.txt").write_text("outside revision\n")
    git("add", "outside.txt")
    git("commit", "-qm", "staged revision")
    results.append({"kind": "commit", "payload": article_git.article_git_commit_diff("fixture", git("rev-parse", "HEAD")),
                    "original": original, "modified": staged})
    return root, results


def test_article_git_complete_context(comparisons):
    root, results = comparisons
    for item in results:
        payload = item["payload"]
        assert not payload["truncated"]
        # Before, between and after distant changes, including the very last line.
        for line in (1, 30, 120, 200, 240):
            assert f" Unchanged manuscript line {line}\n" in payload["diff"]
        assert "outside.txt" not in payload["diff"]
    (root / "new.tex").write_text("brand new\n")
    assert "+brand new" in article_git.article_git_diff("fixture", "new.tex")["diff"]
    (root / "main.tex").unlink()
    deleted = article_git.article_git_diff("fixture", "main.tex")["diff"]
    assert "-Unchanged manuscript line 240" in deleted


def test_article_git_complete_context_browser(comparisons):
    _, results = comparisons
    script = Path(__file__).with_name("javascript") / "article_git_diff.browser.cjs"
    lease = acquire("browser", 1, stop=lambda: False)
    try:
        result = subprocess.run(["node", str(script)], input=json.dumps(results),
                                capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        lease.release()
