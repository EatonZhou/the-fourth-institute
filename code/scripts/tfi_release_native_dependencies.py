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
import time
from http.client import IncompleteRead
from urllib.error import URLError
from urllib.request import Request, urlopen
import zipfile

from management_common.distribution_targets import identify_host
from management_common.release_dependencies import profiles
from management_common.installation_progress import report
from scripts.tfi_instance_layout import native_dependencies_root

CATALOG = Path(__file__).resolve().parents[1] / 'config/release-native-dependencies.json'
MAX_DOWNLOAD = 256 * 1024 * 1024
MAX_EXPANDED = 2 * 1024 * 1024 * 1024
DISPLAY_NAMES = {'postgres': 'PostgreSQL 数据库', 'nats': 'NATS 消息服务', 'node': 'Node.js 运行环境'}


def artifact_name(entry):
    digest = entry['sha256']
    if not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('Invalid native dependency digest')
    return digest + '.archive'


def checksum(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


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


def download(entry, destination, *, attempts=3, offline=False):
    """Resume owned archive caches; publish only a complete pinned artifact."""
    destination = Path(destination)
    if os.path.normcase(str(destination.absolute())) != os.path.normcase(str(destination.resolve())):
        raise ValueError('Native dependency cache path is redirected')
    partial = destination.with_name(destination.name + '.part')
    name = DISPLAY_NAMES.get(entry['name'], entry['name']) + ' ' + entry['version']
    for path in (destination, partial):
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError('Native dependency cache is not a regular file')
        if path.exists() and path.stat().st_size > MAX_DOWNLOAD:
            raise ValueError('Native dependency download exceeds size limit')
    if destination.exists():
        if checksum(destination) != entry['sha256']:
            raise ValueError('Native dependency cache checksum mismatch: ' + name)
        report('使用安装包或缓存中的 ' + name)
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    if partial.exists() and checksum(partial) == entry['sha256']:
        partial.replace(destination)
        return destination
    if offline:
        raise RuntimeError('安装包缺少内置依赖 ' + name + '，请重新获取完整安装包。')
    for attempt in range(1, attempts + 1):
        size = partial.stat().st_size if partial.exists() else 0
        report(f'正在准备 {name}，已下载 {size / 1048576:.1f} MB（尝试 {attempt}/{attempts}）')
        headers = {'Accept-Encoding': 'identity'}
        if size:
            headers['Range'] = f'bytes={size}-'
        try:
            with urlopen(Request(entry['url'], headers=headers), timeout=30) as response:
                if not response.geturl().startswith('https://'):
                    raise ValueError('Native dependency download redirected outside HTTPS')
                status = response.status
                total = None
                if status == 206:
                    match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+|\*)',
                                         response.headers.get('Content-Range', ''))
                    if not match or int(match[1]) != size or int(match[2]) < size:
                        raise ValueError('Invalid native dependency resume response')
                    total = int(match[3]) if match[3] != '*' else None
                elif status == 200:
                    size = 0  # A server without Range support sent the whole file.
                    length = response.headers.get('Content-Length')
                    total = int(length) if length is not None else None
                else:
                    raise OSError(f'Unexpected HTTP status {status}')
                if total is not None and (total < size or total > MAX_DOWNLOAD):
                    raise ValueError('Native dependency download exceeds size limit')
                # read1 yields received bytes promptly; read(n) can wait for the
                # entire block and discard useful progress on a socket timeout.
                read = getattr(response, 'read1', response.read)
                last_report = 0
                with partial.open('ab' if size else 'wb') as stream:
                    while block := read(64 * 1024):
                        size += len(block)
                        if size > MAX_DOWNLOAD:
                            raise ValueError('Native dependency download exceeds size limit')
                        stream.write(block)
                        if time.monotonic() - last_report >= 1:
                            extent = f' / {total / 1048576:.1f} MB' if total else ' MB'
                            report(f'正在下载 {name}：{size / 1048576:.1f}{extent}')
                            last_report = time.monotonic()
                if total is not None and size != total:
                    raise OSError('Native dependency download ended before the declared length')
            if checksum(partial) != entry['sha256']:
                partial.unlink()
                raise ValueError('Native dependency checksum mismatch: ' + name)
            partial.replace(destination)
            return destination
        except (OSError, URLError, IncompleteRead) as exc:
            if attempt == attempts:
                saved = partial.stat().st_size if partial.exists() else 0
                raise RuntimeError(
                    f'{name} 下载未完成（{type(exc).__name__}），已保留 {saved / 1048576:.1f} MB。'
                    '请检查网络后点击重试，将继续使用已下载内容。') from exc
            report(f'{name} 连接中断，正在续传（尝试 {attempt + 1}/{attempts}）')
            time.sleep(attempt)


def unpack(archive, destination, *, zipped=False):
    if os.name == 'nt':
        # Python's elevated mkdtemp parent can grant only Administrators. PG's
        # restricted-token child disables that group, so its DLLs need a grant
        # to the actual user SID. This new extraction root supplies that ACL.
        from management_common.file_permissions import ensure_private_directory
        ensure_private_directory(destination)
    else:
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
        result = api.run_native(command, target=target, timeout=90)
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
            if not receipt.is_file() or json.loads(receipt.read_text(encoding='utf-8'))['artifact'] != entry:
                raise ValueError('Refusing to replace unowned or mismatched native tools')
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='.prepare-', dir=destination.parent) as temporary:
                temporary = Path(temporary)
                archive = native_dependencies_root(target) / 'archives' / artifact_name(entry)
                download(entry, archive, **({'offline': True} if target.get('offline_dependencies') else {}))
                report('正在解压并校验 ' + DISPLAY_NAMES.get(entry['name'], entry['name']))
                tree = temporary / 'contents'
                unpack(archive, tree, zipped=entry['url'].endswith('.zip'))
                source = package_root(tree, entry)
                (source / receipt.name).write_text(json.dumps({'artifact': entry}, indent=2) + '\n', encoding='utf-8')
                source.rename(destination)
        compatibility = {}
        if entry['name'] == 'postgres' and entry['platform'].startswith('windows/'):
            from scripts.windows.utf8_manifest import prepare_postgres
            compatibility = prepare_postgres(destination)
        # Probe before any service registration. A failed probe leaves its
        # receipt for diagnosis/retry and never activates an incomplete install.
        versions = verify(api, target, entry)
        value = {'artifact': entry, 'resolved_versions': versions, 'status': 'verified'}
        if compatibility:
            value['windows_compatibility'] = compatibility
        api.write_json(receipt, value)
        receipts.append(value)
    return receipts
