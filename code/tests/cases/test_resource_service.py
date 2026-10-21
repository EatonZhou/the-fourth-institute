from __future__ import annotations
from management_portal.society_service import business_fact_views

from tests.cases.canonical_domain_fixture import canonical_projection

from pathlib import Path
from unittest import mock

import pytest

from tests.cases.registry_support import registry_document

from management_portal import resource_index_backend
from management_portal.society_service import resource_client
from management_portal.portal_access_control import PortalAccessStore
from management_portal.resources import ResourceOperationError, ResourceService
from management_portal.resources.access import ResourceAccessPolicy
from management_portal.resources.collaboration_registry import (
    COLLABORATION_DESCRIPTOR_SCHEMA,
    CollaborationDescriptorStore,
)
from management_portal.resources.schema import normalize_resource_attributes
from management_portal.resources.providers import install_action_providers


def test_resource_address_trims_boundary_whitespace_after_decoding(subtests, isolated_portal_case):
    """资源地址在解码后去掉首尾空白和斜杠"""
    for address in [
    "%20decision_4a9ba5dfada6%20",
    "%20%2Fdecision_4a9ba5dfada6%2F%20",
    " \tdecision_4a9ba5dfada6\t ",
]:
        with subtests.test(address=address), isolated_portal_case():
            parsed = ResourceService().parse_address(address)
            assert parsed.value == "decision_4a9ba5dfada6"
            assert parsed.root_id == parsed.value


def test_resource_address_rejects_decoded_control_characters(subtests, isolated_portal_case):
    """解码后含 NUL、换行或回车的资源地址被拒绝"""
    for suffix in ["%00", "%0A", "%0D"]:
        with subtests.test(suffix=suffix), isolated_portal_case():
            with pytest.raises(ValueError, match="invalid resource address"):
                ResourceService().parse_address(f"decision_4a9ba5dfada6{suffix}")


def test_resource_read_scope_has_one_normalized_contract_contracts(subtests, isolated_portal_case):
    """read_scope 只接受规范取值，资源 ID 的校验、生成和类型推断统一由身份注册表决定"""
    with subtests.test(contract='test_resource_read_scope_has_one_normalized_contract'), isolated_portal_case() as case_path:
        assert normalize_resource_attributes({'read_scope': 'GLOBAL'})['read_scope'] == 'global'
        with pytest.raises(ValueError, match='invalid resource read_scope'):
            normalize_resource_attributes({'read_scope': 'shared'})
    with subtests.test(contract='test_identity_registry_is_the_only_type_validation_and_generation_rule'), isolated_portal_case() as case_path:
        service = ResourceService()
        generated = service.new_id('dataset')
        parsed = service.validate_id(generated, 'dataset')
        assert parsed.resource_type == 'dataset'
        assert len(parsed.code) == 12
        assert service.validate_id('RobotCompany', 'project').resource_type == 'project'
        assert service.validate_id('alice', 'user').resource_type == 'user'
        with pytest.raises(ValueError, match='does not match type'):
            service.validate_id('dataset_0123456789ab', 'model')
        with pytest.raises(ValueError, match='registered externally'):
            service.new_id('project')
        with pytest.raises(ValueError, match='unregistered resource type'):
            service.new_id('made_up')
    with subtests.test(contract='test_get_supplies_the_canonical_type_to_the_index'), isolated_portal_case():
        resource_id = 'paper_0123456789ab'
        index = mock.Mock()
        index.get_resource.return_value = {'resource_id': resource_id, 'resource_type': 'paper', 'title': 'Paper', 'attributes': {}}
        service = ResourceService(index_provider=index)
        assert service.get(resource_id, actor='test-actor')['resource_id'] == resource_id
        index.get_resource.assert_called_once_with(resource_id, resource_types=('paper',), actor='test-actor')
    with subtests.test(contract='test_get_uses_registered_id_exception_types_without_guessing'), isolated_portal_case():
        index = mock.Mock()
        index.get_resource.return_value = {'resource_id': 'AI员工', 'resource_type': 'project', 'title': 'AI员工', 'attributes': {}}
        service = ResourceService(index_provider=index)
        assert service.get('AI员工', actor='test-actor')['resource_type'] == 'project'
        index.get_resource.assert_called_once_with('AI员工', resource_types=frozenset({'project', 'user'}), actor='test-actor')
    with subtests.test(contract='test_formal_derived_resident_reference_uses_lineage_url'), isolated_portal_case():
        index = mock.Mock()
        index.get_resource.return_value = {'resource_id': 'resident_fedcba987654', 'resource_type': 'resident', 'title': 'Derived resident', 'summary': '', 'facets': ['resident', 'collaboration'], 'attributes': {'relations': [{'kind': 'root', 'target_id': 'resident_0123456789ab'}]}}
        policy = mock.Mock(spec=ResourceAccessPolicy)
        service = ResourceService(index_provider=index, access_policy=policy)
        service.register_resolver('resident', lambda address, **_context: {'address': address, 'root_id': address, 'resource_type': 'resident', 'target_kind': 'resident', 'record': {'resource_id': address}, 'portal_url': '/lineage?resident=resident_fedcba987654', 'exists': True})
        resolved = service.resolve('resident_fedcba987654', actor='CASE-A')
        reference = service.reference('resident_fedcba987654', actor='CASE-A')
        assert resolved['portal_url'] == '/lineage?resident=resident_fedcba987654'
        assert reference['url'] == '/lineage?resident=resident_fedcba987654'
    with subtests.test(contract='test_resolution_contract_and_access_policy_are_applied_at_the_facade'), isolated_portal_case():
        policy = mock.Mock(spec=ResourceAccessPolicy)
        service = ResourceService(access_policy=policy)
        service.register_resolver('device', lambda address, **_context: {'root_id': address, 'resource_type': 'device', 'target_kind': 'device', 'record': {'resource_id': address, 'name': 'Build host'}})
        resolved = service.resolve('device_0123456789ab', actor='alice')
        assert resolved['schema'] == 'tfi.resource_resolution.v2'
        assert resolved['address'] == 'device_0123456789ab'
        assert resolved['resource_id'] == 'device_0123456789ab'
        assert resolved['record']['name'] == 'Build host'
        policy.authorize_read.assert_called_once_with(resolved, actor='alice')


