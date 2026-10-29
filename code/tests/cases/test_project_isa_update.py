""":module: spec.design-governance"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml

from management_portal.hatg.contracts import InstructionError
from management_portal.resources import isa as resource_isa
from tests.cases.test_project_registration_ack import ACTOR, create, project_registration  # noqa: F401


def test_update_project_changes_each_registry_field(project_registration, monkeypatch):
    """UpdateProject 更新注册字段，归一化项目类型，标签去除空白与重复项"""
    service, _index, _access, registry = project_registration
    registry.write_text(yaml.safe_dump({
        'schema_version': 1, 'projects': [], 'status_vocab': ['active', 'paused'],
    }))
    project_id = create()['project']['resource_id']
    monkeypatch.setattr(resource_isa, '_project_record', lambda pid, actor: {'resource_id': pid})
    arguments = {'project_id': project_id,
                 'changes': {'status': 'paused', 'ddl': '2026-12-31', 'project_type': '学术',
                             'description': '  新的说明  ', 'tags': ['robot', ' vla ', 'robot', '']}}
    result = resource_isa.update_project(SimpleNamespace(argument=arguments.get, caller=ACTOR))
    stored = yaml.safe_load(registry.read_text())['projects'][0]
    assert (stored['status'], stored['ddl'], stored['project_type']) == ('paused', '2026-12-31', 'academic')
    assert (stored['description'], stored['tags']) == ('新的说明', ['robot', 'vla'])
    assert result['project']['resource_id'] == project_id


def test_update_project_refuses_fields_it_does_not_change(project_registration, monkeypatch):
    """UpdateProject 不修改 path 等其他字段"""
    project_id = create()['project']['resource_id']
    monkeypatch.setattr(resource_isa, '_project_record', lambda pid, actor: {'resource_id': pid})
    arguments = {'project_id': project_id, 'changes': {'path': '/elsewhere'}}
    with pytest.raises(InstructionError) as error:
        resource_isa.update_project(SimpleNamespace(argument=arguments.get, caller=ACTOR))
    assert error.value.code == 'instruction.invalid_arguments'
