from __future__ import annotations

import pytest

from management_portal.execution.harness_content_recall import (
    MAX_RECALL_READ_CHARS,
    read_text_slice,
    search_text,
)


def test_search_text_returns_small_exact_offset_addressable_snippet_contracts(subtests, isolated_portal_case):
    """文本召回返回可按偏移读取的小片段并支持中文匹配"""
    with subtests.test(contract='test_search_text_returns_small_exact_offset_addressable_snippet'), isolated_portal_case() as case_path:
        content = "header\n" + "ordinary line\n" * 2_000
        needle = "UNIQUE_RESOURCE_NEEDLE=portal-harness"
        content += f"before\n{needle}\nafter\n"

        result = search_text(
            content,
            needle,
            max_hits=3,
            context_chars=240,
        )

        assert result["match_mode"] == "exact_phrase"
        assert result["hit_count"] == 1
        hit = result["hits"][0]
        assert needle in hit["content"]
        assert hit["next_offset"] - hit["offset"] < 600
        recalled = read_text_slice(
            content,
            offset=hit["offset"],
            limit=hit["next_offset"] - hit["offset"],
        )
        assert recalled["content"] == hit["content"]
    with subtests.test(contract='test_search_text_falls_back_to_ranked_terms'), isolated_portal_case() as case_path:
        content = (
            "alpha only\n"
            "resource cache unrelated\n"
            "the portal resource cache retains an exact revision hash\n"
            "omega\n"
        )

        result = search_text(
            content,
            "portal cache revision unavailable phrase",
            context_chars=100,
        )

        assert result["match_mode"] == "query_terms"
        assert result["hit_count"] >= 1
        assert "exact revision hash" in result["hits"][0]["content"]
        assert result["hits"][0]["score"]["matched_term_count"] >= 3
    with subtests.test(contract='test_ranked_term_score_only_uses_terms_returned_in_the_snippet'), isolated_portal_case() as case_path:
        content = (
            "AgentHarness title\n"
            + "unrelated introduction\n" * 20
            + "AgentHarness plugin 不识别 Resident business types\n"
        )

        result = search_text(
            content,
            "AgentHarness 不识别 Resident",
            context_chars=240,
        )

        assert result["match_mode"] == "query_terms"
        assert "不识别 Resident" in result["hits"][0]["content"]
    with subtests.test(contract='test_search_text_supports_cjk_partial_matching'), isolated_portal_case() as case_path:
        content = "前言\n统一资源索引可以按统一资源 ID 定位文件。\n结尾"

        result = search_text(content, "资源索引定位")

        assert result["match_mode"] == "query_terms"
        assert result["hit_count"] == 1
        assert "统一资源索引" in result["hits"][0]["content"]
    with subtests.test(contract='test_recall_limits_and_invalid_offsets_are_bounded'), isolated_portal_case() as case_path:
        content = "x" * (MAX_RECALL_READ_CHARS + 100)
        selected = read_text_slice(content, offset=0, limit=10**9)

        assert len(selected["content"]) == MAX_RECALL_READ_CHARS
        with pytest.raises(ValueError, match="outside"):
            read_text_slice(content, offset=len(content) + 1)
        with pytest.raises(ValueError, match="query required"):
            search_text(content, "")
