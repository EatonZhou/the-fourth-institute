""":importance: P1"""
from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

from management_portal import paper_backend, paper_state_store


@pytest.mark.parametrize("saved_result", [False, True])
def test_review_settles_dead_owner_without_regenerating(tmp_path, saved_result):
    """评审持有进程已退出时复用已存结果或标记错误且不重生成

    :importance: P2"""
    from management_portal import business_tasks

    owner = business_tasks.process_identity()
    owner["start_ticks"] = "-1"  # Same PID after reuse must not be considered alive.
    job = {"job_id": "interrupted", "paper_id": "paper_one", "created_by": "alice",
           "review_id": "review_one", "status": "running", "process_owner": owner}
    review = {"review_id": "review_one", "summary": "Already generated", "status": "done"}
    paper_state_store.append("review_jobs", job)
    if saved_result:
        paper_state_store.append("reviews", review)
    with (mock.patch.object(paper_backend, "_notify_paper_projection"),
          mock.patch.object(paper_backend, "_generate_ai_review") as generate):
        settled = paper_backend.get_review_status(job_id="interrupted", actor="alice")["job"]
        assert settled["status"] == ("done" if saved_result else "error")
        assert settled["result"] == (review if saved_result else {})
        assert paper_backend.get_review_status(job_id="interrupted", actor="alice")["job"] == settled
        if saved_result:
            assert paper_backend.wait_review("interrupted", actor="alice") == review
        else:
            with pytest.raises(RuntimeError, match="interrupted"):
                paper_backend.wait_review("interrupted", actor="alice")
        generate.assert_not_called()


def test_review_does_not_settle_live_or_unproven_owner():
    """评审任务持有进程存活或无法确认时保持运行

    :importance: P2"""
    from management_portal import business_tasks

    owner = business_tasks.process_identity()
    assert owner["pid"] == os.getpid()
    assert business_tasks.process_is_alive(owner)
    assert business_tasks.process_is_alive({**owner, "host": "another-host"})
    assert business_tasks.process_is_alive({**owner, "pid_namespace": "another-namespace"})
    assert not business_tasks.process_is_alive({**owner, "boot_id": "prior-boot", "pid_namespace": "prior-namespace"})
    for identity in (owner, {}):
        job = {"job_id": "active", "status": "running", "process_owner": identity}
        with mock.patch.object(paper_state_store, "merge") as write:
            assert paper_backend._settle_interrupted_review(job) == job
            write.assert_not_called()


def test_review_recovery_preserves_concurrent_completion():
    """评审恢复保留并发完成结果且不覆盖存储

    :importance: P2"""
    job = {"job_id": "race", "status": "running", "process_owner": {"pid": 1}}
    review = {"review_id": "review_one"}
    paper_state_store.append("reviews", review)
    completed = {**job, "status": "done", "review_id": "review_one"}
    paper_state_store.append("review_jobs", completed)
    completed["result"] = review
    with (mock.patch("management_portal.business_tasks.process_is_alive", return_value=False),
          mock.patch.object(paper_state_store, "merge") as write):
        assert paper_backend._settle_interrupted_review(job) == completed
        write.assert_not_called()


def test_review_status_recovers_latest_current_actor_job_contracts(subtests, isolated_portal_case):
    """评审状态按当前用户选择最新任务且未知任务报错"""
    with subtests.test(contract='test_review_status_recovers_latest_current_actor_job'), isolated_portal_case() as case_path:
        older = {'job_id': 'older', 'paper_id': 'paper_one', 'created_by': 'alice', 'status': 'done', 'created_at': 1}
        current = {'job_id': 'current', 'paper_id': 'paper_one', 'created_by': 'alice', 'status': 'running', 'created_at': 2}
        other = {'job_id': 'other', 'paper_id': 'paper_one', 'created_by': 'bob', 'status': 'running', 'created_at': 3}
        for job in (older, current, other):
            paper_state_store.append('review_jobs', job)
        assert paper_backend.get_review_status(paper_id='paper_one', actor='alice') == {'job': current}
        assert paper_backend.get_review_status(paper_id='paper_two', actor='alice') == {'job': None}
    with subtests.test(contract='test_review_status_rejects_unknown_job_without_starting_work'), isolated_portal_case() as case_path:
        with mock.patch.object(paper_backend, '_review_job_by_id', return_value=None), pytest.raises(KeyError):
            paper_backend.get_review_status(job_id='missing', actor='alice')


def test_review_status_checks_actor_and_paper(subtests, isolated_portal_case):
    """评审状态查询校验身份与关联论文"""
    scenarios = (
        ("alice", "paper_one", None),
        ("bob", "paper_one", None),
        ("Alice", "paper_one", None),
        ("alice", "paper_two", ValueError),
        ("bob", "paper_two", ValueError),
        ("", "paper_one", PermissionError),
        ("resident_invalid", "paper_one", ValueError),
    )
    for actor, paper_id, error in scenarios:
        with subtests.test(actor=actor, paper_id=paper_id), isolated_portal_case():
            job = {"job_id": "current", "paper_id": "paper_one", "created_by": "alice", "status": "running"}
            with mock.patch.object(paper_backend, "_review_job_by_id", return_value=job):
                if error is None:
                    assert paper_backend.get_review_status(job_id="current", paper_id=paper_id, actor=actor) == {"job": job}
                else:
                    with pytest.raises(error):
                        paper_backend.get_review_status(job_id="current", paper_id=paper_id, actor=actor)


