from contextlib import nullcontext
from contextvars import ContextVar
from copy import deepcopy
import json
import threading
from unittest.mock import Mock

import pytest

from management_common.operation_identity import current_operation_identity, operation_identity_scope
from management_common.principal import OperationIdentity, Principal
from management_portal import paper_collection_jobs, paper_collector, paper_pdf, paper_projection, paper_records, paper_state_store, resident_handoff


ACTOR = "collector_test_owner"
PAPER = "paper_0123456789ab"
HANDLE = {"resident_id": "resident_123456789abc", "lineage_id": "lineage_123456789abc",
          "message_id": "msg_actual", "event_id": "event_fixture", "effect_id": "effect_fixture",
          "trigger_path_id": "trigger_path_123456789abc"}


def test_public_collector_exposes_stored_receipt_without_private_context(monkeypatch):
    """公开的收集任务只展示已存回执，不含提示词和凭据，也不执行 Resident"""
    receipt = paper_projection.receipt_from_resident_handle({**HANDLE, "reply_message_ids": ["msg_reply"]})
    job = {"job_id": "collector-fixture", "created_by": ACTOR, "invocation": {
        **receipt, "prompt": "private prompt", "credential": "private credential", "actor": "internal actor",
    }}
    original = deepcopy(job)
    runner = Mock(side_effect=AssertionError("public read must not execute a Resident"))
    monkeypatch.setattr(paper_projection, "turn", runner)

    public = paper_collection_jobs._public_paper_collection_job(job)

    assert public["invocation"] == receipt
    assert "private" not in json.dumps(public)
    assert job == original
    runner.assert_not_called()


@pytest.mark.parametrize("receipt", [None, {}])
def test_public_collector_keeps_missing_receipt_missing(receipt):
    """收集任务没有回执时，公开视图的 invocation 保持为空"""
    job = {"job_id": "collector-legacy", "created_by": ACTOR, "invocation": receipt}

    assert paper_collection_jobs._public_paper_collection_job(job)["invocation"] is None


@pytest.fixture
def collector(monkeypatch, tmp_path):
    job = {"job_id": "collector-fixture", "direction": "TAU-test Attention 只查原库，不重复登记，不联网",
           "created_by": ACTOR, "created_at": 100, "max_papers": 1, "status": "queued",
           "collector_contract": paper_collector.CONTRACT, "requested_message_id": "msg_planned"}
    updates, library = [], [{"id": PAPER, "title": "Attention Is All You Need", "created_at": 50}]
    monkeypatch.setattr(paper_collection_jobs, "PAPER_COLLECTOR_WORK_ROOT", tmp_path)
    monkeypatch.setattr(paper_collection_jobs, "_paper_collection_job_by_id", lambda _: deepcopy(job))

    def save(_, patch):
        updates.append(deepcopy(patch))
        job.update(deepcopy(patch))
        return deepcopy(job)

    monkeypatch.setattr(paper_collection_jobs, "_set_paper_collection_job", save)
    index = Mock(side_effect=lambda *, actor: deepcopy(library) if actor == ACTOR else pytest.fail("wrong actor"))
    monkeypatch.setattr(paper_collection_jobs, "resource_index_papers", index)
    monkeypatch.setattr(paper_pdf.subprocess, "run", Mock(side_effect=AssertionError("legacy subprocess")))
    with paper_collector.authenticated_request({"type": "human", "id": ACTOR}):
        yield job, updates, library, index


def output(job, **overrides):
    return {"schema": paper_collector.RESULT_SCHEMA, "job_id": job["job_id"], "status": "completed",
            "papers": [{"paper_id": PAPER, "action": "existing"}], "summary": "已有论文，未重复登记",
            "error": "", **overrides}


def raw_result(message):
    return {**HANDLE, "message": message, "messages": [
        {"message_id": "msg_reply", "role": "agent", "reply_to_message_id": "msg_actual",
         "content_parts": [{"type": "text", "text": message}],
         "usage": {"input_tokens": 12}}], "usage": {"message_id": "provider_usage", "input_tokens": 12}}


