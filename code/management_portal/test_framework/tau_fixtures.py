"""Release test samples through product owners before removing their files."""
from __future__ import annotations

import json
import hashlib
import re
import uuid
from pathlib import Path

from management_portal.hatg.contracts import InstructionError
from management_portal.shared_state import atomic_replace_json
from management_portal.test_framework.tau_residents import TAU_ACTOR
from management_common.directory_paths import remove_path
from management_common.resource_identity import get_resource_type_registry


def _resource_type(resource_id: str) -> str:
    """Project names are identity exceptions, not resource-ID prefixes."""
    identity = get_resource_type_registry()
    try:
        return identity.parse(resource_id).resource_type
    except ValueError:
        identity.validate(resource_id, 'project')
        return 'project'


def resource_release_order(resource_id: str) -> int:
    # Business owners release their own projections before direct allocations.
    return {'resident': 1, 'lineage': 2, 'workspace': 3}.get(_resource_type(resource_id), 0)


def _release_resource(resource_id: str, key: str) -> None:
    """Resume an ambiguous release; replace only a definitively failed request."""
    from management_common.instance_manifest import instance_runtime_root
    directory = instance_runtime_root() / 'test-resource-cleanup'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (hashlib.sha256(key.encode()).hexdigest() + '.json')
    if path.is_symlink():
        raise ValueError('Resource cleanup journal cannot be a symlink')
    state = json.loads(path.read_text()) if path.exists() else {}
    if state and (state.get('resource_id') != resource_id or state.get('scope_key') != key):
        raise ValueError('Resource cleanup journal identity changed')
    if state.get('completed'):
        return
    if not state or state.get('rejected'):
        state = {'resource_id': resource_id, 'scope_key': key,
                 'key': key + ':' + uuid.uuid4().hex}
        atomic_replace_json(path, state)
    try:
        _release_resource_once(resource_id, state['key'])
    except InstructionError as error:
        if error.commit_state == 'not_committed' and error.code != 'instruction.in_progress':
            state['rejected'] = error.code
            atomic_replace_json(path, state)
        raise
    state['completed'] = True
    atomic_replace_json(path, state)


def _release_resource_once(resource_id: str, key: str) -> None:
    """Use the lifecycle owner, including when the UI already removed it."""
    from management_portal import isa_client

    if resource_id.startswith('upload_'):
        from management_portal.resources.upload_files import remove_upload
        remove_upload(resource_id)
        return
    if resource_id.startswith('script_run_'):
        from .run_cleanup import delete_script_run
        delete_script_run(resource_id)
        return

    try:
        if _resource_type(resource_id) == 'project':
            # Project is a registered identity exception, including names
            # beginning with "project-". Reuse the same deletion request even
            # after the identity is gone: its effects may still be pending.
            try:
                result = isa_client.call(TAU_ACTOR, 'DeleteProject', {'project_id': resource_id}, key=key)
            except InstructionError as error:
                if error.code == 'instruction.forbidden':
                    # A completed deletion removed its permission facts. Ask
                    # the owner for this actor's receipt; never infer absence
                    # from a denied read or bypass authorization to delete.
                    try:
                        result = isa_client.call(TAU_ACTOR, 'GetProjectDeletion', {'project_id': resource_id})
                    except InstructionError:
                        raise error
                    if (result.get('project_id') != resource_id
                            or result.get('registration_status') != 'unregistered'
                            or result.get('effects', {}).get('status') != 'complete'):
                        raise RuntimeError('Project deletion has unfinished or unconfirmed owner effects')
                    return
                if error.code != 'instruction.not_found' or error.details.get('reason') == 'no_permit':
                    raise
                from management_portal import wall_backend
                if any(p['id'] == resource_id for p in wall_backend.projects(TAU_ACTOR)['projects']):
                    wall_backend.release_test_project(resource_id, TAU_ACTOR)
                return
            if (result.get('project_id') != resource_id
                    or result.get('registration_status') != 'unregistered'
                    or result.get('effects', {}).get('status') != 'complete'):
                raise RuntimeError('Project deletion has unfinished owner effects')
            return
        if resource_id.startswith('family_'):
            # Family is a domain-owned projection, not a registry mutation.
            # Unfamilize preserves members that may predate this test; the
            # allocation scope independently deletes only newly made members.
            resolved = isa_client.call(TAU_ACTOR, 'ResolveResource', {'address': resource_id})
            resource = resolved['resource']
            lineage_id = resource.get('attributes', {}).get('lineage_id', '')
            if resource.get('resource_id') != resource_id or not re.fullmatch(r'lineage_[0-9a-f]{12}', lineage_id):
                raise ValueError('Family cleanup requires its authoritative Lineage')
            isa_client.call(TAU_ACTOR, 'Unfamilize',
                            {'lineage_id': lineage_id, 'family_id': resource_id}, key=key)
            return
        isa_client.call(TAU_ACTOR, 'UnregisterResource', {'resource_id': resource_id}, key=key)
    except InstructionError as exc:
        if exc.code == 'instruction.not_found' and exc.details.get('reason') != 'no_permit':
            return
        if exc.code == 'instruction.forbidden' and _resource_type(resource_id) != 'project':
            # Unregistration removes permission facts too. A second deletion
            # can therefore fail authorization before reaching the owner.
            # Only the ordinary resolver's explicit absence confirms cleanup;
            # denial, unavailable services and existing resources stay pending.
            try:
                isa_client.call(TAU_ACTOR, 'ResolveResource', {'address': resource_id})
            except InstructionError as resolution:
                if resolution.code == 'instruction.not_found' and resolution.details.get('reason') != 'no_permit':
                    return
        raise


