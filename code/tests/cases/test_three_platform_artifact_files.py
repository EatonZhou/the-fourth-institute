"""Artifact storage uses shared pinned reads, not POSIX-only descriptor paths."""
import os
from types import SimpleNamespace

import pytest

from management_common import file_durability
from management_portal.workbench_projections import application_build as build


def test_artifact_storage_without_posix_directory_flags(tmp_path, monkeypatch):
    namespace = {k: v for k, v in vars(os).items() if k not in {"O_DIRECTORY", "O_NOFOLLOW"}}
    monkeypatch.setattr(build, "os", SimpleNamespace(**namespace))
    monkeypatch.setattr(file_durability, "os", SimpleNamespace(**{**vars(os), "name": "nt"}))
    monkeypatch.setattr(build, "_artifact_root", lambda: tmp_path)
    digest = build._store({"files": {}}, {"index.html": b"page"})
    assert build._regular_bytes(tmp_path / digest, "files/index.html", limit=4) == b"page"
    assert not list(tmp_path.glob(".pending-*"))


def test_pinned_artifact_read_rejects_links_and_limits(tmp_path):
    (tmp_path / "original").write_bytes(b"payload")
    (tmp_path / "link").symlink_to(tmp_path / "original")
    with pytest.raises((ValueError, OSError)):
        build._regular_bytes(tmp_path, "link", limit=100)
    os.link(tmp_path / "original", tmp_path / "hardlink")
    with pytest.raises(build.ProjectionError):
        build._regular_bytes(tmp_path, "hardlink", limit=100)
    with pytest.raises(build.ProjectionError):
        build._regular_bytes(tmp_path, "original", limit=1)
