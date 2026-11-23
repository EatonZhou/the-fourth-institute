"""Inline attachment prompt composition and its captured Chat projection.

:module: resident.conversation
:design: decision_c616d197abbb
"""
from types import SimpleNamespace

import pytest

from management_portal.society_service import resident_intake_domain as intake
from management_portal.society_service import resident_media_domain as media
from management_portal.society_service.resident_message_contract import input_parts
from management_portal.execution.message_projection import message_facts, project_message, wrap_message, inline_message_text


@pytest.mark.parametrize("quoted", [False, True])
def test_three_files_are_resolved_in_place_and_projected_once(tmp_path, monkeypatch, quoted):
    parts = []
    expected = ""
    for i in range(3):
        upload = "upload_" + str(i) * 24
        path = tmp_path / upload / "payload"
        path.parent.mkdir()
        path.write_text("private file contents", encoding="utf-8")
        text = f"\n第{i + 1}份（"
        parts.extend([{"type": "text", "text": text},
                      {"type": "file", "upload_id": upload, "name": "稿件.txt"},
                      {"type": "text", "text": "）\n"}])
        expected += text + f"[file: {path}]）\n"
    original = {"role": "user", "content_parts": parts}
    content, selection, *_ = input_parts(original)
    # Only upload storage and unrelated context services are isolated. Intake,
    # authorization/normalization and prompt construction use production owners.
    from management_portal.resources import isa
    monkeypatch.setattr(isa, "read_upload", lambda upload: {
        "ready": True, "created_by": "tester", "path": str(tmp_path / upload / "payload"),
        "file_name": "稿件.txt", "media_type": "text/plain",
    })
    monkeypatch.setattr(intake, "_ensure_backend_accepts_context_refs", lambda *a: None)
    monkeypatch.setattr(intake, "resolve_context_refs", lambda *a: {})
    monkeypatch.setattr(intake, "_resident_message_cwd", lambda *a: tmp_path)
    monkeypatch.setattr(intake.resident_runtime_domain, "resident_media_workspace_roots", lambda *a: (tmp_path,))
    monkeypatch.setattr(intake.resident_record_domain, "sessions_path", lambda *a: tmp_path / "runtime")
    quote = {"author_id": "tester", "text": "previous text"} if quoted else None
    if quote:
        original["quote"] = quote
    request = SimpleNamespace(message=content, protocol_input=original, context_refs={}, input_media=selection,
        attachment_actor="tester", resident_id="resident_111111111111", message_id="msg_" + "a" * 32,
        working_directory="", quote=quote, relay=SimpleNamespace(sender=""))
    prepared = intake._prepared_input(request, {"resident_id": request.resident_id}, {}, True)
    from management_portal.execution.message_projection import quote_block
    assert prepared.provider_input == ((quote_block(quote) + "\n\n") if quote else "") + expected
    assert prepared.input_media == []
    assert "private file contents" not in prepared.provider_input
    assert prepared.provider_input.count("[file: ") == 3
    shown = project_message("capture", message_facts(original, file_references=prepared.file_references), wrap_message(prepared.provider_input))
    assert [p["text"] for p in shown["content_parts"] if p["type"] == "text"] == [p["text"] for p in parts if p["type"] == "text"]
    assert [p["source"] for p in shown["content_parts"] if p["type"] == "file"] == [str(tmp_path / ("upload_" + str(i)*24) / "payload") for i in range(3)]
    assert [p["name"] for p in shown["content_parts"] if p["type"] == "file"] == ["稿件.txt"]*3


def test_repeated_reference_whitespace_and_native_image_are_preserved(tmp_path):
    path = tmp_path / "data.txt"
    path.write_text("data")
    parts = [{"type": "text", "text": " compare "},
             {"type": "file", "path": str(path)},
             {"type": "text", "text": "\tand\n"},
             {"type": "image", "upload_id": "upload_image"},
             {"type": "file", "path": str(path)},
             {"type": "text", "text": " "}]
    message = {"role": "user", "content_parts": parts}
    body, attachments, *_ = input_parts(message)
    files = media.resident_input_media([p for p in attachments if p["type"] == "file"],
        actor="tester", resident_id="resident_111111111111", content=body, workspace_roots=[tmp_path])
    prompt = inline_message_text(parts, {p["reference_source"]:p["source"] for p in files})
    assert prompt == f" compare [file: {path}]\tand\n[file: {path}] "
    assert project_message("capture", message_facts(message), wrap_message(prompt))["content_parts"] == parts
    assert [part["type"] for part in attachments] == ["file", "image", "file"]


def test_file_without_text_and_unreferenced_metadata(tmp_path):
    path = tmp_path / "data.txt"
    path.write_text("data")
    body, *_ = input_parts({"content_parts": [{"type": "file", "path": str(path)}]})
    assert body == f"[file: {path}]"
    assert inline_message_text([{"type": "text", "text": "only text"}], {"unused": str(path)}) == "only text"


def test_local_file_authorization_still_precedes_resolution(tmp_path):
    path = tmp_path / "outside.txt"
    path.write_text("private")
    with pytest.raises(PermissionError):
        media.resident_input_media([{"type": "file", "source": str(path)}],
            actor="tester", resident_id="resident_111111111111", content="", workspace_roots=[])


