"""
:importance: P1
:aspects: functional, behavior
"""
from __future__ import annotations

from management_portal import paper_projection, paper_reading_jobs


def _result(message: str = "# Analysis\n\n" + paper_reading_jobs.DEEP_READ_HEADINGS) -> dict:
    return {
        "resident_id": "resident_deepread01",
        "message_id": "msg_deepread01",
        "lineage_id": "lineage_deepread01",
        "message": message,
        "usage": {"model": "test-model"},
    }


def _install_job(monkeypatch, tmp_path, state: dict) -> None:
    monkeypatch.setattr(
        paper_reading_jobs,
        "_latest_deep_read_job",
        lambda **_kwargs: dict(state),
    )
    monkeypatch.setattr(
        paper_reading_jobs,
        "_set_deep_read_job",
        lambda _job_id, patch: state.update(patch) or dict(state),
    )
    monkeypatch.setattr(
        paper_reading_jobs,
        "get_paper_detail",
        lambda _paper_id: {
            "paper": {"id": state["paper_id"], "title": "Paper"}
        },
    )
    monkeypatch.setattr(
        paper_reading_jobs,
        "_local_pdf_for_paper",
        lambda _paper: tmp_path / "paper.pdf",
    )
    monkeypatch.setattr(
        paper_reading_jobs,
        "_extract_pdf_text",
        lambda *_args, **_kwargs: "paper text " * 200,
    )
    monkeypatch.setattr(
        paper_reading_jobs,
        "_write_analysis_file",
        lambda *_args: tmp_path / "analysis.md",
    )
    monkeypatch.setattr(
        paper_reading_jobs,
        "_update_paper_fields",
        lambda *_args, **_kwargs: {},
    )


def test_deep_read_submits_its_task_to_the_role_resident_and_persists_binding(
    monkeypatch,
    tmp_path,
) -> None:
    """Deep Read 把任务交给 paper:deep-read 角色 Resident 并保存输入绑定

    Deep Read 只向已绑定的 paper:deep-read Resident 投递一次任务（不附带 Provider、模型或系统提示），并在 job 中保存 Resident 与规范输入消息 ID，成功后进入 done。

    :design: decision_e37f240e186d
    """
    state = {
        "job_id": "deep-read-test",
        "paper_id": "paper_0123456789ab",
        "status": "queued",
        "created_by": "sample_admin",
    }
    _install_job(monkeypatch, tmp_path, state)
    observed: dict = {}

    def complete(**kwargs):
        observed.update(kwargs)
        kwargs["on_submitted"](paper_projection.receipt_from_resident_handle({
            "resident_id": "resident_deepread01",
            "message_id": "msg_deepread01",
            "lineage_id": "lineage_deepread01",
        }))
        return _result()

    monkeypatch.setattr(
        "management_portal.paper_projection.turn",
        complete,
    )

    paper_reading_jobs._run_deep_read_job(state["job_id"])

    assert observed["resident_scope"] == "paper:deep-read"
    assert observed["business_ref"] == "paper-deep-read:deep-read-test"
    # The bound role Resident runs the task with its own Provider and model.
    assert not {"provider", "provider_id", "model", "system_prompt"} & set(observed)
    assert "## 可迁移价值与适用边界" in observed["prompt"]
    assert "RobotWorker" not in observed["prompt"]
    assert state["resident_id"] == "resident_deepread01"
    assert state["resident_input_message_id"] == "msg_deepread01"
    assert "resident_message_id" not in state
    assert state["status"] == "done"


