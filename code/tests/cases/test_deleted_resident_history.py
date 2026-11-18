"""Deleted identity exports use retained facts, current authority and bounded pages."""
import base64
import json
from types import SimpleNamespace

import pytest

from management_portal.society_service import resident_deleted_history as history
from tests.cases.test_resident_deleted_workspace_v31_integration import RID, sparse_history


OTHER = "resident_444444444444"


@pytest.fixture
def retained(sparse_history, monkeypatch):
    fixture = sparse_history
    allowed, checks = {"manager"}, []

    def authorize(actor, action, arguments):
        checks.append((actor, action, arguments))
        return actor in allowed and arguments == {"resident_id": RID}

    monkeypatch.setattr(history.permission_system, "allows", authorize)
    monkeypatch.setattr(history.personal_ama, "reaches", lambda *args, **kwargs: True)
    monkeypatch.setattr(history, "action_for_parameter", lambda *args: history.ACTION)
    monkeypatch.setattr(history, "get_agent_data_store", lambda: fixture.agents)
    return SimpleNamespace(**vars(fixture), allowed=allowed, checks=checks)


def _message(store, text, suffix):
    return store.display_chat_message(RID, {
        "message_id": "msg_" + suffix * 32, "role": "user", "author_id": "manager",
        "content_parts": [{"type": "text", "text": text}],
        "created_at": "2026-10-07T00:00:00Z",
    })


def _read(section, **kwargs):
    return history.read_history(RID, "manager", section=section, **kwargs)


@pytest.mark.parametrize("column_type", ["TEXT", "JSONB"])
def test_history_metadata_case_supports_old_and_new_storage_types(retained, column_type):
    from psycopg import sql
    from management_portal.execution.agent_data_history import MAX_METADATA_BYTES

    segment_id = retained.agents.put_segment(
        segment_type="input", content="Bounded body", created_by_agent_id=RID,
        metadata={"message": {"message_id": "msg_" + "8" * 32, "role": "user"},
                  "padding": "x" * (MAX_METADATA_BYTES + 1)},
    )
    retained.agents.append_agent_segments(RID, [segment_id], actor_ref="fixture")
    # Old installations retain TEXT while fresh test schemas use JSONB. Only
    # the disposable Agent database is altered; no production schema migration.
    with retained.agents.database.transaction() as connection:
        # Fresh-schema expression indexes/statistics use JSONB operators.
        # They are not part of the legacy read fixture; remove only these
        # derived structures before changing its physical column type.
        if column_type == "TEXT":
            for row in connection.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname=current_schema() "
                "AND tablename='segments' AND indexdef LIKE %s", ("%metadata_json%",),
            ).fetchall():
                connection.execute(sql.SQL("DROP INDEX {}").format(sql.Identifier(row["indexname"])))
            for row in connection.execute(
                "SELECT stxname AS statistics_name FROM pg_statistic_ext "
                "WHERE stxrelid='segments'::regclass",
            ).fetchall():
                connection.execute(sql.SQL("DROP STATISTICS {}").format(sql.Identifier(row["statistics_name"])))
        connection.execute(
            "ALTER TABLE segments ALTER COLUMN metadata_json TYPE " + column_type
            + " USING metadata_json::" + column_type,
        )
        observed = connection.execute(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema=current_schema() AND table_name='segments' AND column_name='metadata_json'",
        ).fetchone()
        assert observed["data_type"] == column_type.lower()
    # Populate before the type change. The read must not rely on a writer to
    # normalize metadata, and must keep both CASE arms type-compatible.
    first = _read("messages", limit=1)
    assert first["items"][0]["content_parts"] == [{"type": "text", "text": "Stored historical evidence"}]
    assert first["items"][0]["content_omitted"] is False
    second = _read("messages", limit=1, cursor=first["next_cursor"])
    assert second["items"][0]["segment_id"] == segment_id
    assert second["items"][0]["content_omitted"] is True
    assert not second["next_cursor"]
    assert _read("segments")["items"][-1]["segment_id"] == segment_id


