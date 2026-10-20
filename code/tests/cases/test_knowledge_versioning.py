""":importance: P3"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from unittest import mock

import yaml
import pytest

from knowledge_board_lan import knowledge_backend
from management_portal.resource_index_backend import ProjectKnowledgeAdapter


KNOWLEDGE_ID = "thinking_0123456789ab"


def _markdown(*, number: int = 3) -> str:
    return f"""---
kb_id: {KNOWLEDGE_ID}
type: thinking
project: AlignMe
created_at: 2026-07-22
version:
  number: {number}
  created_at: "2026-07-22T08:00:00Z"
  created_by:
    user: "sample_admin"
    agent: "resident_0123456789ab"
  updated_at: "2026-07-23T09:30:00Z"
  updated_by:
    user: "sample_admin"
    agent: "resident_0123456789ab"
importance: 7
metaness: 3
ai_generated: false
links: {{}}
---

# Versioned Knowledge
"""


def _frontmatter(text: str) -> dict:
    return yaml.safe_load(text.split("---", 2)[1])


def test_version_record_exposes_user_and_agent_attribution_contracts(subtests, isolated_portal_case):
    """版本记录分别给出创建和更新的用户与 agent，旧格式只取日期且不编造创建者"""
    with subtests.test(contract='test_version_record_exposes_user_and_agent_attribution'), isolated_portal_case() as case_path:
        version = knowledge_backend.knowledge_version_record(
            _markdown(),
            mtime=1784730000,
        )

        assert version == {
            "number": 3,
            "created_at": "2026-07-22T08:00:00Z",
            "updated_at": "2026-07-23T09:30:00Z",
            "created_by": {
                "user": "sample_admin",
                "agent": "resident_0123456789ab",
            },
            "updated_by": {
                "user": "sample_admin",
                "agent": "resident_0123456789ab",
            },
            "recorded": True,
        }
    with subtests.test(contract='test_legacy_version_record_uses_dates_without_inventing_creators'), isolated_portal_case() as case_path:
        version = knowledge_backend.knowledge_version_record(
            "---\ncreated_at: 2026-07-22\n---\n\nLegacy\n",
            mtime=1784730000,
        )

        assert version["number"] == 1
        assert version["created_at"] == "2026-07-22"
        assert version["updated_at"].endswith("Z")
        assert version["created_by"] == {"user": "", "agent": ""}
        assert version["updated_by"] == {"user": "", "agent": ""}
        assert version["recorded"] is False


def test_project_knowledge_index_projects_the_version_contract_contracts(tmp_path, subtests, isolated_portal_case):
    """知识索引带出版本和 metaness，编辑主文件或附属文件都推进条目版本并保留创建信息"""
    with subtests.test(contract='test_project_knowledge_index_projects_the_version_contract'), isolated_portal_case() as case_path:
        tmp_path = case_path
        primary = tmp_path / "item.md"
        primary.write_text(_markdown(), encoding="utf-8")

        item = knowledge_backend._read_kb_file(
            str(primary),
            "AlignMe",
            {"thinking"},
            {"thinking": "thinking"},
            entry_root=tmp_path,
            entry_files=[primary],
        )
        record = ProjectKnowledgeAdapter()._record_for_item(
            item,
            statuses={"AlignMe": "active"},
            order=0,
        )

        assert record is not None
        assert record["attributes"]["version"]["number"] == 3
        assert record["attributes"]["metaness"] == 3
        assert record["attributes"]["version"]["created_by"] == {
            "user": "sample_admin",
            "agent": "resident_0123456789ab",
        }
    with subtests.test(contract='test_primary_edit_advances_version_and_preserves_creation_audit'), isolated_portal_case() as case_path:
        tmp_path = case_path
        primary = tmp_path / "item.md"
        primary.write_text(_markdown(), encoding="utf-8")
        record = {
            "address": KNOWLEDGE_ID,
            "kb_id": KNOWLEDGE_ID,
            "type": "thinking",
            "path": str(primary),
            "selected_path": str(primary),
            "file_id": "file-primary",
            "selected_file_id": "file-primary",
        }

        with mock.patch.object(
            knowledge_backend,
            "resolve_item_address",
            return_value=record,
        ):
            result = knowledge_backend.update_item_text(
                KNOWLEDGE_ID,
                _markdown().replace("# Versioned Knowledge", "# Updated Knowledge"),
                actor="CASE-A",
            )

        stored = primary.read_text(encoding="utf-8")
        version = _frontmatter(stored)["version"]
        assert version["number"] == 4
        assert version["created_at"] == "2026-07-22T08:00:00Z"
        assert version["created_by"] == {
            "user": "sample_admin",
            "agent": "resident_0123456789ab",
        }
        assert version["updated_by"] == {"user": "CASE-A", "agent": ""}
        assert "# Updated Knowledge" in stored
        assert result["text"] == stored
        assert result["version"]["number"] == 4
    with subtests.test(contract='test_companion_edit_advances_the_owning_knowledge_version'), isolated_portal_case() as case_path:
        tmp_path = case_path
        primary = tmp_path / "item.md"
        companion = tmp_path / "notes.txt"
        primary.write_text(_markdown(), encoding="utf-8")
        companion.write_text("old notes", encoding="utf-8")
        record = {
            "address": f"{KNOWLEDGE_ID}/file/notes.txt",
            "kb_id": KNOWLEDGE_ID,
            "type": "thinking",
            "path": str(primary),
            "selected_path": str(companion),
            "file_id": "file-primary",
            "selected_file_id": "file-notes",
        }

        with mock.patch.object(
            knowledge_backend,
            "resolve_item_address",
            return_value=record,
        ):
            result = knowledge_backend.update_item_text(
                record["address"],
                "new notes",
                actor="resident_aabbccddeeff",
            )

        version = _frontmatter(primary.read_text(encoding="utf-8"))["version"]
        assert companion.read_text(encoding="utf-8") == "new notes"
        assert version["number"] == 4
        assert version["updated_by"] == {
            "user": "",
            "agent": "resident_aabbccddeeff",
        }
        assert result["text"] == "new notes"
        assert result["version"]["number"] == 4


def test_primary_edit_rejects_invalid_metaness_without_writing(tmp_path, subtests, isolated_portal_case):
    """编辑主文件时 metaness 不是 1 到 10 的整数则拒绝，文件保持原样"""
    for value in [True, 1.5, "4", None, 0, 11]:
        with subtests.test(value=value), isolated_portal_case() as case_path:
            tmp_path = case_path
            primary = tmp_path / "item.md"
            original = _markdown()
            primary.write_text(original)
            metadata = _frontmatter(original)
            metadata["metaness"] = value
            modified = "---\n" + yaml.safe_dump(metadata) + "---\n# Invalid metadata\n"
            record = {"address": KNOWLEDGE_ID, "kb_id": KNOWLEDGE_ID, "type": "thinking", "path": str(primary), "selected_path": str(primary)}
            with mock.patch.object(knowledge_backend, "resolve_item_address", return_value=record):
                with pytest.raises(ValueError, match="metaness"):
                    knowledge_backend.update_item_text(KNOWLEDGE_ID, modified, actor="CASE-A")
            assert primary.read_text() == original


def test_updates_to_different_knowledge_resources_write_concurrently(
    tmp_path: Path,
) -> None:
    """不同知识条目的更新可以同时写入"""
    second_id = "thinking_abcdef012345"
    first_path = tmp_path / "first.md"
    second_path = tmp_path / "second.md"
    first_text = _markdown().replace("# Versioned Knowledge", "# First")
    second_text = _markdown().replace(
        KNOWLEDGE_ID,
        second_id,
    ).replace("# Versioned Knowledge", "# Second")
    first_path.write_text(first_text, encoding="utf-8")
    second_path.write_text(second_text, encoding="utf-8")
    records = {
        KNOWLEDGE_ID: {
            "address": KNOWLEDGE_ID,
            "kb_id": KNOWLEDGE_ID,
            "type": "thinking",
            "path": str(first_path),
            "selected_path": str(first_path),
            "file_id": "file-first",
            "selected_file_id": "file-first",
        },
        second_id: {
            "address": second_id,
            "kb_id": second_id,
            "type": "thinking",
            "path": str(second_path),
            "selected_path": str(second_path),
            "file_id": "file-second",
            "selected_file_id": "file-second",
        },
    }
    first_write_started = threading.Event()
    release_first_write = threading.Event()
    second_write_started = threading.Event()
    original_write = knowledge_backend._write_text_atomic

    def controlled_write(path: Path, text: str) -> None:
        if path == first_path:
            first_write_started.set()
            release_first_write.wait(timeout=2)
        elif path == second_path:
            second_write_started.set()
        original_write(path, text)

    with (
        mock.patch.object(
            knowledge_backend,
            "resolve_item_address",
            side_effect=lambda address, **_kwargs: records[address],
        ),
        mock.patch.object(
            knowledge_backend,
            "_write_text_atomic",
            side_effect=controlled_write,
        ),
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        first = executor.submit(
            knowledge_backend.update_item_text,
            KNOWLEDGE_ID,
            first_text.replace("# First", "# First updated"),
        )
        assert first_write_started.wait(timeout=1)
        second = executor.submit(
            knowledge_backend.update_item_text,
            second_id,
            second_text.replace("# Second", "# Second updated"),
        )
        try:
            assert second_write_started.wait(timeout=1)
        finally:
            release_first_write.set()
        first.result(timeout=2)
        second.result(timeout=2)


def test_updates_within_one_knowledge_resource_remain_serialized(
    tmp_path: Path,
) -> None:
    """同一知识条目下不同文件的更新依次写入"""
    primary = tmp_path / "item.md"
    first_companion = tmp_path / "first.txt"
    second_companion = tmp_path / "second.txt"
    primary.write_text(_markdown(), encoding="utf-8")
    first_companion.write_text("first", encoding="utf-8")
    second_companion.write_text("second", encoding="utf-8")
    first_address = f"{KNOWLEDGE_ID}/file/first.txt"
    second_address = f"{KNOWLEDGE_ID}/file/second.txt"
    records = {
        first_address: {
            "address": first_address,
            "kb_id": KNOWLEDGE_ID,
            "type": "thinking",
            "path": str(primary),
            "selected_path": str(first_companion),
            "file_id": "file-primary",
            "selected_file_id": "file-first",
        },
        second_address: {
            "address": second_address,
            "kb_id": KNOWLEDGE_ID,
            "type": "thinking",
            "path": str(primary),
            "selected_path": str(second_companion),
            "file_id": "file-primary",
            "selected_file_id": "file-second",
        },
    }
    first_write_started = threading.Event()
    release_first_write = threading.Event()
    second_write_started = threading.Event()
    original_write = knowledge_backend._write_text_atomic

    def controlled_write(path: Path, text: str) -> None:
        if path == first_companion:
            first_write_started.set()
            release_first_write.wait(timeout=2)
        elif path == second_companion:
            second_write_started.set()
        original_write(path, text)

    with (
        mock.patch.object(
            knowledge_backend,
            "resolve_item_address",
            side_effect=lambda address, **_kwargs: records[address],
        ),
        mock.patch.object(
            knowledge_backend,
            "_write_text_atomic",
            side_effect=controlled_write,
        ),
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        first = executor.submit(
            knowledge_backend.update_item_text,
            first_address,
            "first updated",
        )
        assert first_write_started.wait(timeout=1)
        second = executor.submit(
            knowledge_backend.update_item_text,
            second_address,
            "second updated",
        )
        try:
            assert not second_write_started.wait(timeout=0.15)
        finally:
            release_first_write.set()
        assert second_write_started.wait(timeout=1)
        first.result(timeout=2)
        second.result(timeout=2)
