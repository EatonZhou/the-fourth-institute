"""The operations script preserves actual caller and recovery boundaries.

:module: workflow.article
:importance: P1
"""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace

import pytest

from scripts import article_restore as script
from management_portal.resources import article_restore_commit as restore
from tests.cases.permission_test_support import permissions  # noqa: F401
from tests.cases.test_article_restore_commit import world, USER  # noqa: F401
from tests.cases.test_article_restore_preview import world as preview_world  # noqa: F401
from tests.cases.test_article_restore_evidence import ARTICLE, RESIDENT, ACTOR

RUN = 'script_run_111111111111111111111111'


def entry(monkeypatch, world, step, **context):
    from management_portal import resources
    from management_portal.hatg import store

    outputs = []
    run = {'caller_id': ACTOR, 'script_run_id': RUN,
           'arguments': {'step': step, **world.args}, **context}
    calls = []
    def trusted(operation):
        calls.append(operation)
        return run
    monkeypatch.setattr(script, 'script_context', trusted)
    monkeypatch.setitem(sys.modules, 'portal_script', SimpleNamespace(set_output=outputs.append))
    monkeypatch.setattr(resources, 'get_resource_service', lambda: world.service)
    def receipts(database, *, create):
        assert database.schema.endswith('resource_instructions')
        assert create is False
        return world.receipt
    monkeypatch.setattr(store, 'ExecutionStore', receipts)
    return run, calls, outputs


def test_script_check_is_read_only_and_uses_original_caller(preview_world, monkeypatch):
    run, calls, outputs = entry(monkeypatch, preview_world, 'check')
    assert script.main() == 0
    assert calls == ['article_restore']
    assert outputs[0]['mode'] == 'dry_run' and outputs[0]['can_apply'] is False
    assert preview_world.registry.get('article', ARTICLE) is None
    assert not (preview_world.project / 'articles').exists()


def test_script_apply_preserves_request_identity_and_original_author(world, monkeypatch):
    run, calls, outputs = entry(monkeypatch, world, 'apply')
    assert script.main() == 0
    assert script.main() == 0
    assert outputs[0] == outputs[1]
    assert outputs[0]['mode'] == 'partial_restore'
    assert outputs[0]['final_version_recovered'] is False
    doc = world.registry.get('article', ARTICLE)
    assert doc['recovery']['restored_by'] == ACTOR
    assert 'created_by' not in doc
    audit = world.registry.get('article_restorations', ARTICLE)
    assert audit['approval']['user'] == USER
    assert world.destination.joinpath('main.md').is_file()
    run['script_run_id'] = 'script_run_222222222222222222222222'
    assert script.main() == 0
    assert outputs[2] == outputs[0]
    run['arguments']['primary_version'] = 'f' * 64
    with pytest.raises(ValueError, match='Another restoration operation'):
        script.main()


@pytest.mark.parametrize('key', ['actor', 'caller_id', 'operation_id', 'lineage_id'])
def test_script_arguments_cannot_override_context(preview_world, monkeypatch, key):
    run, _, outputs = entry(monkeypatch, preview_world, 'check')
    run['arguments'][key] = 'forged'
    with pytest.raises(ValueError, match='exact evidence'):
        script.main()
    assert not outputs and not preview_world.calls


@pytest.mark.parametrize('actor', ['portal', 'ai-socket-relay'])
def test_host_identity_cannot_be_restore_caller(preview_world, monkeypatch, actor):
    _, _, outputs = entry(monkeypatch, preview_world, 'check', caller_id=actor)
    with pytest.raises(PermissionError, match='authenticated User or Resident'):
        script.main()
    assert not outputs and not preview_world.calls