def test_upload_admission_capture_and_reload_use_one_ordered_document(tmp_path, monkeypatch, permissions, resident_record):
    """Real upload and Segment storage, with no AI execution or fabricated aliases."""
    import json
    import os
    from dataclasses import replace
    from management_portal.resources import isa
    from management_portal.hatg.contracts import InstructionCall, InstructionContext
    from management_portal.execution.agent_data_store import AgentDataStore
    from management_portal.execution.agent_data_projection import AgentDataProjector
    from management_portal.society_service.resident_conversation_records import protocol_message
    from tests.cases.test_agent_data_projection import _turn_request, _runtime_session

    permissions.users("tester", category="intern")
    monkeypatch.setattr(isa, "upload_root", lambda: tmp_path / "uploads")
    kernel = isa.build_kernel()
    files = []
    for name in ["稿件 version 1.tex", "稿件 version 1.tex", "meta.json"]:
        data = b"uploaded file bytes"
        result = kernel.execute(InstructionCall.from_payload({"instruction": "CreateUpload", "arguments": {
            "purpose": "conversation_attachment", "file_name": name, "size": len(data), "media_type": "text/plain"}}),
            InstructionContext(caller="tester", entry="web")).raise_for_error()
        isa.complete_upload(result["upload_id"], data, actor="tester")
        meta = isa.read_upload(result["upload_id"])
        assert meta["file_name"] == name
        assert __import__('pathlib').Path(meta["path"]).name == name
        files.append({"type": "file", "upload_id": result["upload_id"], "name": name})
    literal = f"[file: {files[0]['upload_id']}] [[portal-file:literal]]"
    parts = [{"type": "text", "text": "前文 **"}, files[1], {"type": "text", "text": "**\n"},
             files[0], {"type": "text", "text": "\t"}, files[2], {"type": "text", "text": " 再看 "},
             files[1], {"type": "text", "text": "\n字面 " + literal + " 后文\n"}]
    original = {"message_id": "msg_"+"a"*32, "role": "user", "author_id": "tester", "content_parts": parts}
    content, selection, *_ = input_parts(original)
    monkeypatch.setattr(intake, "_ensure_backend_accepts_context_refs", lambda *a: None)
    monkeypatch.setattr(intake, "resolve_context_refs", lambda *a: {})
    monkeypatch.setattr(intake, "_resident_message_cwd", lambda *a: tmp_path)
    monkeypatch.setattr(intake.resident_runtime_domain, "resident_media_workspace_roots", lambda *a: (tmp_path,))
    monkeypatch.setattr(intake.resident_record_domain, "sessions_path", lambda *a: tmp_path / "runtime")
    request = SimpleNamespace(message=content, protocol_input=original, context_refs={}, input_media=selection,
        attachment_actor="tester", resident_id=resident_record["resident_id"], message_id=original["message_id"],
        working_directory="", quote=None, relay=SimpleNamespace(sender=""))
    prepared = intake._prepared_input(request, {"resident_id": request.resident_id}, {}, True)
    assert literal in prepared.provider_input
    assert prepared.provider_input.endswith(" 后文\n")
    assert prepared.provider_input.count(isa.read_upload(files[1]["upload_id"])["path"]) == 2
    assert "uploaded file bytes" not in prepared.provider_input
    from management_portal.society_service.resident_conversation_stream import get_repository
    request.actor_id = "tester"
    request.channel = ""
    request.relay.delegated = False
    stored = intake.ResidentInput.user_message(request, request.resident_id, prepared.file_references)
    assert "file_references" not in protocol_message(stored)
    repository = get_repository()
    repository.messages.accept_user_message(request.resident_id, stored)
    accepted = repository.state_store.mailbox_entry(request.resident_id, original["message_id"])["message"]
    assert accepted["file_references"] == prepared.file_references
    commit = repository.messages.prepare_user_message(request.resident_id, stored)
    repository.messages.commit_prepared_user_message(commit)
    store = AgentDataStore()
    before = store.chat_message(request.resident_id, original["message_id"])
    assert before["content_parts"] == parts
    turn = replace(_turn_request(request.resident_id), prompt=wrap_message(prepared.provider_input),
                   metadata={"user_message_id":original["message_id"]})
    AgentDataProjector(store).begin(turn, _runtime_session(turn, "attachment-roundtrip"))
    # Reopen through the persisted owner, just as a refreshed Chat does.
    fresh = AgentDataStore().chat_message(request.resident_id, original["message_id"])
    shown_files = [p for p in fresh["content_parts"] if p["type"] == "file"]
    assert [p["upload_id"] for p in shown_files] == [files[i]["upload_id"] for i in (1,0,2,1)]
    assert all(p["source"] == isa.read_upload(p["upload_id"])["path"] for p in shown_files)
    assert [p for p in fresh["content_parts"] if p["type"] == "text"] == [p for p in parts if p["type"] == "text"]
    segment = store.segment(fresh["segment_id"])
    assert segment["content"] == wrap_message(prepared.provider_input)
    assert all("text" not in p for p in segment["metadata"]["message"]["parts"])
    changed = project_message("changed", segment["metadata"]["message"], wrap_message("changed prompt"))
    assert changed["content_parts"] == [{"type":"text", "text":"changed prompt"}]
    # Idempotent replay is proved against the admitted ancestor, not a second body.
    assert any(p["content_parts"] == parts for p in store.chat_message_ancestors(fresh["segment_id"], original["message_id"]))
    if os.environ.get("PORTAL_ATTACHMENT_PROJECTION_FIXTURE"):
        __import__('pathlib').Path(os.environ["PORTAL_ATTACHMENT_PROJECTION_FIXTURE"]).write_text(
            json.dumps({"before":before,"after":fresh,"prompt":segment["content"]}, ensure_ascii=False))


from tests.cases.permission_test_support import permissions  # noqa: E402,F401
from tests.cases.resident_execution_test_support import resident_record  # noqa: E402,F401
