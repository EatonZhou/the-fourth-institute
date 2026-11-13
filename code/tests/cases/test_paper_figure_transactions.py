""":importance: P1"""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time
from types import SimpleNamespace
from unittest import mock

import pytest

from management_portal import paper_figure_extraction, paper_figure_jobs, paper_figures, paper_projection, paper_store


@pytest.fixture
def figure_store(tmp_path, monkeypatch):
    paper = {"id": "paper_0123456789ab", "_store_root": str(tmp_path), "created_by": "alice"}
    path = tmp_path / "figures.json"
    path.write_text(json.dumps([{"figure_id": "fig-001", "explanation_status": "queued", "generated": [{"generated_id": "gen-one", "status": "queued"}]}]))
    monkeypatch.setattr(paper_store, "_ensure_data_root", lambda: None)
    monkeypatch.setattr(paper_store, "_paper_by_id", lambda identifier: paper)
    monkeypatch.setattr(paper_figures, "_paper_by_id", lambda identifier: paper)
    monkeypatch.setattr(paper_figure_jobs, "_paper_by_id", paper_figures._paper_by_id)
    monkeypatch.setattr(paper_figures, "_figures_json_path", lambda value: path)
    monkeypatch.setattr(paper_figures, "_notify_paper_projection", lambda *args, **kwargs: None)
    return paper, path


def run_figure(figure_store, target, *args, generated_id="", **kwargs):
    """Use the real lease/file transaction boundary with a deterministic worker."""
    from management_portal import paper_job_ownership
    from management_portal.paper_figure_ownership import FigureAdapter

    paper, _ = figure_store
    adapter = FigureAdapter(paper["id"], "fig-001", generated_id)
    results = []
    assert paper_job_ownership.run(adapter.identity, lambda _: results.append(target(*args, **kwargs)), adapter=adapter)
    return results[0]


def test_concurrent_figure_updates_preserve_each_completion(figure_store, monkeypatch):
    """并发更新插图与生成项保留各自完成字段"""
    paper, path = figure_store
    original = paper_figures._load_figures

    def delayed(value):
        result = original(value)
        time.sleep(0.005)
        return result

    monkeypatch.setattr(paper_figures, "_load_figures", delayed)
    def update(index):
        if index % 2:
            paper_figure_jobs._set_figure_generated_item(paper["id"], "fig-001", "gen-one", {f"field{index}": index})
        else:
            paper_figure_jobs._set_figure_fields(paper["id"], "fig-001", {f"field{index}": index})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(update, range(16)))
    result = json.loads(path.read_text())[0]
    for index in range(16):
        target = result["generated"][0] if index % 2 else result
        assert target[f"field{index}"] == index
    assert list(path.parent.glob("*.tmp")) == []


def test_figure_failure_is_not_published_as_ai_success(figure_store):
    """插图解释失败时记录错误且不发布成功内容"""
    paper, path = figure_store
    with mock.patch.object(paper_figure_jobs, "_generate_figure_explanation", side_effect=RuntimeError("provider unavailable")):
        result = run_figure(figure_store, paper_figure_jobs.run_figure_explain_job, paper["id"], "fig-001", "alice")
    assert result["figure"]["explanation_status"] == "error"
    assert result["figure"]["explanation_error"] == "provider unavailable"
    assert not json.loads(path.read_text())[0].get("explanation")


def test_fast_figure_completion_is_not_overwritten_by_launcher(figure_store):
    """插图任务快速完成后启动器不覆盖完成状态"""
    from management_portal import business_tasks

    paper, path = figure_store
    def finish(*args, **kwargs):
        paper_figure_jobs._set_figure_fields(paper["id"], "fig-001", {"explanation_status": "done"})

    with mock.patch.object(business_tasks, "start", side_effect=finish):
        assert paper_figure_jobs._start_figure_explain_task(paper, "fig-001", "alice") == ""
    assert json.loads(path.read_text())[0]["explanation_status"] == "done"


def test_paper_figure_media_uses_authorized_resident_upload(figure_store):
    """论文插图通过带当前身份的 Resident 上传接口传入媒体"""
    from management_portal import society_service
    from management_portal.workbench_projections import native

    paper, path = figure_store
    image = path.parent / "figure.png"
    image.write_bytes(b"test-image")
    uploader = mock.Mock(return_value={"path": "/authorized/image.png", "content_type": "image/png"})
    with mock.patch.object(native, "ensure", return_value={"resident_id": "resident_figure01"}), mock.patch.object(
        society_service, "get_society_service", return_value=SimpleNamespace(workspace=SimpleNamespace(save_workspace_pasted_image=uploader)),
    ), mock.patch.object(
        paper_projection,
        "turn",
        return_value={
            "message": "Image observed",
            "usage": {},
            "invocation": paper_projection.receipt_from_resident_handle({
                "resident_id": "resident_figure01",
                "lineage_id": "lineage_paper01",
                "message_id": "msg_image_observed",
            }),
        },
    ) as turn:
        run_figure(figure_store, paper_figure_jobs._run_figure_resident, "Explain", paper, {"figure_id": "fig-001"}, "explain", images=[image])
    payload = uploader.call_args.args[0]
    assert payload["resident_id"] == "resident_figure01"
    assert uploader.call_args.kwargs["actor"] == "alice"
    assert turn.call_args.kwargs["input_media"][0]["source"] == "/authorized/image.png"


