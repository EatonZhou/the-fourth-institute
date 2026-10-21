"""Resident Folder and Lineage Folder files use the Workspace file address, and ReadResource lists directories.

:module: resource.identity
:importance: P1
:aspects: functional, behavior
"""
import copy
import json
from types import SimpleNamespace
from unittest import mock

import pytest

from management_portal.hatg.contracts import InstructionError
from management_portal.resources import ResourceService
from management_portal.resources import isa as resource_isa
from management_portal.resources.collaboration_registry import CollaborationDescriptorStore
from management_portal.resources.providers import install_resolvers
from management_portal.resources.registration import ResourceRegistrationStore

LINEAGE = "lineage_0123456789ab"
RESIDENT = "resident_0123456789ab"


@pytest.fixture
def folders(tmp_path, monkeypatch):
    # The Lineage entry under the Lineage root links to its Project folder, as in production.
    real = tmp_path / "project" / LINEAGE
    resident = real / "residents" / RESIDENT
    (resident / "notes").mkdir(parents=True)
    (resident / "resident.md").write_text("# Resident\n", encoding="utf-8")
    (resident / "notes" / "plan.md").write_text("plan", encoding="utf-8")
    (resident / ".claude").mkdir()
    (resident / ".claude" / "session.json").write_text("{}", encoding="utf-8")
    (resident / "secrets").mkdir()
    (resident / "secrets" / "key.txt").write_text("never listed", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside the folder", encoding="utf-8")
    (resident / "escape.md").symlink_to(outside)
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / LINEAGE).symlink_to(real, target_is_directory=True)
    monkeypatch.setenv("PORTAL_LINEAGE_FOLDER_ROOT", str(agents))
    records = {
        RESIDENT: {"resource_id": RESIDENT, "resource_type": "resident", "title": "Resident", "source": {},
                   "facets": ["resident", "collaboration"], "url": "/lineage?resident=" + RESIDENT,
                   "attributes": {"status": "active", "relations": [{"kind": "lineage", "target_id": LINEAGE}]}},
        LINEAGE: {"resource_id": LINEAGE, "resource_type": "lineage", "title": "Lineage", "source": {},
                  "facets": ["lineage", "collaboration"], "url": "/lineage?lineage=" + LINEAGE,
                  "attributes": {"status": "active"}},
    }
    index = mock.Mock()
    index.indexed_types.return_value = frozenset({"resident", "lineage"})
    index.has_indexed_type.side_effect = lambda kind: kind in {"resident", "lineage"}
    index.get_resource.side_effect = lambda resource_id, *_args, **_kwargs: copy.deepcopy(records[resource_id])
    service = ResourceService(
        index_provider=index, collaboration_store=CollaborationDescriptorStore(),
        registration_store=ResourceRegistrationStore(), resolver_loader=install_resolvers,
    )
    from management_portal import permission_system

    monkeypatch.setattr(permission_system, "configured_mode", lambda: permission_system.OPEN)
    monkeypatch.setattr(resource_isa, "_service", lambda: service)
    return SimpleNamespace(service=service, resident=resident, real=real)


def read(address, **arguments):
    """ReadResource as alice."""
    return resource_isa.read_resource(SimpleNamespace(
        argument={"address": address, **arguments}.get, caller="alice", name="ReadResource"))


def test_resident_and_lineage_folders_list_and_read_by_file_address(folders):
    """Resident Folder 与 Lineage Folder 用 <ID>/file/<路径> 列目录和读文件

    读 <ID>/file 返回直接子项，点开头与凭证类路径不列出且只计数；Lineage Folder 经入口链接读到真实目录，两种地址读到同一文件。
    """
    listing = read(f"{RESIDENT}/file")
    assert listing["kind"] == "directory"
    assert [entry["name"] for entry in listing["entries"]] == ["notes", "escape.md", "resident.md"]
    assert listing["hidden_count"] == 2 and listing["truncated"] is False
    notes = listing["entries"][0]
    assert (notes["kind"], notes["path"], notes["address"]) == ("directory", "notes", f"{RESIDENT}/file/notes")
    assert listing["entries"][1]["kind"] == "symlink"
    nested = read(f"{RESIDENT}/file/notes")
    assert [entry["address"] for entry in nested["entries"]] == [f"{RESIDENT}/file/notes/plan.md"]
    assert read(f"{RESIDENT}/file/resident.md")["text"] == "# Resident\n"
    through_lineage = read(f"{LINEAGE}/file/residents/{RESIDENT}/resident.md")
    assert through_lineage["text"] == "# Resident\n"
    assert through_lineage["content_url"] == "/api/resources/content?address=" + f"{LINEAGE}/file/residents/{RESIDENT}/resident.md".replace("/", "%2F")
    assert [entry["name"] for entry in read(f"{LINEAGE}/file")["entries"]] == ["residents"]


@pytest.mark.parametrize("suffix", ["file/.claude/session.json", "file/secrets/key.txt", "file/escape.md",
                                    "file/../outside.txt", "file/notes/../resident.md"])
def test_folder_files_refuse_hidden_paths_symlinks_and_dot_segments(folders, suffix):
    """Resident Folder 文件地址拒绝隐藏路径、符号链接和点路径

    点开头与凭证类路径像不存在一样被拒绝，指向外部的符号链接和 .. 路径也不能读取。
    """
    with pytest.raises((KeyError, ValueError)):
        folders.service.read_content(f"{RESIDENT}/{suffix}", actor="alice")


def test_read_resource_previews_and_metadata_of_folder_files(folders):
    """ReadResource 的 preview 截断长文本、不读二进制，meta_only 只返回元数据

    preview 超过 max_bytes 时返回前段并标记 truncated、不带内容摘要；图片只标记 binary；meta_only 不带内容；非文件地址不接受这两个参数。
    """
    (folders.resident / "long.md").write_text("é" * 10, encoding="utf-8")
    preview = read(f"{RESIDENT}/file/long.md", preview=True, max_bytes=5)
    # A cut through the last character keeps the whole characters before it.
    assert (preview["text"], preview["truncated"], preview["binary"]) == ("éé", True, False)
    assert "version" not in preview and preview["kind"] == "markdown" and preview["size"] == 20
    whole = read(f"{RESIDENT}/file/long.md", preview=True)
    assert whole["text"] == "é" * 10 and whole["truncated"] is False and whole["version"]
    (folders.resident / "image.png").write_bytes(b"\x89PNG\x00")
    image = read(f"{RESIDENT}/file/image.png", preview=True)
    assert (image["binary"], image["text"], image["kind"]) == (True, "", "image")
    meta = read(f"{RESIDENT}/file/resident.md", meta_only=True)
    assert "text" not in meta and meta["name"] == "resident.md" and meta["size"] == 11
    assert meta["content_url"].endswith("resident.md") and int(meta["mtime_ns"]) > 0
    assert read(f"{RESIDENT}/file/notes", meta_only=True)["entries"][0]["name"] == "plan.md"
    with pytest.raises(InstructionError):
        read(RESIDENT, meta_only=True)


def test_directory_content_over_http_is_its_listing(folders):
    """目录地址的字节内容是子项列表

    ResourceService 读目录地址返回目录媒体类型的 JSON，Portal 内容地址据此返回列表而不是文件。
    """
    data, content_type, _resolved = folders.service.read_content(f"{RESIDENT}/file/notes", actor="alice")
    assert content_type == "application/vnd.tfi.directory+json"
    assert [entry["name"] for entry in json.loads(data)["entries"]] == ["plan.md"]
