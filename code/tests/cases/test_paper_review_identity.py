from __future__ import annotations

from management_portal.paper_resource_index import PaperManifestAdapter, PaperReviewAdapter

from pathlib import Path
from unittest import mock

from management_portal import paper_backend, paper_projection, resource_index_backend


def test_figure_usage_exposes_resident_message_identity_contracts(tmp_path, subtests, isolated_portal_case):
    """图表 Resident 用量记录规范输入身份，Review 索引为单一公开资源

    图表解释的用量带有角色 Resident 与规范输入消息 ID（不保留 message_id），事件文件记录同一身份；Paper Review 适配器把一篇 Review 索引为一个公开资源。

    :design: decision_e37f240e186d
    """
    with subtests.test(contract='test_figure_usage_exposes_resident_message_identity'), isolated_portal_case() as case_path:
        tmp_path = case_path
        receipt = paper_projection.receipt_from_resident_handle({
            'resident_id': 'resident_figure01',
            'lineage_id': 'lineage_paper01',
            'message_id': 'message_figure01',
        })
        execution_result = {
            'message': 'Figure analysis',
            'invocation': receipt,
            'usage': {'input_tokens': 12, **receipt},
        }
        with mock.patch('management_portal.paper_projection.turn', return_value=execution_result):
            content, usage = paper_backend._run_figure_resident('Analyze this figure', {'_store_root': str(tmp_path), 'created_by': 'alice'}, {'figure_id': 'figure-1'}, 'analysis')
        assert content == 'Figure analysis'
        assert usage['resident_id'] == 'resident_figure01'
        assert usage['input_message_id'] == 'message_figure01'
        assert 'message_id' not in usage
        assert '"resident_id": "resident_figure01"' in Path(usage['events_path']).read_text(encoding='utf-8')
    with subtests.test(contract='test_paper_review_adapter_indexes_review_as_one_public_resource'), isolated_portal_case() as case_path:
        tmp_path = case_path
        paper_store = paper_backend.PaperStore(tmp_path / 'papers')
        paper_store.ensure()
        source = tmp_path / 'review_abcdef012345.md'
        source.write_text('# Review\n', encoding='utf-8')
        review = {'review_id': 'review_abcdef012345', 'paper_id': 'paper_0123456789ab', 'title': 'Review', 'summary': 'Summary', 'created_by': 'alice', 'created_at': 123, 'status': 'ai_generated'}
        with mock.patch.object(paper_backend, '_all_reviews', return_value=[review]), mock.patch.object(paper_backend, '_review_markdown_path', return_value=source), mock.patch.object(paper_backend, 'PAPER_STORE', paper_store), mock.patch.object(paper_backend, '_paper_by_id', return_value={'id': 'paper_0123456789ab', 'title': 'Paper', 'project_ids': ['RoboLab']}):
            records, state = PaperReviewAdapter().scan()
        assert state == {'review_count': 1, 'live_review_count': 1, 'markdown_review_count': 0, 'resource_errors': []}
        assert len(records) == 1
        assert records[0]['resource_id'] == 'review_abcdef012345'
        assert records[0]['resource_type'] == 'review'
        assert records[0]['source']['path'] == str(source)
        assert records[0]['attributes']['relations'] == [{'kind': 'review_of', 'target_id': 'paper_0123456789ab'}]
        assert records[0]['url'] == '/paper?paper=paper_0123456789ab'
    with subtests.test(contract='test_paper_review_adapter_indexes_persisted_markdown_without_live_row'), isolated_portal_case() as case_path:
        tmp_path = case_path
        paper_store = paper_backend.PaperStore(tmp_path / 'papers')
        reviews_dir = paper_store.items_root / '2026' / 'example' / 'reviews'
        reviews_dir.mkdir(parents=True)
        source = reviews_dir / 'review_abcdef012345.md'
        source.write_text('---\nschema: tfi.paper_review.v1\nreview_id: "review_abcdef012345"\npaper_id: "paper_0123456789ab"\ncreated_by: "alice"\ncreated_at: 123\nstatus: ai_generated\n---\n\n# Persisted Review\n\nPersisted summary.\n\n## Reviewer A\n', encoding='utf-8')
        with mock.patch.object(paper_backend, 'PAPER_STORE', paper_store), mock.patch.object(paper_backend, '_all_reviews', return_value=[]), mock.patch.object(paper_backend, '_paper_by_id', return_value={'id': 'paper_0123456789ab', 'title': 'Paper', 'project_ids': ['RoboLab']}):
            records, state = PaperReviewAdapter().scan()
        assert state == {'review_count': 1, 'live_review_count': 0, 'markdown_review_count': 1, 'resource_errors': []}
        assert records[0]['resource_id'] == 'review_abcdef012345'
        assert records[0]['title'] == 'Persisted Review'
        assert records[0]['summary'] == 'Persisted summary.'
        store = resource_index_backend.IndexStore()
        store.publish_snapshot('paper_review', records, state)
        assert store.get('review_abcdef012345', actor='alice')['source']['path'] == str(source)


def test_review_builders_keep_the_allocated_canonical_id() -> None:
    """结构化与文本回退评审均保留已分配规范标识"""
    paper = {"id": "paper_0123456789ab", "title": "Example"}
    review_id = "review_abcdef012345"
    structured = paper_backend._coerce_ai_review(
        {
            "title": "Review", "summary": "Summary",
            "overall_recommendation": "Borderline", "confidence": "Low",
            "author_action_memo": "Check evidence",
            "reviewers": [{"id": key, "body": f"Review {key}"} for key in ("A", "B", "C")],
        },
        raw_content="Review body",
        paper=paper,
        related=[],
        usage={},
        actor="alice",
        review_id=review_id,
        title="",
    )
    fallback = paper_backend._fallback_ai_review_from_text(
        "Review body",
        paper=paper,
        related=[],
        usage={},
        actor="alice",
        review_id=review_id,
        title="",
    )

    assert structured["review_id"] == review_id
    assert fallback["review_id"] == review_id
