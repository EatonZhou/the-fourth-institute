"""Prepare the pinned native programs selected by a code checkout's catalog.

They are installed beside the code in dependencies/native, shared by every
instance running that code. No package manager, elevation, global PATH change or
service activation. PostgreSQL is located from here when an instance is created;
the manifest then records it because its version is bound to the database.
"""
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

from management_common.code_environment import dependencies_directory, native_directory
from management_common.distribution_targets import identify_host
from management_common.file_lock import lock_stream
from management_common.installation_progress import report
from management_common.native_output import run_native_capture

PACKAGES = ('postgres', 'nats', 'node')
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


def catalog_entry(code, name, *, host=None):
    """The pinned artifact of one native program for this host and checkout."""
    if name not in PACKAGES:
        raise ValueError('Unknown native dependency: ' + str(name))
    host = host or identify_host()
    platform_key = host['platform'] + '/' + host['architecture']
    catalog = json.loads((Path(code) / 'config/release-native-dependencies.json').read_text(encoding='utf-8'))['packages']
    if platform_key not in catalog:
        raise ValueError('No native dependency artifacts for ' + platform_key)
    if host['platform'] == 'linux':
        import platform
        libc, _ = platform.libc_ver()
        if libc and libc != 'glibc':
            raise ValueError('Pinned native programs require glibc Linux')
    directory = native_directory(code, name, host)
    if directory.resolve() != directory.absolute():
        raise ValueError('Native dependency directory is redirected')
    return dict(catalog[platform_key][name], name=name, platform=platform_key, directory=str(directory))


def postgres_dependency(directory):
    """The manifest's data-bound PostgreSQL location, from a prepared program directory."""
    directory = Path(directory)
    suffix = '.exe' if os.name == 'nt' else ''
    for program in ('initdb', 'postgres', 'pg_isready', 'psql', 'createdb'):
        if not (directory / 'bin' / (program + suffix)).is_file():
            raise ValueError('PostgreSQL directory lacks ' + program)
    if not (directory / 'share/postgres.bki').is_file():
        raise ValueError('PostgreSQL directory lacks its initialization files')
    return {key: str(directory / key) for key in ('bin', 'lib', 'share')}


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


def verify(entry):
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
    environment = native_environment(entry)
    versions = {}
    for name, command in commands.items():
        result = run_native_capture(command, env=environment, timeout=90)
        version = result.stdout.strip()
        if result.returncode or not version:
            status = f'0x{result.returncode & 0xffffffff:08X}'
            detail = ' (required DLL missing)' if status == '0xC0000135' else ''
            raise RuntimeError(f'Native dependency cannot run on this host: {name}; exit {status}{detail}')
        if name != 'npm' and not re.search(r'(?<![\d.])' + re.escape(entry['version']) + r'(?![\d.])', version):
            raise ValueError('Native dependency version differs from its catalog: ' + name)
        versions[name] = version
    return versions


def native_environment(entry):
    """PostgreSQL's private libraries stay with its own executables."""
    environment = {key: value for key, value in os.environ.items()
                   if key in {'PATH', 'SystemRoot', 'SYSTEMROOT', 'WINDIR', 'LANG', 'TMP', 'TEMP', 'TMPDIR'}
                   or key.startswith('LC_')}
    if entry['name'] == 'postgres':
        library = str(Path(entry['directory']) / 'lib')
        if os.name == 'nt':
            environment['PATH'] = library + os.pathsep + environment.get('PATH', '')
        else:
            environment['DYLD_LIBRARY_PATH' if entry['platform'].startswith('macos/') else 'LD_LIBRARY_PATH'] = library
    return environment


def prepare(code, names=PACKAGES, *, offline=False):
    """Download, unpack and probe pinned programs; reuse verified ones."""
    base = dependencies_directory(code) / 'native'
    base.mkdir(parents=True, exist_ok=True)
    if base.resolve() != base.absolute():
        raise ValueError('Code dependencies cannot be redirected')
    with (base / '.prepare.lock').open('a+b') as stream, lock_stream(stream):
        return [_prepare(base, catalog_entry(code, name), offline=offline) for name in names]


def _prepare(base, entry, *, offline):
    destination = Path(entry['directory'])
    receipt = destination / 'tfi-native-dependency.json'
    if destination.is_symlink():
        raise ValueError('Native dependency directory is redirected')
    if destination.exists():
        if not receipt.is_file() or json.loads(receipt.read_text(encoding='utf-8'))['artifact'] != entry:
            raise ValueError('Refusing to replace unowned or mismatched native programs')
    else:
        with tempfile.TemporaryDirectory(prefix='.prepare-', dir=base) as temporary:
            archive = download(entry, base / 'archives' / artifact_name(entry), offline=offline)
            report('正在解压并校验 ' + DISPLAY_NAMES.get(entry['name'], entry['name']))
            tree = Path(temporary) / 'contents'
            unpack(archive, tree, zipped=entry['url'].endswith('.zip'))
            source = package_root(tree, entry)
            (source / receipt.name).write_text(json.dumps({'artifact': entry}, indent=2) + '\n', encoding='utf-8')
            source.rename(destination)
    compatibility = {}
    if entry['name'] == 'postgres' and entry['platform'].startswith('windows/'):
        from scripts.windows.utf8_manifest import prepare_postgres
        from scripts.windows.msvc_runtime import prepare as prepare_crt
        compatibility = prepare_postgres(destination)
        compatibility['msvc_runtime'] = prepare_crt(base, entry, offline=offline)
    # Probe before any service registration. A failed probe leaves its
    # receipt for diagnosis/retry and never activates an incomplete program.
    value = {'artifact': entry, 'resolved_versions': verify(entry), 'status': 'verified'}
    if compatibility:
        value['windows_compatibility'] = compatibility
    receipt.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    return value


if __name__ == '__main__':
    import argparse
    import sys
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--code', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--package', action='append', choices=PACKAGES)
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    print(json.dumps(prepare(args.code.resolve(), args.package or PACKAGES, offline=args.offline), ensure_ascii=False))
