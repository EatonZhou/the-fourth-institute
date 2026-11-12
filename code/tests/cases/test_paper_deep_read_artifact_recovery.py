"""Deep-read artifact recovery preserves the original caller's authorization.

:importance: P1
:aspects: functional
"""
from contextlib import nullcontext
from copy import deepcopy
import hashlib

import pytest

from management_portal import isa_client, paper_projection, paper_reading_jobs as jobs, permission_system


@pytest.fixture
def recovery(monkeypatch, tmp_path, request):
    paper_id, resident_id, input_id = "paper_0123456789ab", "resident_0123456789ab", "msg_original"
    original = {"job_id": "deep-read-failed", "paper_id": paper_id, "status": "error", "error": "9 sections, 0 found",
                "created_by": "old-caller", "resident_id": resident_id, "resident_input_message_id": input_id,
                "resident_handoff": paper_projection.receipt_from_resident_handle({"resident_id": resident_id, "message_id": input_id})}
    records = {original["job_id"]: deepcopy(original)}
    content = "# Analysis\n" + jobs.DEEP_READ_HEADINGS
    message = {"message_id": "msg_forwarded", "author_id": resident_id, "role": "user",
               "content_parts": [{"type": "text", "text": content}]}
    payload = {"paper_id": paper_id, "job_id": original["job_id"], "input_message_id": input_id,
               "source_resident_id": "resident_abcdefabcdef", "source_message_id": "msg_forwarded",
               "content_sha256": hashlib.sha256(content.encode()).hexdigest()}
    state = {"message_id": input_id, "state": "completed"}
    calls, writes = [], []
    def call(actor, instruction, args):
        assert actor == "current-caller"
        calls.append((instruction, args))
        if instruction == "GetInputState":
            return {"input_state": state}
        assert instruction == "GetMessage", "Recovery must never submit an input"
        return {"message": message}
    monkeypatch.setattr(isa_client, "call", call)
    monkeypatch.setattr(permission_system, "permitted_ids", lambda actor, *args: [paper_id] if actor == "current-caller" else [])
    if getattr(request, "param", False):
        jobs.paper_state_store.append("deep_read_jobs", original)
        monkeypatch.setattr(jobs.PAPER_STORE, "write_run_record", lambda job: None)
        monkeypatch.setattr(jobs, "_notify_paper_projection", lambda *args, **kwargs: None)
    else:
        monkeypatch.setattr(jobs, "_latest_deep_read_job", lambda *, job_id="", paper_id="": records.get(job_id) if job_id else list(records.values())[-1])
        monkeypatch.setattr(jobs.paper_state_store, "locked", lambda *args: nullcontext())
        monkeypatch.setattr(jobs.paper_state_store, "append", lambda kind, record: records.update({record["job_id"]: deepcopy(record)}))
        monkeypatch.setattr(jobs, "_set_deep_read_job", lambda job_id, patch: records[job_id])
    def metadata(pid, *, sections=None):
        assert sections == [], 'Artifact recovery must not load other sections or recover figures'
        return {"paper": {"id": pid, "title": "Fixture"}}
    monkeypatch.setattr(jobs, "get_paper_detail", metadata)
    monkeypatch.setattr(jobs, "_paper_store_file", lambda *args: tmp_path / "analysis.md")
    monkeypatch.setattr(jobs, "_update_paper_fields", lambda *args: writes.append(args))
    monkeypatch.setattr(paper_projection, "turn", lambda **kwargs: pytest.fail("must not call model"))
    return original, records, payload, message, state, calls, writes, tmp_path


def test_recovery_saves_selected_generated_text_and_preserves_failed_job(recovery):
    """复用同角色生成消息，沿正文校验保存；旧失败及原输入保留，不再生成。"""
    original, records, payload, message, state, calls, writes, root = recovery
    result = jobs.recover_deep_read(payload, "current-caller")
    assert result["status"] == "done"
    assert records[original["job_id"]] == original
    assert result["created_by"] == "current-caller"
    assert result["usage"]["artifact_recovery"]["source_message_id"] == "msg_forwarded"
    assert result["resident_input_message_id"] == payload["input_message_id"]
    assert "## 关键结果" in (root / "analysis.md").read_text()
    assert len(writes) == 1
    assert jobs.recover_deep_read(payload, "current-caller") == result
    assert len(writes) == 1
    assert {name for name, args in calls} == {"GetInputState", "GetMessage"}


def test_provenance_refresh_only_loads_metadata(recovery):
    original, records, payload, message, state, calls, writes, root = recovery
    recovered = jobs.recover_deep_read(payload, 'current-caller')
    path = root / 'analysis.md'
    body = path.read_text().split('\n---\n', 1)[1]
    refreshed = jobs.refresh_deep_read_provenance({
        'paper_id': payload['paper_id'], 'job_id': recovered['job_id'],
        'expected_analysis_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
    }, 'current-caller')
    assert refreshed['job_id'] == recovered['job_id']
    assert path.read_text().split('\n---\n', 1)[1] == body
    assert records[original['job_id']] == original


