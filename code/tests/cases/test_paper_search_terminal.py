from unittest import mock

from management_portal import semantic_search_jobs, semantic_search_store, shared_search_adapter
from tests.cases.registry_support import put_registry_document


def test_deleted_paper_search_rejects_late_worker_and_refresh_writes(subtests, isolated_portal_case):
    """已删除论文检索拒绝迟到工作者与刷新状态写入"""
    for late_status in ["done", "error", "queued"]:
        with subtests.test(late_status=late_status), isolated_portal_case():
            search = {
                "search_id": "search_0123456789ab", "surface": "paper", "created_by": "alice",
                "status": "running", "progress": 35,
            }
            store = semantic_search_store.PAPER_SEARCHES
            with store.locked():
                store.write([search])
            with (
                mock.patch.object(semantic_search_jobs, "publish_local_projection_change"),
                mock.patch.object(shared_search_adapter, "cancel_run") as cancel_run,
            ):
                semantic_search_jobs.delete_search("paper", {"search_id": search["search_id"]}, "alice")
                cancel_run.assert_called_once_with(search, deleted=True)
                deleted = semantic_search_jobs.search_by_id("paper", search["search_id"])
                result = semantic_search_jobs.set_search("paper", search["search_id"], {"status": late_status, "progress": 88})
                assert result == deleted
                assert semantic_search_jobs.search_by_id("paper", search["search_id"]) == deleted
                assert deleted["status"] == "deleted"
                assert deleted["progress"] == 100
