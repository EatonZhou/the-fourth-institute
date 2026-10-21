""":importance: P3"""
from __future__ import annotations

from management_portal import knowledge_archive_projection as projection


def test_archive_file_change_publishes_exact_actor_and_job_contracts(monkeypatch, tmp_path, subtests, isolated_portal_case):
    """归档文件变化按实际操作者和任务发布且忽略临时文件"""
    with subtests.test(contract='test_archive_file_change_publishes_exact_actor_and_job'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            root = tmp_path / "archive"
            changed = root / "sample_admin" / "ka_123" / "result.json"
            changed.parent.mkdir(parents=True)
            events: list[tuple[tuple, dict]] = []
            monkeypatch.setattr(projection, "ARCHIVE_ROOT", root)
            monkeypatch.setattr(
                projection,
                "publish_local_projection_change",
                lambda *args, **kwargs: events.append((args, kwargs)),
            )

            projection._archive_change(str(changed))

            assert events == [
                (
                    ("knowledge-archive", "ka_123"),
                    {"actor": "sample_admin", "fields": ["result.json"]},
                )
            ]
    with subtests.test(contract='test_archive_file_change_ignores_atomic_temporary_file'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            root = tmp_path / "archive"
            changed = root / "sample_admin" / "ka_123" / "record.json.tmp"
            changed.parent.mkdir(parents=True)
            events: list[object] = []
            monkeypatch.setattr(projection, "ARCHIVE_ROOT", root)
            monkeypatch.setattr(
                projection,
                "publish_local_projection_change",
                lambda *args, **kwargs: events.append((args, kwargs)),
            )

            projection._archive_change(str(changed))

            assert events == []