@pytest.mark.parametrize("condition", ["permission", "source_access", "wrong_input", "running", "wrong_author", "hash", "brief", "newer_job", "existing_analysis"])
def test_recovery_rejects_unauthorized_unconfirmed_or_stale_artifacts(recovery, monkeypatch, condition):
    """权限、原输入、作者、内容摘要、产物结构与并发变化任一不满足均不写入。"""
    original, records, payload, message, state, calls, writes, root = recovery
    actor = "current-caller"
    if condition == "permission":
        actor = "reader"
    elif condition == "source_access":
        monkeypatch.setattr(isa_client, "call", lambda *args: (_ for _ in ()).throw(PermissionError("source denied")))
    elif condition == "wrong_input":
        payload["input_message_id"] = "msg_other"
    elif condition == "running":
        state["state"] = "running"
    elif condition == "wrong_author":
        message["author_id"] = "resident_other"
    elif condition == "hash":
        payload["content_sha256"] = "stale"
    elif condition == "brief":
        message["content_parts"][0]["text"] = "任务完成，全文已发送。"
        payload["content_sha256"] = hashlib.sha256(message["content_parts"][0]["text"].encode()).hexdigest()
    elif condition == "newer_job":
        records["newer"] = {"job_id": "newer", "status": "running"}
    elif condition == "existing_analysis":
        (root / "analysis.md").write_text("newer analysis")
    with pytest.raises((PermissionError, ValueError, RuntimeError)):
        jobs.recover_deep_read(payload, actor)
    assert records[original["job_id"]] == original
    assert writes == []
    assert not (root / "analysis.md").exists() or (root / "analysis.md").read_text() == "newer analysis"


def test_recovery_checks_permission_again_after_source_read(recovery, monkeypatch):
    """来源读取期间撤权时，保存前拒绝。"""
    checks = iter([True, False])
    monkeypatch.setattr(permission_system, "permitted_ids", lambda *args: [recovery[2]["paper_id"]] if next(checks) else [])
    with pytest.raises(PermissionError):
        jobs.recover_deep_read(recovery[2], "current-caller")
    assert recovery[6] == []


def test_paper_function_task_requires_full_final_reply(monkeypatch):
    """业务每轮输入直接指定完整最终产物，避免按普通消息协议只向门面发全文。"""
    seen = []
    monkeypatch.setattr(paper_projection.native, "turn", lambda name, **kwargs: seen.append(kwargs) or {
        "resident_id": "resident_0123456789ab", "message_id": "msg_input", "message": "result"})
    for role in ("paper:deep-read", "paper:summary", "paper:exp"):
        paper_projection.turn(actor="caller", resident_scope=role, prompt="required body")
    for options in seen:
        assert "最终回复" in options["prompt"] and "完整正文" in options["prompt"]
        assert "不要通过 DeliverMessage" in options["prompt"]
        assert options["prompt"].endswith("required body")


@pytest.mark.parametrize("recovery", [True], indirect=True)
def test_recovery_persists_separate_job_and_provenance_in_database(recovery):
    """真实隔离 PostgreSQL 保留旧失败、来源与新 done 身份；重复调用不新增记录。"""
    original, _, payload, _, _, _, writes, _ = recovery
    before = jobs.paper_state_store.get("deep_read_jobs", original["job_id"])
    result = jobs.recover_deep_read(payload, "current-caller")
    saved = jobs.paper_state_store.get("deep_read_jobs", result["job_id"])
    assert saved == result
    assert saved["usage"]["artifact_recovery"]["input_message_id"] == payload["input_message_id"]
    assert saved["resident_handoff"]["input_message_id"] == payload["input_message_id"]
    assert jobs.paper_state_store.get("deep_read_jobs", original["job_id"]) == before
    assert jobs._latest_deep_read_job(paper_id=payload["paper_id"])["job_id"] == result["job_id"]
    assert jobs.recover_deep_read(payload, "current-caller") == result
    assert len(jobs.paper_state_store.records("deep_read_jobs", paper_id=payload["paper_id"])) == 2
    assert len(writes) == 1


def test_http_recovery_preserves_auth_read_and_write_checks(recovery, monkeypatch):
    """HTTP 恢复沿现有认证及 Paper 读取边界，领域保存另验 contributor。"""
    from unittest.mock import Mock
    from management_portal.http_paper import PaperHttp
    payload = recovery[2]
    auth = {"actor": "current-caller"}
    read = Mock()
    http = PaperHttp(respond=lambda _r, status, body: (status, body), exception_response=None, media_file=None,
                     get_auth=lambda _: auth, authorize=lambda *_: "current-caller", read_json=lambda _: payload,
                     read_multipart=None, multipart_limit=None, body_too_large=None, require_resource=read,
                     require_active_actor=None, project_ids=lambda _: [], notify_index=None)
    route = http.post_backend(jobs, "recover_deep_read")
    assert route(None, None, None)[0] == 200
    read.assert_called_once_with(payload["paper_id"], "current-caller")
    monkeypatch.setattr(permission_system, "permitted_ids", lambda *args: [])
    assert route(None, None, None)[0] == 403
    auth.clear()
    assert route(None, None, None)[0] == 401
    assert len(recovery[6]) == 1
