"""Watchboard inheritance participates in the Resident's owned creation.

:design: decision_0571f3741e23
:design: decision_4aa13bc21e8e
:module: resident.conversation
:risk: no-external-side-effects
:profiles: local.fixture
"""
import pytest

from management_portal.society_service import resident_catalog, resident_record_domain, resident_watchboard_domain, watchboard_lineage
from tests.cases.isa_test_support import isolated_instruction_store  # noqa: F401
from tests.cases.test_resident_registration_resource_ack import registration  # noqa: F401
from tests.cases.test_resident_fork_resource_ack import fork_request


@pytest.mark.parametrize("mode", ["full", "recent-5", "recent-10", "message", "compacted"])
def test_watchboard_fork_publishes_independent_files_with_its_resident(registration, monkeypatch, mode):
    parent, _ = registration
    source = resident_catalog.get_catalog().get(parent["resident_id"])
    root = watchboard_lineage.root(source)
    root.mkdir(parents=True)
    (root / "agent.yaml").write_text("commands: {}\n", encoding="utf-8")
    (root / "config.yaml").write_text("original: true\n", encoding="utf-8")
    monkeypatch.setattr(resident_watchboard_domain, "has_watchboard",
                        lambda record: record["resident_id"] == source["resident_id"])
    result = fork_request(parent, mode)().raise_for_error()
    child = resident_catalog.get_catalog().get(result["resident_id"])
    copied = watchboard_lineage.root(child)
    assert copied != root
    assert (copied / "agent.yaml").read_text() == "commands: {}\n"
    assert (copied / "config.yaml").read_text() == "original: true\n"
    (copied / "config.yaml").write_text("changed: true\n", encoding="utf-8")
    assert (root / "config.yaml").read_text() == "original: true\n"
    assert not resident_watchboard_domain.has_watchboard(child)


def test_failed_watchboard_copy_keeps_unpublished_files_owned(registration, monkeypatch):
    parent, _ = registration
    source = resident_catalog.get_catalog().get(parent["resident_id"])
    folder = resident_record_domain.resident_folder_path(source)
    before = set(folder.parent.glob("resident_*"))
    copied = []

    def interrupted(_source, *, resident_folder):
        marker = resident_folder / "workspace" / "watchboard" / "partial.txt"
        marker.parent.mkdir(parents=True)
        marker.write_text("partial", encoding="utf-8")
        copied.append(resident_folder)
        raise OSError("copy interrupted")

    monkeypatch.setattr(resident_watchboard_domain, "inherit_watchboard", interrupted)
    result = fork_request(parent)()
    assert result.status == "failed"
    assert "copy interrupted" in result.error["message"]
    assert len(copied) == 1 and copied[0].name.startswith(".tfi-create-")
    assert set(folder.parent.glob("resident_*")) == before
    # Only the creation owner can reclaim its recorded staging inode.
    from management_portal.resources.creation import finish_instance_creations
    finish_instance_creations()
    assert not copied[0].exists()
    assert folder.exists()
