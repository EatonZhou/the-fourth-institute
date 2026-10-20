#!/usr/bin/env python3
"""Verify the example seed and assemble an installation bundle from shared code."""
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / 'code'))
from scripts.tfi_instance_layout import GENERATED_CODE_DIRECTORIES


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inventory(root):
    rows = []
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or path.name == '.git':
            raise ValueError(f'Symlink or Git metadata in release: {path}')
        if path.is_file():
            rows.append({'path': path.relative_to(root).as_posix(), 'sha256': digest(path), 'size': path.stat().st_size})
    return sorted(rows, key=lambda row: row["path"])


def prepare(repo, *, target_platforms=None):
    seed = repo / 'instance/example_instance/seed'
    specification = json.loads((seed / 'manifest.json').read_text(encoding='utf-8'))
    if specification['schema'] != 'tfi.instance-seed.v1':
        raise ValueError('Unsupported instance seed')
    manifest = specification['engineering']
    if target_platforms is not None:
        from management_common.distribution_targets import resolve_targets
        manifest.update(resolve_targets({'target_platforms': target_platforms}, manifest.get('features', [])))
    for name, relative in manifest['files'].items():
        if Path(relative).parts != ('data', name):
            raise ValueError('Invalid seed directory')
        (seed / relative).mkdir(parents=True, exist_ok=True)
    actual = [row for row in inventory(seed) if row['path'] != 'manifest.json']
    if actual != sorted(specification['inventory'], key=lambda row: row['path']):
        raise ValueError('Example seed differs from its recorded inventory')
    for name in ('accounts_root', 'secrets_dir'):
        if any((seed / manifest['files'][name]).iterdir()):
            raise ValueError('Example seed must not contain live accounts or secrets')
    suffix = '-' + '-'.join(target_platforms) if target_platforms else ''
    build = repo / ('.build/example_instance' + suffix)
    if build.is_symlink() or (repo / '.build').is_symlink():
        raise ValueError('Build directory cannot be a symlink')
    build.mkdir(parents=True, exist_ok=True)
    destination = build / 'engineering'
    temporary = build / 'engineering.pending'
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir()
    shutil.copytree(repo / 'code', temporary / 'code',
                    ignore=shutil.ignore_patterns(*GENERATED_CODE_DIRECTORIES, '*.pyc', '*.pyo'))
    for name in ('data', 'database'):
        shutil.copytree(seed / name, temporary / name)
    shutil.copyfile(repo / 'INSTALL.md', temporary / 'INSTALL.md')
    rows = inventory(temporary)
    code_rows = [{**row, 'path': row['path'][5:]} for row in rows if row['path'].startswith('code/')]
    canonical = json.dumps(code_rows, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    manifest.update(schema='tfi.release-engineering.v1', inventory=rows,
                    code={'path': 'code', 'sha256': hashlib.sha256(canonical.encode()).hexdigest()},
                    installer={'entrypoint': 'code/scripts/tfi_release_install.py'})
    manifest_path = temporary / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    # Use the installer's own read-only contract before publishing a candidate.
    # This does not prepare dependencies, create services, or import a database.
    from scripts.tfi_release_install import validate_engineering
    validate_engineering(manifest_path, {
        'run_id': manifest['run_id'],
        'supervisor_resident_id': manifest['supervisor_resident_id'],
        'install_root': str(repo / 'instance/example_instance'),
    })
    if destination.exists():
        shutil.rmtree(destination)
    temporary.rename(destination)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform', choices=('linux', 'windows', 'macos'), action='append')
    args = parser.parse_args()
    bundle = prepare(Path(__file__).resolve().parent, target_platforms=args.platform)
    print(f'Example seed verified; installation bundle: {bundle}')
    print('Read INSTALL.md. Instance data is never imported by this command.')


if __name__ == '__main__':
    main()