def test_paper_summary_and_ccf_send_one_task_to_their_role_residents(subtests, isolated_portal_case):
    """论文摘要与 CCF 补全各向自己的角色 Resident 投递一次任务

    简短摘要只投递给 paper:summary，CCF 补全只投递给 paper:ccf；调用只带发起者和角色，由角色 Resident 按自己的设置执行。

    :aspects: functional, behavior
    :design: decision_e37f240e186d
    """
    for feature, scope in [("summary", "paper:summary"), ("ccf", "paper:ccf")]:
        with subtests.test(feature=feature), isolated_portal_case() as case_path:
            paper = {"id": "paper_0123456789ab", "title": "Test fixture", "abstract": "Synthetic material"}
            with mock.patch.object(paper_backend, "_resident_reply", return_value=("受控测试摘要。", {})) as complete:
                if feature == "summary":
                    paper_backend._generate_paper_summary(paper, actor="alice")
                else:
                    paper_backend._generate_ccf_enrichment_summary(paper, [], actor="alice")
            complete.assert_called_once()
            assert complete.call_args.kwargs == {"actor": "alice", "resident_scope": scope}


def test_a_failed_paper_summary_is_an_error_and_is_not_delivered_again():
    """论文摘要失败时直接报错且不重新投递

    paper:summary 返回错误时，摘要生成直接报错，只投递一次；任务文本保留摘要材料对证据性质的限定。

    :aspects: functional, behavior
    :design: decision_e37f240e186d
    """
    paper = {
        "id": "paper_0123456789ab",
        "title": "Failure fixture",
        "abstract": "Synthetic material",
    }
    with mock.patch.object(
        paper_backend, "_resident_reply", side_effect=RuntimeError("paper:summary returned an empty reply"),
    ) as complete, pytest.raises(RuntimeError, match="empty reply"):
        paper_backend._generate_paper_summary(paper, actor="alice")

    complete.assert_called_once()
    # The request keeps the source's own qualifiers (synthetic, example, unverified) instead of promoting them.
    assert "如实保留摘要材料对证据性质的限定" in complete.call_args.args[0]


def test_paper_review_runs_as_portal_business_task(
    tmp_path: Path,
) -> None:
    """论文评审作为后台业务任务运行并保存结果与索引通知

    :importance: P2"""
    paper = {
        "id": "paper_0123456789ab",
        "title": "Durable Review Paper",
    }
    review = {
        "review_id": "review_abcdef012345",
        "paper_id": paper["id"],
        "title": "Review",
        "summary": "Shared result",
        "created_by": "sample_admin",
    }
    with (
        mock.patch.object(paper_backend, "DATA_ROOT", tmp_path),
        mock.patch.object(
            paper_backend,
            "_all_papers",
            return_value=[paper],
        ),
        mock.patch.object(
            paper_backend,
            "get_resource_service",
        ) as resource_service,
        mock.patch(
            "management_portal.business_tasks.start",
            return_value=True,
        ) as start,
    ):
        resource_service.return_value.new_id.return_value = review["review_id"]
        job = paper_backend.start_review(
            {"paper_id": paper["id"]},
            "sample_admin",
        )

        assert job["status"] == "queued"
        assert "runner" not in job
        assert "execution_id" not in job or not job["execution_id"]
        start.assert_called_once()

        with (
            mock.patch.object(
                paper_backend,
                "_generate_ai_review",
                return_value=review,
            ),
            mock.patch.object(
                paper_backend,
                "_review_markdown_path",
                return_value=tmp_path / "review.md",
            ),
            mock.patch.object(
                paper_backend,
                "_write_review_markdown",
            ),
        ):
            result = paper_backend._run_review_job(job["job_id"])

        assert result == review
        stored_job = paper_backend._review_job_by_id(job["job_id"])
        assert stored_job["status"] == "done"
        assert stored_job["result"] == review
        assert paper_backend.list_reviews(paper["id"]) == {"reviews": [review]}
        # The stored review list has no file to watch; the review index is told.
        resource_service.return_value.notify_source_changed.assert_called_once_with(
            resource_type="review", kind="updated",
        )


def test_review_status_by_job_checks_the_job_paper(isolated_portal_case):
    """按 job_id 查询评审状态时，对这个任务所属的论文做读取判定"""
    from management_portal.http_paper import PaperHttp

    with isolated_portal_case():
        paper_state_store.append("review_jobs", {"job_id": "job_one", "paper_id": "paper_one",
                                                 "created_by": "alice", "status": "running"})
        respond, require = mock.Mock(), mock.Mock(side_effect=PermissionError("hidden"))
        http = PaperHttp(respond=respond, exception_response=None, media_file=None, get_auth=None, authorize=None,
                         read_json=None, read_multipart=None, multipart_limit=0, body_too_large=None,
                         backend=lambda: paper_backend, require_resource=require, require_active_actor=None,
                         project_ids=None, notify_index=None)
        with pytest.raises(PermissionError):
            http.review_status(None, {"job_id": ["job_one"]}, "bob")
        require.assert_called_once_with("paper_one", "bob")
        respond.assert_not_called()
        require.side_effect = None
        http.review_status(None, {"job_id": ["job_one"]}, "alice")
        assert respond.call_args.args[2]["job"]["job_id"] == "job_one"