def test_resident_resource_bundle_uses_one_resource_service_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同步 Resident 资源包只发一次 ResourceService 请求，并带上 Workspace 关系"""
    calls: list[list[dict]] = []

    class Service:
        @staticmethod
        def sync_collaboration_resources(resources):
            payload = [dict(item) for item in resources]
            calls.append(payload)
            return {
                "resources": [
                    {"resource_id": item["descriptor"]["resource_id"]}
                    for item in payload
                ],
            }

    monkeypatch.setattr(resource_client, "get_resource_service", lambda: Service())
    resident_id = "resident_0123456789ab"
    workspace_id = "workspace_abcdefabcdef"
    result = resource_client.sync_resident_resource_bundles(
        [{
            "resident_id": resident_id,
            "lineage_id": "lineage_222222222222",
            "title": "Resident",
            "project_id": "RobotCompany",
            "backend": "codex",
            "workspace_bindings": [{
                "workspace_id": workspace_id,
                "path": str(tmp_path / "workspace"),
            }],
        }],
        updated_by="CASE-A",
    )

    assert len(calls) == 1
    assert [
        item["descriptor"]["resource_id"]
        for item in calls[0]
    ] == [resident_id]
    assert {"kind": "workspace", "target_id": workspace_id} in calls[0][0]["descriptor"]["relations"]
    assert result == 1


def test_resident_bundle_sync_preserves_shared_workspace_references(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """共享 Workspace 的多个 Resident 各自保留该关系，不另写 Workspace 描述符"""
    synchronized: list[dict] = []

    class Service:
        @staticmethod
        def sync_collaboration_resources(resources):
            synchronized.extend(dict(item) for item in resources)
            return {
                "resources": [
                    {"resource_id": item["descriptor"]["resource_id"]}
                    for item in resources
                ]
            }

    monkeypatch.setattr(resource_client, "get_resource_service", lambda: Service())
    workspace_id = "workspace_abcdefabcdef"
    workspace_path = str(tmp_path / "shared")
    records = [
        {
            "resident_id": "resident_111111111111",
            "lineage_id": "lineage_111111111111",
            "workspace_bindings": [{"workspace_id": workspace_id, "path": workspace_path}],
        },
        {
            "resident_id": "resident_222222222222",
            "lineage_id": "lineage_111111111111",
            "workspace_bindings": [{"workspace_id": workspace_id, "path": workspace_path}],
        },
    ]

    assert resource_client.sync_resident_resource_bundles(records) == 2
    workspaces = [
        item for item in synchronized
        if item["descriptor"]["resource_id"] == workspace_id
    ]
    assert workspaces == []
    assert [item["descriptor"]["resource_id"] for item in synchronized] == [
        "resident_111111111111",
        "resident_222222222222",
    ]
    assert all({"kind": "workspace", "target_id": workspace_id} in
               item["descriptor"]["relations"] for item in synchronized)


def test_directory_domain_views_preserve_supplied_facts_without_resource_rpc(monkeypatch):
    """目录领域视图原样保留传入的记录字段，不附加权限字段，不调用资源 RPC"""
    monkeypatch.setattr(resource_client, "get_resource_service", lambda: pytest.fail("domain view used resource RPC"))
    records = [{"lineage_id": "lineage_111111111111", "title": "Live", "project_id": "RobotCompany"},
               {"lineage_id": "lineage_222222222222", "title": "Other"}]
    hydrated = business_fact_views.canonical_views(records, id_field="lineage_id", actor="sample_admin")
    assert hydrated == records and hydrated[0] is not records[0]


def test_collaboration_sync_notifies_the_exact_descriptor_path_contracts(monkeypatch, tmp_path, subtests, isolated_portal_case):
    """协作描述符同步按该描述符的变更键通知索引，内容未变时不重写"""
    with subtests.test(contract='test_collaboration_sync_notifies_the_exact_descriptor_path'), isolated_portal_case():
        with monkeypatch.context() as monkeypatch:
            item = canonical_projection("resident_0123456789ab", title="Resident")
            descriptor = item["descriptor"]
            collaboration = CollaborationDescriptorStore()
            service = ResourceService(collaboration_store=collaboration)
            notify = mock.Mock()
            monkeypatch.setattr(service, '_notify_mutation', notify)
            service.sync_collaboration_resource(descriptor)
            notify.assert_called_once_with(path=collaboration.change_key(descriptor['resource_id']), resource_id=descriptor['resource_id'], resource_type='resident', kind='updated')
    with subtests.test(contract='test_collaboration_store_skips_semantically_unchanged_write'), isolated_portal_case():
        with monkeypatch.context() as monkeypatch:
            store = CollaborationDescriptorStore()
            descriptor = {'schema': COLLABORATION_DESCRIPTOR_SCHEMA, 'resource_id': 'resident_0123456789ab', 'title': 'Resident', 'summary': '', 'status': 'active', 'project_ids': [], 'relations': [], 'attributes': {}, 'created_at': '', 'updated_at': '', 'deleted_at': ''}
            first, first_changed = store.put_with_change(descriptor)
            monkeypatch.setattr('management_portal.resources.registry_store.RegistryWriter.put', lambda *_args: (_ for _ in ()).throw(AssertionError('unchanged descriptor must not be rewritten')))
            second, second_changed = store.put_with_change(descriptor)
            assert first_changed is True
            assert second_changed is False
            assert second == first


def test_unchanged_collaboration_sync_does_not_notify_contracts(monkeypatch, subtests, isolated_portal_case):
    """协作描述符内容未变时再次同步不发变更通知"""
    with subtests.test(contract='test_unchanged_collaboration_sync_does_not_notify'), isolated_portal_case():
        with monkeypatch.context() as monkeypatch:
            item = canonical_projection("resident_0123456789ab", title="Resident")
            descriptor = item["descriptor"]
            collaboration = CollaborationDescriptorStore()
            service = ResourceService(collaboration_store=collaboration)
            service.sync_collaboration_resource(**item)
            notify = mock.Mock()
            monkeypatch.setattr(service, '_notify_mutation', notify)
            service.sync_collaboration_resource(descriptor)
            notify.assert_not_called()

def test_collaboration_bundle_validates_before_writing_and_notifies_after_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """协作资源包先整体校验再写入、提交后才通知，含 Workspace 或非法 ID 时整包不写"""
    collaboration = CollaborationDescriptorStore()
    service = ResourceService(
        collaboration_store=collaboration,

    )
    resident_id = "resident_0123456789ab"
    lineage_id = "lineage_987654321abc"
    workspace_id = "workspace_abcdefabcdef"
    resources = [
        {
            "descriptor": {
                "schema": COLLABORATION_DESCRIPTOR_SCHEMA,
                "resource_id": resident_id,
                "title": "Resident",
                "summary": "",
                "status": "active",
                "project_ids": [],
                "relations": [],
                "attributes": {},
                "created_at": "",
                "updated_at": "",
                "deleted_at": "",
            },

        },
        {
            "descriptor": {
                "schema": COLLABORATION_DESCRIPTOR_SCHEMA,
                "resource_id": workspace_id,
                "title": "Workspace",
                "summary": "",
                "status": "active",
                "project_ids": [],
                "relations": [{"kind": "resident", "target_id": resident_id}],
                "attributes": {"path": str(tmp_path / "workspace")},
                "created_at": "",
                "updated_at": "",
                "deleted_at": "",
            },

        },
    ]
    resources[0] = canonical_projection(resident_id, title="Resident", lineage_id=lineage_id)
    # Workspace projections are refused (WorkspaceStore owns them); a Lineage is the second live item.
    workspace_item = resources[1]
    resources[1] = canonical_projection(lineage_id, title="Lineage")
    notifications: list[str] = []

    def notify(**kwargs) -> None:
        assert registry_document("resident", resident_id) is not None
        assert registry_document("lineage", lineage_id) is not None
        notifications.append(str(kwargs["resource_id"]))

    monkeypatch.setattr(service, "_notify_mutation", notify)

    result = service.sync_collaboration_resources(resources)

    assert [item["resource_id"] for item in result["resources"]] == [
        resident_id,
        lineage_id,
    ]
    assert notifications == [resident_id, lineage_id]

    other_resident_id = "resident_abcdef012345"
    with pytest.raises(ValueError, match="Workspace mutations belong to the Workspace store"):
        service.sync_collaboration_resources([
            canonical_projection(other_resident_id, title="Other", lineage_id=lineage_id),
            workspace_item,
        ])
    assert registry_document("resident", other_resident_id) is None
    assert notifications == [resident_id, lineage_id]

    invalid_bundle_root = tmp_path / "invalid-bundle"
    invalid_bundle = [
        resources[0],
        {
            **resources[1],
            "descriptor": {**resources[1]["descriptor"], "resource_id": "lineage_invalid"},
        },
    ]
    fresh = ResourceService(
        collaboration_store=CollaborationDescriptorStore(),

    )
    with pytest.raises(ValueError):
        fresh.sync_collaboration_resources(invalid_bundle)
    assert not list(invalid_bundle_root.glob("*.yaml"))
    assert not (
        tmp_path / "invalid-bundle-collaboration" / "residents"
    ).exists()


def test_collaboration_store_cold_exact_write_does_not_parse_the_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """新打开的协作描述符存储写入单条记录时不读取已有快照或变更"""
    writer = CollaborationDescriptorStore()
    template = {
        "schema": COLLABORATION_DESCRIPTOR_SCHEMA,
        "title": "Resident",
        "summary": "",
        "status": "active",
        "project_ids": [],
        "relations": [],
        "attributes": {},
        "created_at": "",
        "updated_at": "",
        "deleted_at": "",
    }
    for index in range(32):
        writer.put({
            **template,
            "resource_id": f"resident_{index:012x}",
        })
    cold = CollaborationDescriptorStore()
    reads: list[str] = []
    for method in ("snapshot", "changes_since", "get_many"):
        monkeypatch.setattr(cold._registry, method, lambda *args, _method=method: reads.append(_method))
    cold.put({
        **template,
        "resource_id": "resident_ffffffffffff",
    })

    assert reads == []


def test_address_resolution_is_registered_by_type() -> None:
    """地址解析器按资源类型注册，重复注册或未登记的类型被拒绝"""
    service = ResourceService()
    resolved_calls: list[tuple[str, str]] = []

    def resolve(address: str, **context):
        resolved_calls.append((address, str(context.get("actor") or "")))
        return {
            "address": address,
            "root_id": address.partition("/")[0],
            "resource_type": "device",
            "target_kind": "device",
            "record": {"resource_id": address},
            "exists": True,
        }

    service.register_resolver("device", resolve)
    parsed = service.parse_address("/device_0123456789ab/")
    result = service.resolve("device_0123456789ab", actor="alice")

    assert parsed.root_id == "device_0123456789ab"
    assert parsed.resource_type == "device"
    assert result["target_kind"] == "device"
    assert resolved_calls == [("device_0123456789ab", "alice")]
    with pytest.raises(ValueError, match="already registered"):
        service.register_resolver("device", resolve)
    with pytest.raises(ValueError, match="unregistered resource type"):
        service.register_resolver("unknown", resolve)


def test_exact_address_query_uses_actor_aware_resolver_before_fuzzy_index() -> None:
    """查询词是精确资源地址时按调用者身份解析，不走模糊索引查询"""
    index = mock.Mock()
    index.get_resource.return_value = {
        "resource_id": "repo_0123456789ab",
        "resource_type": "repo",
        "title": "Portal repo",
        "summary": "Canonical checkout",
        "facets": ["repo"],
        "attributes": {"project_ids": ["TFI Portal"], "status": "active"},
        "url": "/resource/repo?id=repo_0123456789ab",
    }
    service = ResourceService(index_provider=index)
    calls: list[tuple[str, str]] = []

    def resolve(address: str, **context):
        calls.append((address, str(context.get("actor") or "")))
        return {
            "address": address,
            "root_id": "repo_0123456789ab",
            "resource_type": "repo",
            "target_kind": "file",
            "record": {"resource_id": "repo_0123456789ab"},
            "exists": True,
        }

    service.register_resolver("repo", resolve)
    payload = service.query(
        q="repo_0123456789ab/file/README.md",
        actor="sample_admin",
        resource_types=["repo"],
        projects=["TFI Portal"],
        statuses=["active"],
        limit=10,
    )

    assert calls == [("repo_0123456789ab/file/README.md", "sample_admin")]
    assert payload["total"] == 1
    assert payload["resources"][0]["address"] == "repo_0123456789ab/file/README.md"
    assert payload["resources"][0]["title"] == "Portal repo"
    assert (
        payload["resources"][0]["url"]
        == "/resource/repo?item=repo_0123456789ab"
    )
    index.query.assert_not_called()


def test_reference_projects_preview_metadata(isolated_portal_case):
    """资源引用带上解析结果中的内容类型、媒体类型和大小"""
    with isolated_portal_case():
        index = mock.Mock()
        index.get_resource.return_value = {
            'resource_id': 'lineage_0123456789ab',
            'resource_type': 'lineage',
            'title': 'Root Lineage',
            'attributes': {},
        }
        service = ResourceService(index_provider=index)
        address = 'lineage_0123456789ab/program/component/event_source_111111111111'
        service.register_resolver('lineage', lambda requested, **_context: {
            'address': requested,
            'root_id': 'lineage_0123456789ab',
            'resource_type': 'lineage',
            'target_kind': 'lineage_program_component',
            'record': {'resource_id': 'lineage_0123456789ab'},
            'filename': 'event-report.md',
            'content_kind': 'markdown',
            'content_size': 42,
            'exists': True,
        })

        reference = service.reference(address, actor='alice')

        assert reference['content_kind'] == 'markdown'
        assert reference['media_type'] == 'text/markdown'
        assert reference['content_size'] == 42


def test_nested_reference_presents_the_resolved_child_not_its_indexed_root_contracts(tmp_path, subtests, isolated_portal_case):
    """嵌套地址的引用展示解析出的子对象标题和链接，动作不看项目角色"""
    with subtests.test(contract='test_nested_reference_presents_the_resolved_child_not_its_indexed_root'), isolated_portal_case() as case_path:
        tmp_path = case_path
        index = mock.Mock()
        index.get_resource.return_value = {'resource_id': 'lineage_0123456789ab', 'resource_type': 'lineage', 'title': 'Root Lineage', 'summary': 'Root summary', 'facets': ['lineage'], 'attributes': {'status': 'active'}}
        service = ResourceService(index_provider=index)
        address = 'lineage_0123456789ab/program/component/event_source_111111111111'
        service.register_resolver('lineage', lambda requested, **_context: {'address': requested, 'root_id': 'lineage_0123456789ab', 'resource_type': 'lineage', 'target_kind': 'lineage_program_component', 'record': {'resource_id': 'lineage_0123456789ab'}, 'title': 'B 完成一轮', 'summary': 'Event Source · resident.work_finished', 'portal_url': '/lineage?lineage=lineage_0123456789ab&program_item=event_source_111111111111', 'exists': True})
        reference = service.reference(address, actor='alice')
        assert reference['resource_id'] == 'lineage_0123456789ab'
        assert reference['title'] == 'B 完成一轮'
        assert reference['summary'] == 'Event Source · resident.work_finished'
        assert reference['url'].endswith('&program_item=event_source_111111111111')
        assert reference['facets'] == ['lineage']
    with subtests.test(contract='test_actions_are_not_gated_by_project_roles'), isolated_portal_case() as case_path:
        tmp_path = case_path
        skill_id = 'skill_0123456789ab'
        access = PortalAccessStore()
        access.upsert_user('reader', actor='CASE-A')
        index = mock.Mock()
        index.get_resource.return_value = {'resource_id': skill_id, 'resource_type': 'skill', 'title': 'Shared Skill', 'facets': ['knowledge', 'skill'], 'attributes': {'project_ids': ['Shared Skills'], 'read_scope': 'global'}}
        service = ResourceService(index_provider=index, portal_access=access)
        service.register_action_provider('skill', {'content.read': lambda resource_id, _payload, **_context: {'resource_id': resource_id, 'read': True}, 'content.write': lambda resource_id, _payload, **_context: {'resource_id': resource_id, 'written': True}})
        assert service.action(skill_id, 'content.read', {}, actor='reader') == {'resource_id': skill_id, 'read': True}
        assert service.action(skill_id, 'content.write', {}, actor='reader') == {'resource_id': skill_id, 'written': True}


def test_resolution_rejects_every_identity_alias_in_a_resource_record_contracts(subtests, isolated_portal_case):
    """解析结果记录中带 device_id 等身份别名时拒绝，未注册解析器的类型直接报错"""
    with subtests.test(contract='test_resolution_rejects_every_identity_alias_in_a_resource_record'), isolated_portal_case() as case_path:
        service = ResourceService()
        service.register_resolver('device', lambda address, **_context: {'root_id': address, 'resource_type': 'device', 'record': {'resource_id': address, 'device_id': address}})
        with pytest.raises(ValueError, match='identity aliases: device_id'):
            service.resolve('device_0123456789ab')
    with subtests.test(contract='test_registered_type_without_a_resolver_is_rejected'), isolated_portal_case() as case_path:
        index = mock.Mock()
        service = ResourceService(index_provider=index)
        with pytest.raises(ValueError, match='no address resolver registered for device'):
            service.resolve('device_0123456789ab', actor='alice')
        assert not hasattr(service, 'register_fallback_resolver')
    with subtests.test(contract='test_change_notification_routes_by_resource_or_source_not_adapter_name'), isolated_portal_case():
        adapter = _TypedAdapter()
        index = resource_index_backend.ResourceIndexService(store=resource_index_backend.IndexStore(), adapters=[adapter], start_worker=False)
        service = ResourceService(index_provider=index)
        created = service.notify_source_changed(resource_id='paper_0123456789ab', kind='created')
        coalesced = service.notify_source_changed(path='/tmp/example.paper', kind='updated')
        assert created['accepted'] is True
        assert created['targets'] == ['paper']
        assert created['queued'] == ['paper']
        assert coalesced['accepted'] is True
        assert coalesced['targets'] == ['paper']
        assert coalesced['queued'] == []
        assert index.adapter_layer._pending['paper'].full is True
        with pytest.raises(ValueError, match='does not match type'):
            service.notify_source_changed(resource_id='paper_0123456789ab', resource_type='search')
    with subtests.test(contract='test_portal_device_credential_action_targets_effective_resource'), isolated_portal_case():
        device_id = 'device_0123456789ab'
        parent_id = 'device_abcdef012345'
        service = ResourceService(action_provider_loader=install_action_providers)
        with mock.patch('management_portal.device_identity_backend.effective_ssh_access', return_value={'host': 'gpu-parent', 'via_resource_id': parent_id, 'credential_saved': False}), mock.patch('management_portal.device_identity_backend.save_device_ssh_password', return_value={'resource_id': parent_id, 'credential_saved': True}) as save_credential:
            result = service.action(device_id, 'ssh.credential.save', {'password': 'secret'}, actor='alice')
        assert result == {'resource_id': device_id, 'via_resource_id': parent_id, 'credential_saved': True}
        save_credential.assert_called_once_with(parent_id, 'secret')
    with subtests.test(contract='test_registration_projects_without_refresh_wait'), isolated_portal_case():
        repo_id = 'repo_0123456789ab'
        index = mock.Mock()
        index.change_targets.return_value = ['resource_registry']
        index.queue_change.return_value = ['resource_registry']
        index.wait_for_refresh.return_value = True
        service = ResourceService(index_provider=index)
        service.register_mutation_provider('repo', register=lambda _payload, **_context: {'resource_id': repo_id}, unregister=lambda resource_id, **_context: {'resource_id': resource_id, 'deleted': True})
        result = service.register('repo', {'path': '/tmp/sample'}, actor='test-actor', wait_for_index=True, index_timeout=7)
        assert result == {'resource_id': repo_id}
        index.project_registered.assert_called_once_with('repo', result)
        index.wait_for_refresh.assert_not_called()
    with subtests.test(contract='test_committed_registration_reports_projection_failure'), isolated_portal_case():
        repo_id = 'repo_0123456789ab'
        index = mock.Mock()
        index.project_registered.side_effect = RuntimeError('index unavailable')
        service = ResourceService(index_provider=index)
        service.register_mutation_provider('repo', register=lambda _payload, **_context: {'resource_id': repo_id}, unregister=lambda resource_id, **_context: {'resource_id': resource_id, 'deleted': True})
        with pytest.raises(ResourceOperationError) as raised:
            service.register('repo', {'path': '/tmp/sample'}, actor='test-actor')
        assert raised.value.details['domain_committed'] is True
        assert raised.value.details['stage'] == 'index_projection'


def test_failed_resolver_loader_rolls_back_before_retry() -> None:
    """解析器加载失败时回滚已注册部分，下次请求重新加载成功"""
    attempts = 0

    def load(service: ResourceService) -> None:
        nonlocal attempts
        attempts += 1
        service.register_resolver(
            "device",
            lambda address, **_context: {
                "root_id": address,
                "resource_type": "device",
                "record": {"resource_id": address},
            },
        )
        if attempts == 1:
            raise RuntimeError("resolver loader failed")

    service = ResourceService(resolver_loader=load)
    with pytest.raises(RuntimeError, match="resolver loader failed"):
        service.resolve("device_0123456789ab", actor="test-actor")

    assert service.resolve("device_0123456789ab", actor="test-actor")["resource_id"] == (
        "device_0123456789ab"
    )
    assert attempts == 2


def test_indexed_resolver_loader_retries_after_temporary_index_failure() -> None:
    """索引暂时故障导致解析器加载失败后，下次请求重试成功"""
    class FlakyIndex:
        def __init__(self) -> None:
            self.calls = 0

        def indexed_types(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary index failure")
            return frozenset({"device"})

        @staticmethod
        def supports_type(resource_type: str) -> bool:
            return resource_type == "device"

    index = FlakyIndex()

    def load(service: ResourceService) -> None:
        service.register_index_backed_resolver(
                lambda address, **_context: {
                    "root_id": address,
                    "resource_type": "device",
                    "record": {"resource_id": address},
                }
            )

    service = ResourceService(index_provider=index, resolver_loader=load)

    with pytest.raises(RuntimeError, match="temporary index failure"):
        service.capabilities("device")

    capabilities = service.capabilities("device")
    assert capabilities["resolvable"] is True
    assert index.calls == 2


class _TypedAdapter:
    name = "paper"
    resource_types = frozenset({"paper"})

    def fingerprint(self) -> str:
        return "one"

    def scan(self):
        return [], {}

    def owns_path(self, path: Path) -> bool:
        return path.suffix == ".paper"


def test_a_resident_registers_a_project_resource_without_a_project_role() -> None:
    """Resident 无需 Project 角色即可登记 Project 资源

    ResourceService.register 不检查 Project 角色：Resident 登记带 project_id 的实验资源时直接成功。
    """
    experiment_id = "experiment_0123456789ab"
    index = mock.Mock()
    service = ResourceService(index_provider=index)
    service.register_mutation_provider(
        "experiment",
        register=lambda payload, **_context: {
            "resource_id": experiment_id,
            **payload,
        },
    )

    created = service.register(
        "experiment",
        {"project_id": "ACLink", "title": "Sparse model experiment"},
        actor="resident_111111111111",
    )

    assert created["resource_id"] == experiment_id
    assert created["project_id"] == "ACLink"
    index.project_registered.assert_called_once_with("experiment", created)


def test_open_content_resolves_once_before_reading_or_streaming(tmp_path) -> None:
    """打开资源内容时只解析一次再读取或流式返回，匿名调用被拒绝"""
    service = ResourceService(index_provider=mock.Mock())
    address = "information_0123456789ab"
    file_address = "skill_0123456789ab/file/report.pdf"
    file_path = tmp_path / "report.pdf"
    file_path.write_bytes(b"pdf")
    counts = {"information": 0, "file": 0, "read": 0}

    def information_resolve(value, **_context):
        counts["information"] += 1
        return {"address": value, "root_id": value, "resource_type": "information", "target_kind": "knowledge", "record": {"resource_id": value}, "exists": True}

    def information_read(resolved, **_context):
        counts["read"] += 1
        return b"body", "text/plain", resolved

    def file_resolve(value, **_context):
        counts["file"] += 1
        return {"address": value, "root_id": "skill_0123456789ab", "resource_type": "skill", "target_kind": "file", "record": {"resource_id": "skill_0123456789ab"}, "resolved_path": str(file_path), "exists": True}

    service.register_resolver("information", information_resolve, read_content=information_read, replace=True)
    service.register_resolver("skill", file_resolve, read_content=information_read, replace=True)

    data, content_type, resolution = service.open_content(address, actor="alice")
    assert (data, content_type, resolution["address"]) == (b"body", "text/plain", address)
    assert service.open_content(file_address, actor="alice")["resolved_path"] == str(file_path)
    assert counts == {"information": 1, "file": 1, "read": 1}
    with pytest.raises(PermissionError):
        service.open_content(address, actor="")
    assert counts["read"] == 1


def test_every_signed_in_user_may_reference_and_update_resource_content() -> None:
    """任何登录用户都可以引用并修改资源正文

    引用与修改共用同一条规则：viewer 与 editor 都得到可下载、可编辑、可保存的引用，并都能修改正文。
    """
    resource_id = "information_0123456789ab"
    index = mock.Mock()
    index.get_resource.return_value = {
        "resource_id": resource_id,
        "resource_type": "information",
        "title": "Editable information",
        "facets": ["knowledge"],
        "attributes": {"project_ids": ["AI员工"]},
    }
    service = ResourceService(index_provider=index)
    service.register_resolver(
        "information",
        lambda _address, **_context: {
            "address": resource_id,
            "root_id": resource_id,
            "resource_type": "information",
            "target_kind": "knowledge",
            "record": {
                "resource_id": resource_id,
                "title": "Editable information",
                "filename": "editable-information.md",
                "version": {"number": 4},
            },
            "filename": "editable-information.md",
            "exists": True,
        },
        read_content=lambda *_args, **_kwargs: (
            b"# Editable information\n",
            "text/markdown",
            {},
        ),
    )
    service.register_mutation_provider(
        "information",
        update=lambda current_id, payload, **_context: {
            "resource_id": current_id,
            "text": payload["text"],
            "project_ids": ["AI员工"],
        },
        content_updatable=True,
    )

    viewer_reference = service.reference(resource_id, actor="viewer")
    assert viewer_reference["capabilities"]["edit"] is True
    assert service.update(resource_id, {"text": "viewer edit"}, actor="viewer")["text"] == "viewer edit"

    editor_reference = service.reference(resource_id, actor="editor")
    assert editor_reference["filename"] == "editable-information.md"
    assert editor_reference["version"] == "4"
    assert editor_reference["capabilities"] == {
        "download": True,
        "edit": True,
        "save": True,
    }
    updated = service.update(resource_id, {"text": "editor edit"}, actor="editor")
    assert updated["text"] == "editor edit"


def test_resource_mutations_dispatch_by_type_and_notify_the_derived_index(tmp_path) -> None:
    """资源登记、更新、注销按类型分发并通知索引，权限同步失败时报告已提交"""
    repo_id = "repo_0123456789ab"
    calls: list[tuple[str, object, str]] = []
    index = mock.Mock()
    index.change_targets.return_value = ["resource_registry"]
    index.queue_change.return_value = ["resource_registry"]
    service = ResourceService(index_provider=index)

    def register(payload, **context):
        calls.append(
            (
                "register",
                payload,
                context["actor"],
            )
        )
        return {"resource_id": repo_id, "name": "sample"}

    def unregister(resource_id, **context):
        calls.append(
            (
                "unregister",
                resource_id,
                context["actor"],
            )
        )
        return {"resource_id": resource_id, "deleted": True}

    def update(resource_id, payload, **context):
        calls.append(
            (
                "update",
                (resource_id, payload),
                context["actor"],
            )
        )
        return {"resource_id": resource_id, "name": "updated"}

    service.register_mutation_provider(
        "repo",
        register=register,
        unregister=unregister,
        update=update,
    )
    created = service.register(
        "repo",
        {"path": "/tmp/sample"},
        actor="alice",
    )
    deleted = service.unregister(
        repo_id,
        actor="alice",
    )
    updated = service.update(
        repo_id,
        {"operation": "set_remote"},
        actor="alice",
    )

    assert created == {"resource_id": repo_id, "name": "sample"}
    assert deleted == {"resource_id": repo_id, "deleted": True}
    assert updated == {"resource_id": repo_id, "name": "updated"}
    assert calls == [
        (
            "register",
            {"path": "/tmp/sample"},
            "alice",
        ),
        ("unregister", repo_id, "alice"),
        (
            "update",
            (repo_id, {"operation": "set_remote"}),
            "alice",
        ),
    ]
    index.project_registered.assert_called_once_with("repo", created)
    index.project_unregistered.assert_called_once_with("repo", repo_id)
    assert index.change_targets.call_args_list == [
        mock.call(
            path="",
            resource_id=repo_id,
            resource_type="repo",
        ),
        mock.call(
            path="",
            resource_id=repo_id,
            resource_type="repo",
        ),
    ]
    assert index.queue_change.call_args_list == [
        mock.call(("resource_registry",), path=""),
        mock.call(("resource_registry",), path=""),
    ]
    with pytest.raises(
        ValueError,
        match="no mutation provider registered for article",
    ):
        service.register("article", {"path": "/tmp/article"})

    from management_portal.hatg.contracts import normalize_error
    from management_portal.resources.errors import (
        ResourceOperationError, resource_error_from_payload, resource_error_payload,
    )

    for operation, kind in (("register", "create"), ("update", "update"), ("unregister", "delete")):
        committed = tmp_path / operation

        def mutate(*_args, **_kwargs):
            committed.write_text(operation, encoding="utf-8")
            return {"resource_id": repo_id}

        from management_portal import permission_system

        failing = mock.Mock(side_effect=ValueError("missing_canonical_parent"))
        target = ResourceService(index_provider=index)
        target.register_mutation_provider("repo", register=mutate, update=mutate, unregister=mutate)
        args = ("repo", {}) if operation == "register" else (repo_id, {}) if operation == "update" else (repo_id,)
        index.reset_mock()
        hook = "retire_resources" if operation == "unregister" else "sync_resource"
        with mock.patch.object(permission_system, hook, failing), pytest.raises(ResourceOperationError) as raised:
            getattr(target, operation)(*args, actor="alice")
        assert committed.read_text(encoding="utf-8") == operation
        failing.assert_called_once()
        index.queue_change.assert_not_called()
        for error in (raised.value, resource_error_from_payload(resource_error_payload(raised.value))):
            failure = normalize_error(error).to_dict()
            assert failure["code"] == "instruction.failed"
            assert failure["commit_state"] == "committed"
            assert failure["retryable"] is False
            assert failure["details"]["resource_id"] == repo_id
            assert failure["details"]["mutation"] == kind
            assert failure["details"]["stage"] == "authorization_sync"
            assert failure["details"]["cause"]["message"] == "missing_canonical_parent"

    target = ResourceService(index_provider=index)
    target.register_mutation_provider("repo", register=mock.Mock(side_effect=ValueError("invalid input")))
    with pytest.raises(ValueError) as raised:
        target.register("repo", {}, actor="alice")
    failure = normalize_error(raised.value).to_dict()
    assert failure["commit_state"] == "not_committed"
    assert failure["code"] == "instruction.invalid_arguments"


def test_resource_actions_dispatch_by_resource_type() -> None:
    """资源动作按类型分发，Project 动作以请求者身份确认类型和权限"""
    service = ResourceService()
    calls: list[tuple[str, dict, str]] = []

    def test_ssh(resource_id, payload, **context):
        calls.append(
            (
                resource_id,
                payload,
                context["actor"],
            )
        )
        return {
            "resource_id": resource_id,
            "via_resource_id": resource_id,
            "reachable": True,
        }

    service.register_action_provider("device", {"ssh.test": test_ssh})
    result = service.action(
        "device_0123456789ab",
        "ssh.test",
        {"timeout_seconds": 5},
        actor="alice",
    )

    assert result == {
        "resource_id": "device_0123456789ab",
        "via_resource_id": "device_0123456789ab",
        "reachable": True,
    }
    assert calls == [
        (
            "device_0123456789ab",
            {"timeout_seconds": 5},
            "alice",
        )
    ]
    with pytest.raises(
        ValueError,
        match="device resources do not support action terminal.start",
    ):
        service.action("device_0123456789ab", "terminal.start")
    with pytest.raises(ValueError, match="invalid resource action"):
        service.register_action_provider("device", {"SSH TEST": test_ssh})

    # Project IDs are externally registered. Resolve their type under the
    # requesting actor; an anonymous lookup can hide an otherwise visible ID.
    read_summary = mock.Mock(return_value={"text": "Project summary"})
    service.register_action_provider("project", {"summary.read": read_summary})
    with mock.patch.object(service, "get") as get, mock.patch.object(service, "_require_mutation_actor"):
        get.return_value = {"resource_id": "ACLink", "resource_type": "project"}
        assert service.action("ACLink", "summary.read", actor="alice") == {"text": "Project summary"}
        get.assert_called_once_with("ACLink", actor="alice")
        read_summary.assert_called_once_with("ACLink", {}, actor="alice")
        read_summary.reset_mock()
        get.side_effect = PermissionError("project access revoked")
        with pytest.raises(PermissionError, match="project access revoked"):
            service.action("ACLink", "summary.read", actor="bob")
        get.assert_called_with("ACLink", actor="bob")
        read_summary.assert_not_called()


def test_warm_waits_for_index_readiness(tmp_path, monkeypatch) -> None:
    """ResourceService 预热时索引尚未就绪则重试直到可查询"""
    # The server warm-up queries the index once, waiting for an index that is
    # still starting.
    from management_portal.resources import service as service_module
    from management_portal.resources.errors import ResourceServiceUnavailable

    index = mock.Mock()
    index.query.side_effect = [ResourceServiceUnavailable("resource projection unavailable: paper"), {"resources": []}]
    service = ResourceService.__new__(ResourceService)
    service._index = lambda: index
    monkeypatch.setattr(service_module.time, "sleep", lambda _seconds: None)

    service.warm()

    assert index.query.call_args_list == [mock.call(actor="", limit=1)] * 2


def test_warm_without_an_index_stops_quietly(tmp_path, monkeypatch) -> None:
    """没有索引时 ResourceService 预热直接结束不报错"""
    service = ResourceService.__new__(ResourceService)
    service._index_provider = None

    service.warm()


def test_collaboration_descriptor_rejects_old_permission_fields() -> None:
    """协作描述保留普通属性并拒绝旧权限字段"""
    store = CollaborationDescriptorStore()
    value = {"schema": "tfi.collaboration-descriptor.v2", "resource_id": "resident_0123456789ab",
             "attributes": {"retained": True}}
    assert store.normalize(value)["attributes"] == value["attributes"]
    for field in ("visibility", "resource_capabilities", "capabilities"):
        with pytest.raises(ValueError):
            store.normalize({**value, "attributes": {**value["attributes"], field: []}})
