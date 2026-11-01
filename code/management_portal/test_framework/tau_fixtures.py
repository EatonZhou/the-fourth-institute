"""Release test samples through product owners before removing their files."""
from __future__ import annotations

import json
import hashlib
import re
import shutil
import uuid
from pathlib import Path

from management_portal.hatg.contracts import InstructionError
from management_portal.shared_state import atomic_replace_json
from management_portal.test_framework.tau_residents import TAU_ACTOR


def _release_resource(resource_id: str, key: str) -> None:
    """Use the lifecycle owner, including when the UI already removed it."""
    from management_portal import isa_client

    try:
        if resource_id.startswith('family_'):
            # Family is a domain-owned projection, not a registry mutation.
            # Unfamilize preserves members that may predate this test; the
            # allocation scope independently retires only newly made members.
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
        if exc.code != 'instruction.not_found':
            raise


def _delete_upload(upload_id: str, key: str) -> None:
    from management_portal import isa_client

    try:
        isa_client.call(TAU_ACTOR, 'DeleteUpload', {'upload_id': upload_id}, key=key)
    except InstructionError as exc:
        if exc.code != 'instruction.not_found':
            raise


def _release_workspace(resource_id: str, key: str, actors: set[str]) -> None:
    """Only detach this operation's users of a Workspace it allocated."""
    from management_portal import isa_client

    try:
        _release_resource(resource_id, key)
        return
    except InstructionError as exc:
        users = exc.details.get('users') or []
        if (exc.code != 'instruction.conflict' or exc.details.get('reason') != 'workspace_in_use'
                or not users or any(user.get('usage') != 'binding' or user.get('resident_id') not in actors
                                    for user in users)):
            raise
    for user in users:
        resident_id = user['resident_id']
        isa_client.call(TAU_ACTOR, 'UnbindWorkspace',
                        {'resident_id': resident_id, 'workspace_id': resource_id},
                        key=f'{key}:unbind:{resident_id}')
    # A new key is needed after the not_committed refusal has been repaired.
    _release_resource(resource_id, key + ':unbound')


def _fixture_workspaces(roots: list[Path]) -> list[str]:
    """Find asynchronous product allocations in the case's owned directories.

    WALL jobs can create a Workspace after the HTTP request's allocation scope
    has ended. Read the authoritative store, not an eventually updated index.
    Discovery grants no rights: retirement still runs the ordinary ISA checks.
    """
    from management_portal.resources.workspace_store import get_workspace_store
    roots = [root.resolve() for root in roots if not root.is_symlink()
             and (not root.exists() or root.is_dir())]
    if not roots:
        return []
    return [wid for wid, record in get_workspace_store().descriptors().items()
            if record['status'] == 'active' and any(
                Path(record['attributes']['path']).is_relative_to(root) for root in roots)]


def cleanup(run_dir: Path, *, timeout: float = 30) -> dict:
    from management_portal import portal_features

    root = run_dir.resolve()
    # A later cleanup is a new attempt after an owner/permission failure.
    # Transport retries within each ISA call still reuse this attempt's key.
    attempt_id = uuid.uuid4().hex
    from management_common.resource_lifecycle import LifecycleStore

    lifecycle = LifecycleStore()
    scope_id = hashlib.sha256(str(root).encode()).hexdigest()
    lifecycle.seal(scope_id, timeout=timeout)
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
        or not re.fullmatch(r'[a-z][a-z0-9_]*_[0-9a-f]{12}', resource_id)
        for resource_id in resources
    ):
        raise ValueError('测试清理清单 resources 必须是正式资源 ID 字符串数组，不接受对象或删除状态声明')
    uploads = manifest.get('uploads', [])
    if not isinstance(uploads, list) or any(
        not isinstance(upload_id, str) or not re.fullmatch(r'upload_[0-9a-f]{24}', upload_id)
        for upload_id in uploads
    ):
        raise ValueError('测试清理清单 uploads 必须是 CreateUpload 返回的上传句柄字符串数组')
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
    # New allocations and legacy manifests use the same domain deletion entry.
    automatic = lifecycle.pending(scope_id)
    fixture_workspaces = _fixture_workspaces([root / 'fixtures', *owned_content])
    # Business objects own their projection members; release those objects
    # before any directly allocated members or temporary teams.
    targets = sorted(dict.fromkeys([*automatic, *resources, *fixture_workspaces]),
                     key=lambda rid: {'resident': 1, 'lineage': 2, 'workspace': 3}.get(rid.partition('_')[0], 0))
    actors = lifecycle.actors(scope_id)
    released_resources = []
    for resource_id in targets:
        try:
            key = f'tau-fixture:{root.name}:{resource_id}:{attempt_id}'
            if resource_id.startswith('workspace_') and resource_id in automatic:
                _release_workspace(resource_id, key, actors)
            else:
                _release_resource(resource_id, key)
            lifecycle.released(scope_id, resource_id)
            released_resources.append(resource_id)
            if resource_id in resources:
                resources.remove(resource_id)
        except Exception as exc:
            errors.append(f'{resource_id}: {exc}')
    released_uploads = []
    for upload_id in list(uploads):
        try:
            _delete_upload(upload_id, f'tau-upload:{root.name}:{upload_id}:{attempt_id}')
            released_uploads.append(upload_id)
            uploads.remove(upload_id)
        except Exception as exc:
            errors.append(f'{upload_id}: {exc}')
    if manifest_path.exists():
        atomic_replace_json(manifest_path, manifest)
    if errors:
        # A registered/running project must keep its files for the next attempt.
        raise RuntimeError('；'.join(errors))
    standalone_paths = tau_fixture_paths.cleanup(root)
    if content and content.exists():
        # Recheck the receipt after the product owners have finished.
        shutil.rmtree(tau_fixture_paths.inspect_content(root))
        standalone_paths.append(str(content))
    lifecycle.complete(scope_id)
    removed = standalone_paths
    for path in scratch:
        if path.is_symlink():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)
        removed.append(path.name)
    # Remove legacy helpers at the evidence root too. Logs/screenshots survive
    # failure and cancellation; executable helper files are never evidence.
    for path in root.rglob('*'):
        if path.is_file() and path.suffix.lower() in {'.py', '.pyc', '.js', '.cjs', '.mjs', '.sh'}:
            path.unlink()
            removed.append(str(path.relative_to(root)))
    return {**released, 'paths': removed,
            'resource_lifecycle_id': scope_id, 'resources': released_resources, 'uploads': released_uploads}
