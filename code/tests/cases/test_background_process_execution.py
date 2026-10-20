from __future__ import annotations

import json
import threading
from unittest.mock import Mock

from knowledge_board_lan import knowledge_backend
from management_portal import (
    board_semantic_search_backend,
    business_tasks,
    resident_handoff,
    semantic_search_jobs,
    shared_search_adapter,
)
from tests.cases.test_shared_search_invocation import runtime
from management_portal.shared_search_invocation import read_shared_search_plan


def _set_execution_response(runtime, monkeypatch, response, *, complete_after_cancel=False):
    original_status = runtime.executions.status

    def status(execution_id, **kwargs):
        result = original_status(execution_id, **kwargs)
        result["result"]["output"]["message"] = json.dumps(response)
        if complete_after_cancel:
            state = "completed" if execution_id in runtime.executions.cancelled else "running"
            result["status"] = result["result"]["status"] = state
        return result

    monkeypatch.setattr(runtime.executions, "status", status)


def _isolate_search_jobs(runtime, monkeypatch, surface):
    monkeypatch.setattr(semantic_search_jobs, "publish_local_projection_change", Mock())
    monkeypatch.setattr(resident_handoff, "run_resident_turn", Mock(side_effect=AssertionError("actor-scoped invocation forbidden")))
    runtime.binding["surface"] = surface
    return lambda search_id: semantic_search_jobs.search_by_id(surface, search_id)


def test_business_task_runs_without_execution_submission() -> None:
    """业务任务无需提交执行请求即可运行"""
    finished = threading.Event()

    assert business_tasks.start(
        "test-business-task",
        finished.set,
    )
    assert finished.wait(2)


def test_board_search_starts_portal_business_task(monkeypatch, runtime) -> None:
    """看板检索启动业务任务并以创建者身份执行"""
    from management_portal import wall_backend
    monkeypatch.setattr(wall_backend, "project", Mock())
    getter = _isolate_search_jobs(runtime, monkeypatch, "wall")
    catalog = [{"item_id": "wall_0123456789ab", "title": "Migration"}]
    monkeypatch.setattr(board_semantic_search_backend, "search_catalog", Mock(return_value=catalog))
    _set_execution_response(runtime, monkeypatch, {"results": [{"item_id": catalog[0]["item_id"], "score": 91}]})
    schedule = Mock(return_value=True)
    monkeypatch.setattr(business_tasks, "start", schedule)

    search = semantic_search_jobs.start_search("wall", {"project": "portal", "query": "migration"}, "alice")
    job = getter(search["search_id"])
    assert job["execution_contract"] == shared_search_adapter.CONTRACT
    assert job["run_generation"] == 1
    assert schedule.call_count == 1
    _, target, surface, search_id, generation = schedule.call_args.args
    assert target is semantic_search_jobs.run_job
    assert (surface, search_id, generation) == ("wall", job["search_id"], job["run_generation"])
    target(surface, search_id, generation)

    saved = getter(search_id)
    assert saved["status"] == "done"
    assert len(runtime.executions.submitted) == 1
    request = runtime.executions.submitted[0]
    assert request.actor == "alice"
    assert request.session.mode == "stateless"
    assert saved["message_id"] == request.metadata["user_message_id"]
    plan = read_shared_search_plan(job=saved, surface="wall")
    assert plan["resident_id"] == runtime.binding["resident_id"]
    assert plan["surface"] == "wall"
    assert "service_invocation" not in saved
    assert runtime.owner.get_invocation(job=saved, surface="wall")["receipt"]["status"] == "completed"
    semantic_search_jobs.start_task(saved)
    assert schedule.call_count == 1


def test_knowledge_search_starts_portal_business_task(monkeypatch, runtime) -> None:
    """知识检索启动业务任务并持久化无状态执行结果"""
    getter = _isolate_search_jobs(runtime, monkeypatch, "knowledge")
    candidate = {"kb_id": "decision_0123456789ab", "type": "decision", "title": "Migration"}
    candidates = Mock(return_value={"items": [candidate]})
    monkeypatch.setattr(knowledge_backend, "resource_index_snapshot", candidates)
    _set_execution_response(runtime, monkeypatch, {"results": [{"kb_id": candidate["kb_id"], "score": 92}]})
    schedule = Mock(return_value=True)
    monkeypatch.setattr(business_tasks, "start", schedule)

    search = semantic_search_jobs.start_search("knowledge", {"query": "migration"}, "alice")
    job = getter(search["search_id"])
    assert job["execution_contract"] == shared_search_adapter.CONTRACT
    assert job["run_generation"] == 1
    assert schedule.call_count == 1
    _, target, surface, search_id, generation = schedule.call_args.args
    assert target is semantic_search_jobs.run_job
    assert (surface, search_id, generation) == ("knowledge", job["search_id"], job["run_generation"])
    target(surface, search_id, generation)

    saved = getter(search_id)
    assert saved["status"] == "done"
    assert saved["results"][0]["kb_id"] == candidate["kb_id"]
    assert len(runtime.executions.submitted) == 1
    request = runtime.executions.submitted[0]
    assert request.actor == "alice"
    assert request.session.mode == "stateless"
    assert saved["message_id"] == request.metadata["user_message_id"]
    plan = read_shared_search_plan(job=saved, surface="knowledge")
    assert plan["resident_id"] == runtime.binding["resident_id"]
    assert plan["surface"] == "knowledge"
    assert "service_invocation" not in saved
    assert runtime.owner.get_invocation(job=saved, surface="knowledge")["receipt"]["status"] == "completed"
    assert all(call.kwargs == {"active_only": False, "actor": "alice"} for call in candidates.call_args_list)
    semantic_search_jobs.start_task(saved)
    assert schedule.call_count == 1
