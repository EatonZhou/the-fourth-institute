"""Paper provenance uses historical generation facts, with no model rerun.

:importance: P1
:aspects: functional, permissions, recovery
"""
from copy import deepcopy
import hashlib
import yaml
import pytest

from tests.cases.test_paper_deep_read_artifact_recovery import recovery
from management_portal import isa_client, paper_reading_jobs as jobs, permission_system


def test_analysis_metadata_distinguishes_missing_model_from_reported_model():
    for usage, expected in [({}, "未记录"), ({"provider": "kimi", "account_id": "subscription", "model": "historic-model"}, "historic-model")]:
        document = jobs._deep_read_analysis_document({"id": "paper_0123456789ab"}, "# Body", usage)
        metadata = yaml.safe_load(document.split("---", 2)[1])
        assert metadata["llm_model"] == expected
        assert metadata["llm_provider"] == usage.get("provider", "未记录")
        assert metadata["llm_api"] == "未记录"


@pytest.fixture
def saved(recovery, monkeypatch):
    original, records, payload, message, state, calls, writes, root = recovery
    result = jobs.recover_deep_read(payload, "current-caller")
    result["resident_handoff"]["usage_message_id"] = "msg_reply"
    result["resident_handoff"]["reply_message_ids"] = ["msg_reply"]
    state.update(reply_message_ids=["msg_reply"], work={"execution_id": "execution-original", "provider_turn_id": "turn-original"})
    reply = {"message_id": "msg_reply", "author_id": original["resident_id"], "reply_to_message_id": state["message_id"], "usage": {"output_tokens": 7}}
    def call(actor, instruction, args):
        assert actor == "current-caller"
        if instruction == "GetInputState":
            return {"input_state": state}
        assert instruction == "GetMessage" and args["message_id"] == "msg_reply"
        return {"message": reply}
    monkeypatch.setattr(isa_client, "call", call)
    monkeypatch.setattr(jobs, "_set_deep_read_job", lambda jid, patch: records[jid].update(patch) or deepcopy(records[jid]))
    document = (root / "analysis.md").read_text()
    payload = {"paper_id": original["paper_id"], "job_id": result["job_id"], "expected_analysis_sha256": hashlib.sha256(document.encode()).hexdigest()}
    return original, records, result, state, reply, payload, root, document


@pytest.mark.parametrize("has_model", [False, True])
def test_refresh_preserves_body_timestamp_selection_and_failed_job(saved, has_model):
    original, records, result, state, reply, payload, root, before = saved
    if has_model:
        reply["usage"].update(provider="kimi", account_id="original-account", model="original-model")
    result = jobs.refresh_deep_read_provenance(payload, "current-caller")
    after = (root / "analysis.md").read_text()
    before_header, after_header = (yaml.safe_load(d.split("---", 2)[1]) for d in (before, after))
    assert after.split("---", 2)[2] == before.split("---", 2)[2]
    assert after_header["generated_at"] == before_header["generated_at"]
    assert after_header["llm_model"] == ("original-model" if has_model else "未记录")
    assert after_header["llm_execution_id"] == "execution-original"
    assert after_header["llm_source_selection"] == "explicit_generated_message"
    assert after_header["llm_source_message_id"] == "msg_forwarded"
    assert records[original["job_id"]] == original
    assert result["usage"]["generation_source"]["input_message_id"] == state["message_id"]


@pytest.mark.parametrize("failure", ["permission", "revoked", "digest", "newer", "input", "reply", "access"])
def test_refresh_rejects_stale_or_unauthorized_provenance(saved, monkeypatch, failure):
    original, records, result, state, reply, payload, root, before = saved
    actor = "current-caller"
    if failure == "permission":
        actor = "reader"
    elif failure == "revoked":
        permits = iter([True, False])
        monkeypatch.setattr(permission_system, "permitted_ids", lambda *a: [original["paper_id"]] if next(permits) else [])
    elif failure == "digest":
        payload["expected_analysis_sha256"] = "stale"
    elif failure == "newer":
        records["newer"] = {"job_id": "newer", "status": "done"}
    elif failure == "input":
        state["state"] = "running"
    elif failure == "reply":
        reply["reply_to_message_id"] = "other-input"
    elif failure == "access":
        monkeypatch.setattr(isa_client, "call", lambda *a: (_ for _ in ()).throw(PermissionError("source access denied")))
    with pytest.raises((PermissionError, ValueError)):
        jobs.refresh_deep_read_provenance(payload, actor)
    assert (root / "analysis.md").read_text() == before


def test_http_provenance_preserves_auth_and_paper_read_checks(saved, monkeypatch):
    from management_portal.http_paper import PaperHttp
    from unittest.mock import Mock
    auth, read = {"actor": "current-caller"}, Mock()
    http = PaperHttp(respond=lambda r, status, body: (status, body), exception_response=None, media_file=None,
        get_auth=lambda _: auth, authorize=lambda *_: "current-caller", read_json=lambda _: saved[5],
        read_multipart=None, multipart_limit=None, body_too_large=None, require_resource=read,
        require_active_actor=None, project_ids=lambda _: [], notify_index=None)
    route = http.post_backend(jobs, "refresh_deep_read_provenance")
    assert route(None, None, None)[0] == 200
    read.assert_called_once_with(saved[5]["paper_id"], "current-caller")
    monkeypatch.setattr(permission_system, "permitted_ids", lambda *a: [])
    assert route(None, None, None)[0] == 403
    auth.clear()
    assert route(None, None, None)[0] == 401