def test_paper_figure_rejects_outside_images_before_creating_resident(figure_store, tmp_path):
    """论文插图在创建 Resident 前拒绝论文目录外图片"""
    from management_portal.workbench_projections import native

    paper, path = figure_store
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"test")
    paper["_store_root"] = str(tmp_path / "paper")
    Path(paper["_store_root"]).mkdir()
    with mock.patch.object(native, "ensure") as ensure, pytest.raises(PermissionError):
        paper_figure_jobs._run_figure_resident("Explain", paper, {"figure_id": "fig-001"}, "explain", images=[outside])
    ensure.assert_not_called()


def test_paper_registration_preserves_complete_tag_tokens(subtests, isolated_portal_case):
    """论文登记完整保留字符串或列表中的标签词"""
    for tags in ["synthetic, optimization", ["synthetic", "optimization"]]:
        with subtests.test(tags=tags), isolated_portal_case() as case_path:
            result = paper_store._clean_paper({"id": "paper_0123456789ab", "title": "Synthetic paper", "tags": tags}, "alice")
            assert result["tags"] == ["synthetic", "optimization"]


def test_figure_persists_input_binding_before_waiting(figure_store):
    """插图调用在等待前持久化已分配输入绑定"""

    paper, path = figure_store
    def invoke(_name, **options):
        options["on_assigned"]({
                "resident_id": "resident_figure01",
                "lineage_id": "lineage_paper01",
                "status": "assigned",
            })
        binding = json.loads(path.read_text())[0]["explanation_invocation"]
        assert binding["input_message_id"] == ""
        assert "message_id" not in binding
        assert binding["requested_message_id"] == options["message_id"]
        assert binding["actor"] == "alice"
        raise RuntimeError("process interrupted")

    with mock.patch.object(paper_projection.native, "turn", side_effect=invoke), pytest.raises(RuntimeError, match="process interrupted"):
        run_figure(figure_store, paper_figure_jobs._run_figure_resident, "Explain", paper, {"figure_id": "fig-001"}, "explain")
    assert json.loads(path.read_text())[0]["explanation_invocation"]["resident_id"] == "resident_figure01"


@pytest.mark.parametrize("generated_id", ["", "gen-one"])
def test_figure_resumes_same_input_without_upload_or_submit(figure_store, generated_id):
    """插图恢复复用原输入且不重新上传或提交"""
    from management_portal import resident_handoff
    from management_portal.workbench_projections import native

    paper, path = figure_store
    figure = json.loads(path.read_text())[0]
    operation = figure["generated"][0] if generated_id else figure
    operation["invocation" if generated_id else "explanation_invocation"] = {
        **paper_projection.receipt_from_resident_handle({
            "resident_id": "resident_figure01",
            "lineage_id": "lineage_paper01",
            "message_id": "msg_original",
        }),
        "actor": "alice",
    }
    with mock.patch.object(resident_handoff, "resume_and_wait", return_value={"message": "Recovered image result", "resident_id": "resident_figure01", "lineage_id": "lineage_paper01", "message_id": "msg_original"}) as resume, mock.patch.object(paper_projection, "turn") as submit, mock.patch.object(native, "ensure") as ensure:
        content, usage = run_figure(figure_store, lambda: paper_figure_jobs._run_figure_resident("Unused", paper, figure, "explain", images=[path.parent / "not-staged.png"], generated_id=generated_id), generated_id=generated_id)
    assert content == "Recovered image result"
    assert resume.call_args.kwargs["message_id"] == "msg_original"
    submit.assert_not_called()
    ensure.assert_not_called()