def test_history_reads_before_chat_reset_without_live_state_or_folders(retained, monkeypatch):
    retained.agents.record_context_reset(
        RID, reset_at="2026-10-08T00:00:00Z", actor_ref="fixture", trace_position=1,
    )
    assert retained.agents.read_chat_messages(RID) == []
    before = retained.agents.trace_length(RID)

    def forbidden(*args, **kwargs):
        raise AssertionError("History export must not write state or initialize an Agent")

    monkeypatch.setattr(retained.store.database, "write", forbidden)
    monkeypatch.setattr(retained.agents.database, "write", forbidden)
    monkeypatch.setattr(retained.agents, "ensure_agent", forbidden)
    page = _read("messages")
    assert page["identity"]["deleted_at"]
    assert page["items"][0]["content_parts"] == [{"type": "text", "text": "Stored historical evidence"}]
    assert page["items"][0]["content_omitted"] is False
    assert retained.agents.trace_length(RID) == before
    with retained.store.database.read() as connection:
        assert connection.execute("SELECT 1 FROM conversation_states WHERE resident_id=%s", (RID,)).fetchone() is None


def test_pagination_rechecks_authority_and_binds_cursor_to_target_and_section(retained):
    _message(retained.agents, "Second", "a")
    first = _read("messages", limit=1)
    token = first["next_cursor"]
    assert token
    second = _read("messages", cursor=token, limit=1)
    assert second["items"][0]["content_parts"][0]["text"] == "Second"
    assert second["next_cursor"] == ""
    assert all(check[1:] == (history.ACTION, {"resident_id": RID}) for check in retained.checks)
    with pytest.raises(ValueError, match="cursor"):
        _read("segments", cursor=token)
    altered = json.loads(base64.urlsafe_b64decode(token))
    altered[1] = OTHER
    with pytest.raises(ValueError, match="cursor"):
        _read("messages", cursor=base64.urlsafe_b64encode(json.dumps(altered).encode()).decode())
    retained.allowed.clear()
    with pytest.raises(PermissionError):
        _read("messages", cursor=token)


@pytest.mark.parametrize("actor", ["viewer", "former_creator", "", "deleted_lineage_admin"])
def test_no_read_from_viewer_creator_or_deleted_lineage_fact(retained, monkeypatch, actor):
    def forbidden(*args, **kwargs):
        raise AssertionError("Unauthorized reads must not inspect tombstones or Agent facts")

    monkeypatch.setattr(history.resident_catalog, "get_catalog", forbidden)
    monkeypatch.setattr(history, "get_agent_data_store", forbidden)
    with pytest.raises(PermissionError):
        history.read_history(RID, actor, section="messages")


def test_missing_active_empty_and_personal_ama_are_distinct(retained, monkeypatch):
    retained.store.database.write(lambda connection: connection.execute(
        "INSERT INTO residents(resident_id,title,deleted_at,deleted_by) VALUES (%s,%s,%s,%s)",
        (OTHER, "Empty deleted identity", "2026-10-08T00:00:00Z", "fixture"),
    ))
    monkeypatch.setattr(history.permission_system, "allows", lambda *args: True)
    empty = history.read_history(OTHER, "manager", section="messages")
    assert empty["items"] == [] and empty["next_cursor"] == ""
    assert empty["availability"]["agent"] == "not_recorded"
    with pytest.raises(KeyError):
        history.read_history("resident_555555555555", "manager")
    monkeypatch.setattr(history.personal_ama, "reaches", lambda *args, **kwargs: False)
    with pytest.raises(KeyError):
        _read("identity")
    monkeypatch.setattr(history.personal_ama, "reaches", lambda *args, **kwargs: True)
    catalog = history.resident_catalog.get_catalog()
    monkeypatch.setattr(catalog, "get_historical", lambda rid: {"resident_id": rid})
    with pytest.raises(KeyError, match="deleted resident"):
        _read("identity")


def test_cursor_from_another_deleted_target_is_rejected_even_with_both_grants(retained, monkeypatch):
    _message(retained.agents, "Second", "a")
    cursor = _read("messages", limit=1)["next_cursor"]
    retained.store.database.write(lambda connection: connection.execute(
        "INSERT INTO residents(resident_id,title,deleted_at,deleted_by) VALUES (%s,%s,%s,%s)",
        (OTHER, "Other deleted identity", "2026-10-08T00:00:00Z", "fixture"),
    ))
    monkeypatch.setattr(history.permission_system, "allows", lambda *args: True)
    with pytest.raises(ValueError, match="cursor"):
        history.read_history(OTHER, "manager", section="messages", cursor=cursor)


