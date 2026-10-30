from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest
import yaml

from management_portal.resources import isa as resource_isa
from management_portal.resources import ResourceService
from management_portal.resources.collaboration_registry import CollaborationDescriptorStore
from management_portal.resources.providers import project
from management_portal.resources.registration import ResourceRegistrationStore
from tests.cases.test_project_resource_provider import FakeAccessStore
from tests.cases.registry_support import ProjectRegistryYaml, write_projects


ACTOR = 'resident_0123456789ab'


@pytest.fixture
def project_registration(tmp_path, monkeypatch):
    registry = ProjectRegistryYaml()
    write_projects({
        'schema_version': 1,
        'projects': [],
        'status_vocab': ['active'],
    })
    monkeypatch.setattr(project, 'SHARE_ROOT', tmp_path)
    monkeypatch.setattr(project, 'PROJECTS_ROOT', tmp_path / 'Projects')
    access = FakeAccessStore()
    monkeypatch.setattr(project, 'get_portal_access_store', lambda: access)
    index = mock.Mock()
    index.change_targets.return_value = ['project_registry']
    index.queue_change.return_value = ['project_registry']
    index.wait_for_refresh.return_value = True
    service = ResourceService(
        index_provider=index,

        collaboration_store=CollaborationDescriptorStore(),
        registration_store=ResourceRegistrationStore(),
        mutation_provider_loader=project.install_mutation_provider,
    )
    monkeypatch.setattr(resource_isa, '_service', lambda: service)
    from management_portal import society_service
    monkeypatch.setattr(society_service, 'get_society_service', lambda: SimpleNamespace(
        lineage=SimpleNamespace(provision_project_principal=mock.Mock())))
    return service, index, access, registry


def _payload():
    return {'id': 'project-ack-isolated-fixture', 'description': 'Isolated receipt boundary',
            'project_type': 'other'}


def create(arguments=None):
    payload = _payload()
    arguments = arguments or {'project_id': payload['id'], 'description': payload['description'],
                              'project_type': payload['project_type']}
    return resource_isa.create_project(SimpleNamespace(argument=arguments.get, caller=ACTOR))


@pytest.mark.parametrize('index_finished', [True, False])
def test_project_registration_ack_publishes_without_waiting_for_the_index(
    project_registration, index_finished,
):
    """项目注册直接发布记录且不等待索引扫描"""
    service, index, access, registry = project_registration
    index.wait_for_refresh.return_value = index_finished
    result = create()
    assert set(result) == {'project'}
    assert result['project']['resource_id'] == _payload()['id']
    assert 'initial_admin_ids' not in result['project']
    assert access.updates == []
    projects = yaml.safe_load(registry.read_text())['projects']
    assert len(projects) == 1 and projects[0]['id'] == _payload()['id']
    assert 'index_projection' not in result['project']
    index.wait_for_refresh.assert_not_called()
    index.project_registered.assert_called_once_with('project', result['project'])


