"""Portable script targets cross only the installed pure contribution boundary.

:module: operations.deployment
:design: decision_898b8911a2ff
:profiles: local.fixture
:risk: no-external-side-effects
"""
import pytest

from management_portal import portal_features, research_map_automation


def test_target_path_reaches_domain_owner_without_changing_source_only_contract(monkeypatch):
    seen = []

    def existing(source):
        seen.append(source)
        return source

    monkeypatch.setattr(portal_features, 'contributions', lambda hook: (
        ('existing', existing), ('projects', research_map_automation.migrate_digest_script)))
    source = research_map_automation.LEGACY_SCRIPT
    target = '/opt/tfi/the-fourth-institute/code/scripts/research_map_digest.py'
    migrated = portal_features.migrate_workflow_script(source, script_path=target)
    assert migrated == research_map_automation.digest_script(target)
    assert seen == [source]
    assert portal_features.migrate_workflow_script(migrated, script_path=target) == migrated
    custom = '# custom\n' + source
    assert portal_features.migrate_workflow_script(custom, script_path=target) == custom


def test_owner_typeerror_is_not_retried_without_target(monkeypatch):
    seen = []

    def broken(source, *, script_path=None):
        seen.append(script_path)
        raise TypeError('owner failed')

    monkeypatch.setattr(portal_features, 'contributions', lambda hook: (('broken', broken),))
    with pytest.raises(TypeError, match='owner failed'):
        portal_features.migrate_workflow_script('source', script_path='/target.py')
    assert seen == ['/target.py']


def test_targeted_migration_preserves_single_owner_rule(monkeypatch):
    def first(source, **options):
        return source + options['script_path']

    monkeypatch.setattr(portal_features, 'contributions', lambda hook: (
        ('first', first), ('second', lambda source: source + '\ncustom')))
    with pytest.raises(ValueError, match='multiple features'):
        portal_features.migrate_workflow_script('source', script_path='/target.py')


def test_installed_studio_contribution_upgrades_only_its_original_flows():
    from management_portal.society_service.lineage_effect_script import deliver_message_script
    from management_portal.workbench_projections.studio import FLOWS
    from management_portal.workbench_projections.studio_credentials import studio_script

    portal_features.contributions.cache_clear()
    try:
        providers = dict(portal_features.contributions('workflow.script_migration'))
        assert 'workbench' in providers
        target = '/opt/tfi/the-fourth-institute/code/scripts/research_map_digest.py'
        for _title, prompt in FLOWS.values():
            original = deliver_message_script(prompt, lend_temporary_credentials=True)
            migrated = portal_features.migrate_workflow_script(original, script_path=target)
            assert migrated == studio_script(prompt)
            assert portal_features.migrate_workflow_script(migrated, script_path=target) == migrated
            custom = original + '\noutput custom="retained"'
            assert portal_features.migrate_workflow_script(custom, script_path=target) == custom
    finally:
        portal_features.contributions.cache_clear()
