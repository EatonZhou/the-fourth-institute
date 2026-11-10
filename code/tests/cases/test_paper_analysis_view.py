import json

from management_portal.paper_analysis_view import analysis_view


def test_summary_is_v0_without_writing_or_claiming_a_deep_read(tmp_path):
    summary = tmp_path / "summary.md"
    summary.write_text("中文短摘要")
    summary.with_suffix(".usage.json").write_text(json.dumps({"resident_id": "resident_0123456789ab", "total_tokens": 99}))
    missing = tmp_path / "analysis.md"
    view = analysis_view("", missing, summary)
    assert view["kind"] == "summary" and view["body"] == "中文短摘要"
    assert view["provenance"]["total_tokens"] == 99
    assert not missing.exists()


def test_deep_read_keeps_body_and_hides_managed_header(tmp_path):
    path = tmp_path / "analysis.md"
    raw = '---\nschema: tfi.paper_analysis.v1\nllm_resident_id: resident_0123456789ab\nllm_model: 未记录\n---\n\n# Analysis\n正文'
    path.write_text(raw)
    path.with_suffix(".usage.json").write_text(json.dumps({"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}))
    view = analysis_view(raw, path, None)
    assert view["body"] == "# Analysis\n正文" and view["kind"] == "analysis"
    assert view["provenance"]["total_tokens"] == 14
    assert view["provenance"]["model"] == "未记录"
    assert path.read_text() == raw


def test_ordinary_yaml_is_preserved(tmp_path):
    raw = '---\ntitle: user notes\n---\n正文'
    assert analysis_view(raw, None, None)["body"] == raw
