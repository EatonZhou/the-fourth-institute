"""Build complete, pinned desktop wheelhouses (Windows and native macOS)."""
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile


def target_settings(target='windows/x86_64'):
    from packaging.markers import default_environment
    from packaging.tags import cpython_tags, compatible_tags, mac_platforms
    env = default_environment()
    env.update(python_version='3.12', python_full_version='3.12.15',
               implementation_version='3.12.15', implementation_name='cpython',
               platform_python_implementation='CPython', extra='')
    if target == 'windows/x86_64':
        env.update(sys_platform='win32', os_name='nt', platform_system='Windows', platform_machine='AMD64')
        platforms = ['win_amd64']
        relative_lock = 'scripts/windows/requirements-core.lock'
    elif target in {'macos/aarch64', 'macos/x86_64'}:
        arch = 'arm64' if target.endswith('/aarch64') else 'x86_64'
        env.update(sys_platform='darwin', os_name='posix', platform_system='Darwin', platform_machine=arch)
        platforms = list(mac_platforms((15, 0), arch))
        relative_lock = f'scripts/macos/requirements-core-{arch}.lock'
    else:
        raise ValueError('Unsupported Python wheelhouse target: ' + target)
    tags = list(cpython_tags((3, 12), abis=['cp312'], platforms=platforms))
    tags += list(compatible_tags((3, 12), interpreter='cp312', platforms=platforms))
    return env, tags, platforms, relative_lock


def validate(source, wheels, lock, *, target='windows/x86_64'):
    # Build-only imports: the Windows installer itself needs only stdlib + pip.
    from packaging.requirements import Requirement
    from packaging.specifiers import SpecifierSet
    from packaging.utils import canonicalize_name, parse_wheel_filename

    pins = {}
    for line in lock.read_text(encoding='utf-8').splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        requirement, sha = line.split(' --hash=sha256:')
        req = Requirement(requirement)
        if req.url or req.marker or req.extras or len(sha) != 64:
            raise ValueError('Invalid desktop Python lock entry: ' + line)
        if canonicalize_name(req.name) in pins:
            raise ValueError('Duplicate Python lock entry: ' + line)
        pins[canonicalize_name(req.name)] = (req, sha)
    env, tags, _, _ = target_settings(target)
    tags = set(tags)
    packages, rows = {}, []
    for path in sorted(wheels.glob('*.whl')):
        name, version, _, wheel_tags = parse_wheel_filename(path.name)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if name not in pins or version not in pins[name][0].specifier or sha != pins[name][1]:
            raise ValueError('Wheel does not match Python lock: ' + path.name)
        if name in packages or not wheel_tags & tags:
            raise ValueError('Duplicate or incompatible desktop wheel: ' + path.name)
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise ValueError('Damaged wheel: ' + path.name)
            names = [n for n in archive.namelist() if n.endswith('.dist-info/METADATA')]
            if len(names) != 1:
                raise ValueError('Invalid wheel metadata: ' + path.name)
            metadata = BytesParser().parsebytes(archive.read(names[0]))
        if (canonicalize_name(metadata['Name']) != name or metadata['Version'] != str(version)
                or '3.12.15' not in SpecifierSet(metadata.get('Requires-Python', ''))):
            raise ValueError('Incompatible Python metadata: ' + path.name)
        packages[name] = (version, [Requirement(r) for r in metadata.get_all('Requires-Dist', [])])
        rows.append({'path': path.name, 'size': path.stat().st_size, 'sha256': sha})
    if not pins or set(packages) != set(pins):
        raise ValueError('Incomplete desktop wheelhouse')

    def roots(path):
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.split('#', 1)[0].strip()
            if line.startswith('-r '):
                yield from roots(path.parent / line[3:].strip())
            elif line:
                yield Requirement(line)

    extras = {name: {''} for name in packages}

    def require(req):
        name = canonicalize_name(req.name)
        if req.url or name not in packages or packages[name][0] not in req.specifier:
            raise ValueError('Desktop wheelhouse is missing a compatible dependency: ' + str(req))
        extras[name].update(req.extras)

    for req in roots(source / 'requirements.txt'):
        if not req.marker or req.marker.evaluate(env):
            require(req)
    while True:
        before = {name: set(values) for name, values in extras.items()}
        for name, (_, requirements) in packages.items():
            for req in requirements:
                if not req.marker or any(req.marker.evaluate({**env, 'extra': e}) for e in extras[name]):
                    require(req)
        if extras == before:
            break
    return rows


def bundle(repo, destination, cache, *, target='windows/x86_64'):
    source = repo / 'code'
    _, _, platforms, relative_lock = target_settings(target)
    lock = source / relative_lock
    cache.mkdir(parents=True, exist_ok=True)
    try:
        rows = validate(source, cache, lock, target=target)
    except (OSError, ValueError, zipfile.BadZipFile):
        with tempfile.TemporaryDirectory(prefix='python-wheels-', dir=cache.parent) as temporary:
            wheels = Path(temporary)
            subprocess.run([sys.executable, '-m', 'pip', '--isolated', 'download',
                            '--index-url', 'https://pypi.org/simple', '--find-links', str(cache),
                            *[arg for platform in platforms for arg in ('--platform', platform)], '--python-version', '3.12',
                            '--implementation', 'cp', '--abi', 'cp312', '--only-binary=:all:',
                            '--no-deps', '--require-hashes', '--disable-pip-version-check',
                            '--timeout', '120', '--retries', '5', '--dest', str(wheels),
                            '-r', str(lock)], check=True)
            rows = validate(source, wheels, lock, target=target)
            # Copy only the validated generation; never ship stale cache wheels.
            result = destination / 'dependencies/python-wheels'
            result.mkdir(parents=True)
            for row in rows:
                shutil.copyfile(wheels / row['path'], result / row['path'])
                shutil.copyfile(wheels / row['path'], cache / row['path'])
    else:
        result = destination / 'dependencies/python-wheels'
        result.mkdir(parents=True)
        for row in rows:
            shutil.copyfile(cache / row['path'], result / row['path'])
    shutil.copyfile(lock, result / 'requirements.lock')
    receipt = {'target': 'cp312-' + platforms[0], 'path': 'dependencies/python-wheels',
               'lock_sha256': hashlib.sha256(lock.read_bytes()).hexdigest(), 'files': rows}
    (result / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    return receipt