def install_native(monkeypatch, message, before_return=None):
    def turn(name, **options):
        assert name == "paper" and options["workflow"]["name"] == options["resident_scope"] == "paper:collector"
        assert options["actor"] == current_operation_identity().principal.id == ACTOR
        options["on_assigned"]({**HANDLE, "message_id": "msg_planned", "status": "queued"})
        options["on_submitted"](HANDLE)
        if before_return:
            before_return(options)
        return raw_result(message)

    mock = Mock(side_effect=turn)
    monkeypatch.setattr(paper_projection.native, "turn", mock)
    return mock


def test_canonical_full_request_receipt_and_existing_count(monkeypatch, collector):
    """论文收集发送完整未截断的请求，完成后记录实际输入、回复和用量消息回执"""
    job, updates, _, index = collector
    job["direction"] += "约束" * 700 + "尾部禁止联网"
    native = install_native(monkeypatch, json.dumps(output(job)))
    paper_collection_jobs._run_paper_collection_job(job["job_id"])
    assert native.call_count == 1 and index.call_count == 2
    request = json.loads(native.call_args.kwargs["prompt"].split("本次请求 JSON：\n")[1])
    assert request == {k: job[k] for k in ("job_id", "direction", "max_papers")}
    assert job["status"] == "done" and job["registered_count"] == 0
    assert job["results"][0]["title"] == "Attention Is All You Need"
    assert job["invocation"]["input_message_id"] == "msg_actual"
    assert job["invocation"]["reply_message_ids"] == ["msg_reply"]
    assert job["invocation"]["usage_message_id"] == "msg_reply"
    assert job["invocation"]["requested_message_id"] == "msg_planned"
    assert job["usage"]["usage_reported_message_id"] == "provider_usage"
    receipts = [u["invocation"] for u in updates if "invocation" in u]
    assert receipts[0]["input_identity_status"] == "unconfirmed" and not receipts[0]["input_message_id"]
    public = paper_collection_jobs._public_paper_collection_job(job)
    assert public["result_count"] == 1 and public["registered_count"] == 0
    summary = paper_collection_jobs._paper_collection_filter_summary(job["job_id"])
    assert summary["result_count"] == 1 and summary["registered_count"] == 0


@pytest.mark.parametrize("error", [PermissionError("HATG root required"), ValueError("route unavailable"),
                                  RuntimeError("execution failed"), TimeoutError("observation unavailable")])
def test_failure_stops_without_provider_retry_or_crawl(monkeypatch, collector, error, tmp_path):
    """论文收集执行出错时只调用一次即停止，任务记为失败并保存错误"""
    job, *_ = collector
    native = Mock(side_effect=error)
    monkeypatch.setattr(paper_projection.native, "turn", native)
    with pytest.raises(type(error), match=str(error)):
        paper_collection_jobs._run_paper_collection_job(job["job_id"])
    assert native.call_count == 1 and job["status"] == "error"
    assert (tmp_path / job["job_id"] / "stderr.log").read_text() == str(error)


@pytest.mark.parametrize("bad", ["", "{}", "completed", '{"registered": []}'])
def test_missing_business_receipt_is_not_done(monkeypatch, collector, bad):
    """Resident 回复为空、不是 JSON 或缺少字段时，论文收集不算完成"""
    job, *_ = collector
    install_native(monkeypatch, bad)
    with pytest.raises(ValueError):
        paper_collection_jobs._run_paper_collection_job(job["job_id"])
    assert job["status"] == "error"


@pytest.mark.parametrize("overrides,exception", [
    ({"job_id": "wrong"}, ValueError), ({"status": "error", "error": "save failed"}, RuntimeError),
    ({"status": "completed", "error": "authorization failed"}, RuntimeError),
    ({"papers": None}, ValueError),
    ({"papers": [{"paper_id": "paper_invented", "action": "existing"}]}, PermissionError),
    ({"papers": [{"paper_id": PAPER, "action": "registered"}]}, ValueError),
    ({"papers": [{"paper_id": PAPER, "action": "suggested"}]}, ValueError),
    ({"papers": [{"paper_id": PAPER, "action": "existing"}] * 2}, ValueError),
])
def test_result_contract_visibility_and_registration_claims(monkeypatch, collector, overrides, exception):
    """收集结果的任务 ID 不符、报告失败、论文不可见或登记声明不实时任务失败"""
    job, *_ = collector
    install_native(monkeypatch, json.dumps(output(job, **overrides)))
    with pytest.raises(exception):
        paper_collection_jobs._run_paper_collection_job(job["job_id"])
    assert job["status"] == "error" and not job.get("results")