@pytest.mark.parametrize('instruction', ['CreateProject', 'RegisterResource'])
def test_committed_project_is_readable_to_principal_initialization_before_scan(
    project_registration, monkeypatch, instruction,
):
    """新建 Project 在扫描前即可供 Principal 初始化读取

    CreateProject 和 RegisterResource 使用真实 Project 索引适配器；后置 Principal 初始化立即读到正确路径、类型和说明，不等待扫描。

    :importance: P1
    :design: decision_4648f7fa3295
    """
    from management_portal import society_service, resource_index_registries
    from management_portal.society_service import resource_client

    service, index, _, _ = project_registration
    # Keep this fixture's registered path in its isolated directory rather
    # than translating the shared-root alias to the host's /share.
    monkeypatch.setattr(project, '_registry_value_for_path', lambda path: str(path))
    adapter = resource_index_registries.ProjectRegistryAdapter()
    records = {}

    def publish(resource_type, record):
        records[record['resource_id']] = adapter.project_record(resource_type, record)

    index.project_registered.side_effect = publish
    index.get_resource.side_effect = lambda resource_id, **_: records[resource_id]
    monkeypatch.setattr(resource_client, 'get_resource_service', lambda: service)
    paths = []

    def provision(project_id, actor):
        root = resource_client.project_root(project_id, actor=actor)
        assert root == (project.PROJECTS_ROOT / project_id).resolve()
        assert root.is_dir()
        paths.append(root)

    monkeypatch.setattr(society_service, 'get_society_service', lambda: SimpleNamespace(
        lineage=SimpleNamespace(provision_project_principal=provision)))
    if instruction == 'CreateProject':
        create()
    else:
        arguments = {'resource_type': 'project', 'fields': _payload()}
        resource_isa.register_resource(SimpleNamespace(
            argument=arguments.get, caller=ACTOR))
    assert len(paths) == 1
    assert records[_payload()['id']]['attributes']['path'] == str(paths[0])
    assert records[_payload()['id']]['attributes']['project_type'] == 'other'
    assert records[_payload()['id']]['summary'] == _payload()['description']
    index.wait_for_refresh.assert_not_called()


def test_repeating_project_create_conflicts_without_adding_another_record(project_registration):
    """重复创建项目报告冲突且不增加注册记录"""
    _, _, _, registry = project_registration
    project.create_project(_payload())
    with pytest.raises(FileExistsError, match='already exists'):
        project.create_project(_payload())
    projects = yaml.safe_load(registry.read_text())['projects']
    assert len(projects) == 1 and projects[0]['id'] == _payload()['id']


@pytest.mark.parametrize('instruction', ['CreateProject', 'RegisterResource'])
def test_project_creation_replays_only_principal_initialization_after_failure(project_registration, monkeypatch, instruction):
    """项目初始化失败后重放仅重试主体初始化"""
    from management_portal import society_service
    from management_portal.society_service import lineage_domain
    from management_portal.hatg.contracts import InstructionCall, InstructionContext
    from management_portal.hatg.kernel import InstructionKernel
    from management_portal.hatg.store import ExecutionStore
    from management_common.portal_database import portal_database

    service, _, _, _ = project_registration
    attempts = []
    def provision(project_id, actor):
        assert project.project_registry.project(project_id)['id'] == project_id
        attempts.append((project_id, actor))
        if len(attempts) == 1:
            raise RuntimeError('Lineage unavailable')
        return {'lineage_id': 'lineage_0123456789ab', 'resident_id': 'resident_0123456789ab'}
    monkeypatch.setattr(lineage_domain, 'provision_project_principal', provision)
    client = SimpleNamespace(lineage=SimpleNamespace(
        provision_project_principal=provision, retry_lineage_projections=lineage_domain.retry_lineage_projections))
    monkeypatch.setattr(society_service, 'get_society_service', lambda: client)
    kernel = InstructionKernel(resource_isa.binding(), store=ExecutionStore(portal_database(schema='project_lifecycle_recovery')),
                               authorize=lambda *_: None)
    args = ({'project_id': _payload()['id'], 'project_type': 'other'} if instruction == 'CreateProject'
            else {'resource_type': 'project', 'fields': _payload()})
    call = InstructionCall(instruction, args, idempotency_key='project-principal-recovery')
    with mock.patch.object(service, 'register', wraps=service.register) as register:
        first = kernel.execute(call, InstructionContext(ACTOR, 'cli'))
        assert first.error['code'] == 'instruction.projection_pending', first.error
        again = kernel.execute(call, InstructionContext(ACTOR, 'cli'))
        assert again.status == 'committed', again.error
        assert again.replayed
        assert register.call_count == 1
    assert attempts == [(_payload()['id'], ACTOR)] * 2
    assert (again.result.get('project') or again.result['resource'])['resource_id'] == _payload()['id']
