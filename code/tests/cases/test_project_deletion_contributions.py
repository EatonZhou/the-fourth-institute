"""Feature-owned deletion effects replay the Project owner's frozen receipt.

:module: resource.service
:design: decision_4aa13bc21e8e
"""
from copy import deepcopy

import pytest

from management_portal import portal_features, presentation_store, presentation_project_deletion, project_registry
from management_portal.resources import project_deletion_effects as effects
from management_portal.resources.registry_store import get_registry_store


@pytest.fixture
def favorites(monkeypatch):
    declared = portal_features.contributions('project.deletion_effects')
    assert ('presentation', presentation_project_deletion.PROJECT_DELETION_EFFECTS) in declared
    monkeypatch.setattr(portal_features, 'contributions',
                        lambda hook: declared if hook == 'project.deletion_effects' else ())
    # A Project name can be reused after deletion. These effects may consume
    # only saved identities, never rediscover the new Project's workbenches.
    monkeypatch.setattr(project_registry, 'project',
                        lambda *args: pytest.fail('deletion effect rediscovered a Project'))
    for actor in ('alice', 'bob'):
        for target in ('workbench_aaaaaaaaaaaa', 'workbench_bbbbbbbbbbbb', 'workbench_cccccccccccc'):
            presentation_store.set_favorite(actor, 'workbench', target, True)
        presentation_store.set_favorite(actor, 'paper', 'workbench_aaaaaaaaaaaa', True)
    return {
        'project_id': 'Reused', 'deleted_by': 'alice',
        'workbench_ids': ['workbench_aaaaaaaaaaaa', 'workbench_bbbbbbbbbbbb'],
        'effects': {'status': 'pending', 'attempts': 1,
                    'completed': {'permissions': None, 'avatar': None, 'index': None},
                    'errors': {'favorites': 'previous owner unavailable'}},
    }


def save(receipt):
    with get_registry_store().writer(effects.COLLECTION) as writer:
        writer.put(effects.COLLECTION, receipt['project_id'], receipt)


def test_contribution_previews_without_mutating_favorites(favorites):
    before = deepcopy(favorites)
    callback = presentation_project_deletion.PROJECT_DELETION_EFFECTS['favorites']
    assert callback(favorites, write=False) == favorites['workbench_ids']
    assert favorites == before
    for actor in ('alice', 'bob'):
        assert len(presentation_store.favorite_ids(actor, 'workbench')) == 3


def test_old_error_cursor_retries_partial_cleanup_from_frozen_ids(favorites, monkeypatch):
    original = presentation_store.forget_favorite_target
    failed = False
    def interrupted(surface, target):
        nonlocal failed
        if target == favorites['workbench_ids'][1] and not failed:
            failed = True
            raise OSError('preference owner unavailable')
        return original(surface, target)
    monkeypatch.setattr(presentation_store, 'forget_favorite_target', interrupted)
    save(favorites)
    pending = effects.run('Reused')
    assert pending['effects']['status'] == 'pending'
    assert pending['effects']['errors'] == {'favorites': 'preference owner unavailable'}
    assert 'favorites' not in pending['effects']['completed']
    assert presentation_store.favorite_ids('alice', 'workbench') == [
        'workbench_bbbbbbbbbbbb', 'workbench_cccccccccccc']
    effects.run_pending()
    finished = get_registry_store().get(effects.COLLECTION, 'Reused')
    assert finished['effects']['status'] == 'complete'
    assert finished['effects']['errors'] == {}
    assert finished['effects']['attempts'] == 3
    assert finished['effects']['completed']['favorites'] == favorites['workbench_ids']
    assert 'relation:presentation' not in finished['effects']['completed']
    for actor in ('alice', 'bob'):
        assert presentation_store.favorite_ids(actor, 'workbench') == ['workbench_cccccccccccc']
        assert presentation_store.favorite_ids(actor, 'paper') == ['workbench_aaaaaaaaaaaa']
    assert effects.run('Reused') == finished


def test_old_completed_cursor_is_not_applied_again(favorites, monkeypatch):
    favorites['effects']['completed']['favorites'] = None
    favorites['effects']['errors'] = {}
    save(favorites)
    monkeypatch.setattr(presentation_store, 'forget_favorite_target',
                        lambda *args: pytest.fail('completed effect was applied again'))
    result = effects.run('Reused')
    assert result['effects']['status'] == 'complete'
    assert result['effects']['completed']['favorites'] is None
    assert len(presentation_store.favorite_ids('alice', 'workbench')) == 3


def test_missing_feature_does_not_discard_its_saved_error(favorites, monkeypatch):
    monkeypatch.setattr(portal_features, 'contributions', lambda hook: ())
    save(favorites)
    result = effects.run('Reused')
    assert result['effects']['status'] == 'pending'
    assert result['effects']['errors'] == favorites['effects']['errors']
    assert len(presentation_store.favorite_ids('alice', 'workbench')) == 3
