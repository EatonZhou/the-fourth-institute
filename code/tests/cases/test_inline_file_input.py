"""Inline attachment prompt composition and its captured Chat projection.

:module: resident.conversation
:design: decision_c616d197abbb
"""
from types import SimpleNamespace

import pytest

from management_portal.society_service import resident_intake_domain as intake
from management_portal.society_service import resident_media_domain as media
from management_portal.society_service.resident_message_contract import input_parts
from management_portal.execution.message_projection import message_facts, project_message, wrap_message


@pytest.mark.parametrize("quoted", [False, True])
def test_three_files_are_resolved_in_place_and_projected_once(tmp_path, monkeypatch, quoted):
    parts = []
    expected = ""
    for i in range(3):
        upload = "upload_" + str(i) * 24
        path = tmp_path / upload / "稿件.txt"
        path.parent.mkdir()
        path.write_text("private file contents", encoding="utf-8")
        text = f"\n第{i + 1}份（"
        parts.extend([{"type": "text", "text": text},
                      {"type": "file", "upload_id": upload, "name": path.name},
                      {"type": "text", "text": "）\n"}])
        expected += text + f"[file: {path}]）\n"
    original = {"role": "user", "content_parts": parts}
    content, selection, *_ = input_parts(original)
    # Only upload storage and unrelated context services are isolated. Intake,
    # authorization/normalization and prompt construction use production owners.
    from management_portal.resources import isa
    monkeypatch.setattr(isa, "read_upload", lambda upload: {
        "ready": True, "created_by": "tester", "path": str(tmp_path / upload / "稿件.txt"),
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
    request = SimpleNamespace(message=content, context_refs={}, input_media=selection,
        attachment_actor="tester", resident_id="resident_111111111111", message_id="msg_" + "a" * 32,
        working_directory="", quote=quote, relay=SimpleNamespace(sender=""))
    prepared = intake._prepared_input(request, {"resident_id": request.resident_id}, {}, True)
    from management_portal.execution.message_projection import quote_block
    assert prepared.provider_input == ((quote_block(quote) + "\n\n") if quote else "") + expected
    assert prepared.input_media == []
    assert "private file contents" not in prepared.provider_input
    assert prepared.provider_input.count("[file: ") == 3
    assert project_message("capture", message_facts(original), wrap_message(prepared.provider_input))["content_parts"] == parts


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
    prompt = media.resolve_file_references(body, files)
    assert prompt == f" compare [file: {path}]\tand\n[file: {path}] "
    assert project_message("capture", message_facts(message), wrap_message(prompt))["content_parts"] == parts
    assert [part["type"] for part in attachments] == ["file", "image", "file"]


def test_file_without_text_and_unreferenced_metadata(tmp_path):
    path = tmp_path / "data.txt"
    path.write_text("data")
    body, *_ = input_parts({"content_parts": [{"type": "file", "path": str(path)}]})
    assert body == f"[file: {path}]"
    assert media.resolve_file_references("only text", [{"media_type": "file", "source": str(path)}]) == "only text"


def test_local_file_authorization_still_precedes_resolution(tmp_path):
    path = tmp_path / "outside.txt"
    path.write_text("private")
    with pytest.raises(PermissionError):
        media.resident_input_media([{"type": "file", "source": str(path)}],
            actor="tester", resident_id="resident_111111111111", content="", workspace_roots=[])
