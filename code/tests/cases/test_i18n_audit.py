"""English UI audit: guide bodies and project initialization.

:module: workbench.panels
:importance: P1
:design: decision_27253e25f016
"""
import re
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from management_portal import http_user_guide as guide
from management_portal.hatg.catalog import instruction_catalog
from management_portal.resources.providers import project
from tests.cases.registry_support import write_projects


@pytest.mark.parametrize("name", guide.GUIDES)
def test_complete_english_guides_have_working_headings_and_current_instructions(name):
    source = guide.guide_source(name, "en")
    assert not re.search(r"[\u3400-\u9fff]", source)
    _, outline = guide._render(name, source)
    # Check all original top-level topics have English counterparts.
    assert len(outline) == len(guide._render(name, guide.guide_source(name))[1])
    assert len(source.split()) > 1000
    if name == "agent":
        named = {word for word in re.findall(r"`([A-Z][A-Za-z]+)`", source)
                 if re.fullmatch(r"(?:[A-Z][a-z]+){2,}", word)}
        assert named and not named - set(instruction_catalog())
    page = guide.render_guide_page("en").decode()
    assert f'href="/guide/{name}.md?lang=en"' in page
    assert all(heading in page for _, heading in outline)


def test_guide_language_follows_explicit_query_then_portal_preference():
    request = SimpleNamespace(headers={"Accept-Language": "zh-CN", "Cookie": "portal_language=en"})
    assert guide._language(request, urlsplit("/guide")) == "en"
    assert guide._language(request, urlsplit("/guide?lang=zh-CN")) == "zh-CN"
    request.headers["X-Portal-Language"] = "zh-CN"
    assert guide._language(request, urlsplit("/guide")) == "zh-CN"
    assert guide._language(SimpleNamespace(headers={"Cookie": "unrelated=value"}), urlsplit("/guide")) == "zh-CN"


def test_guide_markdown_caches_keep_language_variants_separate(monkeypatch):
    from management_portal import serve_management_portal as portal
    sent = []
    monkeypatch.setattr(portal, "_serve_bytes", lambda *args, **kwargs: sent.append((args, kwargs)))
    for language in ("en", "zh-CN"):
        guide.guide_markdown(SimpleNamespace(headers={"Cookie": f"portal_language={language}"}),
                             urlsplit("/guide/agent.md"), "")
    assert sent[0][0][2] != sent[1][0][2]
    assert sent[0][1]["extra_headers"]["ETag"] != sent[1][1]["extra_headers"]["ETag"]
    for _, options in sent:
        assert options["extra_headers"]["Vary"] == "Accept-Language, Cookie, X-Portal-Language"


def test_english_project_initialization_preserves_user_description_and_existing_files(tmp_path, monkeypatch):
    write_projects("schema_version: 1\nprojects: []\n")
    monkeypatch.setattr(project, "SHARE_ROOT", tmp_path)
    monkeypatch.setattr(project, "PROJECTS_ROOT", tmp_path / "Projects")
    description = "User text: 中文说明 / {p0}"
    record = project.create_project({"id": "EnglishSeed", "language": "en", "description": description})
    english = (tmp_path / "Projects" / "EnglishSeed" / "summary.md").read_text()
    assert "## 1. Project goals" in english and "## 3. Current status" in english
    assert description in english
    assert "language" not in record
    existing = tmp_path / "existing" / "summary.md"
    existing.parent.mkdir()
    existing.write_text("用户的既有 Summary")
    project._write_project_seed_files(tmp_path / "zh", "Example", description, "company", [])
    assert "## 1. 项目目标" in (tmp_path / "zh" / "summary.md").read_text()
    assert existing.read_text() == "用户的既有 Summary"
    with pytest.raises(ValueError, match="template language"):
        project.create_project({"id": "InvalidLanguage", "language": "fr"})
    assert not (tmp_path / "Projects" / "InvalidLanguage").exists()


