from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from management_portal import paper_backend


def test_ccf_enrichment_uses_portal_business_task_and_shared_result(
    tmp_path: Path,
) -> None:
    """CCF 论文补全作为 Portal 业务任务运行，结果写入共享缓存且不带 execution_id"""
    paper = {
        "title": "A Durable Paper",
        "authors": "A. Author",
        "dblp_key": "conf/test/durable",
    }
    work_root = tmp_path / "ccf-enrichment"
    with (
        mock.patch.object(
            paper_backend,
            "CCF_ENRICHMENT_CACHE_ROOT",
            work_root,
        ),
        mock.patch.object(paper_backend, "DATA_ROOT", tmp_path / "data"),
        mock.patch(
            "management_portal.business_tasks.start",
            return_value=True,
        ) as start,
    ):
        accepted = paper_backend.ccf_paper_enrichment(
            {"paper": paper},
            actor="sample_admin",
        )
        assert accepted["status"] == "pending"
        assert "runner" not in accepted
        assert accepted["business_task_id"].startswith("paper-ccf:")
        assert "execution_id" not in accepted
        start.assert_called_once()
        request_path = paper_backend._ccf_enrichment_request_path(
            paper_backend._ccf_enrichment_cache_path(paper)
        )
        request = json.loads(request_path.read_text(encoding="utf-8"))
        assert request["paper"]["title"] == paper["title"]

        with (
            mock.patch.object(
                paper_backend,
                "_fetch_ccf_enrichment_sources",
                return_value=[
                    {
                        "abstract": "Durable abstract.",
                        "source": "fixture",
                    }
                ],
            ),
            mock.patch.object(
                paper_backend,
                "_generate_ccf_enrichment_summary",
                return_value=(
                    "共享且可恢复的论文摘要。",
                    {"source": "fixture"},
                ),
            ),
        ):
            paper_backend._run_ccf_enrichment_request(request_path)

        completed = json.loads(
            paper_backend._ccf_enrichment_cache_path(paper).read_text(
                encoding="utf-8"
            )
        )
        assert completed["status"] == "done"
        assert completed["summary_zh"] == "共享且可恢复的论文摘要。"
        assert "execution_id" not in completed
