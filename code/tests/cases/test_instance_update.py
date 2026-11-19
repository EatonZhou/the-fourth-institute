import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from management_common.instance_assets import reconcile
from management_common.instance_manifest import load, validate
from scripts import instance_update as update
from tests.cases.instance_test_support import write_instance


def test_assets_create_upgrade_conflict_and_retry(tmp_path):
    ledger = {}
    root = tmp_path / 'panel'
    assert reconcile(root, {'agent.yaml': 'first'}, ledger)['files'][0]['status'] == 'create'
    assert not root.exists()
    reconcile(root, {'agent.yaml': 'first'}, ledger, apply=True)
    reconcile(root, {'agent.yaml': 'second'}, ledger, apply=True)
    assert (root / 'agent.yaml').read_text() == 'second'
    assert reconcile(root, {'agent.yaml': 'second'}, {}, apply=True)['files'][0]['status'] == 'current'
    (root / 'agent.yaml').write_text('user edit')
    assert reconcile(root, {'agent.yaml': 'third'}, ledger, apply=True)['blockers']
    assert (root / 'agent.yaml').read_text() == 'user edit'


def test_assets_refuse_symlink_and_escape(tmp_path):
    (tmp_path / 'link').symlink_to(tmp_path / 'outside')
    with pytest.raises(ValueError):
        reconcile(tmp_path, {'link': 'secret overwrite'}, {}, apply=True)
    with pytest.raises(ValueError):
        reconcile(tmp_path, {'../outside': 'escape'}, {}, apply=True)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    instance = load(write_instance(tmp_path / 'instance'))
    states = {c: {'active': 'active', 'load': 'loaded', 'pid': 1}
              for c in instance.document['services']['components']}
    states['worker'] = {'active': 'inactive', 'load': 'loaded', 'pid': 0}
    calls = []
    def status(_):
        return {c: dict(s) for c, s in states.items()}
    def state_name(name):
        return name.removeprefix('tfi-fixture-').removesuffix('.service')
    def set_state(name, active):
        states[state_name(name)]['active'] = active
    control = SimpleNamespace(status=lambda name: dict(states[state_name(name)]),
                              start=lambda name: set_state(name, 'active'),
                              stop=lambda name: set_state(name, 'inactive'))
    def lifecycle(action, root, **kwargs):
        calls.append(action)
        for c in states:
            states[c]['active'] = 'inactive' if action == 'stop' or c in kwargs.get('excluded_components', ()) else 'active'
    monkeypatch.setattr(update.services, 'status', status)
    monkeypatch.setattr(update.services, 'controller', lambda _: control)
    monkeypatch.setattr(update.services, 'verify_definitions', lambda *a, **k: None)
    monkeypatch.setattr(update.services, 'install_definitions', lambda *a, **k: calls.append('definitions'))
    monkeypatch.setattr(update.services, 'lifecycle', lifecycle)
    monkeypatch.setattr(update.services, 'start_blocker', lambda _: '')
    monkeypatch.setattr(update.services, 'wait_database', lambda _: None)
    monkeypatch.setattr(update, 'code_identity', lambda _: {'release': {'commit': 'a'}})
    options = dict(prepare=lambda *a, **k: calls.append('prepare'),
                   child=lambda *args: json.dumps({'owners': {}, 'blockers': [], 'services': []}),
                   observe=lambda _: {'checks': [{'ok': True}]})
    return instance, states, calls, options


def test_update_restores_stopped_intent_and_receipt(rig):
    instance, states, calls, options = rig
    result = update.run(instance, **options)
    assert result['status'] == 'updated'
    assert states['worker']['active'] == 'inactive'
    assert result['before']['web']['active'] == 'active'
    assert calls == ['prepare', 'stop', 'definitions', 'start']
    assert update.current(instance)


