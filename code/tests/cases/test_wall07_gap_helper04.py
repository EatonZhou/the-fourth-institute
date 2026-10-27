from management_portal.execution.contracts import InputMedia
from management_portal.execution.contracts import MAX_INPUT_MEDIA_MIME_CHARS
from management_portal.execution.contracts import MAX_INPUT_MEDIA_NAME_CHARS
from management_portal.execution.contracts import MAX_INPUT_MEDIA_SOURCE_CHARS
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import base64
import json
import os
import uuid

import pytest

from management_portal.hatg import routing
from management_portal.hatg.contracts import InstructionContext
from tests.cases.isa_test_support import reset_kernels, run
from management_portal.execution.agent_data_store import get_agent_data_store
from management_portal import conversation_index_projection
from management_portal.society_service import (
    active_agent_projection,
    lineage_domain,
    lineage_handoff_backend,
    lineage_primitives,
    project_domain,
    resident_directory_domain,
    resident_domain,
    resident_execution_work,
    resident_protocol,
    resource_client,
    state_store,
)
from management_portal.society_service import lineage_program_effect_rules
from management_portal.society_service import resident_intake_domain
from management_portal.society_service import resident_media_domain
from management_portal.society_service import resident_record_domain
from management_portal.society_service import resident_runtime_domain
from management_portal.society_service import resident_workspace_domain
from tests.cases.test_resident_folder_creation_contract import (
    _new_session_payload, future_creation_store,
)
from tests.cases.test_resident_conversation_performance import (
    A, B, history_repository, query_work,
)
from tests.cases.effect_script_support import effect_config, lower_effect_call
from tests.cases.permission_test_support import permissions  # noqa: F401


ACTOR = "sample_admin"
CANONICAL_RESIDENT_INPUT = resident_protocol.deliver_resident_input


@pytest.fixture
def isolated_team(future_creation_store, tmp_path, monkeypatch):
    reset_kernels()
    monkeypatch.setattr(project_domain, "project_root_for_id", lambda project_id: tmp_path)

    source = resident_domain.register_resident(_new_session_payload("Helper04 source"), ACTOR)
    target = lineage_domain.create_lineage_resident(
        source["lineage_id"], _new_session_payload("Helper04 target"), ACTOR,
    )["resident"]
    target = resident_workspace_domain.find_resident(target["resident_id"])
    for record in (source, target):
        resource_client.sync_resident(record, updated_by=ACTOR)
    assert Path(source["default_cwd"]).is_relative_to(tmp_path)
    assert Path(target["default_cwd"]).is_relative_to(tmp_path)
    yield SimpleNamespace(source=source, target=target, lineage_id=source["lineage_id"])
    reset_kernels()


def instruction(team, name, arguments, *, key=None, succeed=True):
    execution = run(name, arguments, key=key or f"helper04-{uuid.uuid4().hex}")
    if succeed:
        assert execution.status == "committed", execution.to_payload()
    else:
        assert execution.status != "committed", execution.to_payload()
    return execution


def deliver(call, actor=ACTOR):
    """Execute an Effect's DeliverMessage call the way the Effect runtime does."""

    return routing.execute(call, InstructionContext(caller=actor, entry="lineage"))


def test_workspace_isa_binding_and_unbind_preserve_files(isolated_team, tmp_path):
    """BindWorkspace 与 UnbindWorkspace 更新导航并保留文件"""
    team = isolated_team
    resident_id = team.target["resident_id"]
    for index, name in enumerate(("BindWorkspace", "BindWorkspace")):
        reference = tmp_path / f"reference-{index}"
        reference.mkdir()
        marker = reference / "retained.txt"
        marker.write_text("retained fixture")
        mounted = instruction(team, name, {"resident_id": resident_id, "path": str(reference)})
        record = resident_workspace_domain.find_resident(resident_id)
        binding = next(value for value in record["workspace_bindings"] if value["path"] == str(reference))
        assert binding["workspace_id"].startswith("workspace_")
        assert record["default_cwd"] == team.target["default_cwd"]
        document = Path(resident_record_domain.resident_folder_path(record)) / "workspaces.md"
        assert str(reference) in document.read_text()
        assert str(reference) in json.dumps(mounted.result)
        instruction(team, "UnbindWorkspace", {"resident_id": resident_id, "workspace_id": binding["workspace_id"]})
        after = resident_workspace_domain.find_resident(resident_id)
        assert not any(value["path"] == str(reference) for value in after["workspace_bindings"])
        assert str(reference) not in document.read_text()
        assert marker.read_text() == "retained fixture"
    before = deepcopy(resident_workspace_domain.find_resident(resident_id)["workspace_bindings"])
    instruction(team, "UnbindWorkspace", {"resident_id": resident_id, "root_id": "resident_workspace"}, succeed=False)
    assert resident_workspace_domain.find_resident(resident_id)["workspace_bindings"] == before


