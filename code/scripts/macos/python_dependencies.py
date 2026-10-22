"""Refresh macOS wheel locks explicitly; ordinary builds consume fixed locks."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.windows.python_dependencies import bundle, target_settings, validate


# These are the last compatible published Intel wheels and their matching
# WebAuthn closure. cbor2 6.x and cryptography 49+ no longer ship Intel wheels.
# Keep this explicit: ordinary builds must never resolve or silently downgrade.
VERSION_OVERRIDES = {
    'macos/x86_64': {
        'cbor2': '5.9.0', 'cryptography': '48.0.1',
        'webauthn': '2.8.0', 'pyopenssl': '26.2.0',
    },
}


def cache_directory(repo, target):
    lock = repo / 'code' / target_settings(target)[3]
    generation = hashlib.sha256(lock.read_bytes()).hexdigest()[:16]
    return repo / '.build/macos/python-cache' / target.split('/')[1] / generation


def refresh_locks(repo):
    """Reuse reviewed versions with explicit Intel compatibility overrides.

    Resolution is explicit maintenance, never part of first launch or normal
    packaging. Target-specific metadata is checked after download, so a Mac-only
    dependency omitted from these versions fails rather than shipping a gap.
    """
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name, parse_wheel_filename
    source = repo / 'code'
    pins = []
    for line in (source / 'scripts/windows/requirements-core.lock').read_text().splitlines():
        if line.strip() and not line.startswith('#'):
            req = Requirement(line.split(' --hash=')[0])
            version = list(req.specifier)
            if len(version) != 1 or version[0].operator != '==':
                raise ValueError('Expected exact dependency version: ' + line)
            pins.append((canonicalize_name(req.name), version[0].version))

    def metadata(pin):
        name, version = pin
        with urlopen(f'https://pypi.org/pypi/{name}/{version}/json', timeout=60) as response:
            return pin, json.load(response)['urls']
    targets = {target: [(name, VERSION_OVERRIDES.get(target, {}).get(name, version))
                        for name, version in pins]
               for target in ('macos/aarch64', 'macos/x86_64')}
    with ThreadPoolExecutor(max_workers=6) as pool:
        releases = dict(pool.map(metadata, dict.fromkeys(pin for values in targets.values() for pin in values)))

    generated = []
    for target, target_pins in targets.items():
        _, tags, _, relative_lock = target_settings(target)
        rank = {}
        for index, tag in enumerate(tags):
            rank.setdefault(tag, index)
        selected = []
        for name, version in target_pins:
            candidates = []
            for item in releases[name, version]:
                if item['packagetype'] != 'bdist_wheel' or item.get('yanked'):
                    continue
                wheel_name, wheel_version, _, wheel_tags = parse_wheel_filename(item['filename'])
                matches = wheel_tags & rank.keys()
                if wheel_name == name and str(wheel_version) == version and matches:
                    candidates.append((min(rank[tag] for tag in matches), item['filename'], item))
            if not candidates:
                raise ValueError(f'No compatible wheel for {name}=={version} on {target}')
            selected.append((name, version, min(candidates)[2]))
        with tempfile.TemporaryDirectory(prefix='tfi-macos-wheels-') as temporary:
            wheels = Path(temporary)
            lock = wheels / 'requirements.lock'
            lock.write_text('# CPython 3.12.15 / ' + target + '; complete offline closure.\n' + ''.join(
                f'{name}=={version} --hash=sha256:{item["digests"]["sha256"]}\n'
                for name, version, item in selected))
            generation = hashlib.sha256(lock.read_bytes()).hexdigest()[:16]
            cache = repo / '.build/macos/python-cache' / target.split('/')[1] / generation
            def download(selection):
                _, _, item = selection
                cached = cache / item['filename']
                destination = wheels / item['filename']
                if cached.is_file() and hashlib.sha256(cached.read_bytes()).hexdigest() == item['digests']['sha256']:
                    shutil.copyfile(cached, destination)
                else:
                    with urlopen(item['url'], timeout=120) as response, destination.open('wb') as stream:
                        shutil.copyfileobj(response, stream)
                if destination.stat().st_size != item['size']:
                    raise ValueError('Truncated wheel: ' + item['filename'])
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(download, selected))
            rows = validate(source, wheels, lock, target=target)
            cache.mkdir(parents=True, exist_ok=True)
            for row in rows:
                shutil.copyfile(wheels / row['path'], cache / row['path'])
            generated.append((source / relative_lock, lock.read_bytes()))
            print(f'{target}: verified {len(rows)} wheels', flush=True)
    # Both targets must validate before replacing either committed lock.
    for path, data in generated:
        path.write_bytes(data)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh-locks', action='store_true', required=True)
    parser.parse_args()
    refresh_locks(Path(__file__).resolve().parents[3])
