from __future__ import annotations

from unittest import mock

from management_portal import paper_backend


def _paper(paper_id: str, title: str, **fields: object) -> dict:
    return {
        "id": paper_id,
        "title": title,
        "year": "2026",
        "venue": "arXiv",
        "has_pdf": False,
        "has_analysis": False,
        "has_summary": False,
        **fields,
    }


def test_library_text_search_prioritizes_title_before_metadata_matches_contracts(subtests, isolated_portal_case):
    """论文文本搜索优先标题与精确标签再匹配元数据"""
    with subtests.test(contract='test_library_text_search_prioritizes_title_before_metadata_matches'), isolated_portal_case() as case_path:
        papers = [
            _paper(
                "paper_000000000001",
                "A Reliability Study",
                abstract="Closing the deployment gap in industrial robotics.",
            ),
            _paper(
                "paper_000000000002",
                "Understanding the Generalization Gap",
            ),
            _paper(
                "paper_ac946c60be00",
                "GaP: A Graph-as-Policy Multi-Agent Self-Learning Harness",
            ),
        ]
        with (
            mock.patch.object(paper_backend, "resource_index_papers", return_value=papers),
            mock.patch.object(paper_backend, "_all_reviews", return_value=[]),
        ):
            result = paper_backend.list_library({"q": "GaP", "compact": "1"}, actor="sample_admin")

        assert [paper["id"] for paper in result["papers"]] == [
            "paper_ac946c60be00",
            "paper_000000000002",
            "paper_000000000001",
        ]
    with subtests.test(contract='test_library_text_search_prioritizes_exact_tag_before_abstract_match'), isolated_portal_case() as case_path:
        papers = [
            _paper(
                "paper_000000000001",
                "A Reliability Study",
                abstract="Variational automation for commercial robots.",
            ),
            _paper(
                "paper_000000000002",
                "Robot Learning Harness",
                tags=["Variational Automation"],
            ),
        ]
        with (
            mock.patch.object(paper_backend, "resource_index_papers", return_value=papers),
            mock.patch.object(paper_backend, "_all_reviews", return_value=[]),
        ):
            result = paper_backend.list_library(
                {"q": "Variational Automation", "compact": "1"},
                actor="sample_admin",
            )

        assert [paper["id"] for paper in result["papers"]] == [
            "paper_000000000002",
            "paper_000000000001",
        ]


def test_library_preview_keeps_tree_and_export_rows_then_hydrates_next_page(isolated_portal_case):
    """论文预览保留完整列表与导出字段并按页补全摘要"""
    with isolated_portal_case():
        papers = [
            _paper(f"paper_{index:012d}", f"Paper {index}", abstract=f"Abstract {index}", session="Main")
            for index in range(3)
        ]
        with (
            mock.patch.object(paper_backend, "resource_index_papers", return_value=papers),
            mock.patch.object(paper_backend, "_all_reviews", return_value=[]),
        ):
            full = paper_backend.list_library({"compact": "1"}, actor="sample_admin")
            library = paper_backend.list_library({"compact": "1", "preview_limit": "1"}, actor="sample_admin")
            page = paper_backend.list_library(
                {"compact": "1", "rows_only": "1", "offset": "1", "limit": "2"},
                actor="sample_admin",
            )

        assert len(library["papers"]) == library["summary"]["visible_count"] == 3
        assert full["papers"][1]["abstract"] == "Abstract 1"
        assert library["papers"][0]["abstract"] == "Abstract 2"
        assert [row["title"] for row in library["papers"]] == ["Paper 2", "Paper 1", "Paper 0"]
        assert "abstract" not in library["papers"][1]
        assert library["papers"][1]["session"] == "Main"
        assert [row["abstract"] for row in page["papers"]] == ["Abstract 1", "Abstract 0"]
        assert "summary" not in page and "folders" not in page