def test_failed_execution_without_segment_is_indexed_for_exact_agent(retained):
    for execution_id, agent_id in (("execution-ours", RID), ("execution-other", OTHER)):
        retained.agents.reserve_execution(execution_id=execution_id, agent_id=agent_id,
                                         actor_ref="old_actor", provider="codex")
        retained.agents.finish_execution(execution_id, status="failed")
    page = _read("executions", limit=1)
    assert [row["execution_id"] for row in page["items"]] == ["execution-ours"]
    assert page["items"][0]["status"] == "failed"
    assert page["items"][0]["agent_id"] == RID
    assert page["next_cursor"] == ""
    assert "inputs" not in page["items"][0]


def test_execution_keyset_has_no_cross_agent_or_tie_loss(retained):
    for execution_id in ("execution-a", "execution-b"):
        retained.agents.reserve_execution(execution_id=execution_id, agent_id=RID,
            actor_ref="fixture", provider="codex", created_at="2026-10-07T00:00:00Z")
    first = _read("executions", limit=1)
    second = _read("executions", limit=1, cursor=first["next_cursor"])
    assert [first["items"][0]["execution_id"], second["items"][0]["execution_id"]] == ["execution-a", "execution-b"]
    assert not second["next_cursor"]


def test_segments_do_not_read_bodies_or_foreign_execution_details(retained, monkeypatch):
    retained.agents.reserve_execution(execution_id="execution-foreign", agent_id=OTHER,
                                     actor_ref="fixture", provider="codex")
    segment_id = retained.agents.put_segment(
        segment_type="tool", content="private tool body", metadata={"private": "not an export field"},
        created_by_agent_id=RID, execution_id="execution-foreign",
    )
    retained.agents.append_agent_segments(RID, [segment_id], actor_ref="fixture")

    def forbidden(*args, **kwargs):
        raise AssertionError("Segment index must not hydrate content")

    monkeypatch.setattr(retained.agents, "read_sequence", forbidden)
    monkeypatch.setattr(retained.agents, "segment", forbidden)
    page = _read("segments")
    row = next(item for item in page["items"] if item["segment_id"] == segment_id)
    assert row["execution_id"] == "" and row["content_sha256"]
    assert "metadata" not in row and "content" not in row
    assert "private tool body" not in json.dumps(page)


def test_oversized_message_is_explicitly_omitted_and_page_continues(retained):
    from management_portal.execution.agent_data_history import MAX_BODY_BYTES
    _message(retained.agents, "x" * (MAX_BODY_BYTES + 100), "b")
    _message(retained.agents, "After large item", "c")
    items = _read("messages")["items"]
    assert items[1]["content_omitted"] is True
    assert items[1]["content_bytes"] > MAX_BODY_BYTES
    assert items[2]["content_parts"][0]["text"] == "After large item"
    assert "x" * (MAX_BODY_BYTES + 100) not in json.dumps(items)


def test_foreign_message_and_inline_attachment_bytes_are_not_exported(retained):
    shared = retained.agents.put_segment(
        segment_type="input", content="external source secret", created_by_agent_id=OTHER,
        metadata={"message": {"message_id": "msg_" + "d" * 32, "role": "user", "author_id": "source"}},
    )
    retained.agents.append_agent_segments(RID, [shared], actor_ref="fixture")
    retained.agents.display_chat_message(RID, {
        "message_id": "msg_" + "e" * 32, "role": "user", "author_id": "manager",
        "content_parts": [{"type": "image_url", "image_url": "data:image/png;base64,SECRET_ATTACHMENT"}],
    })
    page = _read("messages")
    external = next(item for item in page["items"] if item["segment_id"] == shared)
    assert external["content_omitted"]
    assert external["omission_reason"] == "external_source_requires_separate_authorization"
    serialized = json.dumps(page)
    assert "external source secret" not in serialized
    assert "SECRET_ATTACHMENT" not in serialized


