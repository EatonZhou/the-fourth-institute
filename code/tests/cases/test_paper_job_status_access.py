from unittest import mock

import pytest

from management_portal import paper_backend


def test_status_authorizes_actual_paper_before_resuming_or_mutating(subtests, isolated_portal_case):
    """查询任务状态在恢复或修改前鉴权实际关联论文"""
    for operation, lookup, mutation in [
    ("get_deep_read_status", "_latest_deep_read_job", "_launch_deep_read_business_task"),
    ("get_exp_status", "_latest_exp_job", "_mark_stale_exp_job"),
]:
        with subtests.test(operation=operation, lookup=lookup, mutation=mutation), isolated_portal_case() as case_path:
            job = {"job_id": "status-job", "paper_id": "paper_0123456789ab", "status": "running"}
            service = mock.Mock()
            service.get.side_effect = PermissionError("Paper viewer required")
            with (
                mock.patch.object(paper_backend, lookup, return_value=job),
                mock.patch.object(paper_backend, "get_resource_service", return_value=service),
                mock.patch.object(paper_backend, mutation) as resume,
                pytest.raises(PermissionError, match="Paper viewer"),
            ):
                getattr(paper_backend, operation)(actor="denied-reader", job_id=job["job_id"])
            service.get.assert_called_once_with(job["paper_id"], actor="denied-reader")
            resume.assert_not_called()


def test_status_validates_selectors_and_authorizes_idle_paper(subtests, isolated_portal_case):
    """任务状态校验选择参数且空闲论文仍需鉴权"""
    for operation, lookup in [
    ("get_deep_read_status", "_latest_deep_read_job"),
    ("get_exp_status", "_latest_exp_job"),
]:
        with subtests.test(operation=operation, lookup=lookup), isolated_portal_case() as case_path:
            service = mock.Mock()
            with (
                mock.patch.object(paper_backend, lookup, return_value=None),
                mock.patch.object(paper_backend, "get_resource_service", return_value=service),
            ):
                with pytest.raises(ValueError, match="paper_id or job_id"):
                    getattr(paper_backend, operation)(actor="reader")
                with pytest.raises(KeyError):
                    getattr(paper_backend, operation)(actor="reader", job_id="unknown")
                result = getattr(paper_backend, operation)(actor="reader", paper_id="paper_0123456789ab")
            assert result["status"] == "idle"
            service.get.assert_called_once_with("paper_0123456789ab", actor="reader")


def test_status_rejects_mismatched_paper_and_returns_authorized_completed_job(subtests, isolated_portal_case):
    """任务状态拒绝论文不匹配且可返回已授权完成任务"""
    for operation, lookup in [
    ("get_deep_read_status", "_latest_deep_read_job"),
    ("get_exp_status", "_latest_exp_job"),
]:
        with subtests.test(operation=operation, lookup=lookup), isolated_portal_case() as case_path:
            job = {"job_id": "status-job", "paper_id": "paper_0123456789ab", "status": "done"}
            service = mock.Mock()
            with (
                mock.patch.object(paper_backend, lookup, return_value=job),
                mock.patch.object(paper_backend, "get_resource_service", return_value=service),
            ):
                with pytest.raises(ValueError, match="another paper"):
                    getattr(paper_backend, operation)(actor="reader", job_id=job["job_id"], paper_id="paper_abcdefabcdef")
                result = getattr(paper_backend, operation)(actor="reader", job_id=job["job_id"])
            assert result == job


def test_status_requires_authenticated_actor(subtests, isolated_portal_case):
    """论文任务状态查询拒绝空身份"""
    for operation in ["get_deep_read_status", "get_exp_status"]:
        with subtests.test(operation=operation), isolated_portal_case() as case_path:
            with pytest.raises(PermissionError, match="actor"):
                getattr(paper_backend, operation)(actor="", paper_id="paper_0123456789ab")
