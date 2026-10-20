from __future__ import annotations

import copy
import json
import urllib.error
import urllib.request
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from management_portal import semantic_search_store, shared_search_adapter as adapter, shared_search_diagnostics as diagnostics
from management_portal.service_invocation_contract import batch_manifest
from tests.cases.registry_support import put_registry_document, registry_document, remove_registry_document
from tests.cases.test_resource_portal_routes import _server
from tests.cases.test_shared_search_invocation import job, runtime

SEARCH = "search_0123456789ab"
SECRET = "PRIVATE-CANDIDATE-AND-KEY-MUST-NOT-LEAK"


@pytest.fixture
def observed(runtime, monkeypatch):
    current = {**job(search_id=SEARCH), "query": "Unicode query",
               "execution_contract": adapter.CONTRACT, "status": "error"}
    # Who ResourceService lets read the Search; cases revoke by editing it.
    readers = {"alice", "bob"}
    identity = {key: current[key] for key in diagnostics.IDENTITY_FIELDS}
    key = adapter.result_key(current["surface"])
    catalogs = [[{key: "article_0123456789ab", "title": SECRET}],
                [{key: "article_1123456789ab", "title": "Unicode 中文"}]]
    prompts = [adapter.candidate_prompt(current, rows) for rows in catalogs]
    plan = runtime.owner.prepare_batches(job=identity, surface="knowledge", batches=batch_manifest(identity, prompts))
    states = {batch["message_id"]: {"message_id": batch["message_id"],
              "state": "completed" if index == 0 else "failed", "error": SECRET,
              "reply_message_ids": ["msg_reply"] if index == 0 else [], "updated_at": "2026-09-25T00:00:00Z"}
              for index, batch in enumerate(plan["batches"])}
    semantic_search_store.run_input(SEARCH, identity,
                                   lambda: {"identity": identity, "catalog": [row for rows in catalogs for row in rows], "prompts": prompts})
    resources = Mock()

    def resolve(selected, *, actor):
        if selected != SEARCH:
            raise KeyError(selected)
        if actor not in readers:
            raise PermissionError("not readable")
        return {"root_id": SEARCH, "resource_type": "search", "exists": not current.get("deleted"), "record": {"surface": "knowledge"}}

    def get_input(resident_id, actor, message_id):
        if actor != "alice":
            raise PermissionError("input belongs to another user")
        return copy.deepcopy(states[message_id])

    resources.resolve.side_effect = resolve
    access = Mock()
    access.is_active.side_effect = lambda actor: actor != "disabled"
    readonly = Mock(wraps=runtime.owner.read_plan)
    inputs = Mock(side_effect=get_input)
    monkeypatch.setattr(diagnostics, "get_resource_service", lambda: resources)
    monkeypatch.setattr(diagnostics, "get_portal_access_store", lambda: access)
    monkeypatch.setattr(diagnostics.semantic_search_jobs, "search_by_id", lambda surface, selected: copy.deepcopy(current))
    monkeypatch.setattr(diagnostics, "get_society_service", lambda: SimpleNamespace(
        service_invocation=SimpleNamespace(read_plan=readonly), resident=SimpleNamespace(get_resident_input_state=inputs)))
    return SimpleNamespace(runtime=runtime, current=current, readers=readers, plan=plan, prompts=prompts, states=states,
                           identity=identity, resources=resources, readonly=readonly, inputs=inputs)


def read(observed, actor="alice", generation=1):
    return diagnostics.get_search_run_diagnostics(SEARCH, actor=actor, generation=generation)


def test_diagnostics_reads_standard_input_and_redacted_frozen_statistics(observed):
    """检索运行诊断只读取各批次的标准输入状态和冻结输入统计，隐去候选内容和错误原文"""
    before = copy.deepcopy(observed.states)
    value = read(observed)
    assert value["run_generation"] == 1 and value["parent"]["batch_count"] == 2
    assert [item["input_state"]["state"] for item in value["batches"]] == ["completed", "failed"]
    for index, item in enumerate(value["batches"]):
        assert item["batch_id"] == f"batch-{index + 1:06d}"
        assert item["input_state"]["message_id"] == observed.plan["batches"][index]["message_id"]
        assert item["input"]["candidate_count"] == 1
    assert value["batches"][0]["input_state"]["reply_count"] == 1
    assert SECRET not in json.dumps(value)
    assert "execution" not in value["batches"][0]
    assert before == observed.states
    assert not observed.runtime.executions.submitted
    assert not observed.runtime.executions.cancelled


def test_another_reader_never_borrows_creator_identity(observed):
    """其他可读用户查看诊断时以自己的身份读取批次状态，无读取权限或停用的用户被拒绝"""
    result = read(observed, actor="bob")
    assert all(row["input_state"]["state"] == "not_authorized" for row in result["batches"])
    assert all(call.args[1] == "bob" for call in observed.inputs.call_args_list)
    observed.readers.discard("bob")
    observed.readonly.reset_mock()
    observed.inputs.reset_mock()
    for actor in ("bob", "mallory", "disabled", "", "resident_0123456789ab"):
        with pytest.raises((PermissionError, ValueError)):
            read(observed, actor=actor)
    observed.readonly.assert_not_called()
    observed.inputs.assert_not_called()


