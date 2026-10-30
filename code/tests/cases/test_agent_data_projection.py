from __future__ import annotations
from tests.cases.agent_data_test_support import session_transcript_ids

from pathlib import Path
import json
from dataclasses import replace
from copy import deepcopy
from datetime import datetime

import pytest

from management_portal.execution.agent_data_projection import AgentDataProjector
from management_portal.execution.agent_data_store import AgentDataStore, stable_segment_id
from management_portal.execution.backend_contracts import BackendInvocation
from management_portal.execution.contracts import ExecutionRequest, SessionOptions, InputMedia
from management_portal.execution.agent_data_input import input_message
from management_portal.execution.codex_history import response_items_from_segments
from management_portal.execution.message_projection import project_message
from management_portal.execution.session_manager import RuntimeSession


def _turn_request(agent_id: str) -> ExecutionRequest:
    return ExecutionRequest.new(
        actor="sample_admin",
        prompt="inspect the code",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        agent_id=agent_id,
        correlation_id="job_projection",
        session=SessionOptions(
            mode="persistent",
            binding_id=f"resident:{agent_id}",
            backend_route_id="zero",
        ),
    )


def _runtime_session(
    request: ExecutionRequest,
    backend_session_id: str,
    *,
    is_new: bool = False,
) -> RuntimeSession:
    return RuntimeSession(
        mode="persistent",
        binding_id=request.session.binding_id,
        backend_session_id=backend_session_id,
        backend_route_id="zero",
        is_new_backend_session=is_new,
    )


def _invocation(
    request: ExecutionRequest,
    session: RuntimeSession,
) -> BackendInvocation:
    return BackendInvocation(
        request=request,
        session=session,
        user_prompt=request.prompt,
        system_context="",
        attach_system_context=False,
        is_new_backend_session=session.is_new_backend_session,
    )


def _deliver_captured_message(store: AgentDataStore, agent_id: str, segment_id: str) -> None:
    segment = store.segment(segment_id)
    message = project_message(segment_id, segment["metadata"]["message"], segment["content"])
    store.project_chat_message(agent_id, message)


@pytest.mark.parametrize("envelope", [False, True])
@pytest.mark.parametrize("with_media", [False, True])
def test_chat_projects_quoted_input_after_provider_composition(tmp_path, envelope, with_media):
    """带引用的输入在 Chat 中只显示一次引用

    Provider 把引用块拼进提示词（可带 Portal 运行时外壳和图片）后，Chat 投影仍显示原正文和一份引用，消息 ID 不变。

    :module: resident.conversation
    :importance: P1
    :design: decision_c524c775e0be
    """
    from management_portal.execution.message_projection import wrap_message

    store = AgentDataStore()
    agent_id = "resident_aaaaaaaaaaaa"
    message_id = "msg_" + "b" * 32
    quote = {"message_id": "msg_" + "c" * 32, "author_id": agent_id, "text": "原回复\n第二段依据"}
    body = "请继续检查这一点。"
    original = {"message_id": message_id, "role": "user", "author_id": "CASE-A", "quote": quote,
                "content_parts": [{"type": "text", "text": body}]}
    store.display_chat_message(agent_id, original)
    quoted_input = (f'<quoted-message author="{agent_id}" author_title="Family 架构与实现" '
                    f'message_id="{quote["message_id"]}">\n{quote["text"]}\n</quoted-message>\n\n{body}')
    prompt = "Portal runtime context\n" + wrap_message(quoted_input) if envelope else quoted_input
    request = replace(_turn_request(agent_id), prompt=prompt, metadata={"user_message_id": message_id})
    if with_media:
        image = tmp_path / "image.png"
        image.write_bytes(b"image-bytes")
        request = replace(request, input_media=(InputMedia(
            media_type="image", source_type="local_path", source=str(image), mime_type="image/png",
        ),))
    session = _runtime_session(request, "quote-session")
    projector = AgentDataProjector(store)
    projector.begin(request, session)
    for observed in (False, True):
        if observed:
            projector.observe_invocation(request.execution_id, _invocation(request, session))
        shown = store.chat_message(agent_id, message_id)
        assert shown["content_parts"] == [{"type": "text", "text": body}]
        assert shown["quote"] == quote
        assert shown["message_id"] == message_id
        assert store.segment(shown["segment_id"])["content"] == (
            input_message(prompt, request.input_media) if observed and with_media else prompt
        )


