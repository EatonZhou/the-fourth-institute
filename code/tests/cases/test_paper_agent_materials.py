"""Production task references retain authorization and immutable-byte checks.

:importance: P1
:aspects: functional, permissions, performance
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from management_portal import paper_agent_materials as materials, paper_projection, paper_store, resources


@pytest.fixture
def env(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(paper_store, 'DATA_ROOT', tmp_path)
    monkeypatch.setattr(resources, 'get_resource_service', lambda: SimpleNamespace(get=lambda rid, **kw: seen.append((rid, kw['actor']))))
    monkeypatch.setattr(paper_projection.native, 'lookup', lambda *a, **kw: {'resident_id': 'role-resident'})
    return tmp_path, seen


def prepare(text='Section 2 paper text'):
    return materials.prepare_task_input(actor='reader', scope='paper:deep-read', attempt='deep-read-1',
                                        subject='paper-1', full_prompt=text)


def resolve(reference):
    return materials.resolve_task_reference(actor='reader', target_resident_id='role-resident', reference=reference)


def test_snapshot_is_job_retained_and_reused(env):
    full, reference, snapshot = prepare()
    again, same_reference, same_snapshot = prepare('new text')
    assert (again, same_reference, same_snapshot) == (full, reference, snapshot)
    assert env[1] and all(item == ('paper-1', 'reader') for item in env[1])
    path = Path(snapshot['provenance']['text_path'])
    assert path.read_text() == full and path.stat().st_mode & 0o222 == 0


def test_authorization_precedes_file_writes_and_body_reads(env, monkeypatch):
    def denied(*a, **kw):
        raise PermissionError('denied')
    allowed = resources.get_resource_service
    monkeypatch.setattr(resources, 'get_resource_service', lambda: SimpleNamespace(get=denied))
    with pytest.raises(PermissionError):
        prepare()
    assert not (env[0] / 'job_materials').exists()
    monkeypatch.setattr(resources, 'get_resource_service', allowed)
    _, ref, _ = prepare()
    monkeypatch.setattr(resources, 'get_resource_service', lambda: SimpleNamespace(get=denied))
    monkeypatch.setattr(Path, 'read_bytes', lambda path: pytest.fail('denied caller must not read the body'))
    with pytest.raises(PermissionError):
        resolve(ref)


def test_identity_and_integrity_fail_closed(env):
    _, ref, snapshot = prepare()
    path = Path(snapshot['provenance']['text_path'])
    path.chmod(0o600)
    path.write_text('tamper')
    with pytest.raises(ValueError, match='integrity'):
        resolve(ref)


@pytest.mark.parametrize('target', ['manifest', 'body', 'directory', 'traversal'])
def test_symlink_and_traversal_rejected(env, target):
    _, ref, snapshot = prepare()
    if target == 'traversal':
        ref = {**ref, 'key': '../x'}
    else:
        path = Path(snapshot['provenance']['metadata_path' if target == 'manifest' else 'text_path'])
        if target == 'directory':
            path = path.parent
        saved = path.with_name(path.name + '-original')
        path.rename(saved)
        path.symlink_to(saved, target_is_directory=saved.is_dir())
    with pytest.raises((PermissionError, ValueError)):
        resolve(ref)


def test_resolver_reads_manifest_and_verified_body_once(env, monkeypatch):
    full, ref, snapshot = prepare()
    paths = {Path(snapshot['provenance'][key]) for key in ('text_path', 'metadata_path')}
    opened = []
    original = Path.open
    def observe(path, *a, **kw):
        if path in paths:
            opened.append(path)
        return original(path, *a, **kw)
    monkeypatch.setattr(Path, 'open', observe)
    assert resolve(ref)['full_prompt'] == full
    assert len(opened) == 2 and set(opened) == paths


def test_short_prompt_failure_marker_and_evidence(env):
    full, _, snapshot = prepare()
    prompt = snapshot['input_variants']['path_prompt']
    assert snapshot['provenance']['text_path'] in prompt and full not in prompt
    with pytest.raises(ValueError, match='无法读取'):
        materials.validate_path_result(materials.READ_FAILURE)
    with pytest.raises(ValueError, match='chapter/page'):
        materials.validate_path_result('## Evidence\nNo source location')
    materials.validate_path_result('Evidence from Section 2 and 第3页')
