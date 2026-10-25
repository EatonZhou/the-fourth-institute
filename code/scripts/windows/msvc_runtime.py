"""Prepare app-local PostgreSQL CRT DLLs from the pinned Microsoft package.

Never execute the redistributable installer or depend on a globally installed
CRT. Windows' own expand.exe reads the verified embedded cabinets as data.
"""
import json
import os
from pathlib import Path
import shutil
import tempfile

from management_common.file_permissions import prepare_atomic_permissions
from scripts.tfi_instance_layout import native_dependencies_root
from scripts.tfi_release_native_dependencies import artifact_name, checksum, download


def specification():
    return json.loads(Path(__file__).with_name('msvc-runtime.json').read_text(encoding='utf-8'))


def bundle(destination, cache):
    entry = specification()
    name = artifact_name(entry)
    source = download(entry, cache / name)
    target = destination / 'dependencies/native/archives' / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return {**entry, 'path': target.relative_to(destination).as_posix(), 'size': target.stat().st_size}


def prepare(api, target, postgres):
    entry = specification()
    architecture = postgres['platform'].split('/')[1]
    selected = entry['architectures'][architecture]
    directory = Path(postgres['directory']) / 'bin'
    files = {**selected['files'], 'Microsoft-Visual-C++-License.rtf': entry['license']}
    receipt = {'version': entry['version'], 'archive_sha256': entry['sha256'],
               'architecture': architecture, 'files': {n: f['sha256'] for n, f in files.items()}}
    if all((directory / n).is_file() and not (directory / n).is_symlink()
           and checksum(directory / n) == f['sha256'] for n, f in files.items()):
        return receipt
    archive = download(entry, native_dependencies_root(target) / 'archives' / artifact_name(entry),
                       offline=bool(target.get('offline_dependencies')))
    payload = archive.read_bytes()  # download verifies the complete pinned digest.
    with tempfile.TemporaryDirectory(prefix='.crt-', dir=directory) as temp:
        temp = Path(temp)
        expand = str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/expand.exe')

        def extract(cabinet, member, output):
            output.mkdir(exist_ok=True)
            result = api.run_native([expand, str(cabinet), '-F:' + member, str(output)],
                                    target=target, timeout=90)
            if result.returncode or not (output / member).is_file():
                raise RuntimeError(f'Cannot extract Microsoft C++ runtime: {member} (exit {result.returncode})')
            return output / member

        cabinets = {}
        for name, extent in entry['cabinets'].items():
            data = payload[extent['offset']:extent['offset'] + extent['size']]
            if len(data) != extent['size'] or data[:4] != b'MSCF':
                raise ValueError('Invalid pinned Microsoft runtime cabinet')
            cabinets[name] = temp / (name + '.cab')
            cabinets[name].write_bytes(data)
        runtime = extract(cabinets['payload'], selected['cabinet_member'], temp / 'payload')
        # Verify every selected member before changing any program dependency.
        extracted = {}
        for name, item in files.items():
            source = extract(cabinets['license'] if name.endswith('.rtf') else runtime,
                             item['member'], temp / 'files')
            if checksum(source) != item['sha256']:
                raise ValueError('Microsoft runtime member checksum mismatch: ' + name)
            extracted[name] = source
        for name, source in extracted.items():
            path = directory / name
            if path.is_symlink() or path.resolve() != path.absolute():
                raise ValueError('Redirected Microsoft runtime file')
            if path.is_file() and checksum(path) == files[name]['sha256']:
                continue
            prepare_atomic_permissions(source, path, 0o755)
            source.replace(path)
    return receipt