def test_final_read_revocation_prevents_success(monkeypatch, collector):
    """收集结束时引用的论文已不在可见论文库中，任务以权限错误失败"""
    job, _, library, _ = collector
    install_native(monkeypatch, json.dumps(output(job)), lambda _: library.clear())
    with pytest.raises(PermissionError):
        paper_collection_jobs._run_paper_collection_job(job["job_id"])
    assert job["status"] == "error"


def test_new_registration_requires_saved_resource(monkeypatch, collector):
    """声称新登记的论文须真实存在于论文库才计入登记数"""
    job, _, library, _ = collector
    library.clear()
    install_native(monkeypatch, json.dumps(output(job, papers=[{"paper_id": PAPER, "action": "registered"}])),
                   lambda _: library.append({"id": PAPER, "title": "Real saved paper", "created_at": 101}))
    paper_collection_jobs._run_paper_collection_job(job["job_id"])
    assert job["registered_count"] == 1 and job["status"] == "done"


def test_zero_matches_is_distinct_from_execution_failure(monkeypatch, collector):
    """没有匹配论文时收集任务正常完成且结果为空，与执行失败区分开"""
    job, *_ = collector
    install_native(monkeypatch, json.dumps(output(job, papers=[], summary="No matching papers")))
    paper_collection_jobs._run_paper_collection_job(job["job_id"])
    assert job["status"] == "done" and job["results"] == [] and job["registered_count"] == 0


def test_noncanonical_job_is_not_replayed_or_modified(monkeypatch, collector):
    """缺少当前执行契约的旧收集任务不会被执行或修改"""
    job, updates, *_ = collector
    job.pop("collector_contract")
    old, native = deepcopy(job), Mock()
    monkeypatch.setattr(paper_projection.native, "turn", native)
    with pytest.raises(RuntimeError, match="current execution contract"):
        paper_collection_jobs._run_paper_collection_job(job["job_id"])
    with pytest.raises(RuntimeError, match="current execution contract"):
        paper_collection_jobs._start_paper_collection_task(job)
    assert job == old and not updates
    native.assert_not_called()


@pytest.mark.parametrize("status", ["done", "error", "cancelled", "deleted"])
def test_terminal_job_is_not_replayed(monkeypatch, collector, status):
    """已完成、失败、取消或删除的收集任务不会重新执行"""
    job, updates, *_ = collector
    job["status"] = status
    native = Mock()
    monkeypatch.setattr(paper_projection.native, "turn", native)
    paper_collection_jobs._run_paper_collection_job(job["job_id"])
    native.assert_not_called()
    assert not updates


def test_deleted_while_running_is_not_marked_done(monkeypatch, collector):
    """运行中被删除的论文收集任务不会被标为完成"""
    job, *_ = collector
    install_native(monkeypatch, json.dumps(output(job)), lambda _: job.update(status="deleted", deleted=True))
    paper_collection_jobs._run_paper_collection_job(job["job_id"])
    assert job["status"] == "deleted"


def test_resume_observes_confirmed_input_without_new_turn(monkeypatch, collector):
    """恢复已确认投递的收集任务时观察原输入，不发起新一轮"""
    job, *_ = collector
    job.update(invocation=paper_projection.receipt_from_resident_handle(HANDLE), existing_paper_ids=[PAPER], status="running")
    resume, native = Mock(return_value=raw_result(json.dumps(output(job)))), Mock()
    monkeypatch.setattr(resident_handoff, "resume_and_wait", resume)
    monkeypatch.setattr(paper_projection.native, "turn", native)
    paper_collection_jobs._run_paper_collection_job(job["job_id"])
    native.assert_not_called()
    assert resume.call_args.kwargs["message_id"] == "msg_actual" and job["status"] == "done"