def test_human_notification_isa_persistence_idempotency_and_listing(isolated_team):
    """CreateNotification 持久保存并幂等去重且列表按授权可见"""
    team = isolated_team
    arguments = {"title": "Helper04 isolated approval", "message": "Synthetic notification", "severity": "warning", "notification_id": "human_notice_helper04"}
    arguments["lineage_id"] = team.lineage_id
    first = instruction(team, "CreateNotification", arguments).result
    duplicate = instruction(team, "CreateNotification", arguments).result
    assert first["created"] is True
    assert duplicate["created"] is False
    listed = lineage_domain.list_lineage_human_notifications(ACTOR, [team.lineage_id])
    notices = [notice for lineage in listed["lineages"] for notice in lineage["notifications"]]
    assert len(notices) == 1
    assert notices[0]["message"] == "Synthetic notification"
    assert notices[0]["id"] == "human_notice_helper04"
    denied = lineage_domain.list_lineage_human_notifications("fixture-stranger", [team.lineage_id])
    assert "Synthetic notification" not in json.dumps(denied)


def test_handoff_create_and_prepare_bind_the_target_without_invoke(isolated_team):
    """创建并准备 Workspace Handoff 绑定目标且不触发调用"""
    team = isolated_team
    source_id, target_id = team.source["resident_id"], team.target["resident_id"]
    source_path = Path(team.source["default_cwd"])
    (source_path / "handoff.txt").write_text("isolated handoff fixture")
    created = instruction(team, "CreateWorkspaceHandoff", {"lineage_id": team.lineage_id,
        "source_resident_id": source_id, "target_resident_id": target_id,
        "items": [{"source": {"path": str(source_path)}, "transfer": {"mode": "direct"}}],
    }).result
    prepared = instruction(team, "PrepareWorkspaceHandoff", {"lineage_id": team.lineage_id, "handoff_id": created["handoff_id"]}).result
    assert prepared["status"] == "ready"
    graph = lineage_domain._read_lineage(team.lineage_id, actor=ACTOR)
    edge = next(value for value in graph["edges"] if value["type"] == "handoff")
    assert edge["id"] == created["handoff_id"]
    assert edge["items"][0]["source"]["path"] == str(source_path)
    record = resident_workspace_domain.find_resident(target_id)
    assert any(value["path"] == str(source_path) for value in record["workspace_bindings"])
    with state_store.get_lineage_data_store().database.read() as connection:
        assert connection.execute("SELECT COUNT(*) FROM conversation_input_states").fetchone()[0] == 0
    instruction(team, "CreateWorkspaceHandoff", {"lineage_id": team.lineage_id, "source_resident_id": source_id, "target_resident_id": source_id}, succeed=False)


