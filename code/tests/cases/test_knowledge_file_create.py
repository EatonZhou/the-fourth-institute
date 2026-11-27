"""Knowledge companion creation uses the registered owner and live projection.

:module: workflow.knowledge
:design: decision_3ece26686464
"""
from pathlib import Path

import pytest

from management_portal.resources.errors import ResourceVersionConflict
from tests.cases.test_knowledge_move_action import move_service, ACTOR  # noqa: F401
from tests.cases.permission_test_support import permissions  # noqa: F401


@pytest.fixture
def file_service(move_service):
    return move_service


def invoke(service, rid, **changes):
    payload = {"filename": "usage.md", "text": "# Usage\n", "expected_version": 3, **changes}
    return service.create_knowledge_file(rid, payload["filename"], payload["text"],
                                         payload["expected_version"], actor=ACTOR)


def test_create_is_immediately_readable_and_versions_primary(file_service, permissions):
    service, _index, items, records = file_service
    permissions.grant(ACTOR, "AlignMe")
    rid = items[0]["resource_id"]
    result = invoke(service, rid)
    assert result["version"]["number"] == 4
    assert result["address"] == f"{rid}/file/{rid}/usage.md"
    resolved = service.resolve(result["address"], actor=ACTOR)
    assert Path(resolved["resolved_path"]).read_text() == "# Usage\n"
    assert Path(records[0]["path"]).is_file()
    with pytest.raises(ResourceVersionConflict):
        invoke(service, rid, filename="other.md")
    with pytest.raises(FileExistsError):
        invoke(service, rid, expected_version=4)
    assert service.resolve(rid, actor=ACTOR)["detail"]["version"]["number"] == 4


@pytest.mark.parametrize("filename", ["../escape.md", "a/b.md", "a\\b.md", ".hidden.md", "bad.bin"])
def test_invalid_names_cannot_leave_owner(file_service, permissions, filename):
    service, _index, items, records = file_service
    permissions.grant(ACTOR, "AlignMe")
    with pytest.raises(ValueError):
        invoke(service, items[0]["resource_id"], filename=filename)
    assert "number: 3" in Path(records[0]["path"]).read_text()


def test_denied_actor_and_symlink_cannot_create(file_service, permissions, tmp_path):
    service, _index, items, records = file_service
    rid = items[0]["resource_id"]
    with pytest.raises(PermissionError):
        invoke(service, rid)
    permissions.grant(ACTOR, "AlignMe")
    outside = tmp_path / "outside.md"
    outside.write_text("outside")
    destination = Path(records[0]["path"]).parent / rid / "usage.md"
    destination.symlink_to(outside)
    with pytest.raises(FileExistsError):
        invoke(service, rid)
    assert outside.read_text() == "outside"


def test_public_isa_and_primary_write_failure(file_service, permissions, monkeypatch):
    from knowledge_board_lan import knowledge_backend as kb
    from management_portal.hatg import catalog
    from management_portal.hatg.contracts import InstructionCall, InstructionContext
    from management_portal.hatg.kernel import Invocation
    from management_portal.resources import isa

    service, _index, items, records = file_service
    rid = items[0]["resource_id"]
    permissions.grant(ACTOR, "AlignMe")
    arguments = {"resource_id": rid, "filename": "usage.md", "text": "# Usage\n", "expected_version": 3}
    inv = Invocation(catalog.declaration("CreateKnowledgeFile"),
                     InstructionCall(instruction="CreateKnowledgeFile", arguments=arguments),
                     InstructionContext(caller=ACTOR, entry="harness"), "inst_res_0123456789abcdefabcd")
    assert isa.create_knowledge_file(inv)["version"]["number"] == 4
    primary = Path(records[0]["path"])
    before = primary.read_text()
    def fail(*_args):
        raise OSError("injected primary write failure")
    monkeypatch.setattr(kb, "_write_text_atomic", fail)
    with pytest.raises(OSError):
        invoke(service, rid, filename="failed.md", expected_version=4)
    assert not (primary.parent / rid / "failed.md").exists()
    assert primary.read_text() == before
