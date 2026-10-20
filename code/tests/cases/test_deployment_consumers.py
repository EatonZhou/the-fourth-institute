"""部署配置改变实际功能目标，空配置不会访问实验室的固定对象。

:module: operations.deployment
:design: decision_15aab18c0ab3
"""
import json
from types import SimpleNamespace

import pytest

from management_portal import backup_lineage, portal_pulse_backend as pulse
from management_portal.society_service import system_workflow_switches as switches
from management_portal.watchboards import telepus
from management_portal.workbench_projections import development_projection as development

LID = 'lineage_111111111111'
RID = 'resident_222222222222'
REPO = 'repo_333333333333'


@pytest.fixture
def configure(tmp_path, monkeypatch):
    path = tmp_path / 'site.json'
    monkeypatch.setenv('PORTAL_SITE_CONFIG', str(path))
    monkeypatch.delenv('PORTAL_PULSE_ROOT', raising=False)
    def write(**deployment):
        path.write_text(json.dumps({'deployment': deployment}))
    write()
    return write


def test_backup_uses_configured_lineage_and_keeps_actor(configure):
    configure(backup={'lineage_id': LID})
    calls = []
    def invoke(name, args, actor):
        calls.append((name, args, actor))
        return {'lineage': {'program': {}}}
    workflow = backup_lineage.BackupLineage(invoke)
    result = workflow.schedule('viewer')
    assert result['lineage_id'] == LID
    workflow.trigger('event_source_444444444444', 'operator')
    assert calls == [('GetLineage', {'lineage_id': LID}, 'viewer'),
                     ('TriggerEvent', {'lineage_id': LID, 'event_source_id': 'event_source_444444444444'}, 'operator')]


def test_telepus_configuration_is_read_for_each_match(configure):
    configure(telepus={'resident_id': RID})
    assert telepus.matches(SimpleNamespace(resident_id=RID))
    configure(telepus={'resident_id': 'resident_555555555555'})
    assert not telepus.matches(SimpleNamespace(resident_id=RID))


def test_switch_keys_use_configured_owners(configure):
    configure(system_workflow={'request_timing_lineage_id': LID,
                               'harness_recording_lineage_id': 'lineage_666666666666'})
    assert switches._keys(LID) == {'system:request-timing:enabled'}
    assert switches._keys('lineage_666666666666') == {'system:harness-recording:enabled'}


def test_development_definition_uses_configured_bindings(configure):
    configure(development_projection={'repo_id': REPO, 'lineage_id': LID,
                                      'facade_resident_id': RID, 'project_ids': ['demo'],
                                      'packaging_lineage_id': LID, 'packaging_facade_resident_id': RID})
    definition = development.application_definition('1' * 40)
    assert definition['application']['repo_id'] == REPO
    assert definition['bindings']['main']['lineage_id'] == LID
    assert definition['bindings']['main']['residents']['facade'] == RID
    assert definition['project_ids'] == ['demo']


def test_pulse_configuration_resolves_current_lineage_folder(configure, monkeypatch, tmp_path):
    configure(pulse={'lineage_id': LID})
    roots = []
    def folder(lid):
        roots.append(lid)
        return tmp_path / lid
    monkeypatch.setattr(pulse, 'canonical_lineage_folder', folder)
    assert pulse._pulse_root() == tmp_path / LID / 'memory' / 'pulse'
    assert roots == [LID]