def test_chat_keeps_inline_images_through_resident_input_composition():
    """Resident 输入组合后 Chat 消息仍按原位置保留内联图片"""
    from management_portal.execution.message_projection import wrap_message
    from management_portal.society_service.resident_protocol import _input_parts

    store = AgentDataStore()
    agent_id = "resident_inline_images"
    message_id = "msg_inline_images"
    original = {"message_id": message_id, "role": "user", "author_id": "sample_admin", "content_parts": [
        {"type": "text", "text": "1. First image: "},
        {"type": "image", "upload_id": "upload_first"},
        {"type": "text", "text": "\n2. Second image: "},
        {"type": "image", "upload_id": "upload_second"},
        {"type": "text", "text": "\n3. End.\n"},
    ]}
    store.display_chat_message(agent_id, original)
    assert store.chat_message(agent_id, message_id)["content_parts"] == original["content_parts"]
    content, *_ = _input_parts(original)
    request = replace(_turn_request(agent_id), prompt=wrap_message(content), metadata={"user_message_id": message_id})
    session = _runtime_session(request, "inline-images-session")
    projector = AgentDataProjector(store)
    projector.begin(request, session)
    assert store.chat_message(agent_id, message_id)["content_parts"] == original["content_parts"]


@pytest.mark.parametrize("with_quote", [False, True])
def test_file_projection_preserves_inline_order_and_authored_markers(with_quote):
    """文件展示保留原位引用与用户正文

    :module: resident.conversation
    """
    from management_portal.execution.message_projection import message_facts, quote_block, wrap_message
    from management_portal.society_service.resident_media_domain import (
        resident_message_with_file_references as _resident_message_with_file_references,
    )

    upload_id = "upload_" + "a" * 24
    source = f"/uploads/{upload_id}/manuscript.docx"
    local = "/workspace/notes.txt"
    parts = [
        {"type": "text", "text": f"\n前文，原样保留 [file: {local}]\n"},
        {"type": "file", "upload_id": upload_id, "name": "manuscript.docx"},
        {"type": "text", "text": ""},
        {"type": "file", "source": local},
        {"type": "text", "text": "\n后文）\n"},
        {"type": "file", "path": "/workspace/second.txt"},
    ]
    message = {"role": "user", "content_parts": parts}
    body = "\n".join(part["text"] for part in parts if part["type"] == "text")
    composed = _resident_message_with_file_references(body, [
        {"media_type": "file", "source": path} for path in (source, local, "/workspace/second.txt")
    ])
    if with_quote:
        message["quote"] = {"author_id": "CASE-A", "text": "讨论附件"}
        composed = quote_block(message["quote"]) + "\n\n" + composed
    facts = message_facts(message)
    for content in (body, wrap_message(composed)):
        assert project_message("segment", facts, content)["content_parts"] == parts


@pytest.mark.parametrize("mismatch", ["upload", "name", "path", "extra_text", "boundary", "no_lengths", "no_file", "agent"])
def test_file_projection_keeps_unrecognized_annotations(mismatch):
    """不隐藏未匹配的文件路径与变化正文

    :module: resident.conversation
    """
    from management_portal.execution.message_projection import message_facts, wrap_message

    source = "/uploads/upload_aaaaaaaaaaaaaaaaaaaaaaaa/paper.docx"
    message = {"role": "user", "content_parts": [
        {"type": "text", "text": "before"},
        {"type": "file", "upload_id": "upload_aaaaaaaaaaaaaaaaaaaaaaaa", "name": "paper.docx"},
        {"type": "text", "text": "after"},
    ]}
    body = "before\nafter"
    if mismatch == "upload":
        source = source.replace("upload_a", "upload_b")
    elif mismatch == "name":
        source = source.replace("paper.docx", "other.docx")
    elif mismatch == "path":
        message["content_parts"][1] = {"type": "file", "path": "/workspace/other.docx"}
    elif mismatch == "no_file":
        message["content_parts"].pop(1)
    elif mismatch == "agent":
        message["role"] = "agent"
    facts = message_facts(message)
    if mismatch == "no_lengths":
        for part in facts["parts"]:
            part.pop("length", None)
    composed = body + f"\n\n[file: {source}]"
    if mismatch == "extra_text":
        composed += "\nnew text"
    elif mismatch == "boundary":
        composed = "changed " + composed
    wrapped = wrap_message(composed)
    result = project_message("segment", facts, wrapped)
    assert result["content_parts"][0]["text"] == (wrapped if mismatch == "agent" else composed)


