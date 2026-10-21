"""
:importance: P1
:aspects: functional, behavior
"""
from __future__ import annotations

import copy
import json
from unittest import mock

from management_portal import paper_backend, paper_state_store


def test_review_completion_preserves_incomplete_output_without_fabricating_reviewers(tmp_path, monkeypatch, subtests):
    """Paper 评审完整性与失败原文保留"""
    paper = {"id": "paper_0123456789ab", "title": "Evidence", "abstract": "A synthetic study"}
    valid = {
        "title": "Review", "summary": "Evidence summary", "confidence": "Low",
        "overall_recommendation": "Borderline", "author_action_memo": "Check the data",
        "reviewers": [{"id": key, "body": f"Evidence for {key}"} for key in ("A", "B", "C")],
    }
    valid_json = json.dumps(valid)
    cases = [
        ("valid", valid_json, True),
        (
            "narrated_with_trailing_duplicate",
            f"Inspecting the requested paper.\n```json\n{valid_json}\n```\n"
            f"Saved the result for inspection.\n```\n{valid_json}\n```",
            True,
        ),
        ("truncated", '{"reviewers":[', False),
        ("empty", "", False),
    ]
    for field in ("title", "summary", "confidence", "overall_recommendation", "author_action_memo", "reviewers"):
        incomplete = copy.deepcopy(valid)
        incomplete.pop(field)
        cases.append((f"missing_{field}", json.dumps(incomplete), False))
    for name, reviewers in (
        ("missing_reviewer", valid["reviewers"][:2]),
        ("duplicate_reviewer", [valid["reviewers"][0]] * 3),
        ("unknown_reviewer", valid["reviewers"] + [{"id": "D", "body": "Unknown"}]),
        ("empty_body", [{"id": "A", "body": ""}, *valid["reviewers"][1:]]),
        ("nontext_body", [{"id": "A", "body": {"text": "Wrong shape"}}, *valid["reviewers"][1:]]),
    ):
        incomplete = {**valid, "reviewers": reviewers}
        cases.append((name, json.dumps(incomplete), False))
    analysis = "Analysis evidence\n".ljust(80_000, "x") + "BEYOND REVIEW INPUT"
    analysis_path = tmp_path / "analysis.md"
    analysis_path.write_text(analysis, encoding="utf-8")
    paper["analysis_path"] = str(analysis_path)
    monkeypatch.setattr(paper_backend, "ALLOWED_FILE_ROOTS", [tmp_path])
    monkeypatch.setattr(paper_backend, "_all_papers", lambda: [paper])
    monkeypatch.setattr(paper_backend, "_pdf_text_for_paper", lambda _: "PDF")
    monkeypatch.setattr(paper_backend, "_related_papers", lambda *_: [])
    monkeypatch.setattr(paper_backend, "DATA_ROOT", tmp_path)
    for name, content, complete in cases:
        with subtests.test(case=name):
            state = {"job_id": name, "review_id": "review_abcdef012345", "paper_id": paper["id"], "created_by": "alice"}
            stored = []
            output = tmp_path / f"{name}.md"
            monkeypatch.setattr(paper_backend, "_review_job_by_id", lambda _: dict(state))
            monkeypatch.setattr(paper_backend, "_set_review_job", lambda _, patch: state.update(patch) or dict(state))
            monkeypatch.setattr(paper_state_store, "get", lambda *_: None)
            monkeypatch.setattr(paper_state_store, "append", lambda _, record: stored.append(record) or record)
            monkeypatch.setattr(paper_backend, "_review_markdown_path", lambda _: output)
            with mock.patch.object(paper_backend, "_resident_reply", return_value=(content, {"total_tokens": 123})) as call:
                result = paper_backend._run_review_job(name)
            call.assert_called_once()
            assert call.call_args.kwargs["resident_scope"] == "paper:review"
            assert analysis[:80_000] in call.call_args.args[0]
            assert "BEYOND REVIEW INPUT" not in call.call_args.args[0]
            assert result["library_check"]["analysis_excerpt"] == analysis[:900]
            assert "result" not in state
            assert state["review_id"] == result["review_id"]
            assert result == stored[0]
            assert result["review_id"] == "review_abcdef012345"
            assert result["usage"] == {"total_tokens": 123}
            assert state["status"] == ("done" if complete else "error")
            if complete:
                assert result["status"] == "ai_generated"
                assert [item["body"] for item in result["reviewers"]] == [item["body"] for item in valid["reviewers"]]
            else:
                assert result["status"] == "ai_incomplete"
                assert result["reviewers"] == []
                assert result["raw_response"] == content
                assert result["error"] == state["error"]
                if content:
                    assert output.read_text().count(content) == 1
