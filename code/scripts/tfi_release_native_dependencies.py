"""Prepare pinned native tools in the installation's private runtime directory.

No package manager, elevation, global PATH change or service activation. Planning
is read-only; the installer owns the lock and validates the target before prepare.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile
import tempfile
from urllib.request import urlopen
import zipfile

from management_common.distribution_targets import identify_host
from management_common.release_dependencies import profiles
from scripts.tfi_instance_layout import native_dependencies_root

CATALOG = Path(__file__).resolve().parents[1] / 'config/release-native-dependencies.json'
MAX_DOWNLOAD = 256 * 1024 * 1024
MAX_EXPANDED = 2 * 1024 * 1024 * 1024


def plan(raw_target, manifest, *, host=None):
    """Fill omitted tool paths deterministically; explicit tools remain explicit."""
    target = deepcopy(raw_target)
    host = host or identify_host()
    root = Path(target['install_root']).resolve()
    target.setdefault('python', os.path.abspath(__import__('sys').executable))
    managed = set(target.get('managed_dependencies', []))
    if not managed <= {'postgres', 'nats', 'node'}:
        raise ValueError('Unknown managed native dependency')
    if bool(target.get('pg_bin')) != bool(target.get('pg_share')):
        raise ValueError('Provide both pg_bin and pg_share, or omit both for automatic preparation')
    if not target.get('pg_bin'):
        managed.add('postgres')
    if not target.get('nats_server'):
        managed.add('nats')
    if set(profiles(manifest)) & {'test', 'tau', 'provider'} and not target.get('node_bin'):
        managed.add('node')
    if not managed:
        return target, []
    catalog = json.loads(CATALOG.read_text(encoding='utf-8'))['packages']
    platform_key = host['platform'] + '/' + host['architecture']
    if platform_key not in catalog:
        raise ValueError('No native dependency artifacts for ' + platform_key)
    if host['platform'] == 'linux':
        import platform
        libc, _ = platform.libc_ver()
        if libc and libc != 'glibc':
            raise ValueError('Automatic native tools require glibc Linux; supply compatible explicit tools')
    suffix = '.exe' if host['platform'] == 'windows' else ''
    entries = []
    for name in sorted(managed):
        entry = dict(catalog[platform_key][name], name=name, platform=platform_key)
        base = native_dependencies_root(target) / (name + '-' + entry['version'] + '-' + entry['sha256'][:12])
        values = ({'pg_bin': base / 'bin', 'pg_share': base / 'share', 'pg_lib': base / 'lib'} if name == 'postgres'
                  else {'nats_server': base / ('nats-server' + suffix)} if name == 'nats'
                  else {'node_bin': base if suffix else base / 'bin'})
        for key, value in values.items():
            if value.resolve() != value:
                raise ValueError('Managed dependency path contains a redirected directory')
            if target.get(key) and Path(target[key]).absolute() != value:
                raise ValueError('Managed dependency path differs from its pinned installation location: ' + key)
            target[key] = str(value)
        entries.append(dict(entry, directory=str(base)))
    target['managed_dependencies'] = sorted(managed)
    return target, entries


def download(entry, destination):
    """Stream once, retaining the publisher's pinned digest rather than trust-on-use."""
    digest = hashlib.sha256()
    size = 0
    with urlopen(entry['url'], timeout=60) as response, destination.open('xb') as stream:
        if not response.geturl().startswith('https://'):
            raise ValueError('Native dependency download redirected outside HTTPS')
        while block := response.read(1024 * 1024):
            size += len(block)
            if size > MAX_DOWNLOAD:
                raise ValueError('Native dependency download exceeds size limit')
            digest.update(block)
            stream.write(block)
    if digest.hexdigest() != entry['sha256']:
        raise ValueError('Native dependency checksum mismatch: ' + entry['name'])


