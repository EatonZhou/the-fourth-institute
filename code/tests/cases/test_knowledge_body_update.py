""":importance: P2"""
from types import SimpleNamespace
from unittest import mock

import pytest

from knowledge_board_lan import knowledge_backend, knowledge_source, knowledge_versions
from management_portal.http_knowledge import KnowledgeHttp
from management_portal.resources.errors import ResourceVersionConflict
from management_portal.resources.providers import knowledge
from tests.cases.test_knowledge_versioning import KNOWLEDGE_ID, _markdown, _frontmatter


@pytest.fixture
def item(tmp_path):
    path = tmp_path / "item.md"
    path.write_text(_markdown().replace("links: {}", "links: {}\ncustom: 'keep me' # comment"))
    record = {"address": KNOWLEDGE_ID, "kb_id": KNOWLEDGE_ID, "type": "thinking",
              "path": str(path), "selected_path": str(path), "selected_file_id": "primary"}
    with mock.patch.object(knowledge_backend, "resolve_item_address", return_value=record), \
            mock.patch("management_portal.resources.content_changes.record_text_change") as history:
        yield path, record, history


@pytest.mark.parametrize("body", ["# New title\n\nNew body\n", "", "---\nversion: 999\n---\nLiteral body"])
def test_provider_body_preserves_metadata_and_records_complete_history(item, body):
    path, record, history = item
    before = path.read_text()
    result = knowledge._update_knowledge(KNOWLEDGE_ID, {"body": body, "expected_version": 3},
                                        actor="resident_0123456789ab")
    after = path.read_text()
    old_meta, new_meta = _frontmatter(before), _frontmatter(after)
    assert {k: v for k, v in old_meta.items() if k != "version"} == {
        k: v for k, v in new_meta.items() if k != "version"}
    assert "custom: 'keep me' # comment" in after
    assert new_meta["version"]["number"] == 4
    assert new_meta["version"]["created_by"] == old_meta["version"]["created_by"]
    assert new_meta["version"]["updated_by"]["agent"] == "resident_0123456789ab"
    match = knowledge_source._FRONT_MATTER_PATTERN.match(after)
    assert after[match.end():] == body
    assert result["resource_id"] == KNOWLEDGE_ID
    assert history.call_args.args[2:4] == (before, after)
    with pytest.raises(ResourceVersionConflict):
        knowledge._update_knowledge(KNOWLEDGE_ID, {"body": "stale", "expected_version": 3})
    assert path.read_text() == after
    assert history.call_count == 1


def test_body_uses_metadata_read_inside_resource_lock(item):
    path, record, _ = item
    calls = 0
    def resolve(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            path.write_text(path.read_text().replace("'keep me'", "'updated before lock'"))
        return record
    with mock.patch.object(knowledge_backend, "resolve_item_address", side_effect=resolve):
        knowledge_backend.update_item_text(KNOWLEDGE_ID, body="new body")
    assert _frontmatter(path.read_text())["custom"] == "updated before lock"


@pytest.mark.parametrize("payload", [{}, {"body": None}, {"body": 7}, {"body": []},
    {"text": "full", "body": "body"}, {"text": "", "body": ""},
    {"body": "body", "version_recovery_change_ids": []},
    {"body": "body", "address": "thinking_ffffffffffff"}])
def test_invalid_body_update_does_not_write(item, payload):
    path, _, history = item
    before = path.read_bytes()
    with pytest.raises(ValueError):
        knowledge._update_knowledge(KNOWLEDGE_ID, payload)
    assert path.read_bytes() == before
    history.assert_not_called()


@pytest.mark.parametrize("kind", ["companion", "article", "skill", "no_metadata", "invalid_metadata"])
def test_body_rejects_non_knowledge_primary(item, kind):
    path, record, history = item
    if kind == "companion":
        selected = path.with_name("notes.md")
        selected.write_text("companion body")
        record["selected_path"] = str(selected)
    elif kind in {"article", "skill"}:
        record["type"] = kind
    else:
        path.write_text("no metadata" if kind == "no_metadata" else "---\nx: [\n---\nbody")
    before = path.read_bytes()
    with pytest.raises(ValueError):
        knowledge_backend.update_item_text(KNOWLEDGE_ID, body="replacement")
    assert path.read_bytes() == before
    history.assert_not_called()


@pytest.mark.parametrize("prefix", ["\ufeff---\r\nkey: value\r\n---\r\n", "---\nkey: value\n---"])
def test_body_replacement_preserves_frontmatter_format(prefix):
    combined = knowledge_versions.replace_knowledge_body(prefix + ("old" if prefix.endswith("\n") else ""), "new")
    assert combined == prefix + ("" if prefix.endswith("\n") else "\n") + "new"


def test_http_body_and_conflict_use_same_save_owner(item):
    path, _, history = item
    api = object.__new__(KnowledgeHttp)
    api.knowledge_backend = lambda: knowledge_backend
    api.knowledge_archive_backend = lambda: SimpleNamespace(KnowledgeArchiveServiceUnavailable=ConnectionError)
    api.session_actor = lambda request: "resident_0123456789ab"
    api.require_resource = mock.Mock()
    api.respond = lambda request, status, result: (status, result)
    api.notify_resource_index = mock.Mock()
    payload = {"kb_id": KNOWLEDGE_ID, "body": "HTTP body", "expected_version": 3}
    api.read_json = lambda request: payload
    route = api.post_route(api.post_detail_save)
    assert route(None, None, None)[0] == 200
    assert path.read_text().endswith("HTTP body")
    assert route(None, None, None)[0] == 409
    assert history.call_count == 1
    payload["body"] = None
    assert route(None, None, None)[0] == 400
    api.notify_resource_index.assert_called_once()
