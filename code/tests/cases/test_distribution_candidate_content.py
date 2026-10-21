"""Release previews use the same registered source as the exported content."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from management_portal.distribution import candidates
from management_portal.distribution.selection import resource_file_scope


@pytest.fixture
def preview(monkeypatch):
    records = {}

    @contextmanager
    def source():
        yield SimpleNamespace(read_index=lambda **filters: {key: record for key, record in records.items()
                              if key in filters.get('resource_ids', records)})

    monkeypatch.setattr(candidates, "read_snapshot", source)
    return records, lambda address: candidates.candidate("read", {"address": address})


@pytest.mark.parametrize("source_path", ["", "other"])
def test_repo_preview_and_export_use_canonical_checkout(tmp_path, preview, source_path):
    records, read = preview
    checkout = tmp_path / "canonical"
    checkout.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    (other / "SPEC.md").write_text("wrong checkout")
    for name in ("SPEC.md", "IMPROVE.md"):
        (checkout / name).write_text("## 规范\n", encoding="utf-8")
    identity = "repo_0123456789ab"
    records[identity] = {"resource_type": "repo", "source": {"path": str(other) if source_path else ""},
                         "attributes": {"canonical_checkout": str(checkout)}}
    assert resource_file_scope(identity, records[identity])["source"] == str(checkout)
    for name in ("SPEC.md", "IMPROVE.md"):
        assert read(f"{identity}/file/{name}") == {"encoding": "utf-8", "content": "## 规范\n"}
    records[identity]["attributes"]["canonical_checkout"] = str(tmp_path / "missing")
    with pytest.raises(ValueError, match="canonical checkout"):
        read(f"{identity}/file/SPEC.md")


def test_registered_source_paths_and_knowledge_body_still_read(tmp_path, preview):
    records, read = preview
    doc = tmp_path / "decision.md"
    doc.write_text("decision body", encoding="utf-8")
    records["decision_0123456789ab"] = {"resource_type": "decision", "source": {"path": str(doc)}}
    assert read("decision_0123456789ab")["content"] == "decision body"
    records["repo_0123456789ab"] = {"resource_type": "repo", "source": {"path": str(tmp_path)}}
    assert read("repo_0123456789ab/file/decision.md")["content"] == "decision body"


@pytest.mark.parametrize("child", ["../outside.md", "/outside.md", "escape.md", ".git/config", ".env"])
def test_repo_preview_retains_file_boundaries(tmp_path, preview, child):
    records, read = preview
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("outside")
    (root / "escape.md").symlink_to(outside)
    (root / ".git").mkdir()
    (root / ".git/config").write_text("private configuration")
    (root / ".env").write_text("private environment")
    records["repo_0123456789ab"] = {"resource_type": "repo", "source": {},
                                   "attributes": {"canonical_checkout": str(root)}}
    with pytest.raises(ValueError, match="escapes the resource"):
        read(f"repo_0123456789ab/file/{child}")


def test_repo_preview_retains_text_size_limit(tmp_path, preview):
    records, read = preview
    (tmp_path / "large.md").write_bytes(b"x" * (4 * 1024 * 1024 + 1))
    records["repo_0123456789ab"] = {"resource_type": "repo", "source": {},
                                   "attributes": {"canonical_checkout": str(tmp_path)}}
    with pytest.raises(ValueError, match="at most 4 MiB"):
        read("repo_0123456789ab/file/large.md")
