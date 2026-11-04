"""The public resource action moves registered Knowledge through the live index."""
from pathlib import Path
import hashlib
import time
from unittest.mock import Mock

import pytest

from knowledge_board_lan import knowledge_backend, knowledge_source, knowledge_details
from management_portal import permission_system
from management_portal.hatg import catalog
from management_portal.hatg.contracts import InstructionCall, InstructionContext
from management_portal.hatg.kernel import Invocation
from management_portal.resource_index_backend import ResourceIndexService
from management_portal.resource_index_knowledge import ProjectKnowledgeAdapter, SkillPackageAdapter
from management_portal.resource_index_registries import ProjectRegistryAdapter
from management_portal.resource_index_store import IndexStore
from management_portal.resources import ResourceService, isa
from management_portal.resources.directory_registrations import register_owned
from management_portal.resources.providers import knowledge, project
from tests.cases.permission_test_support import Fact, permissions  # noqa: F401
from tests.cases.registry_support import write_projects
from tests.cases.test_knowledge_versioning import _markdown, KNOWLEDGE_ID

ACTOR = "resident_0123456789ab"


@pytest.fixture
def move_service(tmp_path, monkeypatch, permissions):
    projects = [{"id": name, "path": str(tmp_path / name), "status": "active", "tags": []}
                for name in ("AlignMe", "Destination", "Home")]
    for entry in projects:
        Path(entry["path"]).mkdir()
    write_projects({"projects": projects})
    permissions.place(*(Fact("project", p["id"]) for p in projects),
                      Fact("lineage", "lineage_0123456789ab", {"project": "Home"}),
                      Fact("resident", ACTOR, {"lineage": "lineage_0123456789ab"}))
    monkeypatch.setattr(knowledge_backend, "_parse_projects_yaml", lambda: projects)
    monkeypatch.setattr(knowledge_source, "_parse_projects_yaml", lambda: projects)
    adapter = ProjectKnowledgeAdapter()
    project_adapter = ProjectRegistryAdapter()
    store = IndexStore()
    store.publish_snapshot(adapter.name, [], {})
    store.publish_snapshot(project_adapter.name, [project_adapter.project_record("project", p) for p in projects], {})
    index = ResourceIndexService(store=store, adapters=[adapter, project_adapter, SkillPackageAdapter()], start_worker=False)
    index.adapter_layer._on_projection_changed = None
    service = ResourceService(index_provider=index, resolver_loader=knowledge.install_resolver,
                              action_provider_loader=project.install_action_provider)
    for module in (knowledge_backend, knowledge_source, knowledge_details):
        monkeypatch.setattr(module, "get_resource_service", lambda: service)
    from management_portal import resources
    from skill_board_lan import skill_backend
    monkeypatch.setattr(resources, "get_resource_service", lambda: service)
    monkeypatch.setattr(skill_backend, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(isa, "_service", lambda: service)
    records, items = [], []
    for i in range(17):
        rid = f"thinking_{i:012x}"
        primary = tmp_path / "AlignMe" / "knowledge_base" / "thinking" / (rid + ".md")
        primary.parent.mkdir(parents=True, exist_ok=True)
        primary.write_text(_markdown().replace(KNOWLEDGE_ID, rid))
        companion = primary.parent / rid
        companion.mkdir()
        (companion / "image.png").write_bytes(b"test image bytes")
        register_owned("knowledge", rid, [primary, companion])
        permissions.place(Fact("thinking", rid, {"project": "AlignMe"}))
        records.append({"resource_id": rid, "resource_type": "thinking", "path": str(primary), "project": "AlignMe"})
        items.append({"resource_id": rid, "expected_version": 3})
    index.project_relocated(records)
    yield service, index, items, records
    index.adapter_layer.stop()


def test_public_action_moves_17_and_immediately_resolves_original_ids(move_service, permissions, monkeypatch):
    service, index, items, records = move_service
    permissions.grant(ACTOR, "AlignMe")
    permissions.grant(ACTOR, "Destination")
    before = {r["resource_id"]: Path(r["path"]).read_text().split("---", 2)[2] for r in records}
    apply = Mock(wraps=index.store.apply_batch)
    monkeypatch.setattr(index.store, "apply_batch", apply)
    monkeypatch.setattr(index, "refresh", Mock(side_effect=AssertionError("must not queue a scan")))
    arguments = {"resource_id": "Destination", "action": "knowledge.move", "arguments": {"items": items}}
    declaration = catalog.declaration("RunResourceAction")
    invocation = Invocation(declaration, InstructionCall(instruction="RunResourceAction", arguments=arguments),
                            InstructionContext(caller=ACTOR, entry="harness"), "inst_res_0123456789abcdefabcd")
    started = time.perf_counter()
    result = isa.run_resource_action(invocation)["result"]
    elapsed = time.perf_counter() - started
    assert result["status"] == "completed", result.get("error")
    assert result["projected"] and not result["pending"] and len(result["committed"]) == 17
    assert apply.call_count == 1
    for old in records:
        rid = old["resource_id"]
        resolved = service.resolve(rid, actor=ACTOR)
        path = Path(resolved["resolved_path"])
        assert "Destination" in path.parts
        assert not Path(old["path"]).exists()
        assert path.read_text().split("---", 2)[2] == before[rid]
        assert (path.parent / rid / "image.png").read_bytes() == b"test image bytes"
        assert permissions.store.paths("R", [rid])[rid][-2:] == ["Destination", rid]
    replay = isa.run_resource_action(invocation)["result"]
    assert replay["status"] == "completed"
    assert apply.call_count == 1
    assert {r["version"]["number"] for r in replay["committed"]} == {4}
    print(f"public knowledge.move: 17 resources in {elapsed:.3f}s, one live index write")


@pytest.mark.parametrize("grant", ["AlignMe", "Destination"])
def test_source_and_target_permissions_are_required_before_any_move(move_service, permissions, grant):
    service, _index, items, records = move_service
    permissions.grant(ACTOR, grant)
    with pytest.raises(PermissionError):
        service.action("Destination", "knowledge.move", {"items": items}, actor=ACTOR, resource_type="project")
    assert all(Path(r["path"]).is_file() for r in records)
    assert not (Path(records[0]["path"]).parents[3] / "Destination" / "knowledge_base").exists()


def test_public_mixed_move_preserves_original_ids_attachments_and_permissions(move_service, permissions, monkeypatch):
    service, index, items, records = move_service
    permissions.grant(ACTOR, "AlignMe")
    permissions.grant(ACTOR, "Destination")
    source_project = Path(records[0]["path"]).parents[2]
    skill_records = []
    for package, rid in ((False, "skill_2a6c9d0f3b71"), (True, "skill_1c6e8f2a9b3d")):
        root = source_project / "knowledge_base" / "skill" / (rid if package else rid + ".md")
        path = root / "docs" / "instructions.md" if package else root
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\n{'id' if package else 'kb_id'}: {rid}\nproject: AlignMe\n---\n# Writing\n")
        if package:
            (root / "skill.yaml").write_text("primary_file: docs/instructions.md\n")
            (root / "attachment.bin").write_bytes(b"reference payload")
        register_owned("knowledge", rid, [root])
        permissions.place(Fact("skill", rid, {"project": "AlignMe"}))
        skill_records.append({"resource_id": rid, "resource_type": "skill", "path": str(path),
                              "content_root": str(root if package else path.parent),
                              "single_file": not package, "project": "AlignMe", "project_ids": ["AlignMe"]})
        items.append({"resource_id": rid, "expected_version": hashlib.sha256(path.read_bytes()).hexdigest()})
    index.project_relocated(skill_records)
    apply = Mock(wraps=index.store.apply_batch)
    monkeypatch.setattr(index.store, "apply_batch", apply)
    result = service.action("Destination", "knowledge.move", {"items": items[-3:]}, actor=ACTOR,
                            resource_type="project")
    assert result["status"] == "completed", result.get("error")
    assert apply.call_count == 1
    for record in result["committed"]:
        rid = record["resource_id"]
        resolved = service.resolve(rid, actor=ACTOR)
        assert Path(resolved["resolved_path"]) == Path(record["path"])
        assert permissions.store.paths("R", [rid])[rid][-2:] == ["Destination", rid]
        assert service.get(rid, actor=ACTOR)["attributes"]["project_ids"] == ["Destination"]
        if rid.startswith("skill_"):
            content, _kind, _metadata = service.read_content(rid, actor=ACTOR)
            assert hashlib.sha256(content).hexdigest() == record["version"]
    attachment, _kind, _meta = service.read_content("skill_1c6e8f2a9b3d/file/attachment.bin", actor=ACTOR)
    assert attachment == b"reference payload"
    assert all(not Path(record["path"]).exists() for record in skill_records)