def test_quote_projection_preserves_literal_markup_and_strips_only_the_added_quote():
    """只剥离 Portal 加上的引用块，正文中的同名标签保留

    正文本身写了 quoted-message 标签时，投影只去掉 Portal 添加的那一个引用块，正文原样保留。

    :module: resident.conversation
    :importance: P1
    :design: decision_c524c775e0be
    """
    from management_portal.execution.message_projection import message_facts, project_message, quote_block, wrap_message

    quote = {"author_id": "CASE-A", "text": "讨论 </quoted-message> 的语法"}
    literal = quote_block(quote) + "\n\n这段标签是用户正文中的示例。"
    message = {"message_id": "literal", "role": "user", "quote": quote,
               "content_parts": [{"type": "text", "text": literal}]}
    facts = message_facts(message)
    for content in (wrap_message(literal), wrap_message(quote_block(quote) + "\n\n" + literal)):
        shown = project_message("segment", facts, content)
        assert shown["content_parts"] == message["content_parts"]
        assert shown["quote"] == quote


@pytest.mark.parametrize("mismatch", ["author_id", "message_id", "text", "damaged", "extra_attribute", "no_quote", "agent"])
def test_quote_projection_keeps_unrecognized_or_mismatched_envelopes(mismatch):
    """与原引用不一致的引用块按正文保留

    引用块的作者、消息 ID、摘录不一致，标签损坏、带额外属性、消息没有引用或作者是 Agent 时，投影不剥离这段内容。

    :module: resident.conversation
    :importance: P1
    :design: decision_c524c775e0be
    """
    from management_portal.execution.message_projection import message_facts, project_message, quote_block, wrap_message

    quote = {"author_id": "CASE-A", "message_id": "quoted", "text": "原始摘录"}
    message = {"message_id": "request", "role": "user", "quote": quote,
               "content_parts": [{"type": "text", "text": "真正的正文"}]}
    displayed_quote = dict(quote)
    if mismatch in displayed_quote:
        displayed_quote[mismatch] = "另一条内容"
    content = quote_block(displayed_quote, author_title='带有 "引号" & 字符的名称') + "\n\n真正的正文"
    if mismatch == "damaged":
        content = content.replace("</quoted-message>", "</broken>")
    elif mismatch == "extra_attribute":
        content = content.replace("<quoted-message ", '<quoted-message forged="true" ')
    elif mismatch == "no_quote":
        message.pop("quote")
    elif mismatch == "agent":
        message["role"] = "agent"
    wrapped = wrap_message(content)
    shown = project_message("segment", message_facts(message), wrapped)
    assert shown["content_parts"] == [{"type": "text", "text": wrapped if mismatch == "agent" else content}]


