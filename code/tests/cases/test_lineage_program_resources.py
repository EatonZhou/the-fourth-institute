from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal import society_service, serve_management_portal
from management_portal.society_service import contracts, lineage_program, lineage_reads
from management_portal.society_service import lineage_aggregate
from management_portal.resources.providers import collaboration
from management_portal.resources.service import ResourceService


ROOT_ID = "lineage_0123456789ab"
ITEMS = [
    ("component", "components", "event_source_2134af26c72d"),
    ("component", "components", "effect_0123456789ab"),
    ("wire", "wires", "wire_0123456789ab"),
    ("function-block", "function_blocks", "fbi_0123456789ab"),
]


@pytest.fixture
def program_resources(tmp_path, monkeypatch):
    definition = {"revision": 84, "program": {}}
    for namespace, collection, item_id in ITEMS:
        owner = definition if namespace == "function-block" else definition["program"]
        owner.setdefault(collection, []).append({
            "id": item_id, "title": f"Target {item_id}", "kind": namespace,
        })
    catalog = Mock()
    catalog.get_state.return_value = (definition, {})
    monkeypatch.setattr(lineage_aggregate, "lineage_catalog_store", lambda: catalog)
    projection = Mock(wraps=lineage_reads.get_lineage_program_resource_projection)
    monkeypatch.setattr(society_service, "get_society_service", lambda: SimpleNamespace(
        lineage=SimpleNamespace(get_lineage_program_resource_projection=projection),
    ))
    record = {
        "resource_id": ROOT_ID, "resource_type": "lineage", "title": "Root",
        "attributes": {"status": "active"},
    }
    index = SimpleNamespace(
        get_resource=lambda selected, **context: record,
        supports_type=lambda selected: selected == "lineage",
    )
    service = ResourceService(index_provider=index)
    collaboration.install_resolver(service)
    return service, definition, record, projection


@pytest.mark.parametrize("namespace,collection,item_id", ITEMS)
def test_program_address_resolve_reference_read(program_resources, namespace, collection, item_id):
    """程序子资源地址可解析引用并读取带版本的内容"""
    service, definition, _, _ = program_resources
    address = f"{ROOT_ID}/program/{namespace}/{item_id}"
    resolved = service.resolve(address, actor="test-actor")
    assert resolved["exists"] is True
    assert resolved["resource_id"] == ROOT_ID
    assert resolved["target_kind"] == f"lineage_program_{namespace.replace('-', '_')}"
    assert resolved["portal_url"] == f"/lineage?lineage={ROOT_ID}&program_item={item_id}"
    reference = service.reference(address, actor="test-actor")
    assert reference["title"] == f"Target {item_id}"
    assert reference["resource_id"] == ROOT_ID
    assert reference["url"] == resolved["portal_url"]
    data, content_type, _ = service.read_content(address, actor="test-actor")
    assert content_type == "application/json"
    assert json.loads(data)["item"]["id"] == item_id
    assert json.loads(data)["revision"] == definition["revision"]
    with pytest.raises(ValueError, match="exceeds max_bytes"):
        service.read_content(address, actor="test-actor", max_bytes=1)


def test_event_producer_address_is_consumable(program_resources):
    """事件生成的组件地址可读取资源引用"""
    service, _, _, _ = program_resources
    address = lineage_program.program_component_address(ROOT_ID, ITEMS[0][2])
    assert service.reference(address, actor="test-actor")["title"] == f"Target {ITEMS[0][2]}"


@pytest.mark.parametrize("operation", ["resolve", "reference", "read_content"])
def test_program_read_is_open_to_valid_actors(program_resources, operation):
    """开放模式下任何有效主体都可读取程序，空身份被拒绝"""
    service, _, _, projection = program_resources
    address = f"{ROOT_ID}/program/component/{ITEMS[0][2]}"
    getattr(service, operation)(address, actor="portal-other")
    assert projection.called
    with pytest.raises(PermissionError):
        getattr(service, operation)(address, actor="")


def test_program_missing_and_deleted_targets(program_resources):
    """程序目标缺失或删除时不可读取"""
    service, definition, record, projection = program_resources
    address = f"{ROOT_ID}/program/component/{ITEMS[0][2]}"
    definition["program"]["components"] = []
    assert service.resolve(address, actor="test-actor")["exists"] is False
    for operation in ("reference", "read_content"):
        with pytest.raises(KeyError):
            getattr(service, operation)(address, actor="test-actor")
    projection.reset_mock()
    record["attributes"]["status"] = "deleted"
    assert service.resolve(address, actor="test-actor")["exists"] is False
    with pytest.raises(KeyError):
        service.read_content(address, actor="test-actor")
    projection.assert_not_called()


@pytest.mark.parametrize("suffix", [
    "program/component/unknown_0123456789ab", "program/other/event_source_2134af26c72d",
    "program/component/event_source_2134af26c72d/extra",
])
def test_program_rejects_undeclared_namespaces(program_resources, suffix):
    """程序地址拒绝未声明命名空间与多余路径"""
    service, _, _, projection = program_resources
    with pytest.raises(ValueError):
        service.resolve(f"{ROOT_ID}/{suffix}", actor="test-actor")
    projection.assert_not_called()


def test_program_root_lookup_does_not_read_program(program_resources):
    """解析 Lineage 根地址不读取程序投影"""
    service, _, _, projection = program_resources
    assert service.resolve(ROOT_ID, actor="test-actor")["exists"] is True
    projection.assert_not_called()


def test_program_projection_is_read_only_service_contract():
    """程序资源投影登记为只读服务操作"""
    assert "get_lineage_program_resource_projection" in contracts.DOMAIN_METHODS["lineage"]
    assert contracts.operation_contract("lineage", "get_lineage_program_resource_projection").mode == "read"


@pytest.mark.parametrize("actor,missing,expected_status", [
    ("test-actor", False, "resolved"),
    ("test-actor", True, "not_found"),
    ("portal-other", False, "resolved"),
])
def test_attached_event_reference(program_resources, actor, missing, expected_status):
    """事件文本中的组件引用解析到画布目标并报告缺失"""
    from management_portal.resource_refs import resolve_resource_references
    from management_portal.resources.portal import canonical_portal_location

    service, definition, _, _ = program_resources
    address = lineage_program.program_component_address(ROOT_ID, ITEMS[0][2])
    if missing:
        definition["program"]["components"] = []
    references = resolve_resource_references(
        content=f"[HATG Event]\nEvent Source：{address}\n事件：manual.triggered",
        actor=actor, service=service,
    )
    assert len(references) == 1
    assert references[0]["address"] == address
    assert references[0]["status"] == expected_status
    if expected_status == "resolved":
        assert canonical_portal_location(address, references[0]["url"]) == (
            f"/lineage?lineage={ROOT_ID}&program_item={ITEMS[0][2]}"
        )


@pytest.mark.parametrize("actor,missing", [
    ("test-actor", False), ("test-actor", True), ("portal-other", False),
])
def test_bridge_preserves_program_target_or_fails(program_resources, monkeypatch, actor, missing):
    """资源桥接保留程序目标且缺失时返回空地址"""
    service, definition, _, _ = program_resources
    monkeypatch.setattr(serve_management_portal, "get_resource_service", lambda: service)
    address = lineage_program.program_component_address(ROOT_ID, ITEMS[0][2])
    if missing:
        definition["program"]["components"] = []
    location = serve_management_portal._resource_portal_location(address, actor)
    assert location == (
        f"/lineage?lineage={ROOT_ID}&program_item={ITEMS[0][2]}"
        if not missing else ""
    )
