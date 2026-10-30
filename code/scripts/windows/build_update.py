"""Assemble a retained-data update from the same fixed payload as the EXE.

The caller supplies the existing ReleaseStore run ID and explicit supported
installation manifests. This is an artifact builder, not a version registry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import zipfile

from scripts.tfi_release_install import digest, validate_engineering
from scripts.tfi_instance_layout import source_inventory


def build(payload, destination, *, run_id, origins):
    payload, destination = Path(payload), Path(destination)
    receipt = json.loads((payload / 'windows-package.json').read_text(encoding='utf-8'))
    if receipt.get('source_dirty') is not False or not re.fullmatch('[0-9a-f]{40}', receipt.get('source_commit', '')):
        raise ValueError('Update requires a fixed clean published source')
    if not run_id or not origins or any(not re.fullmatch('[0-9a-f]{64}', value) for value in origins):
        raise ValueError('ReleaseStore run and exact origin manifests are required')
    destination.mkdir(parents=True)
    with zipfile.ZipFile(payload / 'example-engineering.zip') as archive:
        for item in archive.infolist():
            path = PurePosixPath(item.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in item.filename:
                raise ValueError('Unsafe engineering archive member')
        archive.extractall(destination)
    def hashes(path):
        return {name: row['sha256'] for name, row in source_inventory(path).items()}
    if hashes(destination / 'code') != hashes(payload / 'code'):
        raise ValueError('EXE payload and update engineering source differ')
    wheels = destination / 'python-wheels'
    shutil.copytree(payload / 'dependencies/python-wheels', wheels)
    manifest_path = destination / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['run_id'] = run_id
    manifest.setdefault('upgrade', {})['application'] = {
        'schema': 'tfi.windows-application-upgrade.v1',
        'from_manifest_sha256': sorted(set(origins)),
        'lineage_schema': 'tfi.lineage-data.v65',
        'migration_owner': 'code/scripts/tfi_release_schema.py',
        'migration_sha256': digest(destination / 'code/scripts/tfi_release_schema.py'),
        'wheelhouse': 'python-wheels', 'package': receipt,
    }
    manifest['inventory'] += [{'path': path.relative_to(destination).as_posix(),
        'sha256': digest(path), 'size': path.stat().st_size} for path in sorted(wheels.rglob('*')) if path.is_file()]
    manifest['inventory'].sort(key=lambda row: row['path'])
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    validate_engineering(manifest_path, {'run_id': run_id,
        'supervisor_resident_id': manifest['supervisor_resident_id'],
        'install_root': str(destination.parent / 'validation-only-target')})
    return {'source_commit': receipt['source_commit'], 'run_id': run_id,
            'manifest_sha256': digest(manifest_path), 'artifact_kind': 'application-update',
            'native_verification': 'pending'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--from-manifest', action='append', required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.payload, args.output, run_id=args.run_id, origins=args.from_manifest)))
