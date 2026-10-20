"""Independent Family and attachment contracts through production DOM/CSS owners.

:aspects: ui, behavior
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def _run(name, message=None):
    source = Path(__file__).with_name("javascript") / "message_state_family.cjs"
    result = subprocess.run(
        ["node", str(source), name],
        input=json.dumps({"routeScript": portal_route_script().decode(), "message": message}),
        capture_output=True, text=True, timeout=30,
    )
    print(result.stdout, end="")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == f"PASS {name}\n"


def test_interleaved_append_preserves_independent_groups():
    """Family 两个请求的回复交错追加时，各请求组的折叠状态互不影响

    Stable case test_be0d776d27a1."""
    _run("interleaved-append")


def test_status_and_completion_preserve_collapsed_work():
    """Family 请求状态更新或完成时，已折叠的请求组保持折叠

    Stable case test_410883f25d40."""
    _run("status-completion")


def test_paging_keeps_one_collapsed_header():
    """加载更早消息后，已折叠的 Family 请求组仍只有一个组头

    Stable case test_546166971af9."""
    _run("paging-header")


def test_reveal_unfolds_only_requested_work():
    """定位 Family 回复时只展开它所在的请求组，其他组保持折叠

    Stable case test_a1d3616e8a26."""
    _run("reveal-work-item")


def test_ordinary_intermediate_outputs_fold_and_reveal():
    """普通对话的中间输出可折叠，定位到被折叠的消息时自动展开

    Stable case test_8f785545b26b."""
    _run("ordinary-intermediate-output")


def test_captured_file_references_keep_authored_position():
    """重开聊天后文件引用仅在原位置显示一次

    Stable case test_a269fcd82be7; preserve the original production projection.

    :module: resident.conversation
    """
    from management_portal.execution.message_projection import message_facts, project_message, wrap_message
    from management_portal.society_service.resident_media_domain import _resident_message_with_file_references

    upload_id = "upload_" + "a" * 24
    original = {"message_id": "msg_file_order", "role": "user", "author_id": "sample_admin", "content_parts": [
        {"type": "text", "text": "Before the attachment: "},
        {"type": "file", "upload_id": upload_id, "name": "paper.docx"},
        {"type": "text", "text": " After the attachment.\n"},
    ]}
    body = "\n".join(part["text"] for part in original["content_parts"] if part["type"] == "text")
    prompt = _resident_message_with_file_references(
        body, [{"media_type": "file", "source": f"/uploads/{upload_id}/paper.docx"}],
    )
    message = project_message("segment_file_order", message_facts(original), wrap_message(prompt))
    _run("file-reference-order", message)