@pytest.mark.parametrize("has_process_owner", [True, False])
def test_listing_recovers_original_collector_input_after_web_restart(monkeypatch, collector, has_process_owner):
    """Collector 在 Web 重启后恢复原输入并保存结果

    认证后的原请求者读取列表时恢复观察已确认输入，重复轮询不重复执行；保留原回复与论文登记结果。

    :importance: P1
    """
    from management_portal import business_tasks
    job, *_ = collector
    job.update(invocation=paper_projection.receipt_from_resident_handle(HANDLE),
               existing_paper_ids=[PAPER], status="running")
    if has_process_owner:
        job["process_owner"] = {"pid": -1}
    monkeypatch.setattr(business_tasks, "process_is_alive", lambda _: False)
    paper_state_store.append("collector_jobs", deepcopy(job))
    native = Mock(side_effect=AssertionError("recovery must not submit another input"))
    monkeypatch.setattr(paper_projection.native, "turn", native)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    observed = []
    def resume(**options):
        observed.append((current_operation_identity().principal.id, options["message_id"]))
        entered.set()
        assert release.wait(2)
        return raw_result(json.dumps(output(job)))
    monkeypatch.setattr(resident_handoff, "resume_and_wait", resume)
    original = paper_collection_jobs._run_paper_collection_job
    def run(job_id):
        try:
            original(job_id)
        finally:
            finished.set()
    monkeypatch.setattr(paper_collection_jobs, "_run_paper_collection_job", run)
    try:
        paper_collection_jobs.list_paper_collection_jobs(ACTOR)
        assert entered.wait(2)
        assert paper_collection_jobs.list_paper_collection_jobs(ACTOR)["jobs"][0]["status"] == "running"
    finally:
        release.set()
        assert finished.wait(2)
    assert observed == [(ACTOR, "msg_actual")]
    assert job["status"] == "done" and job["results"][0]["paper_id"] == PAPER
    assert job["registered_count"] == 0 and job["invocation"]["reply_message_ids"] == ["msg_reply"]
    native.assert_not_called()


@pytest.mark.parametrize("condition", ["untrusted", "other_owner", "live_process", "unconfirmed", "no_snapshot", "legacy"])
def test_listing_does_not_recover_collector_without_trusted_confirmed_input(monkeypatch, collector, condition):
    """Collector 恢复要求可信调用者和已确认输入

    缺少认证、原请求者不匹配、原进程存活、输入未确认、缺少库快照或旧协议时，列表读取不会重新启动任务。

    :importance: P1
    """
    from management_portal import business_tasks
    job, *_ = collector
    job.update(invocation=paper_projection.receipt_from_resident_handle(HANDLE),
               existing_paper_ids=[PAPER], status="running", updated_at=paper_records._now())
    context = nullcontext()
    if condition == "untrusted":
        context = operation_identity_scope(None)
    elif condition == "other_owner":
        job["created_by"] = "someone_else"
    elif condition == "live_process":
        job["process_owner"] = {"pid": -1}
        monkeypatch.setattr(business_tasks, "process_is_alive", lambda _: True)
    elif condition == "unconfirmed":
        job["invocation"] = paper_projection.receipt_from_resident_handle({**HANDLE, "status": "queued"})
    elif condition == "no_snapshot":
        del job["existing_paper_ids"]
    elif condition == "legacy":
        job["collector_contract"] = "old"
    paper_state_store.append("collector_jobs", deepcopy(job))
    start = Mock(side_effect=AssertionError("must not recover this input"))
    monkeypatch.setattr(paper_collection_jobs, "_start_paper_collection_task", start)
    with context:
        assert paper_collection_jobs.list_paper_collection_jobs(ACTOR)["jobs"][0]["status"] == "running"
    start.assert_not_called()


def test_collector_listing_restores_the_server_authenticated_request_context(monkeypatch):
    """Collector 列表使用服务端认证请求身份

    列表恢复仅传递服务端认证的当前调用者身份，忽略路由外部身份值，并在请求结束后还原调用上下文。

    :importance: P1
    """
    from types import SimpleNamespace
    from management_portal.http_paper import PaperHttp
    observed = []
    http = PaperHttp.__new__(PaperHttp)
    http.get_auth = lambda _: {"type": "human", "id": ACTOR}
    http.authorize = lambda *_: ACTOR
    http.respond = lambda _, status, body: (status, body)
    def listing(actor):
        observed.append((actor, current_operation_identity().principal.id))
        return {"jobs": []}
    monkeypatch.setattr(paper_collection_jobs, "list_paper_collection_jobs", listing)
    with operation_identity_scope(None):
        assert http.get_route(http.collector_jobs)(object(), SimpleNamespace(query=""), "untrusted") == (200, {"jobs": []})
        assert current_operation_identity() is None
    assert observed == [(ACTOR, ACTOR)]