def test_deep_read_recovery_resumes_bound_resident_without_resubmitting(
    monkeypatch,
    tmp_path,
) -> None:
    """恢复论文精读任务时继续观察已绑定 Resident 的原输入，不重新提交

    :importance: P2
    :aspects: functional
    """
    state = {
        "job_id": "deep-read-recovery",
        "paper_id": "paper_0123456789ab",
        "status": "running",
        "created_by": "sample_admin",
        "resident_id": "resident_deepread01",
        "resident_input_message_id": "msg_deepread01",
        "resident_handoff": paper_projection.receipt_from_resident_handle({
            "resident_id": "resident_deepread01",
            "lineage_id": "lineage_deepread01",
            "message_id": "msg_deepread01",
        }),
        "lineage_id": "lineage_deepread01",
    }
    _install_job(monkeypatch, tmp_path, state)
    calls = {"resume": 0, "submit": 0}

    def resume(**kwargs):
        calls["resume"] += 1
        assert kwargs["resident_id"] == "resident_deepread01"
        assert kwargs["message_id"] == "msg_deepread01"
        return _result("# Recovered\n" + paper_reading_jobs.DEEP_READ_HEADINGS)

    def submit(**_kwargs):
        calls["submit"] += 1
        raise AssertionError("recovery must not submit another Resident job")

    monkeypatch.setattr(
        "management_portal.resident_handoff.resume_and_wait",
        resume,
    )
    monkeypatch.setattr(
        "management_portal.paper_projection.turn",
        submit,
    )

    paper_reading_jobs._run_deep_read_job(state["job_id"])

    assert calls == {"resume": 1, "submit": 0}
    assert state["status"] == "done"


def test_a_failed_deep_read_is_an_error_and_is_not_delivered_again(
    monkeypatch,
    tmp_path,
) -> None:
    """Deep Read 执行失败时任务出错且不重新投递

    角色 Resident 的执行失败后，Deep Read job 进入 error 并保留原输入消息 ID，同一任务不会再投递给 Resident。

    :design: decision_e37f240e186d
    """
    state = {
        "job_id": "deep-read-failure",
        "paper_id": "paper_0123456789ab",
        "status": "queued",
        "created_by": "sample_admin",
    }
    _install_job(monkeypatch, tmp_path, state)
    calls: list[str] = []

    def complete(**kwargs):
        calls.append(str(kwargs.get("business_ref") or ""))
        kwargs["on_submitted"](paper_projection.receipt_from_resident_handle({
            "resident_id": "resident_deepread01",
            "message_id": "msg_deepread01",
            "lineage_id": "lineage_deepread01",
        }))
        raise RuntimeError("Resident turn failed")

    monkeypatch.setattr("management_portal.paper_projection.turn", complete)

    paper_reading_jobs._run_deep_read_job(state["job_id"])

    assert calls == ["paper-deep-read:deep-read-failure"]
    assert state["resident_input_message_id"] == "msg_deepread01"
    assert state["status"] == "error"
    assert "Resident turn failed" in state["error"]


def test_deep_read_that_is_not_an_analysis_is_an_error_and_is_not_saved(monkeypatch, tmp_path) -> None:
    """Paper Deep Read 返回的不是分析（如叙述工具调用）时任务失败且不保存

    Deep Read 回复缺少规定小节（少于三分之一）时任务以错误结束并说明原因与输出开头，不写入 analysis、不标记 analysis_ready。
    """
    state = {"job_id": "deep-read-narration", "paper_id": "paper_0123456789ab", "status": "queued", "created_by": "sample_admin"}
    _install_job(monkeypatch, tmp_path, state)
    written = []
    monkeypatch.setattr(paper_reading_jobs, "_write_analysis_file", lambda *args: written.append(args) or tmp_path / "analysis.md")
    narration = "我先查询当前 Resident 的上下文与产物位置，同时读取环境配置。请稍候。```bash\ncat resident.md\n```"
    monkeypatch.setattr("management_portal.paper_projection.turn", lambda **_kwargs: _result(narration))

    paper_reading_jobs._run_deep_read_job(state["job_id"])

    assert state["status"] == "error"
    assert "9 个必需小节只出现 0 个" in state["error"]
    assert "我先查询当前 Resident" in state["error"]
    assert written == []