def test_preflight_failure_never_stops_services(rig):
    instance, states, calls, options = rig
    options['child'] = lambda *a: json.dumps({'blockers': ['missing app'], 'services': []})
    with pytest.raises(ValueError, match='preflight'):
        update.run(instance, **options)
    assert calls == ['prepare']
    assert update.read_receipt(instance)['status'] == 'failed'


def test_stopped_update_receipt_reports_restored_database_state(rig):
    instance, states, calls, options = rig
    for state in states.values():
        state['active'] = 'inactive'
    result = update.run(instance, **options)
    assert result['status'] == 'updated'
    assert all(state['active'] == 'inactive' for state in result['services'].values())


def test_published_workbench_missing_or_invalid_entry_blocks_update(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from management_portal.workbench_projections import instance_update as owner
    rows = [{'id': 'workbench_0123456789ab', 'build': json.dumps({'entries': {}})}]
    db = SimpleNamespace(execute=lambda *a: SimpleNamespace(fetchall=lambda: rows))
    monkeypatch.setattr(owner, 'Store', lambda: SimpleNamespace(database=SimpleNamespace(read=lambda: nullcontext(db))))
    assert owner.update(None, 'verify', {})['blockers']
    rows[0]['build'] = json.dumps({'entries': {'main': 'main.js'}, 'files': {'main.js': {'size': 1}}, 'artifact_hash': 'a'})
    def unavailable(*a):
        raise OSError('missing entry')
    monkeypatch.setattr(owner, 'read_artifact', unavailable)
    assert owner.update(None, 'verify', {})['blockers']


def test_interruption_reuses_original_running_intent(rig):
    instance, states, calls, options = rig
    good = options['child']
    def fail(instance, script, request):
        phase = json.loads(Path(request).read_text())['phase']
        if phase == 'apply':
            raise RuntimeError('interrupted')
        return good()
    options['child'] = fail
    with pytest.raises(RuntimeError):
        update.run(instance, **options)
    assert states['web']['active'] == 'inactive'
    options['child'] = good
    assert update.run(instance, **options)['status'] == 'updated'
    assert states['web']['active'] == 'active'
    assert states['worker']['active'] == 'inactive'


def test_plan_does_not_prepare_stop_or_replace_receipt(rig):
    instance, states, calls, options = rig
    assert update.run(instance, plan_only=True, **options)['status'] == 'planned'
    assert not calls
    assert update.read_receipt(instance) is None


def test_failed_dependency_retry_retains_interrupted_service_intent(rig):
    instance, states, calls, options = rig
    original = dict(options)
    def fail_apply(*args):
        if json.loads(Path(args[-1]).read_text())['phase'] == 'apply':
            raise RuntimeError('interrupted')
        return original['child']()
    options['child'] = fail_apply
    with pytest.raises(RuntimeError):
        update.run(instance, **options)
    options = dict(original)
    def fail_prepare(*a, **k):
        raise RuntimeError('dependency unavailable')
    options['prepare'] = fail_prepare
    with pytest.raises(RuntimeError):
        update.run(instance, **options)
    assert update.read_receipt(instance)['mutation_started']
    update.run(instance, **original)
    assert states['web']['active'] == 'active'
    assert states['worker']['active'] == 'inactive'


def test_bound_weixin_declares_missing_services_and_respects_stop(rig, monkeypatch):
    from contextlib import nullcontext
    from management_portal.telepus import instance_update as owner, contacts_store
    instance, states, calls, options = rig
    monkeypatch.setattr(owner, 'RouterStore', lambda: SimpleNamespace(init_schema=lambda: None))
    monkeypatch.setattr(owner, 'WeixinStore', lambda _: SimpleNamespace(active_actors=lambda: ['local-user']))
    monkeypatch.setattr(contacts_store, 'database', lambda: SimpleNamespace(read=lambda: nullcontext(None)))
    monkeypatch.setattr(contacts_store, 'contacts', lambda _: [])
    result = owner.update(instance, 'plan', {})
    assert set(result['services']) == set(owner.WEIXIN_COMPONENTS)
    assert not result['blockers']
    assert result['start_services'] == result['services']
    for state in states.values():
        state['active'] = 'inactive'
    offline = owner.update(instance, 'plan', {})
    assert not offline['blockers'] and not offline['start_services']
    assert offline['services'] == result['services']
    for state in states.values():
        state['active'] = 'active'
    component = owner.WEIXIN_COMPONENTS[0]
    instance.document['services']['components'].append(component)
    states[component] = {'active': 'inactive', 'load': 'loaded', 'pid': 0}
    result = owner.update(instance, 'plan', {})
    assert any(row['error'] == 'required_bridge_explicitly_stopped' for row in result['blockers'])
    for component in owner.WEIXIN_COMPONENTS:
        if component not in instance.document['services']['components']:
            instance.document['services']['components'].append(component)
        states[component] = {'active': 'inactive', 'load': 'loaded', 'pid': 0}
    result = owner.update(instance, 'plan', {})
    assert not result['blockers'] and not result['services']


def test_local_service_settings_are_validated_and_rendered(tmp_path, monkeypatch):
    from scripts.tfi_release_platform_macos import manifest_unit_text
    import plistlib
    instance = load(write_instance(tmp_path / 'instance'))
    settings = instance.document['services']
    settings['launch_agents_directory'] = str(tmp_path / 'launch-agents')
    settings['path_prefixes'] = {'web': ['/Library/TeX/texbin']}
    instance = validate(instance.root, instance.document)
    unit = plistlib.loads(manifest_unit_text(instance, 'web').encode())
    assert unit['EnvironmentVariables']['PATH'].startswith('/Library/TeX/texbin:')
    monkeypatch.setattr(update.services, 'platform_name', lambda: 'macos')
    assert update.services.definition_directory(instance) == tmp_path / 'launch-agents'
    settings['path_prefixes']['web'] = ['relative']
    with pytest.raises(ValueError):
        validate(instance.root, instance.document)


def test_installed_legacy_panel_repaired_without_contacts(tmp_path, monkeypatch):
    from management_portal.society_service import instance_update as owner
    instance = load(write_instance(tmp_path / 'instance'))
    panel = tmp_path / 'workspace' / 'watchboard'
    resident = {'resident_id': 'resident_7fdebb465cdf'}
    monkeypatch.setattr(owner.resident_catalog, 'get_catalog', lambda: SimpleNamespace(all=lambda: [resident]))
    monkeypatch.setattr(owner.watchboard_lineage, 'installed', lambda r: True)
    monkeypatch.setattr(owner.watchboard_lineage, 'root', lambda r: panel)
    monkeypatch.setattr(owner.panels, '_watchboard_root', lambda r: panel)
    assert not owner.update(instance, 'plan', {})['blockers']
    assert not owner.update(instance, 'apply', {})['blockers']
    assert not owner.update(instance, 'verify', {})['blockers']
    assert sorted(p.name for p in panel.iterdir()) == ['agent.yaml', 'watchboard.yaml']
    assert json.loads((panel / 'watchboard.yaml').read_text())['layout'] == 'telepus-channels'


def test_target_owner_protocol_with_real_isolated_database(tmp_path, monkeypatch):
    from scripts.instance_update_owners import execute
    instance = load(write_instance(tmp_path / 'instance'))
    monkeypatch.setenv('PORTAL_INSTANCE_ROOT', str(instance.root))
    result = execute('plan', {})
    assert not result['blockers'], result['blockers']
    assert set(result['owners']) == {'society', 'telepus', 'workbench'}
    assert {row['component'] for row in result['service_inventory']} == set(instance.document['services']['components'])
    assert not execute('apply', result)['blockers']
    assert not execute('verify', result)['blockers']


def test_unknown_panel_missing_files_blocks_instead_of_inventing_defaults(tmp_path, monkeypatch):
    from management_portal.society_service import instance_update as owner
    instance = load(write_instance(tmp_path / 'instance'))
    panel = tmp_path / 'user-panel'
    monkeypatch.setattr(owner.resident_catalog, 'get_catalog', lambda: SimpleNamespace(all=lambda: [{'resident_id': 'resident_0123456789ab'}]))
    monkeypatch.setattr(owner.watchboard_lineage, 'installed', lambda r: True)
    monkeypatch.setattr(owner.watchboard_lineage, 'root', lambda r: panel)
    assert owner.update(instance, 'apply', {})['blockers'][0]['error'] == 'missing_panel_assets'
    assert not panel.exists()


def test_resume_keeps_new_service_intent(rig, monkeypatch):
    instance, states, calls, options = rig
    initial = {'owners': {}, 'blockers': [], 'services': ['telepus-weixin-router'],
               'start_services': ['telepus-weixin-router']}
    registered = []
    def register(root, name):
        if name not in instance.document['services']['components']:
            instance.document['services']['components'].append(name)
        states[name] = {'active': 'inactive', 'load': 'loaded', 'pid': 0}
        registered.append(name)
    monkeypatch.setattr(update.services, 'register_component', register)
    monkeypatch.setattr(update, 'load', lambda _: instance)
    monkeypatch.setattr(update.services, 'platform_name', lambda: 'windows')
    def child(*args):
        phase = json.loads(Path(args[-1]).read_text())['phase']
        if phase == 'apply':
            raise RuntimeError('interrupted')
        return json.dumps(initial)
    options['child'] = child
    with pytest.raises(RuntimeError):
        update.run(instance, **options)
    def resumed(*args):
        assert json.loads(Path(args[-1]).read_text())['phase'] != 'plan'
        return json.dumps({'owners': {}, 'blockers': []})
    options['child'] = resumed
    result = update.run(instance, **options)
    assert result['status'] == 'updated'
    assert states['telepus-weixin-router']['active'] == 'active'


def test_code_drift_prevents_stop(rig, monkeypatch):
    instance, states, calls, options = rig
    identities = iter([{'release': {'commit': 'a'}}, {'release': {'commit': 'b'}}])
    monkeypatch.setattr(update, 'code_identity', lambda _: next(identities))
    with pytest.raises(ValueError, match='changed'):
        update.run(instance, **options)
    assert calls == ['prepare']


@pytest.mark.parametrize('fails', [False, True])
def test_independent_update_job_uses_updater_and_records_terminal_state(tmp_path, monkeypatch, fails):
    from scripts import tfi_instance_manager as manager, tfi_release_install as installer
    instance = load(write_instance(tmp_path / 'instance'))
    monkeypatch.setattr(manager, 'state_for', lambda _: (instance.root, {}))
    launched = []
    monkeypatch.setattr(manager, 'launch', lambda root, record: launched.append(dict(record)))
    monkeypatch.setattr(manager, 'job_alive', lambda _: True)
    monkeypatch.setattr(manager.time, 'sleep', lambda _: None)
    def run(selected, **options):
        assert selected.instance_id == instance.instance_id
        assert options == {'offline': True}
        assert manager.operation(instance.root)['status'] == 'running'
        if fails:
            raise ValueError('feature conflict')
        return {'id': 'verified-update', 'status': 'updated'}
    monkeypatch.setattr(installer, 'update_code', run)
    accepted = manager.enqueue('update-code', instance.name, offline=True)
    assert accepted['operation']['status'] == 'queued'
    assert len(launched) == 1
    with pytest.raises(ValueError, match='already running'):
        manager.enqueue('update-code', instance.name)
    record = manager.execute(instance.name, accepted['operation']['id'])
    assert record['status'] == ('failed' if fails else 'completed')
    if not fails:
        assert record['update_id'] == 'verified-update'
    assert manager.operation(instance.root)['status'] == record['status']