@pytest.mark.parametrize('step', ['check', 'apply'])
def test_script_still_requires_article_role(world, monkeypatch, step):
    entry(monkeypatch, world, step)
    if step == 'check':
        world.args = {k: v for k, v in world.args.items() if k not in restore.EXTRA}
        entry(monkeypatch, world, step)
    world.permissions.deny(ACTOR, ARTICLE, '@admin')
    with pytest.raises(PermissionError):
        script.main()
    assert world.registry.get('article', ARTICLE) is None


def test_script_requires_verified_run_context_before_any_owner_read(preview_world, monkeypatch):
    _, _, outputs = entry(monkeypatch, preview_world, 'check')
    def reject(operation):
        raise PermissionError('unverified script lease')
    monkeypatch.setattr(script, 'script_context', reject)
    with pytest.raises(PermissionError, match='unverified script lease'):
        script.main()
    assert not outputs and not preview_world.calls


def test_retry_in_a_new_script_run_resumes_same_owned_files(world, monkeypatch):
    run, _, outputs = entry(monkeypatch, world, 'apply')
    original = restore._ArticleRecoveryCreation.publish
    class Crash(BaseException):
        pass
    def crash(creation, initialize):
        original(creation, initialize)
        raise Crash()
    monkeypatch.setattr(restore._ArticleRecoveryCreation, 'publish', crash)
    with pytest.raises(Crash):
        script.main()
    inode = world.destination.stat().st_ino
    assert world.registry.get('article', ARTICLE) is None
    monkeypatch.setattr(restore._ArticleRecoveryCreation, 'publish', original)
    run['script_run_id'] = 'script_run_222222222222222222222222'
    assert script.main() == 0
    assert outputs[0]['resource_id'] == ARTICLE
    assert world.destination.stat().st_ino == inode