def test_content_part_count_type_and_exact_utf8_byte_limits():
    """调用内容部件校验类型数量并按 UTF-8 字节限制元数据"""
    parts = [{"type": "file", "path": "fixture.txt"} for _ in range(32)]
    original = deepcopy(parts)
    assert lineage_primitives.normalize_invocation_content_parts(parts) == parts
    assert parts == original
    with pytest.raises(ValueError, match="32"):
        lineage_primitives.normalize_invocation_content_parts(parts + [parts[0]])
    for invalid in ("text", "identity", "bearer"):
        with pytest.raises(ValueError, match="只接受"):
            lineage_primitives.normalize_invocation_content_parts([{"type": invalid}])
    for part_type in ("image", "file", "context_reference"):
        accepted = {"type": part_type, "path": "fixture.txt"}
        assert lineage_primitives.normalize_invocation_content_parts([accepted]) == [accepted]
    arguments = {"content_parts": [{"type": "context_reference", "resource_id": "workspace_aaaaaaaaaaaa"}], "metadata": ""}
    overhead = len(json.dumps(arguments, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode())
    arguments["metadata"] = "x" * (64000 - overhead - 3) + "界"
    arguments["prompt"] = "p" * lineage_primitives.MAX_INVOCATION_PROMPT_CHARS
    assert lineage_primitives.normalize_invocation_arguments(arguments) == arguments
    arguments["metadata"] += "界"
    with pytest.raises(ValueError, match="64000"):
        lineage_primitives.normalize_invocation_arguments(arguments)


def test_event_attachment_invocation_reaches_canonical_message_with_closed_intake(isolated_team, monkeypatch):
    """事件附件经 DeliverMessage 进入关闭接收节点的排队输入"""
    team = isolated_team
    monkeypatch.setattr(resident_protocol, "deliver_resident_input", CANONICAL_RESIDENT_INPUT)
    target_id = team.target["resident_id"]
    resident_protocol.change_resident_state(target_id, ACTOR, state_domain="intake", transition="close")
    attachment = Path(team.target["default_cwd"]) / "context.txt"
    attachment.write_text("synthetic attachment")
    parts = [{"type": "file", "path": str(attachment)}]
    event = lineage_primitives.make_event("manual.triggered", payload={"prompt": "One invocation", "content_parts": parts})
    invocation = lineage_program_effect_rules._make_program_effect_invocation(event, source_kind="event", source_id="event_source_helper04", include_envelope=False)
    definition = delivery_effect(target_id, fixed="Fixed contract")
    unchanged = deepcopy(definition)
    lowered = lower_effect_call(team.lineage_id, "effect_helper04", definition, invocation)
    assert lowered["arguments"]["message"]["content_parts"] == [{"type": "text", "text": "Fixed contract\n\nOne invocation"}, *parts]
    assert lowered["arguments"]["activation_policy"] == "enqueue_and_trigger"
    delivered = deliver(lowered)
    assert delivered.status == "committed", delivered.to_payload()
    job = resident_execution_work.read(resident_workspace_domain.find_resident(target_id))
    assert job["input_message"]["content_parts"] == lowered["arguments"]["message"]["content_parts"]
    assert job["input_message"]["author_id"] == ACTOR
    assert job["input_media"] == []
    assert job["raw_message"] == f"Fixed contract\n\nOne invocation\n\n[file: {attachment}]"
    assert job["effective_message"] == f"Fixed contract\n\nOne invocation\n\n[file: {attachment}]"
    assert f"[file: {attachment}]" in job["message"]
    assert "synthetic attachment" not in job["message"]
    assert job["activation_policy"] == "enqueue_and_trigger"
    messages = get_agent_data_store().read_chat_messages(target_id)
    assert messages == []
    assert definition == unchanged
    with state_store.get_lineage_data_store().database.read() as connection:
        inputs = connection.execute('SELECT state FROM conversation_input_states WHERE resident_id=%s', (target_id,)).fetchall()
    assert [value[0] for value in inputs] == ["queued"]


def delivery_effect(target_id, *, fixed=None):
    """A DeliverMessage Effect definition with its target endpoint bound."""

    arguments = {"resident_id": {"from": "endpoint.target"}, "activation_policy": {"value": "enqueue_and_trigger"}}
    if fixed is not None:
        arguments["message"] = {"value": fixed}
    return {**effect_config("DeliverMessage", arguments), "endpoints": {"target": target_id}}


def attachment_fix_request(team, parts, prompt="Attachment contract"):
    event = lineage_primitives.make_event("manual.triggered", payload={"prompt": prompt, "content_parts": parts})
    invocation = lineage_program_effect_rules._make_program_effect_invocation(event, source_kind="event", source_id="event_source_helper04", include_envelope=False)
    return lower_effect_call(team.lineage_id, "effect_helper04", delivery_effect(team.target["resident_id"]), invocation)


def assert_attachment_fix_not_persisted(team):
    target_id = team.target["resident_id"]
    assert not resident_execution_work.read(resident_workspace_domain.find_resident(target_id))
    assert get_agent_data_store().read_chat_messages(target_id) == []
    with state_store.get_lineage_data_store().database.read() as connection:
        assert connection.execute('SELECT COUNT(*) FROM conversation_input_states WHERE resident_id=%s', (target_id,)).fetchone()[0] == 0


@pytest.mark.parametrize("source_kind", ["workspace", "upload"])
def test_attachment_fix_mixed_files_and_images_keep_native_contract(isolated_team, monkeypatch, tmp_path, source_kind):
    """混合文件与图片投递保留文件引用并独立暂存原生图片"""
    team = isolated_team
    monkeypatch.setattr(resident_protocol, "deliver_resident_input", CANONICAL_RESIDENT_INPUT)
    root = Path(team.target["default_cwd"])
    attachment = root / "notes.txt"
    attachment.write_text("never expanded by the message owner")
    image = root / "pixel.png"
    image.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="))
    if source_kind == "upload":
        from management_portal.society_service import workspace_contracts
        for path, kind, mime in ((attachment, "file", "text/plain"), (image, "image", "image/png")):
            cache = tmp_path / f"cache-{kind}"
            cache.mkdir()
            monkeypatch.setattr(workspace_contracts, "CHAT_IMAGE_CACHE_ROOT" if kind == "image" else "CHAT_ATTACHMENT_CACHE_ROOT", cache)
            uploaded = cache / path.name
            uploaded.write_bytes(path.read_bytes())
            uploaded.with_suffix(uploaded.suffix + ".json").write_text(json.dumps({"actor": ACTOR, "resident_id": team.target["resident_id"], "file_id": uploaded.name, "content_type": mime, "filename": uploaded.name}))
            if kind == "file":
                attachment = uploaded
            else:
                image = uploaded
    parts = [{"type": "file", "path": str(attachment)}, {"type": "image", "path": str(image)}]
    prompt = f"Read [file: {attachment}]"
    request = attachment_fix_request(team, parts, prompt)
    result = deliver(request)
    assert result.status == "committed", result.to_payload()
    job = resident_execution_work.read(resident_workspace_domain.find_resident(team.target["resident_id"]))
    assert job["input_message"]["content_parts"] == request["arguments"]["message"]["content_parts"]
    assert job["effective_message"].count(f"[file: {attachment}]") == 1
    assert "never expanded" not in job["message"]
    assert len(job["input_media"]) == 1
    native = InputMedia.from_payload(job["input_media"][0])
    # Accepted images are staged per message, independent of the mutable upload.
    assert native.media_type == "image" and job["input_media"][0]["original_source"] == str(image)
    assert "/runtime/input_media/" in native.source and Path(native.source).read_bytes() == Path(image).read_bytes()
    assert job["activation_policy"] == "enqueue_and_trigger"


@pytest.mark.parametrize("kind", ["file", "image"])
@pytest.mark.parametrize("boundary", ["outside", "traversal", "symlink", "relative", "url", "oversize", "empty", "conflicting_type"])
def test_attachment_fix_rejects_unsafe_parts_before_message_persistence(isolated_team, monkeypatch, tmp_path_factory, kind, boundary):
    """不安全附件在消息持久化前被拒绝且不影响外部文件"""
    from management_portal.society_service import workspace_contracts
    team = isolated_team
    monkeypatch.setattr(resident_protocol, "deliver_resident_input", CANONICAL_RESIDENT_INPUT)
    root = Path(team.target["default_cwd"])
    suffix = ".png" if kind == "image" else ".txt"
    attachment = root / ("safe" + suffix)
    attachment.write_bytes(b"fixture bytes")
    outside = tmp_path_factory.mktemp("unbound-attachment") / ("private" + suffix)
    outside.write_bytes(b"private fixture bytes")
    assert not any(outside.is_relative_to(path) for path in resident_runtime_domain.resident_media_workspace_roots(team.target))
    part = {"type": kind, "path": str(attachment)}
    if boundary == "outside":
        part["path"] = str(outside)
    elif boundary == "traversal":
        part["path"] = str(root / ".." / "workspace" / attachment.name)
    elif boundary == "symlink":
        link = root / ("link" + suffix)
        link.symlink_to(outside)
        part["path"] = str(link)
    elif boundary == "relative":
        part["path"] = attachment.name
    elif boundary == "url":
        part = {"type": kind, "url": "https://attachment.invalid/private"}
    elif boundary == "oversize":
        monkeypatch.setattr(workspace_contracts, "CHAT_IMAGE_MAX_BYTES" if kind == "image" else "CHAT_ATTACHMENT_MAX_BYTES", 1)
    elif boundary == "empty":
        attachment.write_bytes(b"")
    elif boundary == "conflicting_type":
        part["media_type"] = "image" if kind == "file" else "file"
    request = attachment_fix_request(team, [part])
    assert deliver(request).status == "failed"
    assert_attachment_fix_not_persisted(team)
    assert outside.read_bytes() == b"private fixture bytes"


@pytest.mark.parametrize("kind", ["file", "image"])
@pytest.mark.parametrize("boundary", ["actor", "resident", "identity", "metadata_symlink"])
def test_attachment_fix_upload_ownership_is_not_replaced_by_path(isolated_team, monkeypatch, tmp_path, kind, boundary):
    """上传附件保留原归属元数据且拒绝身份不符或元数据链接"""
    from management_portal.society_service import workspace_contracts
    team = isolated_team
    monkeypatch.setattr(resident_protocol, "deliver_resident_input", CANONICAL_RESIDENT_INPUT)
    root = tmp_path / "uploads"
    root.mkdir()
    monkeypatch.setattr(workspace_contracts, "CHAT_IMAGE_CACHE_ROOT" if kind == "image" else "CHAT_ATTACHMENT_CACHE_ROOT", root)
    attachment = root / ("attachment.png" if kind == "image" else "attachment.txt")
    attachment.write_bytes(b"synthetic local bytes")
    metadata = {"actor": ACTOR, "resident_id": team.target["resident_id"], "file_id": attachment.name, "content_type": "image/png" if kind == "image" else "text/plain"}
    if boundary == "actor":
        metadata["actor"] = "fixture-other-user"
    elif boundary == "resident":
        metadata["resident_id"] = team.source["resident_id"]
    elif boundary == "identity":
        metadata["file_id"] = "other-file"
    metadata_path = attachment.with_suffix(attachment.suffix + ".json")
    if boundary == "metadata_symlink":
        outside = tmp_path / "outside-metadata.json"
        outside.write_text(json.dumps(metadata))
        metadata_path.symlink_to(outside)
    else:
        metadata_path.write_text(json.dumps(metadata))
    if boundary in {"actor", "resident"}:
        sender = team.source["resident_id"]
        result = deliver(attachment_fix_request(team, [{"type": kind, "path": str(attachment)}]), actor=sender)
        assert result.status == "committed", result.to_payload()
        job = resident_execution_work.read(resident_workspace_domain.find_resident(team.target["resident_id"]))
        assert job["input_message"]["author_id"] == sender
        assert json.loads(metadata_path.read_text()) == metadata
        if kind == "image":
            assert job["input_media"][0]["original_source"] == str(attachment)
        else:
            assert f"[file: {attachment}]" in job["effective_message"]
        return
    assert deliver(attachment_fix_request(team, [{"type": kind, "path": str(attachment)}])).status == "failed"
    assert_attachment_fix_not_persisted(team)


def test_attachment_fix_target_permission_check_still_precedes_file_delivery(isolated_team, monkeypatch, permissions):
    """附件投递前先检查给目标 Resident 发消息的权限

    默认权限方案让全员可以给 Resident 发消息；对调用者写一条 deny 后投递被拒，检查发生在读取任何附件文件之前。

    :design: decision_b820faf42218
    """
    from tests.cases.permission_test_support import Fact

    team = isolated_team
    monkeypatch.setattr(resident_protocol, "deliver_resident_input", CANONICAL_RESIDENT_INPUT)
    permissions.users(ACTOR, "fixture-other-user")
    permissions.place(Fact("lineage", team.lineage_id, creator=ACTOR),
                      Fact("resident", team.target["resident_id"], {"lineage": team.lineage_id}, creator=ACTOR))
    assert not permissions.holds("fixture-other-user", "@contributor", team.target["resident_id"])
    permissions.deny("fixture-other-user", team.target["resident_id"], "@message")
    attachment = Path(team.target["default_cwd"]) / "private.txt"
    attachment.write_text("private fixture")
    denied = deliver(attachment_fix_request(team, [{"type": "file", "path": str(attachment)}]), actor="fixture-other-user")
    assert denied.status == "failed"
    assert denied.error["code"] == "instruction.forbidden", denied.error
    assert_attachment_fix_not_persisted(team)


@pytest.mark.parametrize("backend", ["claude", "provider_api"])
def test_attachment_fix_local_file_requires_local_tools_not_native_images(isolated_team, monkeypatch, backend):
    """本地文件附件在 claude 保留引用且模型 API 拒绝接收"""
    team = isolated_team
    monkeypatch.setattr(resident_protocol, "deliver_resident_input", CANONICAL_RESIDENT_INPUT)
    record = resident_workspace_domain.find_resident(team.target["resident_id"])
    record["backend"] = backend
    record["execution_settings"]["backend"] = backend
    resident_workspace_domain.save_resident_record(record, commit_configuration=True)
    record = resident_workspace_domain.find_resident(team.target["resident_id"])
    assert record["backend"] == backend
    attachment = Path(record["default_cwd"]) / "context.txt"
    attachment.write_text("isolated content")
    request = attachment_fix_request(team, [{"type": "file", "path": str(attachment)}])
    if backend == "provider_api":
        failed = deliver(request)
        assert failed.status == "failed" and "不读取本地文件附件" in failed.error["message"]
        assert_attachment_fix_not_persisted(team)
    else:
        result = deliver(request)
        assert result.status == "committed", result.to_payload()
        job = resident_execution_work.read(resident_workspace_domain.find_resident(record["resident_id"]))
        assert job["input_media"] == []
        assert f"[file: {attachment}]" in job["message"]
        assert job["input_message"]["content_parts"] == request["arguments"]["message"]["content_parts"]


@pytest.mark.parametrize("boundary", ["marker", "size_limit", "name_limit", "mime_limit", "source_limit"])
def test_attachment_fix_file_reference_serialization_bounds(tmp_path, monkeypatch, boundary):
    """文件引用序列化接受大小上限且拒绝非法标记与超长字段"""
    from management_portal.society_service import workspace_contracts
    attachment = tmp_path / ("unsafe].txt" if boundary == "marker" else "file.dat")
    attachment.write_bytes(b"small file")
    part = {"type": "file", "path": str(attachment)}
    if boundary == "size_limit":
        monkeypatch.setattr(workspace_contracts, "CHAT_ATTACHMENT_MAX_BYTES", attachment.stat().st_size)
        result = resident_media_domain.resident_input_media([part], actor=ACTOR, resident_id="resident_000000000004", content="fixture", workspace_roots=[tmp_path])
        assert result[0]["media_type"] == "file" and result[0]["source"] == str(attachment)
        return
    if boundary == "name_limit":
        part["name"] = "a" * (MAX_INPUT_MEDIA_NAME_CHARS + 1)
    elif boundary == "mime_limit":
        part["mime_type"] = "a" * (MAX_INPUT_MEDIA_MIME_CHARS + 1)
    elif boundary == "source_limit":
        part["path"] = "/" + "a" * MAX_INPUT_MEDIA_SOURCE_CHARS
    with pytest.raises(ValueError, match="invalid Resident input file"):
        resident_media_domain.resident_input_media([part], actor=ACTOR, resident_id="resident_000000000004", content="fixture", workspace_roots=[tmp_path])


def test_attachment_fix_bootstrap_preserves_message_and_file_reference(isolated_team, monkeypatch):
    """首次会话启动保留原消息与文件引用且不展开附件正文"""
    team = isolated_team
    monkeypatch.setattr(resident_protocol, "deliver_resident_input", CANONICAL_RESIDENT_INPUT)
    record = resident_workspace_domain.find_resident(team.target["resident_id"])
    record["session_state"] = "idle"
    resident_workspace_domain.save_resident_record(record)
    attachment = Path(record["default_cwd"]) / "bootstrap.txt"
    attachment.write_text("isolated bootstrap file")
    request = attachment_fix_request(team, [{"type": "file", "path": str(attachment)}])
    result = deliver(request)
    assert result.status == "committed", result.to_payload()
    job = resident_execution_work.read(resident_workspace_domain.find_resident(record["resident_id"]))
    assert job["bootstrap_session"] is True
    assert job["input_message"]["content_parts"] == request["arguments"]["message"]["content_parts"]
    assert job["input_media"] == []
    from management_portal.society_service.resident_execution_domain import _prepare_resident_prompt
    job = _prepare_resident_prompt(record, job)
    assert f"[file: {attachment}]" in job["actual_sent_prompt"]
    assert "isolated bootstrap file" not in job["actual_sent_prompt"]


def test_attachment_fix_malformed_media_rejected_before_provider_dispatch(isolated_team):
    """媒体输入结构无效时在持久化和 Provider 派发前拒绝"""
    team = isolated_team
    record = resident_workspace_domain.find_resident(team.target["resident_id"])
    record["backend"] = "provider_api"
    resident_workspace_domain.save_resident_record(record)
    with pytest.raises(ValueError, match="input_media must be an array"):
        resident_intake_domain.persist_resident_input(record["resident_id"], ACTOR, "fixture input", activation_policy="enqueue_and_trigger", input_media={"type": "file", "path": "/nonexistent"})
    assert_attachment_fix_not_persisted(team)


def test_notification_index_produces_browser_contract_from_real_owner(isolated_team, monkeypatch):
    """通知索引从权威服务生成浏览器条目且不泄露他人通知"""
    team = isolated_team
    instruction(team, "CreateNotification", {"lineage_id": team.lineage_id, "title": "Helper04 browser notification", "message": "Only isolated notice bytes", "notification_id": "human_notice_helper04_browser"})
    monkeypatch.setattr(conversation_index_projection, "get_society_service", lambda: SimpleNamespace(lineage=SimpleNamespace(list_lineage_human_notifications=lineage_domain.list_lineage_human_notifications)))
    entries = conversation_index_projection.lineage_human_notification_entries(ACTOR, {team.lineage_id})
    assert len(entries) == 1
    assert entries[0]["title"] == "Helper04 browser notification"
    assert entries[0]["subtitle"] == "Only isolated notice bytes"
    assert entries[0]["url"] == f"/lineage?lineage={team.lineage_id}"
    assert not conversation_index_projection.lineage_human_notification_entries("fixture-stranger", {team.lineage_id})
    destination = os.environ.get("WALL07_HELPER04_ARTIFACT_DIR")
    if destination:
        Path(destination, "wall07-notification-browser-fixture.json").write_text(json.dumps({"entries": entries, "unread_count": 1, "schema": "tfi.portal.agent-conversation-index.v1", "content_signature": "helper04-isolated-notice"}, ensure_ascii=False))


@pytest.mark.parametrize("attachment_kind", ["image", "context_reference"])
def test_attachment_positive_control_uses_canonical_owner_with_closed_intake(isolated_team, monkeypatch, attachment_kind):
    """接收关闭时图片与上下文引用仍完整进入权威排队消息"""
    team = isolated_team
    monkeypatch.setattr(resident_protocol, "deliver_resident_input", CANONICAL_RESIDENT_INPUT)
    target_id = team.target["resident_id"]
    resident_protocol.change_resident_state(target_id, ACTOR, state_domain="intake", transition="close")
    root = Path(team.target["default_cwd"])
    if attachment_kind == "image":
        attachment = root / "pixel.png"
        attachment.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="))
        part = {"type": "image", "path": str(attachment)}
    else:
        part = {"type": "context_reference", "context": {"context_text": "Isolated runtime context", "working_directory": str(root)}}
    event = lineage_primitives.make_event("manual.triggered", payload={"prompt": "One invocation", "content_parts": [part]})
    invocation = lineage_program_effect_rules._make_program_effect_invocation(event, source_kind="event", source_id="event_source_helper04", include_envelope=False)
    definition = delivery_effect(target_id, fixed="Fixed contract")
    baseline = deepcopy(definition)
    lowered = lower_effect_call(team.lineage_id, "effect_helper04", definition, invocation)
    assert lowered["arguments"]["message"]["content_parts"] == [{"type": "text", "text": "Fixed contract\n\nOne invocation"}, part]
    result = deliver(lowered)
    assert result.status == "committed", result.to_payload()
    job = resident_execution_work.read(resident_workspace_domain.find_resident(target_id))
    assert job["input_message"]["content_parts"] == lowered["arguments"]["message"]["content_parts"]
    assert job["input_message"]["author_id"] == ACTOR
    assert job["activation_policy"] == "enqueue_and_trigger"
    messages = get_agent_data_store().read_chat_messages(target_id)
    assert messages == []
    assert definition == baseline
    with state_store.get_lineage_data_store().database.read() as connection:
        inputs = connection.execute('SELECT state FROM conversation_input_states WHERE resident_id=%s', (target_id,)).fetchall()
    assert [value[0] for value in inputs] == ["queued"]


def test_handoff_copy_excludes_file_and_directory_symlinks(tmp_path):
    """Workspace Handoff 复制排除文件和目录符号链接"""
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (external / "private.txt").write_text("synthetic external data")
    (source / "regular.txt").write_text("copied")
    (source / "file-link").symlink_to(external / "private.txt")
    (source / "directory-link").symlink_to(external, target_is_directory=True)
    result = lineage_handoff_backend.materialize_handoff_item({"source": {"path": str(source)}, "target": {"path": str(target)}, "transfer": {"mode": "copy", "method": "filesystem_copy"}}, allowed_target_roots=[tmp_path], checked_at="2026-09-12T00:00:00Z")
    assert (target / "regular.txt").read_text() == "copied"
    assert not (target / "file-link").exists()
    assert not (target / "directory-link").exists()
    assert result["file_count"] == 1


@pytest.mark.parametrize("boundary", ["files", "bytes"])
def test_handoff_manifest_exact_limits_with_virtual_stat(tmp_path, monkeypatch, boundary):
    """交接清单接受文件数与字节上限且拒绝超出一项"""
    root = tmp_path / "source"
    root.mkdir()
    marker = root / "one.txt"
    marker.write_text("")
    original_stat = Path.stat
    count = 60000 if boundary == "files" else 1
    size = 0 if boundary == "files" else 2 * 1024 ** 3
    monkeypatch.setattr(lineage_handoff_backend.os, "walk", lambda *args, **kwargs: iter([(str(root), [], ["one.txt"] * count)]))

    def virtual_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        return SimpleNamespace(st_mode=result.st_mode, st_size=size) if path == marker else result

    monkeypatch.setattr(Path, "stat", virtual_stat)
    files, byte_size, digest = lineage_handoff_backend._manifest(root, max_files=60000, max_bytes=2 * 1024 ** 3)
    assert len(files) == count
    assert byte_size == count * size
    assert digest.startswith("sha256:")
    if boundary == "files":
        count += 1
    else:
        size += 1
    with pytest.raises(ValueError, match="超过"):
        lineage_handoff_backend._manifest(root, max_files=60000, max_bytes=2 * 1024 ** 3)


def test_conversation_snapshot_and_latest_input_cost_stay_bounded_as_history_grows(history_repository, monkeypatch):
    """会话历史增长时快照和最新输入读取查询成本保持有界"""
    repository = history_repository
    repository.agent_data.display_chat_message(A, {"message_id": "visible", "role": "user", "content_parts": [{"type": "text", "text": "fixed visible page"}]})
    observations = []
    # More history than any live Resident holds.  Each inserted row's counter
    # trigger walks the versions this transaction already wrote, so a far larger
    # single-transaction insert (120000 rows) took minutes without testing more.
    for extra_history in (0, 5000):
        if extra_history:
            with repository.state_store.database.transaction() as connection:
                connection.execute(
                    "INSERT INTO conversation_input_states "
                    "SELECT %s, 'extra_' || lpad(index::text, 6, '0'), "
                    "(ARRAY['completed','failed','cancelled'])[index %% 3 + 1], '{}', '2026-08-01T00:00:00Z' "
                    "FROM generate_series(0, %s - 1) AS index",
                    (A, extra_history),
                )
        with query_work(repository, monkeypatch) as snapshot_work:
            snapshot = repository.snapshot(A, history_limit=1)
        with query_work(repository, monkeypatch) as state_work:
            states = repository.state_snapshots([A, B])
        observations.append((snapshot, states, snapshot_work["query_count"], state_work["query_count"]))
    before, after = observations
    assert before[:2] == after[:2]
    assert {value["message_id"] for value in after[0]["input_states"]} == {"visible", "queued", "running"}
    assert after[1][A]["latest_input_state"]["message_id"] == "visible"
    assert after[2] <= before[2] + 2
    assert after[3] <= before[3] + 2


def test_active_projection_skips_idle_bindings_and_rechecks_state_and_permissions(monkeypatch):
    """活跃投影跳过空闲绑定且每次重新检查状态与读取权限"""
    resident_ids = [f"resident_{index:012x}" for index in range(1, 4)]
    records = [{"resident_id": resident_id, "title": f"Sensitive {index}"} for index, resident_id in enumerate(resident_ids)]
    activity = {resident_ids[0]: {"status": "idle"}, resident_ids[1]: {"status": "idle", "queued_messages": [{"message_id": "queued"}]}, resident_ids[2]: {"status": "running", "message_id": "active"}}
    binding_reads, activity_reads, permission_reads = [], [], []
    permitted = {resident_ids[1]}
    monkeypatch.setattr(active_agent_projection.resident_catalog, "get_catalog", lambda: SimpleNamespace(
        state_store=SimpleNamespace(resident_work_ids=lambda: resident_ids),
        get_identity_records=lambda ids: [deepcopy(record) for record in records if record["resident_id"] in ids],
    ))
    monkeypatch.setattr(resident_directory_domain, "is_active_actor", lambda actor: False)
    monkeypatch.setattr(active_agent_projection, "_capacity_snapshot", lambda: (2, {}))

    def read_activity(ids):
        activity_reads.append(list(ids))
        return deepcopy(activity)

    def read_account(record):
        binding_reads.append(record["resident_id"])
        return "two"

    def readable(instruction, parameter, ids, *, actor, **kw):
        permission_reads.append((list(ids), actor))
        return set(ids) & permitted

    monkeypatch.setattr(active_agent_projection.resident_protocol, "read_resident_activity", read_activity)
    monkeypatch.setattr(active_agent_projection, "_resident_account_id", read_account)
    from management_portal import permission_system
    monkeypatch.setattr(permission_system, 'readable_ids', readable)
    first = active_agent_projection.collect(actor="fixture-viewer")
    assert first["status"] == "ok", first["error"]
    assert binding_reads == resident_ids[1:]
    assert first["queued_runs"][0]["resident_id"] == resident_ids[1]
    assert "resident_id" not in first["running_runs"][0]
    assert first["running_runs"][0]["title"] == "Agent"
    activity[resident_ids[1]] = {"status": "idle"}
    permitted.clear()
    permitted.add(resident_ids[2])
    second = active_agent_projection.collect(actor="fixture-viewer")
    assert binding_reads == resident_ids[1:] + [resident_ids[2]]
    assert second["queued"] == 0
    assert second["running_runs"][0]["resident_id"] == resident_ids[2]
    assert len(activity_reads) == len(permission_reads) == 2
    assert all(actor == "fixture-viewer" for _, actor in permission_reads)