def test_unconfirmed_delivery_never_resubmits(monkeypatch, collector):
    """投递未确认的收集任务报错，不重新提交"""
    job, *_ = collector
    job["invocation"] = paper_projection.receipt_from_resident_handle({**HANDLE, "status": "queued"})
    native = Mock()
    monkeypatch.setattr(paper_projection.native, "turn", native)
    with pytest.raises(RuntimeError, match="unconfirmed"):
        paper_collection_jobs._run_paper_collection_job(job["job_id"])
    native.assert_not_called()


@pytest.mark.parametrize("principal", [None, Principal("service", "portal"), Principal("user", "someone_else")])
def test_untrusted_actor_rejected_before_writes(monkeypatch, principal):
    """缺少身份、服务身份或他人身份发起收集时在写入前被拒绝"""
    identity = OperationIdentity(principal, principal) if principal else None
    with operation_identity_scope(identity), pytest.raises(PermissionError):
        paper_collection_jobs.start_paper_collection({"direction": "request", "actor": ACTOR}, ACTOR)
    assert paper_state_store.records("collector_jobs") == []


def test_authenticated_boundary_rejects_conflicting_identity():
    """论文收集的认证请求范围内换成另一身份时被拒绝"""
    with paper_collector.authenticated_request({"type": "human", "id": ACTOR}):
        with pytest.raises(PermissionError):
            with paper_collector.authenticated_request({"type": "human", "id": "someone_else"}):
                pytest.fail("identity changed")


def test_start_preserves_direction_and_uses_authenticated_owner(monkeypatch, tmp_path):
    """创建收集任务时完整保存研究方向，owner 取认证身份，超长方向直接拒绝"""
    monkeypatch.setattr(paper_collection_jobs, "_start_paper_collection_task", lambda job: job)
    direction = "Attention " + "a" * 1200 + " 不联网、不重复登记"
    with paper_collector.authenticated_request({"type": "human", "id": ACTOR}):
        paper_collection_jobs.start_paper_collection({"direction": direction, "max_papers": 1, "created_by": "spoof"}, ACTOR)
        with pytest.raises(ValueError, match="no input was truncated"):
            paper_collection_jobs.start_paper_collection({"direction": "x" * (paper_collector.MAX_DIRECTION_CHARS + 1)}, ACTOR)
    saved = paper_state_store.records("collector_jobs")
    assert len(saved) == 1 and saved[0]["direction"] == direction and saved[0]["max_papers"] == 1
    assert saved[0]["created_by"] == ACTOR and "owner_ids" not in saved[0]
    assert saved[0]["collector_contract"] == paper_collector.CONTRACT


def test_trusted_context_survives_real_business_thread(monkeypatch, collector):
    """后台收集线程继承请求的认证身份和上下文变量"""
    job, *_ = collector
    observed, finished = [], threading.Event()
    request_context = ContextVar("collector_test_request_context")

    def run(_):
        observed.append((current_operation_identity().principal.id, request_context.get()))
        finished.set()

    monkeypatch.setattr(paper_collection_jobs, "_run_paper_collection_job", run)
    token = request_context.set("authenticated-context")
    try:
        paper_collection_jobs._start_paper_collection_task(job)
        assert finished.wait(2), "background task did not observe the request context"
    finally:
        request_context.reset(token)
    assert observed == [(ACTOR, "authenticated-context")]


def test_native_resolves_collector_from_four_and_passes_workflow_binding(monkeypatch, collector):
    """论文收集从 four 绑定解析收集角色 Resident，并传入安装好的工作流绑定"""
    from types import SimpleNamespace
    from management_portal.lineage_workflows import WorkflowBindings

    job, *_ = collector
    native = paper_projection.native
    binding = {"resident_id": HANDLE["resident_id"], "lineage_id": HANDLE["lineage_id"]}
    app = {"bindings": {"main": {"lineage_id": "lineage_main", "residents": {}}, "four": {
        "lineage_id": binding["lineage_id"], "residents": {"paper:collector": binding["resident_id"]}}}}
    initialize = Mock(return_value=app)
    from management_portal.workbench_projections import service as projection_service

    # The application binding reads the projection service when it resolves.
    monkeypatch.setattr(projection_service, "get_service", lambda: SimpleNamespace(initialize=initialize))
    graph = Mock(return_value={"fixture_graph": True})
    monkeypatch.setattr(projection_service, "lineage_graph", graph)
    monkeypatch.setattr(native, "members", lambda _: {binding["resident_id"]})
    workflow = {"event_source_id": "event_source_fixture", "effect_id": "effect_fixture"}
    install = Mock(return_value=workflow)
    monkeypatch.setattr(WorkflowBindings, "install", install)

    def execute(**kwargs):
        assert kwargs["resident_binding"] == binding and kwargs["workflow_binding"] == workflow
        kwargs["on_submitted"](HANDLE)
        return raw_result(json.dumps(output(job)))

    execute_mock = Mock(side_effect=execute)
    monkeypatch.setattr(resident_handoff, "run_resident_turn", execute_mock)
    paper_collection_jobs._run_paper_collection_job(job["job_id"])
    initialize.assert_called_once_with("paper", ACTOR)
    graph.assert_called_once_with(ACTOR, binding["lineage_id"])
    assert install.call_args.kwargs["name"] == "paper:collector"
    assert install.call_args.kwargs["resident_id"] == binding["resident_id"]
    assert execute_mock.call_count == 1 and job["status"] == "done"


