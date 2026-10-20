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
    errors, released = [], {}
    if manifest or any(p.exists() for p in scratch):
        # Each feature releases what the run made in it (WALL: projects under
        # the run, recorded cards); its owner, not the test, does the work.
        for _feature, release in portal_features.contributions('test.fixtures'):
            report, failed = release(root, manifest, TAU_ACTOR, timeout=timeout)
            released.update(report)
            errors.extend(failed)
    # New allocations and legacy manifests use the same domain deletion entry.
    automatic = lifecycle.pending(scope_id)
    # Business objects own their projection members; release those objects
    # before any directly allocated members or temporary teams.
    targets = sorted(dict.fromkeys([*automatic, *resources]),
                     key=lambda rid: {'resident': 1, 'lineage': 2}.get(rid.partition('_')[0], 0))
    released_resources = []
    for resource_id in targets:
        try:
            _release_resource(resource_id, f'tau-fixture:{root.name}:{resource_id}:{attempt_id}')
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
    lifecycle.complete(scope_id)
    removed = []
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
