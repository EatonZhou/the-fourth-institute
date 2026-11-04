"""Deletion completion is readable without resurrecting removed permissions.

:module: workflow.project
:design: decision_4648f7fa3295
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from management_portal import project_registry
from management_portal.resources import project_deletion_effects as effects


@pytest.mark.parametrize('state', ['complete', 'pending'])
def test_original_deleter_observes_real_effect_status_without_running_effects(monkeypatch, state):
    """The receipt observes completed and pending effects; it performs no deletion.

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    receipt = {'project_id': 'sample', 'deleted_by': 'owner', 'deleted_at': 'original-time',
               'effects': {'status': state, 'attempts': 1}}
    rows = {(effects.COLLECTION, 'sample'): receipt,
            (project_registry.COLLECTION, project_registry.DOCUMENT_ID): {'projects': []}}
    monkeypatch.setattr(effects, 'get_registry_store', lambda: SimpleNamespace(get=lambda *key: deepcopy(rows.get(key))))
    monkeypatch.setattr(effects, 'run', lambda *args: pytest.fail('read must not retry effects'))
    result = effects.status_for_actor('sample', 'owner')
    assert result == {'project_id': 'sample', 'registration_status': 'unregistered',
                      'deleted_at': 'original-time', 'effects': {'status': state, 'attempts': 1}}


@pytest.mark.parametrize('actor,registered,has_receipt', [('other', False, True), ('', False, True),
                                                      ('owner', True, True), ('owner', False, False)])
def test_other_actor_missing_receipt_and_reused_project_name_cannot_confirm_deletion(monkeypatch, actor, registered, has_receipt):
    """An old audit cannot disclose another actor's data or retire a new Project.

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    rows = {(effects.COLLECTION, 'sample'): {'deleted_by': 'owner', 'effects': {'status': 'complete'}} if has_receipt else None,
            (project_registry.COLLECTION, project_registry.DOCUMENT_ID): {'projects': [{'id': 'sample'}] if registered else []}}
    monkeypatch.setattr(effects, 'get_registry_store', lambda: SimpleNamespace(get=lambda *key: deepcopy(rows.get(key))))
    with pytest.raises(KeyError):
        effects.status_for_actor('sample', actor)