def test_figure_recovery_writes_cancelled_input_as_artifact_error(figure_store):
    """插图恢复遇到取消输入时写入产物错误且不重提交"""
    from management_portal import resident_handoff

    paper, path = figure_store
    path.write_text(json.dumps([{
        "figure_id": "fig-001",
        "explanation_status": "running",
        "explanation_invocation": {
            **paper_projection.receipt_from_resident_handle({
                "resident_id": "resident_figure01",
                "lineage_id": "lineage_paper01",
                "message_id": "msg_original",
            }),
            "actor": "alice",
        },
    }]))
    def explain(current_paper, figure):
        return paper_figure_jobs._run_figure_resident("unused", current_paper, figure, "explain")

    with mock.patch.object(paper_figure_jobs, "_generate_figure_explanation", side_effect=explain), mock.patch.object(resident_handoff, "resume_and_wait", side_effect=resident_handoff.ResidentInputTerminated({"state": "cancelled"})), mock.patch.object(paper_projection, "turn") as submit:
        run_figure(figure_store, paper_figure_jobs.run_figure_explain_job, paper["id"], "fig-001", "alice")
    result = json.loads(path.read_text())[0]
    assert result["explanation_status"] == "error"
    assert result["explanation_error"] == "Resident input ended with cancelled"
    submit.assert_not_called()


def test_synchronous_figure_generation_also_registers_before_invocation(figure_store):
    """插图生成只在正式队列认领后执行，并保存输入回执"""
    paper, path = figure_store
    def generate(current_paper, figure, request, generated_id, **options):
        stored = json.loads(path.read_text())[0]["generated"]
        assert any(item["generated_id"] == generated_id for item in stored)
        paper_figure_jobs._set_figure_generated_item(
            paper["id"],
            "fig-001",
            generated_id,
            {"invocation": paper_projection.receipt_from_resident_handle({
                "message_id": "msg_current",
                "resident_id": "resident_figure01",
                "lineage_id": "lineage_paper01",
            })},
        )
        return "figures/generated/test.png", "Generated", {}

    with mock.patch.object(paper_figure_jobs, "_generate_figure_image", side_effect=generate):
        payload = {"paper_id": paper["id"], "figure_id": "fig-001", "request": "Change colors", "generated_id": "gen-one"}
        with pytest.raises(paper_figure_jobs.paper_job_ownership.OwnershipLost):
            paper_figure_jobs.generate_figure_draft(payload, "alice")
        run_figure(figure_store, paper_figure_jobs.generate_figure_draft, payload, "alice", generated_id="gen-one")
    completed = next(item for item in json.loads(path.read_text())[0]["generated"] if item.get("status") == "done")
    assert completed["invocation"]["input_message_id"] == "msg_current"


def test_figure_heading_detection_requires_graphics_below_not_above(subtests, isolated_portal_case):
    """图标题裁剪依据下方图形与同编号图注确定区域"""
    for graphic_above, lower_number in [(False, ""), (True, ""), (True, "1"), (True, "2")]:
        with subtests.test(graphic_above=graphic_above, lower_number=lower_number), isolated_portal_case() as case_path:
            from PIL import Image, ImageDraw

            image = Image.new("RGB", (600, 800), "white")
            drawing = ImageDraw.Draw(image)
            drawing.rectangle((50, 110, 220, 120), fill="black")
            drawing.rectangle((50, 180, 300, 210), fill="blue")
            drawing.rectangle((50, 240, 350, 270), fill="orange")
            if graphic_above:
                drawing.rectangle((50, 20, 300, 90), fill="red")
            caption = {"page_width": 600, "page_height": 800, "y_min": 110, "y_max": 120, "number": "1",
                       "page_lines": [{"x_min": 50, "x_max": 220, "y_min": 110, "y_max": 120, "text": "Figure 1"}]}
            if lower_number:
                caption["page_lines"].append({"x_min": 50, "x_max": 350, "y_min": 300, "y_max": 310,
                                              "text": f"Figure {lower_number}. A chart showing the measured values."})
            if lower_number == "1":
                drawing.rectangle((45, 124, 355, 280), outline="black")
                drawing.rectangle((25, 115, 35, 127), fill="green")
                drawing.rectangle((180, 294, 230, 306), fill="purple")
                caption["page_lines"].extend([
                    {"x_min": 25, "x_max": 35, "y_min": 115, "y_max": 128, "text": "1.0"},
                    {"x_min": 180, "x_max": 230, "y_min": 294, "y_max": 307, "text": "Toy budget"},
                ])
            cropped = paper_figure_extraction._crop_figure_below_heading(image, caption)
            if graphic_above and lower_number != "1":
                assert cropped is None
            else:
                colors = {color for count, color in cropped.getcolors(cropped.width * cropped.height)}
                assert (0, 0, 255) in colors
                assert (255, 165, 0) in colors
                assert (255, 0, 0) not in colors
                if lower_number == "1":
                    counts = {color: count for count, color in cropped.getcolors(cropped.width * cropped.height)}
                    assert counts[(0, 128, 0)] == 11 * 13
                    assert counts[(128, 0, 128)] == 51 * 13