def test_permission_revocation_during_observation_is_not_returned(observed):
    """诊断返回前再次校验当前 Search 读取权限

    验证诊断返回前再次校验当前 Search 读取权限；仅使用临时存储与受控 Execution，不执行真实模型请求。

    :module: execution.service
    :importance: P1
    :aspects: functional, behavior
    :risk: no-external-side-effects
    """
    original = observed.inputs.side_effect
    def revoke(*args):
        observed.readers.discard("alice")
        return original(*args)
    observed.inputs.side_effect = revoke
    with pytest.raises(PermissionError):
        read(observed)


def test_historical_generation_and_missing_snapshot_do_not_refresh_runs(observed):
    """查看历史代或冻结输入缺失时诊断照常返回，不触发重新运行"""
    observed.current["run_generation"] = 2
    historical = read(observed)
    assert historical["run_generation"] == 1 and historical["current_generation"] == 2
    semantic_search_store.delete_run_inputs([SEARCH])
    missing = read(observed)
    assert missing["batches"][0]["input"] == {"source": "unknown", "candidate_count": None, "prompt": None}
    assert missing["batches"][0]["input_state"]["state"] == "completed"
    assert semantic_search_store.stored_run_input(SEARCH) is None
    assert diagnostics.get_search_run_diagnostics(SEARCH, actor="alice")["availability"] == "not_recorded"
    with pytest.raises(KeyError):
        read(observed, generation=3)


def test_unsubmitted_and_identity_mismatch_are_distinguished(observed):
    """诊断区分尚未提交的批次和输入或批次身份不一致的情况"""
    selected = observed.plan["batches"][0]["message_id"]
    observed.states[selected]["message_id"] = "msg_unrelated"
    with pytest.raises(ValueError, match="input identity mismatch"):
        read(observed)
    del observed.states[selected]
    result = read(observed)["batches"][0]
    assert result["message_id"] == selected and result["input_state"]["state"] == "not_submitted"
    mixed = copy.deepcopy(observed.plan)
    mixed["batches"][1]["message_id"] = selected
    observed.readonly.side_effect = None
    observed.readonly.return_value = mixed
    with pytest.raises(ValueError, match="batch identity mismatch"):
        read(observed)


def test_corrupt_snapshot_and_raw_errors_are_redacted(observed):
    """冻结输入损坏时诊断标记为不可用，且不泄露原始内容"""
    semantic_search_store.delete_run_inputs([SEARCH])
    semantic_search_store.run_input(SEARCH, observed.identity, lambda: {"identity": observed.identity, "prompts": SECRET})
    value = read(observed)
    assert all(item["input"]["source"] == "unavailable_or_invalid_snapshot" for item in value["batches"])
    assert all(item["input"]["candidate_count"] is None for item in value["batches"])
    assert SECRET not in json.dumps(value)


def test_public_http_entry_uses_session_actor_and_rejects_identity_overrides(observed):
    """统一 Search HTTP 入口仅使用登录身份且脱敏异常

    验证统一 Search HTTP 入口仅使用登录身份且脱敏异常；仅使用临时存储与受控 Execution，不执行真实模型请求。

    :module: execution.service
    :importance: P1
    :aspects: functional, behavior
    :risk: no-external-side-effects
    """
    from management_portal import serve_management_portal as web

    server, thread, base = _server()
    try:
        with patch.object(web, "_check_session", return_value=True), patch.object(web, "_get_session_username", return_value="bob"):
            endpoint = base + f"/api/searches/diagnostics?search_id={SEARCH}&generation=1"
            with urllib.request.urlopen(endpoint) as response:
                assert response.status == 200
                result = json.load(response)
                assert len(result["batches"]) == 2 and SECRET not in json.dumps(result)
            assert observed.inputs.call_args.args[1] == "bob"
            for suffix in ("&actor=alice", "&execution_id=execution-old", "&generation=2", "&surface=paper"):
                with pytest.raises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(endpoint + suffix)
                assert denied.value.code == 400
            for generation in ("", "0", "-1", "true"):
                with pytest.raises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(base + f"/api/searches/diagnostics?search_id={SEARCH}&generation={generation}")
                assert denied.value.code == 400
            observed.readonly.side_effect = RuntimeError(SECRET)
            with pytest.raises(urllib.error.HTTPError) as failed:
                urllib.request.urlopen(endpoint)
            assert failed.value.code == 500 and SECRET not in failed.value.read().decode()
        with patch.object(web, "_check_session", return_value=True), patch.object(web, "_get_session_username", return_value="mallory"):
            with pytest.raises(urllib.error.HTTPError) as denied:
                urllib.request.urlopen(endpoint)
            assert denied.value.code == 403
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
