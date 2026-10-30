"""Optional Article participation in automatic registration and Repo classification."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


REPO = Path(__file__).resolve().parents[2]

# A fresh interpreter is essential: the pytest fixtures import other Portal
# domains. Blocking the whole declared feature models a package without it.
PROBE = r'''
import fnmatch
import importlib.abc
import json
from pathlib import Path
import subprocess
import sys
from unittest import mock

from management_portal import portal_features

mode, action, root = sys.argv[1:]
root = Path(root)
patterns = next(feature.modules for feature in portal_features.FEATURES if feature.key == "article")

def article_module(name):
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)

class FeatureInstallation(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if mode == "missing" and article_module(name):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        if mode == "broken" and name == "management_portal.article_registry":
            raise ModuleNotFoundError("Article dependency is broken", name="optional_probe_missing_dependency")

sys.meta_path.insert(0, FeatureInstallation())
if mode == "missing":
    from tests.cases.feature_packaging import drop_resource_types
    drop_resource_types(patterns)

# Core imports must succeed even when the optional feature cannot be loaded.
from management_portal import permission_system, project_registry, resource_auto_registration
from management_portal import resource_identity_backend
from management_portal.resources import ResourceService, runtime
from management_portal.resources.providers import install_mutation_providers
from management_portal.resources.registry_store import get_registry_store

permission_system.configured_mode = lambda: permission_system.OPEN
projected = []

def project_registered(resource_type, record):
    # The service must project only the already-committed domain record.
    stored = get_registry_store().get(resource_type, record["resource_id"])
    assert stored is not None
    assert stored["id"] == record["resource_id"]
    projected.append(record["resource_id"])

# This child task owns registration, not index storage. Keep the service and
# mutation providers real; isolate only the index and its background queue.
index = mock.Mock()
index.project_registered.side_effect = project_registered
index.change_targets.return_value = ["resource_registry"]
index.queue_change.return_value = ["resource_registry"]
service = ResourceService(index_provider=index, mutation_provider_loader=install_mutation_providers)
runtime._SERVICE = service

project = root / "OptionalProject"
project.mkdir()
code = project / "code"
article = project / "article"
ignored = project / "article_ignored"
for path in (code, article, ignored):
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
for path in (article, ignored):
    (path / "main.tex").write_text("\\documentclass{article}\n\\title{Optional Article}\n\\begin{document}Body\\end{document}\n")
project_registry.replace({**project_registry.empty_registry(), "schema_version": 1,
                          "projects": [{"id": "OptionalProject", "path": str(project)}]})
resource_auto_registration.suppress_paths("article", "article_0123456789ab", [ignored])

def run():
    if action == "register":
        return service.register("repo", {"path": str(article), "project_ids": ["OptionalProject"]},
                                actor="optional-feature-test")
    return resource_auto_registration.ResourceAutoRegistrar(service=service).reconcile()

if mode == "broken":
    try:
        run()
    except ModuleNotFoundError as exc:
        assert exc.name == "optional_probe_missing_dependency", (type(exc).__name__, str(exc))
    else:
        raise AssertionError("broken installed feature was silently ignored")
    assert resource_identity_backend.load_repo_records() == []
    assert projected == []
elif action == "register":
    if mode == "installed":
        try:
            run()
        except ValueError as exc:
            assert "Article storage uses article_*" in str(exc), str(exc)
        else:
            raise AssertionError("Article storage was registered as Repo")
        assert resource_identity_backend.load_repo_records() == []
    else:
        record = run()
        assert record["resource_id"].startswith("repo_")
        assert projected == [record["resource_id"]]
        assert {item["path"] for row in resource_identity_backend.load_repo_records()
                for item in row["checkouts"]} == {str(article)}
else:
    if mode == "missing":
        # A dormant Article row must not be read, validated or used as a Repo
        # boundary in a deployment that does not install Article.
        with get_registry_store().writer("article") as writer:
            writer.put("article", "article_abcdef012345", {"id": "malformed"})
    report = run()
    assert report["resource_errors"] == [], report
    assert report["removed"] == []
    paths = {item["path"] for row in resource_identity_backend.load_repo_records() for item in row["checkouts"]}
    if mode == "missing":
        assert paths == {str(code), str(article), str(ignored)}, paths
        assert report["skipped_articles"] == []
        assert all(value.startswith("repo_") for value in report["registered"])
    else:
        from management_portal import article_registry
        assert paths == {str(code)}, paths
        assert {item["path"] for row in article_registry.load_article_records() for item in row["roots"]} == {str(article)}
        assert {value.split("_")[0] for value in report["registered"]} == {"repo", "article"}
    assert set(projected) == set(report["registered"])
    repeated = run()
    assert repeated["registered"] == [], repeated
    assert repeated["removed"] == [], repeated
    assert repeated["resource_errors"] == [], repeated

if mode == "missing":
    assert not [name for name in sys.modules if article_module(name)]
print(json.dumps({"mode": mode, "action": action, "projected": projected, "ok": True}))
'''


@pytest.mark.parametrize("mode", ["missing", "installed", "broken"])
@pytest.mark.parametrize("action", ["register", "reconcile"])
def test_article_installation_controls_registration(mode: str, action: str, tmp_path: Path) -> None:
    """没有 Article 时 Repo 照常登记；已安装时照常分类，依赖损坏时明确报错

    Article absence permits Repo registration; installed classification and broken dependencies stay explicit.
    """
    assert os.environ["PORTAL_DATABASE_NAMESPACE"].startswith("pytest_")
    completed = subprocess.run(
        [sys.executable, "-c", PROBE, mode, action, str(tmp_path)],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