def _fixture_workspaces(roots: list[Path]) -> list[str]:
    """Find registrations that must not be erased with the fixture directory.

    Location detects a blocker, never proves this case allocated the object.
    """
    from management_portal.resources.workspace_store import get_workspace_store
    roots = [root.resolve() for root in roots if not root.is_symlink()
             and (not root.exists() or root.is_dir())]
    if not roots:
        return []
    # A permission-filtered discovery query cannot prove there are no hidden
    # registrations below a directory. Keep the authoritative guard, including
    # its explicit offline-migration prerequisite, until its owner provides a
    # service-side inventory with the same complete semantics.
    return [wid for wid, record in get_workspace_store().descriptors().items()
            if record['status'] == 'active' and any(
                Path(record['attributes']['path']).is_relative_to(root) for root in roots)]


def cleanup(run_dir: Path, *, timeout: float = 30) -> dict:
    from management_portal import portal_features

    root = run_dir.resolve()
    from management_common.resource_lifecycle import LifecycleStore

    lifecycle = LifecycleStore()
    scope_id = hashlib.sha256(str(root).encode()).hexdigest()
    lifecycle.request_close(scope_id)
    manifest_path = root / 'test-fixtures.json'
    if manifest_path.is_symlink():
        raise ValueError('测试清理清单不能是符号链接')
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    if not isinstance(manifest, dict):
        raise ValueError('测试清理清单必须是对象')
    from management_portal.test_framework import tau_fixture_paths
    standalone = tau_fixture_paths.inspect(root)
    content = tau_fixture_paths.inspect_content(root)
    resources = manifest.get('resources', [])
    if not isinstance(resources, list) or any(
        not isinstance(resource_id, str)
        or not resource_id.strip() or '/' in resource_id or '\x00' in resource_id
        for resource_id in resources
    ):
        raise ValueError('测试清理清单 resources 必须是资源或 Project ID 字符串数组，不接受对象或删除状态声明')
    unowned = [rid for rid in resources if not lifecycle.owns(scope_id, rid)]
    if unowned:
        raise ValueError('测试清理对象缺少本轮分配记录，保留对象和文件：' + ', '.join(unowned))
    uploads = manifest.get('uploads', [])
    if not isinstance(uploads, list) or any(
        not isinstance(upload_id, str) or not re.fullmatch(r'upload_[0-9a-f]{24}', upload_id)
        for upload_id in uploads
    ):
        raise ValueError('测试清理清单 uploads 必须是 CreateUpload 返回的上传句柄字符串数组')
    if any(not lifecycle.owns(scope_id, uid) for uid in uploads):
        raise ValueError('测试上传缺少本轮分配记录，保留文件和清单')
    # Feature owners compare their creation provenance with the actual case,
    # not a scope supplied in a handwritten manifest.
    manifest['_resource_lifecycle_id'] = scope_id
    # Source/script folders are an execution contract, independent of whether
    # the report parsed or its author cited one of the files as evidence.
    scratch = [root / name for name in ('fixtures', 'helpers')]
    owned_content = [*([content] if content else []), *([standalone[0]] if standalone else [])]
    errors, released = [], {}
    if manifest or any(p.exists() for p in [*scratch, *owned_content]):
        # Each feature releases what the run made in it (WALL: projects under
        # the run, recorded cards); its owner, not the test, does the work.
        for source in [root, *(path for path in owned_content if path.exists())]:
            for _feature, release in portal_features.contributions('test.fixtures'):
                report, failed = release(source, manifest, TAU_ACTOR, timeout=timeout)
                for name, value in report.items():
                    if name in released and isinstance(value, (list, int)):
                        released[name] += value
                    else:
                        released[name] = value
                errors.extend(failed)
    if errors:
        raise RuntimeError('；'.join(errors))
    for project_id in released.get('projects', []):
        lifecycle.released(scope_id, project_id)
    # WALL/background owners have stopped their work before waiting for the
    # last allocation lease; otherwise cleanup waits on the work it must stop.
    lifecycle.seal(scope_id, timeout=timeout)
    # A manifest is a reminder of actual allocations, never deletion authority.
    automatic = lifecycle.pending(scope_id)
    # Business objects own their projection members; release those objects
    # before any directly allocated members or temporary teams.
    targets = sorted(dict.fromkeys([*automatic, *resources, *uploads]), key=resource_release_order)
    released_resources = []
    released_uploads = []
    for resource_id in targets:
        try:
            key = f'test-release:{scope_id}:{resource_id}'
            _release_resource(resource_id, key)
            lifecycle.released(scope_id, resource_id)
            released_resources.append(resource_id)
            if resource_id in resources:
                resources.remove(resource_id)
            if resource_id.startswith('upload_'):
                released_uploads.append(resource_id)
                if resource_id in uploads:
                    uploads.remove(resource_id)
        except Exception as exc:
            errors.append(f'{resource_id}: {exc}')
    if manifest_path.exists():
        atomic_replace_json(manifest_path, manifest)
    remaining = _fixture_workspaces([root / 'fixtures', *owned_content])
    if remaining:
        errors.append('夹具目录仍有已登记 Workspace，不能凭路径删除或声称完成：' + ', '.join(remaining))
    if errors:
        # A registered/running project must keep its files for the next attempt.
        raise RuntimeError('；'.join(errors))
    from .tau_fixture_access import release_workspace_access
    from management_portal import isa_client
    permissions = release_workspace_access(root, instruction=lambda name, args, **kw:
        isa_client.call(TAU_ACTOR, name, args, **kw))
    from management_portal.resources.creation import finish_scope_creations
    creation_errors = finish_scope_creations(scope_id) if lifecycle.scope(scope_id) else []
    if creation_errors:
        raise RuntimeError('；'.join(creation_errors))
    standalone_paths = tau_fixture_paths.cleanup(root)
    if content and content.exists():
        # Recheck the receipt after the product owners have finished.
        remove_path(tau_fixture_paths.inspect_content(root))
        standalone_paths.append(str(content))
    lifecycle.complete(scope_id)
    removed = standalone_paths
    for path in scratch:
        if path.is_symlink():
            path.unlink()
        elif path.exists():
            remove_path(path)
        removed.append(path.name)
    # Remove legacy helpers at the evidence root too. Logs/screenshots survive
    # failure and cancellation; executable helper files are never evidence.
    for path in root.rglob('*'):
        if path.is_file() and path.suffix.lower() in {'.py', '.pyc', '.js', '.cjs', '.mjs', '.sh'}:
            path.unlink()
            removed.append(str(path.relative_to(root)))
    return {**released, 'paths': removed,
            'resource_lifecycle_id': scope_id, 'resources': released_resources, 'uploads': released_uploads,
            'permissions': permissions}
