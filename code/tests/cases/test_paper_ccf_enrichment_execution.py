from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from management_portal import paper_ccf_enrichment, paper_store


@pytest.mark.parametrize('web', [False, True])
def test_ccf_terminal_failure_is_observed_until_explicit_retry(tmp_path, monkeypatch, web):
    paper = {'title': 'Failed enrichment', 'dblp_key': 'conf/test/failed'}
    monkeypatch.setattr(paper_ccf_enrichment, 'CCF_ENRICHMENT_CACHE_ROOT', tmp_path)
    cache = paper_ccf_enrichment._ccf_enrichment_cache_path(paper)
    failed = {'status': 'done' if web else 'error', 'web_status': 'error' if web else '',
              'error': 'quota unavailable', 'web_error': 'quota unavailable',
              'summary_zh': 'Existing summary' if web else '', 'started_at': 1, 'updated_at': 2}
    cache.write_text(json.dumps(failed))
    original = cache.read_bytes()
    with mock.patch.object(paper_ccf_enrichment, '_start_ccf_enrichment_task',
                           return_value={'business_task_id': 'one-explicit-retry'}) as start:
        for _ in range(3):
            assert paper_ccf_enrichment.ccf_paper_enrichment({'paper': paper, 'web': web}, 'alice') == failed
        start.assert_not_called()
        assert cache.read_bytes() == original
        pending = paper_ccf_enrichment.ccf_paper_enrichment({'paper': paper, 'web': web, 'retry': True}, 'alice')
        assert pending['status'] == ('web_pending' if web else 'pending')
        assert pending['business_task_id'] == 'one-explicit-retry'
        paper_ccf_enrichment.ccf_paper_enrichment({'paper': paper, 'web': web}, 'alice')
        start.assert_called_once()
        assert start.call_args.kwargs['web_only'] is web


def test_ccf_client_distinguishes_observation_and_retry():
    subprocess.run(['node', '--test', str(Path(__file__).with_name('javascript') / 'paper_ccf_retry.cjs')],
                   check=True, timeout=30)


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
            paper_ccf_enrichment,
            "CCF_ENRICHMENT_CACHE_ROOT",
            work_root,
        ),
        mock.patch.object(paper_store, "DATA_ROOT", tmp_path / "data"),
        mock.patch(
            "management_portal.business_tasks.start",
            return_value=True,
        ) as start,
    ):
        accepted = paper_ccf_enrichment.ccf_paper_enrichment(
            {"paper": paper},
            actor="sample_admin",
        )
        assert accepted["status"] == "pending"
        assert "runner" not in accepted
        assert accepted["business_task_id"].startswith("paper-ccf:")
        assert "execution_id" not in accepted
        start.assert_called_once()
        request_path = start.call_args.args[2]
        request = json.loads(request_path.read_text(encoding="utf-8"))
        assert request["paper"]["title"] == paper["title"]

        with (
            mock.patch.object(
                paper_ccf_enrichment,
                "_fetch_ccf_enrichment_sources",
                return_value=[
                    {
                        "abstract": "Durable abstract.",
                        "source": "fixture",
                    }
                ],
            ),
            mock.patch.object(
                paper_ccf_enrichment,
                "_generate_ccf_enrichment_summary",
                return_value=(
                    "共享且可恢复的论文摘要。",
                    {"source": "fixture"},
                ),
            ),
        ):
            paper_ccf_enrichment._run_ccf_enrichment_request(request_path)
        assert not request_path.exists()

        completed = json.loads(
            paper_ccf_enrichment._ccf_enrichment_cache_path(paper).read_text(
                encoding="utf-8"
            )
        )
        assert completed["status"] == "done"
        assert completed["summary_zh"] == "共享且可恢复的论文摘要。"
        assert "execution_id" not in completed
