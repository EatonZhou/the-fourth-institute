"""Application source references and host receipts share the page revision.

:module: workbench.panels
:importance: P1
:design: decision_7a5ae487e2e9
"""
import copy

import pytest

from management_portal.workbench_projections.store import Store, ProjectionError, validate_manifest


APP = "workbench_111111111111"


def definition():
    return {
        "title": "Application", "html": "", "panels": {},
        "manifest": {"schema": "tfi.projection.v2", "sidebar": [
            {"id": "example", "title": "Example", "source": "package:example"},
        ]},
        "application": {"repo_id": "repo_111111111111", "directory": "app", "commit": "a" * 40,
                        "entries": {"example": "example.html"}},
    }


@pytest.fixture
def builder(monkeypatch):
    from management_portal.workbench_projections import application_build
    calls = []

    def build(application, actor):
        calls.append((copy.deepcopy(application), actor))
        if application["commit"] == "f" * 40:
            raise ProjectionError("projection.application_build", "offline build failed")
        return {"status": "ready", "commit": application["commit"], "artifact_hash": "b" * 64,
                "entries": application["entries"], "files": {"example.html": {"sha256": "c" * 64, "size": 1, "content_type": "text/html"}}}

    monkeypatch.setattr(application_build, "build_application", build)
    monkeypatch.setattr(application_build, "read_artifact", lambda *_: (b"x", "text/html"))
    return calls


def test_source_and_artifact_revision_survive_drafts(builder, monkeypatch):
    """草稿修订保持源码与构建产物的版本关联

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    # bob also may change the page; the build runs as bob.
    store = Store()
    first = store.create(APP, definition(), "alice")
    assert builder[0][1] == "alice"
    store.publish(APP, "alice", 1)
    second = store.update(APP, {"application": {**first["application"], "commit": "d" * 40}}, "bob", 1)
    assert second["application_build"]["commit"] == "d" * 40
    assert builder[1][1] == "bob"
    assert store.get(APP, "alice", published=True)["application_build"] == first["application_build"]
    assert store.get_revision(APP, "alice", 1)["application"] == first["application"]
    assert store.get_revision(APP, "alice", 2)["application"] == second["application"]
    with pytest.raises(ProjectionError):
        store.get_revision(APP, "alice", 3)
    store.update(APP, {"title": "New title"}, "alice", 2)
    assert len(builder) == 2  # unchanged immutable input does not rebuild


def test_failed_build_cannot_replace_published_page_or_save_revision(builder):
    """构建失败不保存修订且不替换已发布页面

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    store = Store()
    first = store.create(APP, definition(), "alice")
    store.publish(APP, "alice", 1)
    with pytest.raises(ProjectionError, match="offline build failed"):
        store.update(APP, {"application": {**first["application"], "commit": "f" * 40}}, "alice", 1)
    current = store.get(APP, "alice")
    assert current["revision"] == current["published_revision"] == 1
    assert current["application_build"] == first["application_build"]


def test_publish_rechecks_immutable_artifacts(builder, monkeypatch):
    """发布前重新核验不可变构建产物

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    from management_portal.workbench_projections import application_build
    store = Store()
    store.create(APP, definition(), "alice")
    def corrupted(*_):
        raise ProjectionError("projection.artifact", "hash mismatch")
    monkeypatch.setattr(application_build, "read_artifact", corrupted)
    with pytest.raises(ProjectionError, match="hash mismatch"):
        store.publish(APP, "alice", 1)
    assert store.get(APP, "alice")["published_revision"] is None


def test_caller_cannot_supply_artifacts(builder, subtests):
    """应用包保存拒绝调用方伪造构建产物

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    store = Store()
    with pytest.raises(ProjectionError, match="宿主"):
        store.create(APP, {**definition(), "application_build": {"status": "ready"}}, "alice")
    invalid = []
    value = definition(); value["application"]["directory"] = "../private"; invalid.append(value)
    value = definition(); value["application"]["commit"] = "HEAD"; invalid.append(value)
    value = definition(); value["application"]["repo_id"] = "/private/path"; invalid.append(value)
    value = definition(); value["application"]["entries"]["example"] = "../example.html"; invalid.append(value)
    value = definition(); value["manifest"]["sidebar"][0]["source"] = "package:missing"; invalid.append(value)
    value = definition(); del value["application"]; invalid.append(value)
    value = definition(); value["application"]["max_bytes"] = True; invalid.append(value)
    for value in invalid:
        with subtests.test(value=value), pytest.raises(ProjectionError):
            validate_manifest(value)
    assert builder == []


def test_application_can_return_to_snippet_without_stale_receipt(builder):
    """应用包切回片段时移除过期构建回执

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    store = Store()
    store.create(APP, definition(), "alice")
    app = store.update(APP, {"application": None, "manifest": None, "panels": {}, "html": "<p>Simple</p>"}, "alice", 1)
    assert "application" not in app and "application_build" not in app
    store.publish(APP, "alice", 2)