@pytest.mark.parametrize("final_is_valid", [True, False])
def test_only_final_linked_reply_is_a_business_receipt(monkeypatch, collector, final_is_valid, tmp_path):
    """只有最后一条关联回复作为收集结果回执，中间进度消息不算"""
    job, *_ = collector
    valid = json.dumps(output(job))
    final, progress = (valid, "I am checking existing papers") if final_is_valid else ("Saving failed", valid)
    raw = raw_result(final)
    raw["messages"].insert(0, {"message_id": "msg_progress", "role": "agent", "reply_to_message_id": "msg_actual",
                               "content_parts": [{"type": "text", "text": progress}]})
    raw["message"] = progress + "\n" + final
    monkeypatch.setattr(paper_projection.native, "turn", Mock(return_value=raw))
    if final_is_valid:
        paper_collection_jobs._run_paper_collection_job(job["job_id"])
        assert job["status"] == "done"
    else:
        with pytest.raises(ValueError):
            paper_collection_jobs._run_paper_collection_job(job["job_id"])
        assert job["status"] == "error"
    assert (tmp_path / job["job_id"] / "stdout.json").read_text() == raw["message"]


def test_listing_marks_a_collector_job_whose_task_is_gone_as_interrupted(monkeypatch):
    """执行进程已不存在的 Collector 任务在列表中标记为中断，不再永久显示运行中

    Collector 任务由一个 Web 进程的业务任务执行；该进程重启后任务不再更新。列表读取时，本进程没有活动任务且超过阈值未更新的 queued/running 任务标记为 error（stale）。
    """
    from management_portal import business_tasks

    now = 2_000_000_000
    jobs = {
        "collector-000000000001": {"job_id": "collector-000000000001", "status": "running", "created_by": ACTOR,
                                   "updated_at": now - paper_collection_jobs.COLLECTOR_STALE_SECONDS - 1},
        "collector-000000000002": {"job_id": "collector-000000000002", "status": "running", "created_by": ACTOR,
                                   "updated_at": now - 60},
        "collector-000000000003": {"job_id": "collector-000000000003", "status": "queued", "created_by": ACTOR,
                                   "updated_at": now - paper_collection_jobs.COLLECTOR_STALE_SECONDS - 1},
    }

    def set_job(job_id, patch):
        jobs[job_id] = {**jobs[job_id], **patch, "updated_at": now}
        return jobs[job_id]

    monkeypatch.setattr(paper_collection_jobs, "_now", lambda: now)
    monkeypatch.setattr(paper_collection_jobs, "_all_paper_collection_jobs", lambda: list(jobs.values()))
    monkeypatch.setattr(paper_collection_jobs, "_set_paper_collection_job", set_job)
    # The third job is still running in this process.
    monkeypatch.setattr(business_tasks, "is_active", lambda task_id: task_id == "paper-collection:collector-000000000003")

    listed = {job["job_id"]: job for job in paper_collection_jobs.list_paper_collection_jobs(ACTOR)["jobs"]}

    assert jobs["collector-000000000001"]["status"] == "error" and jobs["collector-000000000001"]["step"] == "stale"
    assert listed["collector-000000000001"]["status"] == "error"
    assert jobs["collector-000000000002"]["status"] == "running"
    assert jobs["collector-000000000003"]["status"] == "queued"