@pytest.mark.parametrize('harness_prefix', ['', 'Harness instructions\n'])
def test_uncompacted_fork_image_is_frozen_in_agent_data(tmp_path, harness_prefix):
    """输入图片在 AgentData 中冻结为 base64，原文件删除后 Provider 上下文仍含该图片"""
    store = AgentDataStore()
    image = tmp_path / 'picture.png'
    image.write_bytes(b'original-image-bytes')
    request = replace(_turn_request('resident_image'), input_media=(
        InputMedia(media_type='image', source_type='local_path', source=str(image), mime_type='image/png'),
    ))
    session = _runtime_session(request, 'image-thread')
    projector = AgentDataProjector(store)
    projector.begin(request, session)
    projector.observe_invocation(request.execution_id, replace(
        _invocation(request, session), user_prompt=harness_prefix + request.prompt,
    ))
    projector.complete(execution_id=request.execution_id, backend_session_id='image-thread',
                       backend_route_id='zero', turn_id='image-turn', response='image received')
    store.finish_execution(request.execution_id, status="completed", usage=store.execution(request.execution_id)["usage"])
    image.unlink()
    snapshot = store.provider_context_snapshot(provider='codex', backend_route_id='zero',
                                               backend_session_id='image-thread', agent_id='resident_image')
    items = response_items_from_segments(snapshot['segments'])
    inputs = [item for item in items if item.get('role') == 'user']
    assert len(inputs) == 1
    assert inputs[0]['content'] == [
        {'type': 'input_text', 'text': harness_prefix + request.prompt},
        {'type': 'input_image', 'image_url': 'data:image/png;base64,b3JpZ2luYWwtaW1hZ2UtYnl0ZXM='},
    ]


@pytest.mark.parametrize('harness_prefix', ['', '<repo-map>current repository</repo-map>\n'])
def test_fork_seed_and_new_input_remain_distinct_database_context_members(tmp_path, harness_prefix):
    """Fork 继承的压缩上下文与新输入分别进入会话上下文，被压缩的旧片段不在其中"""
    store = AgentDataStore()
    old = store.put_segment(segment_type='model', content='obsolete-original')
    compacted = store.put_segment(segment_type='compaction', content={'replacement_history': ['compacted-summary']})
    fourth = store.put_segment(segment_type='model', content='segment-4-link')
    store.append_agent_segments('resident_source', [old, compacted, fourth], actor_ref='sample_admin')
    store.fork_agent(source_agent_id='resident_source', target_agent_id='resident_child', actor_ref='sample_admin', segment_ids=store.sequence_segment_ids(store.trace_sequence_id('resident_source')))
    request = replace(_turn_request('resident_child'),
                      prompt='compacted-summary\nsegment-4-link\nsegment-5',
                      metadata={'fork_context_input': {
                          'segments': [{'segment_id': compacted}, {'segment_id': fourth}],
                          'prompt': 'segment-5',
                      }})
    session = _runtime_session(request, 'child-session', is_new=True)
    projector = AgentDataProjector(store)
    projector.begin(request, session)
    projector.observe_invocation(request.execution_id, replace(
        _invocation(request, session), user_prompt=harness_prefix + request.prompt,
    ), [])
    projector.complete(execution_id=request.execution_id, backend_session_id='child-session',
                       backend_route_id='zero', turn_id='child-turn', response='child-answer')
    store.finish_execution(request.execution_id, status="completed", usage=store.execution(request.execution_id)["usage"])
    snapshot = store.provider_context_snapshot(provider='codex', backend_route_id='zero',
                                               backend_session_id='child-session', agent_id='resident_child')
    segments = snapshot['segments']
    ids = [item['segment_id'] for item in segments]
    assert old not in ids
    assert ids.index(compacted) < ids.index(fourth)
    current = next(item for item in segments if item['content'] == harness_prefix + 'segment-5')
    assert ids.index(fourth) < ids.index(current['segment_id'])
    trace = store.read_sequence(store.trace_sequence_id('resident_child'), limit=50)['segments']
    assert [item['segment_id'] for item in trace[:3]] == [old, compacted, fourth]
    assert sum('segment-4-link' in str(item['content']) for item in trace) == 1
    # The trace marks the inherited context right before the turn's input,
    # so the session's transcript read from it starts with that context.
    marks = [index for index, item in enumerate(trace) if item['metadata'].get('kind') == 'context_seed']
    assert marks == []
    assert trace[3]['segment_type'] == 'input'
    held = session_transcript_ids(store, snapshot['provider_session_ref'])
    assert held[:2] == [compacted, fourth] and old not in held