def unpack(archive, destination, *, zipped=False):
    destination.mkdir()
    if zipped:
        with zipfile.ZipFile(archive) as source:
            members = source.infolist()
            if sum(row.file_size for row in members) > MAX_EXPANDED or len(members) > 30000:
                raise ValueError('Native dependency archive exceeds expansion limit')
            for row in members:
                path = PurePosixPath(row.filename)
                if (path.is_absolute() or '..' in path.parts or '\\' in row.filename
                        or ':' in row.filename or stat.S_ISLNK(row.external_attr >> 16)):
                    raise ValueError('Unsafe native dependency archive member')
            source.extractall(destination)
    else:
        with tarfile.open(archive) as source:
            members = source.getmembers()
            if sum(row.size for row in members) > MAX_EXPANDED or len(members) > 30000:
                raise ValueError('Native dependency archive exceeds expansion limit')
            # Python 3.12 data filter rejects devices, path traversal and links
            # leaving the destination; internal Node npm links remain usable.
            source.extractall(destination, members=members, filter='data')


def package_root(tree, entry):
    suffix = '.exe' if entry['platform'].startswith('windows/') else ''
    expected = ('bin/postgres' + suffix if entry['name'] == 'postgres' else
                'nats-server' + suffix if entry['name'] == 'nats' else
                'node.exe' if suffix else 'bin/node')
    candidates = [tree, *[p for p in tree.iterdir() if p.is_dir()]]
    matches = [p for p in candidates if (p / expected).is_file()]
    if len(matches) != 1:
        raise ValueError('Native dependency archive has no unique expected layout')
    return matches[0]


def verify(api, target, entry):
    root = Path(entry['directory'])
    suffix = '.exe' if entry['platform'].startswith('windows/') else ''
    if entry['name'] == 'postgres':
        commands = {'postgres': [str(root / 'bin' / ('postgres' + suffix)), '--version']}
    elif entry['name'] == 'nats':
        commands = {'nats': [str(root / ('nats-server' + suffix)), '--version']}
    else:
        node = root / 'node.exe' if suffix else root / 'bin/node'
        npm = root / 'node_modules/npm/bin/npm-cli.js' if suffix else root / 'lib/node_modules/npm/bin/npm-cli.js'
        commands = {'node': [str(node), '--version'], 'npm': [str(node), str(npm), '--version']}
    versions = {}
    for name, command in commands.items():
        result = api.run_native(command, target=target, timeout=30)
        version = result.stdout.strip()
        if result.returncode or not version:
            raise RuntimeError('Native dependency cannot run on this host: ' + name)
        if name != 'npm' and not re.search(r'(?<![\d.])' + re.escape(entry['version']) + r'(?![\d.])', version):
            raise ValueError('Native dependency version differs from its catalog: ' + name)
        versions[name] = version
    return versions


def prepare(api, target, entries):
    if target.get('shared_code_root'):
        with api.installation_lock(native_dependencies_root(target) / '.locks'):
            return _prepare(api, target, entries)
    return _prepare(api, target, entries)


def _prepare(api, target, entries):
    receipts = []
    for entry in entries:
        destination = Path(entry['directory'])
        receipt = destination / 'tfi-native-dependency.json'
        if destination.exists():
            if not receipt.is_file() or json.loads(receipt.read_text())['artifact'] != entry:
                raise ValueError('Refusing to replace unowned or mismatched native tools')
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='.prepare-', dir=destination.parent) as temporary:
                temporary = Path(temporary)
                archive = temporary / 'download'
                download(entry, archive)
                tree = temporary / 'contents'
                unpack(archive, tree, zipped=entry['url'].endswith('.zip'))
                source = package_root(tree, entry)
                (source / receipt.name).write_text(json.dumps({'artifact': entry}, indent=2) + '\n')
                source.rename(destination)
        # Probe before any service registration. A failed probe leaves its
        # receipt for diagnosis/retry and never activates an incomplete install.
        versions = verify(api, target, entry)
        value = {'artifact': entry, 'resolved_versions': versions, 'status': 'verified'}
        api.write_json(receipt, value)
        receipts.append(value)
    return receipts