def test_empty_configuration_does_not_contact_a_default_object(configure, tmp_path):
    from management_portal.resource_backup_backend import ResourceBackupBackend
    configure()
    workflow = backup_lineage.BackupLineage(lambda *args: pytest.fail('unexpected ISA'))
    with pytest.raises(ValueError, match='not configured'):
        workflow.schedule('viewer')
    with pytest.raises(ValueError, match='not configured'):
        workflow.trigger('event_source_444444444444', 'viewer')
    overview = ResourceBackupBackend(tmp_path, workflow=workflow).get_overview('viewer')
    assert overview['permissions'] == {'can_view': True, 'can_run': False, 'can_verify': False, 'can_cancel': False}
    assert overview['restore_guide']['resource_id'] == overview['restore_guide']['url'] == ''
    assert not telepus.matches(SimpleNamespace(resident_id=RID))
    assert switches.workflow_switches() == {}
    with pytest.raises(ValueError, match='must be configured'):
        development.application_definition('1' * 40)
    with pytest.raises(ValueError, match='not configured'):
        pulse._pulse_root()


@pytest.mark.parametrize('value', [None, False, [], 'wrong_id'])
def test_malformed_targets_are_not_silently_disabled(configure, value):
    configure(backup={'lineage_id': value}, telepus={'resident_id': value},
              pulse={'lineage_id': value}, system_workflow={'request_timing_lineage_id': value})
    for operation in (backup_lineage.lineage_id, pulse._pulse_root, switches.workflow_switches,
                      lambda: telepus.matches(SimpleNamespace(resident_id=RID))):
        with pytest.raises(ValueError):
            operation()


def test_backup_script_path_round_trips_through_real_effect_parser(configure, tmp_path):
    from management_portal.society_service import lineage_effect_script as script
    path = tmp_path / 'space "quote" ${literal} and \\backslash.py'
    configure(backup={'script_path': str(path)})
    operations = backup_lineage.migration_operations()
    effect = next(op['value'] for op in operations if op['kind'] == 'component' and op['value']['kind'] == 'effect')
    calls = []
    def execute(request):
        calls.append(request)
        return script.CallResult(True, {'script_run_id': 'script_run_' + 'a' * 24, 'status': 'running'}, {})
    result = script.Interpreter(script.parse(effect['config']['script']), {}, execute).run()
    assert result.status == 'succeeded'
    assert calls[0].arguments['path'] == str(path)
    assert calls[0].arguments['argv'] == ['critical']


def test_example_needs_no_live_binding_and_pulse_override_remains_available(configure, tmp_path, monkeypatch):
    configure(development_projection={'repo_id': REPO})
    value = development.application_definition('1' * 40, example=True)
    assert value['bindings'] == {} and 'chat' not in value['manifest']
    assert value['project_ids'] == [] and value['navigation'] == 'hidden'
    monkeypatch.setenv('PORTAL_PULSE_ROOT', str(tmp_path))
    assert pulse._pulse_root() == tmp_path


def test_removed_managed_switch_binding_cannot_bypass_event_authority(configure, monkeypatch):
    from management_portal.execution.service_settings import ExecutionSettings
    from management_portal.hatg.contracts import InstructionError
    from contextlib import nullcontext
    configure()
    monkeypatch.setattr('management_portal.society_service.lineage_domain._lineage_execution_authorities', lambda _: nullcontext())
    settings = ExecutionSettings()
    key = 'system:request-timing:enabled'
    settings.project_workflow_switch(key, False)
    with pytest.raises(InstructionError, match='部署绑定缺失'):
        switches.update_settings({'model_request_timing_enabled': True}, 'actor')
    assert settings.get()['model_request_timing_enabled'] is False


def test_uninstalled_switch_configuration_preserves_local_settings(configure, monkeypatch):
    from contextlib import nullcontext
    configure()
    monkeypatch.setattr('management_portal.society_service.lineage_domain._lineage_execution_authorities', lambda _: nullcontext())
    assert switches.update_settings({'model_request_timing_enabled': False}, 'actor')['model_request_timing_enabled'] is False


def test_backup_guide_follows_current_site_configuration(configure):
    configure(backup={'restore_guide_resource_id': 'information_777777777777'})
    assert backup_lineage.restore_guide()['url'] == '/resource/knowledge?item=information_777777777777'
    configure()
    assert backup_lineage.restore_guide()['url'] == ''