@pytest.mark.parametrize("recover_projector", [False, True])
@pytest.mark.parametrize("with_media", [False, True])
def test_harness_preparation_reports_have_immutable_replay_identity(tmp_path, recover_projector, with_media):
    """Harness 准备报告不改变输入身份，恢复或重复观测时只保留一条有效输入"""
    store = AgentDataStore()
    request = _turn_request("resident_harness_recovery")
    if with_media:
        image = tmp_path / "image.png"
        image.write_bytes(b"test-image")
        request = replace(request, input_media=(InputMedia(
            media_type="image", source_type="local_path", source=str(image), mime_type="image/png",
        ),))
    session = _runtime_session(request, "provider-session")
    invocation = replace(_invocation(request, session), user_prompt="prepared input")
    first_reports = [{"feature_id": "governor", "prepare_ms": 0.091}]
    next_reports = [{"feature_id": "governor", "prepare_ms": 0.019}]
    content = input_message(invocation.user_prompt, request.input_media) if with_media else invocation.user_prompt
    provider_session = store.ensure_provider_session(
        provider=request.provider, backend_route_id=session.backend_route_id,
        backend_session_id=session.backend_session_id, agent_id=request.agent_id, actor_ref=request.actor,
    )
    projector = AgentDataProjector(store)
    accepted_at = "2026-09-12T10:28:09.908Z"
    projector.begin(request, session, created_at=accepted_at)
    projector.observe_invocation(request.execution_id, invocation, first_reports)
    projector.observe_runtime_event(request.execution_id, "provider_output", {
        "id": "before-recovery", "text": "working", "phase": "commentary",
    })
    _deliver_captured_message(store, request.agent_id, stable_segment_id(
        "provider-model", request.execution_id, "before-recovery", "message",
    ))
    if recover_projector:
        projector = AgentDataProjector(store)
        projector.begin(request, session, created_at=accepted_at)
    projector.observe_invocation(request.execution_id, invocation, next_reports)
    segment_ids = store.sequence_segment_ids(store.trace_sequence_id(request.agent_id))
    preparations = [store.segment(identity) for identity in segment_ids
                    if store.segment(identity)["metadata"].get("kind") == "effective_provider_input"]
    assert len(preparations) == 1
    assert "features" not in preparations[0]["metadata"]
    assert "hidden_from_chat" not in preparations[0]["metadata"]
    projector.observe_invocation(request.execution_id, invocation, deepcopy(next_reports))
    assert store.sequence_segment_ids(store.trace_sequence_id(request.agent_id)) == segment_ids
    projector.complete(
        execution_id=request.execution_id, backend_session_id=session.backend_session_id,
        backend_route_id=session.backend_route_id, turn_id="recovery-turn", response="received",
    )
    store.finish_execution(request.execution_id, status="completed", usage=store.execution(request.execution_id)["usage"])
    snapshot = store.provider_context_snapshot(
        provider=request.provider, backend_route_id=session.backend_route_id,
        backend_session_id=session.backend_session_id, agent_id=request.agent_id,
    )
    inputs = [item for item in response_items_from_segments(snapshot["segments"])
              if item.get("role") == "user"]
    assert "features" not in snapshot["segments"][0]["metadata"]
    assert snapshot["segments"][1]["content"] == "working"
    assert len(inputs) == 1
    if with_media:
        assert inputs[0] == content
    else:
        assert inputs[0]["content"] == [{"type": "input_text", "text": content}]