def test_byte_budget_continuation_does_not_skip_history(retained):
    from management_portal.execution.agent_data_history import MAX_PAGE_BYTES
    for number in range(45):
        _message(retained.agents, "x" * 7000, f"{number:032x}")
    cursor, found, pages = "", [], 0
    while True:
        page = _read("messages", limit=100, cursor=cursor)
        assert len(json.dumps(page["items"], ensure_ascii=False).encode()) <= MAX_PAGE_BYTES
        found.extend(item["segment_id"] for item in page["items"])
        pages += 1
        cursor = page["next_cursor"]
        if not cursor:
            break
        assert pages < 5
    assert pages >= 2 and len(found) == len(set(found)) == 46


def test_attachment_index_keeps_canonical_relative_path_without_reading_bytes(retained, monkeypatch):
    from management_portal import resources

    def forbidden(*args, **kwargs):
        raise AssertionError("History references never read external resource bytes")

    monkeypatch.setattr(resources, "get_resource_service", forbidden)
    reference = {"workspace_id": "workspace_777777777777", "relative_path": "papers/revision.tex"}
    retained.agents.display_chat_message(RID, {
        "message_id": "msg_" + "7" * 32, "role": "user", "author_id": "manager",
        "content_parts": [{"type": "file", "file_reference": reference, "name": "revision.tex"}],
    })
    parts = _read("messages")["items"][-1]["content_parts"]
    attachment = next(part for part in parts if part["type"] == "file")
    assert attachment["file_reference"] == reference
    assert attachment["bytes_not_read"] is True


@pytest.mark.parametrize("path", ["/private/secret", "../secret", "C:\\private\\secret", "//server/share", "a/../../b"])
def test_attachment_index_does_not_return_absolute_or_escaping_paths(path):
    from management_portal.execution.agent_data_history import _references
    part = _references({"type": "file", "file_reference": {
        "workspace_id": "workspace_777777777777", "relative_path": path, "path": "/host/private",
    }})
    assert part["file_reference"] == {"workspace_id": "workspace_777777777777"}


def test_metadata_and_identity_have_explicit_size_limits(retained, monkeypatch):
    from management_portal.execution.agent_data_history import MAX_METADATA_BYTES
    segment = retained.agents.put_segment(
        segment_type="input", content="bounded content", created_by_agent_id=RID,
        metadata={"message": {"message_id": "msg_" + "f" * 32, "role": "user"},
                  "private": "M" * (MAX_METADATA_BYTES + 1)},
    )
    retained.agents.append_agent_segments(RID, [segment], actor_ref="fixture")
    page = _read("messages")
    assert page["items"][-1]["content_omitted"] is True
    assert "M" * MAX_METADATA_BYTES not in json.dumps(page)
    catalog = history.resident_catalog.get_catalog()
    record = catalog.get_historical(RID)
    monkeypatch.setattr(catalog, "get_historical", lambda rid: {**record, "title": "t" * 10000})
    identity = _read("identity")["identity"]
    assert len(identity["title"]) == 4096 and identity["metadata_truncated"]


def test_isa_handler_and_declared_target_use_current_actor(retained):
    from management_portal.hatg import catalog
    from management_portal.society_service.isa import conversation
    declaration = catalog.declaration(history.ACTION)
    assert declaration.target == "resident_id" and not declaration.mutates
    args = {"resident_id": RID, "section": "messages", "limit": 1}
    result = conversation.HANDLERS[history.ACTION](SimpleNamespace(
        caller="manager", argument=lambda key: args.get(key),
    ))
    assert result["items"] and retained.checks[-1][0] == "manager"


@pytest.mark.parametrize("section,after", [("segments", -1), ("messages", True), ("executions", ["bad", "id"])])
def test_invalid_positions_rejected_by_data_owner(retained, section, after):
    with pytest.raises(ValueError):
        retained.agents.retained_history_page(RID, section=section, after=after)


@pytest.mark.parametrize("limit", [0, 101, True])
def test_page_limit_is_enforced_before_read(retained, limit):
    with pytest.raises(ValueError):
        _read("messages", limit=limit)
