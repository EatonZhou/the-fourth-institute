"""Public Knowledge mutation contract is the actual pre-write gate.

:design: decision_ad3e21094f14
:module: resources.types
"""
from copy import deepcopy
from unittest import mock

import pytest
from knowledge_board_lan import knowledge_backend

from management_portal.resources import ResourceService
from management_portal.resources.providers import knowledge, knowledge_tags
from management_portal.resources.mutation_contracts import ResourceMutationValidationError


@pytest.fixture
def service():
    result = ResourceService(index_provider=mock.Mock())
    knowledge.install_mutation_provider(result)
    knowledge_tags.install_mutation_provider(result)
    return result


def test_discovery_returns_detached_knowledge_and_skill_contracts(service):
    first = service.capabilities('decision')['contracts']
    assert first['register']['required'] == ['project_id', 'title']
    assert first['register']['properties']['metaness']['type'] == 'integer'
    assert first['register']['properties']['importance']['default'] == 5
    assert first['update']['oneOf'] == [{'required': ['text']}, {'required': ['body']}]
    first['register']['properties']['importance']['minimum'] = -99
    assert service.capabilities('decision')['contracts']['register']['properties']['importance']['minimum'] == 1
    skill = service.capabilities('skill')['contracts']
    assert 'register' not in skill
    assert skill['update']['required'] == ['text']
    assert 'body' not in skill['update']['properties']
    assert service.capabilities('knowledge_tags')['contracts']['update']['required'] == ['content', 'expected_version']


@pytest.mark.parametrize(('field', 'value', 'keyword'), [
    ('importance', True, 'type'), ('importance', 5.0, 'type'),
    ('importance', '5', 'type'), ('importance', 0, 'minimum'),
    ('metaness', None, 'type'), ('metaness', 11, 'maximum'),
    ('ai_generated', 'false', 'type'), ('links', [], 'type'),
    ('private_token', 'SECRET-MUST-NOT-LEAK', 'additionalProperties'),
])
def test_registration_contract_rejects_before_reservation_or_domain(service, field, value, keyword):
    fields = {'project_id': 'AI员工', 'title': 'Typed mutation', field: value}
    before = deepcopy(fields)
    with mock.patch.object(service._registrations, 'reserve') as reserve, mock.patch.object(
        knowledge_backend, 'register_item'
    ) as domain, pytest.raises(ResourceMutationValidationError) as caught:
        service.register('decision', fields, actor='resident_aaaaaaaaaaaa')
    assert fields == before
    reserve.assert_not_called()
    domain.assert_not_called()
    details = caught.value.details
    assert details['resource_type'] == 'decision'
    assert details['operation'] == 'register'
    assert any(e['path'] == '/' + field and e['keyword'] == keyword for e in details['errors'])
    assert 'SECRET-MUST-NOT-LEAK' not in str(caught.value)


@pytest.mark.parametrize(('resource_id', 'changes', 'path'), [
    ('decision_0123456789ab', {'body': 17}, '/body'),
    ('decision_0123456789ab', {'body': 'valid', 'expected_version': True}, '/expected_version'),
    ('decision_0123456789ab', {'text': 'valid', 'version_recovery_change_ids': ['change_a']}, '/expected_version'),
    ('skill_0123456789ab', {'body': 'unsupported'}, '/body'),
    ('knowledge_tags_7d4e9a2c6f10', {'content': '[]'}, '/expected_version'),
])
def test_update_contract_rejects_before_domain(service, resource_id, changes, path):
    with mock.patch.object(knowledge_backend, 'update_item_text') as domain, pytest.raises(
        ResourceMutationValidationError
    ) as caught:
        service.update(resource_id, changes, actor='resident_aaaaaaaaaaaa')
    domain.assert_not_called()
    assert caught.value.details['operation'] == 'update'
    assert any(e['path'] == path for e in caught.value.details['errors'])