def test_execution_input_is_idempotent_across_claim_recovery_contracts(tmp_path, subtests, isolated_portal_case):
    """认领恢复后重放输入和 Provider 输出不产生重复片段，以第一条最终回复为准"""
    with subtests.test(contract='test_execution_input_is_idempotent_across_claim_recovery'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = AgentDataStore()
        request = _turn_request("resident_recovered_execution")
        session = _runtime_session(request, "provider-session")
        accepted_at = "2026-08-23T12:34:56.789Z"

        AgentDataProjector(store).begin(
            request,
            session,
            created_at=accepted_at,
        )
        AgentDataProjector(store).begin(
            request,
            session,
            created_at=accepted_at,
        )

        trace_ids = store.sequence_segment_ids(store.trace_sequence_id(request.agent_id))
        assert len(trace_ids) == 1
        segment = store.segment(trace_ids[0])
        assert segment["content"] == request.prompt
        assert datetime.fromisoformat(segment["started_at"]) == datetime.fromisoformat(accepted_at)
    with subtests.test(contract='test_provider_replay_is_idempotent_without_transport_identity_storage'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = AgentDataStore()
        request = _turn_request("resident_replayed_provider_item")
        session = _runtime_session(request, "provider-session")
        event = {"id": "provider-item-1", "text": "working", "phase": "commentary"}
        accepted_at = "2026-08-24T00:00:00.000Z"

        first = AgentDataProjector(store)
        first.begin(request, session, created_at=accepted_at)
        first.observe_runtime_event(request.execution_id, "provider_output", event)

        recovered = AgentDataProjector(store)
        recovered.begin(request, session, created_at=accepted_at)
        recovered.observe_runtime_event(request.execution_id, "provider_output", event)

        captured = store.segment(stable_segment_id(
            "provider-model", request.execution_id, "provider-item-1", "message",
        ))
        assert captured["content"] == "working"
        assert "provider_item_id" not in captured["metadata"]
        assert captured["segment_id"] not in store.sequence_segment_ids(store.trace_sequence_id(request.agent_id))
    with subtests.test(contract='test_first_provider_final_is_canonical_when_later_final_is_observed'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = AgentDataStore()
        projector = AgentDataProjector(store)
        request = _turn_request("resident_multiple_finals")
        session = _runtime_session(request, "provider-session")
        projector.begin(request, session)

        for item_id, text in (
            ("answer", "full answer with the command list"),
            ("ack", "reply after running the first command group"),
        ):
            projector.observe_runtime_event(
                request.execution_id,
                "provider_output",
                {
                    "id": item_id,
                    "text": text,
                    "final": True,
                },
            )

        final_segment_id = projector.complete(
            execution_id=request.execution_id,
            backend_session_id="provider-session",
            backend_route_id="zero",
            turn_id="turn_multiple_finals",
            response="reply after running the first command group",
        )
        store.finish_execution(request.execution_id, status="completed", usage=store.execution(request.execution_id)["usage"])

        assert store.segment(final_segment_id)["content"] == (
            "full answer with the command list"
        )
        assert store.segment(stable_segment_id(
            "provider-model", request.execution_id, "ack", "final",
        ))["content"] == "reply after running the first command group"
        assert final_segment_id not in store.sequence_segment_ids(store.trace_sequence_id(request.agent_id))


def test_new_persistent_session_never_uses_a_binding_as_provider_identity(
    tmp_path: Path,
) -> None:
    """新持久会话在 Provider 绑定实际线程前不以 binding 建立 Provider 会话记录"""
    store = AgentDataStore()
    projector = AgentDataProjector(store)
    request = _turn_request("resident_new_session")
    session = _runtime_session(request, "", is_new=True)

    projector.begin(request, session)
    projector.observe_invocation(
        request.execution_id,
        _invocation(request, session),
        [],
    )
    projector.observe_runtime_event(
        request.execution_id,
        "provider_output",
        {"id": "output_before_bind", "text": "done", "final": True},
    )
    with store.database.read() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM provider_sessions"
        ).fetchone()[0] == 0

    projector.observe_runtime_event(
        request.execution_id,
        "provider_input_bound",
        {
            "provider": "codex",
            "backend_session_id": "thread_actual",
            "backend_route_id": "zero",
            "turn_id": "turn_actual",
        },
    )
    final_segment_id = projector.complete(
        execution_id=request.execution_id,
        backend_session_id="thread_actual",
        backend_route_id="zero",
        turn_id="turn_actual",
        response="done",
    )
    store.finish_execution(request.execution_id, status="completed", usage=store.execution(request.execution_id)["usage"])
    _deliver_captured_message(store, request.agent_id, final_segment_id)

    with store.database.read() as connection:
        sessions = connection.execute(
            "SELECT backend_session_id FROM provider_sessions"
        ).fetchall()
    assert [row["backend_session_id"] for row in sessions] == ["thread_actual"]
    transcript = store.session_transcript(
        provider="codex",
        backend_session_id="thread_actual",
        backend_route_id="zero",
    )
    assert [item["content"] for item in transcript["items"]] == [
        "inspect the code",
        "done",
    ]


def test_pre_turn_compaction_replaces_context_before_new_input_is_bound(
    tmp_path: Path,
) -> None:
    """轮次开始前的压缩先替换会话上下文，新输入和回复接在压缩结果之后"""
    store = AgentDataStore()
    agent_id = "resident_compaction"
    provider_session = store.ensure_provider_session(
        provider="codex",
        backend_route_id="zero",
        backend_session_id="thread_existing",
        agent_id=agent_id,
        actor_ref="sample_admin",
    )
    session_ref = provider_session["provider_session_ref"]
    # An earlier turn of the session left this in its context.
    store.begin_execution(
        execution_id="execution-old-turn", agent_id=agent_id, actor_ref="sample_admin",
        provider="codex", started_at="2026-08-23T00:00:00Z",
    )
    store.bind_execution_provider_session("execution-old-turn", provider_session_ref=session_ref)
    store.put_segment(
        segment_id="segment_old_context",
        segment_type="model",
        content="old context",
        started_at="2026-08-23T00:00:00Z",
        finished_at="2026-08-23T00:00:00Z",
        created_by_actor_ref="sample_admin",
        performed_by_actor_ref="codex",
        created_by_agent_id=agent_id,
        execution_id="execution-old-turn",
    )
    store.append_agent_segments(agent_id, ['segment_old_context'], actor_ref='codex')
    projector = AgentDataProjector(store)
    request = _turn_request(agent_id)
    session = _runtime_session(request, "thread_existing")

    projector.begin(request, session)
    projector.observe_compaction(
        execution_id=request.execution_id,
        provider="codex",
        backend_session_id="thread_existing",
        backend_route_id="zero",
        content={
            "replacement_history": [
                {"type": "message", "role": "user", "content": "summary"},
            ]
        },
        created_at="2026-08-23T00:01:00Z",
    )
    projector.observe_invocation(
        request.execution_id,
        _invocation(request, session),
        [],
    )
    final_segment_id = projector.complete(
        execution_id=request.execution_id,
        backend_session_id="thread_existing",
        backend_route_id="zero",
        turn_id="turn_compacted",
        response="new answer",
    )
    store.finish_execution(request.execution_id, status="completed", usage=store.execution(request.execution_id)["usage"])
    _deliver_captured_message(store, agent_id, final_segment_id)

    context_ids = store.session_context_ids(session_ref)
    assert store.segment(context_ids[0])["segment_type"] == "compaction"
    assert store.segment(context_ids[0])["content"]["replacement_history"][0][
        "content"
    ] == "summary"
    assert store.segment(context_ids[1])["content"] == "inspect the code"
    assert context_ids[-1] == final_segment_id
    assert store.sequence_segment_ids(store.trace_sequence_id(agent_id))[0] == (
        "segment_old_context"
    )
    # The session compacted before it took the new input: read from the
    # trace, its transcript has the earlier turn, the compaction, then the input.
    held = session_transcript_ids(store, session_ref)
    assert held[:2] == ["segment_old_context", context_ids[0]]
    assert held.index(context_ids[1]) > 1
    with store.database.read() as connection:
        relation = connection.execute(
            "SELECT * FROM segment_relations WHERE relation_type = 'compressed_from'"
        ).fetchone()
    assert relation["source_segment_id"] == "segment_old_context"
    assert relation["target_segment_id"] == context_ids[0]
    execution = store.execution(request.execution_id)
    assert execution is not None
    assert execution["provider_session_ref"] == session_ref
    assert execution["provider_turn_id"] == "turn_compacted"
    assert execution["status"] == "completed"


def test_initial_fork_compact_projects_inherited_window_then_replacement(
    tmp_path: Path,
) -> None:
    """Fork 首轮前压缩时会话记录依次为继承上下文和压缩结果，上下文只含压缩结果"""
    store = AgentDataStore()
    agent_id = "resident_compacted_child"
    inherited = store.put_segment(
        segment_type="input",
        content="frozen inherited context",
        created_by_agent_id=agent_id,
    )
    store.append_agent_segments(agent_id, [inherited], actor_ref='sample_admin')
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="compact inherited context",
        service_type="agent_session",
        provider="codex",
        operation="compact",
        agent_id=agent_id,
        metadata={
            "initial_context_policy": "compact_before_first_turn",
            "fork_context_input": {"segments": [{"segment_id": inherited}]},
        },
        session=SessionOptions(
            mode="persistent",
            binding_id=f"resident:{agent_id}",
            backend_route_id="zero",
        ),
    )
    session = _runtime_session(request, "compacted-child-session")
    projector = AgentDataProjector(store)
    projector.begin(request, session)
    projector.observe_compaction(
        execution_id=request.execution_id,
        provider="codex",
        backend_session_id="compacted-child-session",
        backend_route_id="zero",
        content={"replacement_history": ["compressed summary"]},
    )
    compacted = projector.complete(
        execution_id=request.execution_id,
        backend_session_id="compacted-child-session",
        backend_route_id="zero",
        turn_id="",
        response="",
    )
    store.finish_execution(request.execution_id, status="completed", usage=store.execution(request.execution_id)["usage"])

    transcript = store.session_transcript(
        provider="codex",
        backend_session_id="compacted-child-session",
        backend_route_id="zero",
    )
    assert [item["segment_id"] for item in transcript["items"]] == [
        inherited,
        compacted,
    ]
    context = store.provider_context_snapshot(
        provider="codex",
        backend_session_id="compacted-child-session",
        backend_route_id="zero",
        agent_id=agent_id,
    )
    assert [item["segment_id"] for item in context["segments"]] == [compacted]
    # The trace marks where the session received the inherited context.
    trace = store.read_sequence(store.trace_sequence_id(agent_id))["segments"]
    assert [item["segment_id"] for item in trace] == [inherited, compacted]
    assert all(item["metadata"].get("kind") != "context_seed" for item in trace)
    assert all(item["metadata"].get("kind") != "context_seed" for item in trace)


def test_a_model_api_residents_inputs_read_from_the_session_each_one_ran_in():
    """实际发送内容从接收该消息的 Provider 会话读取

    provider_api Resident 的每条输入在各自的会话中运行；按消息的执行出处找到那次会话，读出实际发给模型的输入；没有记录的消息得到空结果。

    A provider_api Resident runs each input as a stateless completion in a session of its own:
    what was sent for a message reads from the session its execution opened.

    :module: resident.conversation
    """
    from management_portal.execution.requests import build_chat_completion_request

    store = AgentDataStore()
    agent_id = "resident_" + "d" * 12
    sent = {}
    for index in range(2):
        message_id = f"msg_{index}" + "e" * 31
        store.display_chat_message(agent_id, {"message_id": message_id, "role": "user", "author_id": "CASE-A",
                                              "content_parts": [{"type": "text", "text": f"question {index}"}]})
        request = build_chat_completion_request(
            actor="CASE-A", prompt=f"<resident-runtime-identity/>\nquestion {index}", provider="kimi_primary",
            agent_id=agent_id, metadata={"user_message_id": message_id},
        )
        session = RuntimeSession(mode="stateless", binding_id="", backend_session_id=request.execution_id,
                                 backend_route_id="", is_new_backend_session=True)
        projector = AgentDataProjector(store)
        projector.begin(request, session)
        projector.observe_invocation(request.execution_id, BackendInvocation(
            request=request, session=session, user_prompt=request.prompt, system_context="",
            attach_system_context=False, is_new_backend_session=True,
        ))
        sent[message_id] = request.prompt
    for message_id, prompt in sent.items():
        prompts = store.message_provider_prompts(agent_id, message_id)
        assert [item.get("actual_sent_prompt") or item.get("content") for item in prompts] == [prompt]
    assert store.message_provider_prompts(agent_id, "msg_" + "f" * 32) == []