@pytest.mark.parametrize('size', [65536, 65537, 131072, 131073])
def test_complete_report_uses_sdk_byte_limits(tmp_path, monkeypatch, size):
    from management_portal.society_service.script_runs.clients import portal_script
    from management_portal.society_service.script_runs.runner import result_fields

    artifacts = tmp_path / 'artifacts'
    artifacts.mkdir()
    output = tmp_path / 'output.json'
    monkeypatch.setitem(sys.modules, 'portal_script', portal_script)
    monkeypatch.setenv('PORTAL_SCRIPT_OUTPUT', str(output))
    monkeypatch.setenv('PORTAL_SCRIPT_ARTIFACTS', str(artifacts))
    report = {'mode': 'dry_run', '完整证据': ''}
    remaining = size - len(json.dumps(report, ensure_ascii=False).encode('utf-8'))
    report['完整证据'] = '证' * (remaining // 3) + 'x' * (remaining % 3)
    encoded = json.dumps(report, ensure_ascii=False, allow_nan=False).encode('utf-8')
    assert len(encoded) == size and len(encoded.decode('utf-8')) < 65536
    if size > 131072:
        with pytest.raises(ValueError, match='bounded response budget'):
            script._output(report)
        assert not output.exists() and not list(artifacts.iterdir())
        return
    script._output(report)
    receipt, files = result_fields(tmp_path)
    if size <= 65536:
        assert output.read_bytes() == encoded
        assert receipt == report and files == []
    else:
        assert receipt == {'mode': 'dry_run', 'artifact': 'article-restore-check.json',
                           'bytes': size, 'sha256': hashlib.sha256(encoded).hexdigest()}
        assert files[0]['path'] == receipt['artifact'] and files[0]['size'] == size
        assert len(files) == 1
        preserved = artifacts.joinpath(receipt['artifact']).read_bytes()
        assert preserved == encoded and json.loads(preserved) == report


@pytest.fixture
def running_check(preview_world, monkeypatch, tmp_path):
    """Real SDK/IPC and ScriptRun owner read; transport caller is a fixture."""
    from management_portal import permission_system
    from management_portal.society_service.script_runs import runner, runtime, service, store
    from management_portal.society_service.script_runs.clients import portal_script
    from scripts import portal_operations
    from tests.cases.permission_test_support import Fact

    world = preview_world
    entry(monkeypatch, world, 'check')
    owner = 'lineage_555555555555'
    root = Path(script.__file__).resolve().parents[1]
    monkeypatch.setattr(portal_operations, 'load', lambda: SimpleNamespace(
        code_root=lambda: root, directory=lambda _name: tmp_path / 'runtime'))
    monkeypatch.setattr(portal_operations, 'read_site_config', lambda: {
        'deployment': {'portal_operations': {'lineage_id': owner}}})
    monkeypatch.setattr(runtime, 'RUN_ROOT', tmp_path / 'runtime/script_runs')
    payload = portal_operations.run_arguments('article_restore', {'step': 'check', **world.args})
    world.permissions.place(Fact('lineage', owner))
    assert not permission_system.allows(ACTOR, 'RunScript', payload)
    world.permissions.grant(ACTOR, owner, '@admin')
    assert permission_system.allows(ACTOR, 'RunScript', payload)
    store.admit({**payload, 'script_run_id': RUN, 'caller_id': ACTOR, 'status': 'running'})
    io = runtime.RUN_ROOT / RUN / 'io'
    (io / 'artifacts').mkdir(parents=True)
    # Mutable input hints must not select the caller or evidence arguments.
    (io / 'input.json').write_text(json.dumps({'script_run_id': RUN,
        'caller_id': 'portal', 'lineage_id': 'forged', 'arguments': {'actor': 'forged'}}))
    monkeypatch.setenv('PORTAL_SCRIPT_INPUT', str(io / 'input.json'))
    monkeypatch.setenv('PORTAL_SCRIPT_OUTPUT', str(io / 'output.json'))
    monkeypatch.setenv('PORTAL_SCRIPT_ARTIFACTS', str(io / 'artifacts'))
    monkeypatch.setitem(sys.modules, 'portal_script', portal_script)
    monkeypatch.setattr(script, 'script_context', portal_operations.script_context)
    calls = []
    def forward(request):
        calls.append(request)
        assert request['instruction'] == 'GetScriptRun'
        arguments = request['arguments']
        if not permission_system.allows(ACTOR, 'GetScriptRun', arguments):
            raise PermissionError('run viewer revoked')
        return {'status': 'committed', 'result': service.get(
            arguments['script_run_id'], ACTOR, lineage_id=arguments['lineage_id'])}
    with tempfile.TemporaryDirectory(prefix='article-script-ipc-') as socket_dir:
        address = Path(socket_dir) / 'isa.sock'
        relay = runner.Relay(address, forward, script_run_id=RUN)
        thread = threading.Thread(target=relay.serve_forever, daemon=True)
        thread.start()
        monkeypatch.setenv('PORTAL_ISA_SOCKET', str(address))
        try:
            yield SimpleNamespace(world=world, io=io, owner=owner, calls=calls)
        finally:
            relay.shutdown()
            relay.server_close()
            thread.join(timeout=2)


def test_check_composes_with_current_script_owner_and_sdk(running_check):
    state = running_check
    assert script.main() == 0
    report = json.loads((state.io / 'output.json').read_bytes())
    assert report['mode'] == 'dry_run' and report['article_id'] == ARTICLE
    assert state.world.calls
    assert state.calls == [{'instruction': 'GetScriptRun', 'arguments': {
        'lineage_id': state.owner, 'script_run_id': RUN}}]
    assert not (state.world.project / 'articles').exists()


@pytest.mark.parametrize('revoked', ['run', 'article'])
def test_script_owner_composition_keeps_current_permissions(running_check, revoked):
    state = running_check
    state.world.permissions.deny(ACTOR, state.owner if revoked == 'run' else ARTICLE,
                                 '@viewer' if revoked == 'run' else '@admin')
    with pytest.raises(RuntimeError if revoked == 'run' else PermissionError):
        script.main()
    assert not state.world.calls and not (state.io / 'output.json').exists()
