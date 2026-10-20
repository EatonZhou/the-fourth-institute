#!/usr/bin/env python3
"""Offline deployment cutover, without aliases or implicit service control.

An operator supplies a private JSON manifest with ``moves`` (source/target),
``edits`` (path/candidate/before_sha256), ``links`` (path/target/before_target),
``remove_links`` (path/before_target), ``references`` (path/before_sha256),
and ``services`` (scope: user or system, unit). All paths
are absolute. Edits and links name their PRE-move locations. Candidate files
must be outside moved roots. ``seal`` records exact bytes and metadata after
writers have stopped. ``apply`` requires that seal's digest and a fresh backup
directory; ``rollback`` uses the resulting journal and refuses changed data.
Both directions discover system/user units and activation triggers, refuse
unlisted consumers, mounted descendants/bind aliases, and incomplete Linux
process/mount-namespace visibility. References name external dependency files,
including wrappers and configs; they are CAS protected, never rewritten unless
also listed in edits. Alias removal follows all caller edits. The tool never
unmounts filesystems, disables services, or elevates its identity.
The operator runs a copy of this stdlib-only script outside the moved trees.
This tool neither installs dependencies nor restarts services or repairs Git.
Version 3 seals unit input declarations, content hashes and identities. During
apply/rollback only journal-completed operations authorize cached paths to map
to their new locations or approved edited bytes. Live discovery never stops.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import glob
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode()


def absolute(value: str) -> Path:
    p = Path(value)
    if not p.is_absolute() or '..' in p.parts or any(c in str(p) for c in '\0\n\r') or p == Path('/'):
        raise ValueError('Expected a non-root absolute path')
    # Never traverse a symlink ancestor, even if it currently resolves correctly.
    if any(parent.is_symlink() for parent in p.parents):
        raise ValueError(f'Symlink ancestor: {p}')
    return p


def within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def fingerprint(path: Path) -> dict:
    s = path.lstat()
    row = {'mode': stat.S_IMODE(s.st_mode), 'uid': s.st_uid, 'gid': s.st_gid}
    if stat.S_ISLNK(s.st_mode):
        return row | {'kind': 'link', 'target': os.readlink(path)}
    if stat.S_ISREG(s.st_mode):
        h = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''): h.update(chunk)
        after = path.stat()
        if (s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise RuntimeError(f'Concurrent file change: {path}')
        return row | {'kind': 'file', 'size': s.st_size, 'sha256': h.hexdigest()}
    if stat.S_ISDIR(s.st_mode):
        return row | {'kind': 'directory', 'entries': {p.name: fingerprint(p) for p in sorted(path.iterdir())}}
    raise ValueError(f'Remove or explicitly resolve special file before cutover: {path}')


def identity(path: Path) -> list[int]:
    s = path.lstat()
    return [s.st_dev, s.st_ino]


def mapped(path: Path, moves: list[dict]) -> Path:
    for move in moves:
        root = Path(move['source'])
        if within(path, root): return Path(move['target']) / path.relative_to(root)
    return path


def validate(spec: dict) -> dict:
    if set(spec) != {'moves', 'edits', 'links', 'remove_links', 'references', 'services'}:
        raise ValueError('Manifest requires moves, edits, links, remove_links, references and services')
    if not all(isinstance(spec[k], list) for k in spec): raise ValueError('Manifest fields must be lists')
    sources, targets = [], []
    for m in spec['moves']:
        if set(m) != {'source', 'target'}: raise ValueError('Invalid move')
        a, b = absolute(m['source']), absolute(m['target'])
        if not a.exists() or a.is_symlink(): raise ValueError(f'Absent or aliased source: {a}')
        if b.exists() or b.is_symlink(): raise ValueError(f'Target already exists: {b}')
        if not b.parent.is_dir(): raise ValueError(f'Provision destination parent first: {b.parent}')
        if a.stat().st_dev != b.parent.stat().st_dev: raise ValueError('Cross-filesystem rename requires a separate verified copy plan')
        sources.append(a); targets.append(b)
    roots = sources + targets
    if any(within(a, b) or within(b, a) for i, a in enumerate(roots) for b in roots[i+1:]):
        raise ValueError('Move roots must be distinct and non-overlapping')
    seen = set()
    for edit in spec['edits']:
        if set(edit) != {'path', 'candidate', 'before_sha256'}: raise ValueError('Invalid edit')
        p, c = absolute(edit['path']), absolute(edit['candidate'])
        if p.is_symlink() or not p.is_file() or c.is_symlink() or not c.is_file(): raise ValueError('Edits require regular files')
        if any(within(c, r) for r in roots) or c == p: raise ValueError('Candidate must be outside deployment roots')
        if p in seen: raise ValueError('Duplicate edit')
        if digest(p.read_bytes()) != edit['before_sha256']: raise RuntimeError('Candidate preimage changed; regenerate without discarding concurrent edits')
        seen.add(p)
    for link in spec['links'] + spec['remove_links']:
        if set(link) not in ({'path', 'target', 'before_target'}, {'path', 'before_target'}): raise ValueError('Invalid link')
        if (link in spec['links']) != ('target' in link): raise ValueError('Invalid link operation')
        p = absolute(link['path'])
        if not p.is_symlink() or p in seen: raise ValueError('Link replacement requires a unique existing symlink')
        if os.readlink(p) != link['before_target']: raise RuntimeError('Symlink changed; regenerate candidate')
        target = link.get('target', link['before_target'])
        if not isinstance(target, str) or not target or any(c in target for c in '\0\n\r'): raise ValueError('Invalid link target')
        seen.add(p)
    for ref in spec['references']:
        if set(ref) != {'path', 'before_sha256'}: raise ValueError('Invalid reference')
        p = absolute(ref['path'])
        if p.is_symlink() or not p.is_file() or digest(p.read_bytes()) != ref['before_sha256']:
            raise RuntimeError('Reference preimage changed or is not a regular file')
    service_keys(spec['services'])
    return spec


def service_keys(services: list[dict]) -> set[tuple[str, str]]:
    keys = set()
    for s in services:
        if (not isinstance(s, dict) or set(s) != {'scope', 'unit'} or s['scope'] not in {'user', 'system'}
                or not isinstance(s['unit'], str)
                or not re.fullmatch(r'[A-Za-z0-9_.@:\\-]+\.(service|timer|target|socket|path|mount|automount)', s['unit'])):
            raise ValueError('Expected explicit user/system scope and valid systemd unit')
        key = (s['scope'], s['unit'])
        if key in keys: raise ValueError('Duplicate service')
        keys.add(key)
    return keys


def systemctl(scope: str, *args: str) -> str:
    # The caller must be attached to the SOURCE user's bus, not root's user bus.
    command = ['systemctl', '--user' if scope == 'user' else '--system', *args]
    result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    if result.returncode: raise RuntimeError(f'{scope} service inventory unavailable (exit {result.returncode})')
    return result.stdout


def service_inventory(services: list[dict]) -> dict:
    inventory = {}
    properties = ('Id,Names,LoadState,ActiveState,SubState,MainPID,ControlPID,Job,FragmentPath,DropInPaths,'
                  'ExecStart,ExecStartPre,ExecStartPost,ExecStop,ExecStopPost,Environment,EnvironmentFiles,'
                  'WorkingDirectory,RootDirectory,RootImage,BindPaths,BindReadOnlyPaths,ReadWritePaths,'
                  'ReadOnlyPaths,RequiresMountsFor,Triggers,TriggeredBy,Where,What,Listen,Paths')
    requested = service_keys(services)
    for scope in ('user', 'system'):
        loaded = json.loads(systemctl(scope, 'list-units', '--all', '--output=json', '--no-pager'))
        installed = json.loads(systemctl(scope, 'list-unit-files', '--output=json', '--no-pager'))
        names = {r['unit'] for r in loaded} | {r['unit_file'] for r in installed}
        names.update(unit for kind, unit in requested if kind == scope)
        # Templates are files, not runnable units: `show template@.service`
        # fails. Inspect their merged text, and inspect real instances normally.
        for name in sorted(n for n in names if '@.' in n):
            body = systemctl(scope, 'cat', '--no-pager', '--', name)
            inventory[(scope, name)] = {'Id': name, 'LoadState': 'loaded', 'ActiveState': 'inactive',
                                        'MainPID': '0', 'ControlPID': '0', 'Job': '0', '_UnitFile': body}
        names = sorted(n for n in names if '@.' not in n)
        for start in range(0, len(names), 64):
            output = systemctl(scope, 'show', '--property='+properties, '--no-pager', '--', *names[start:start+64])
            for block in output.strip().split('\n\n'):
                row = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
                if not row.get('Id'): raise RuntimeError('Incomplete service inventory')
                key = (scope, row['Id'])
                # Alias entries may share an Id. Preserve actual canonical identity.
                inventory[key] = row
                for name in row.get('Names', '').split(): inventory[(scope, name)] = row
        if any((scope, name) not in inventory for name in names):
            raise RuntimeError(f'Incomplete {scope} service inventory')
    return inventory


def unit_input_paths(row: dict, transition=None) -> list[str]:
    """Expand cached declarations at their journal-authorized current location."""
    files = row.get('DropInPaths', '').split() + [row.get('FragmentPath', '')]
    value = row.get('EnvironmentFiles', '')
    # `systemctl show` renders an array of path/ignore_errors records. Reject
    # unknown/ambiguous rendering rather than silently losing an input file.
    pattern = re.compile(r'(\S+) \(ignore_errors=(yes|no)\)')
    matches = list(pattern.finditer(value))
    if pattern.sub('', value).strip():
        raise RuntimeError('Cannot parse unit EnvironmentFiles')
    for match in matches:
        path = re.sub(r'\\x([0-9a-fA-F]{2})', lambda m: chr(int(m[1], 16)), match[1])
        if not Path(path).is_absolute(): raise RuntimeError('Non-absolute unit EnvironmentFiles')
        original = path
        if transition: path = str(transition.location(Path(path)))
        # glob suppresses directory PermissionError; stat the literal parent
        # and reject wildcard parents so unreadable data never looks absent.
        if glob.has_magic(str(Path(path).parent)):
            raise RuntimeError('Wildcard EnvironmentFiles parent requires explicit review')
        try:
            with os.scandir(Path(path).parent) as entries: list(entries)
        except FileNotFoundError:
            if match[2] == 'yes': continue
            raise RuntimeError('Required unit EnvironmentFiles directory missing')
        except OSError as error: raise RuntimeError('Cannot inspect unit EnvironmentFiles') from error
        found = glob.glob(path)
        if transition:
            found = [str(transition.original(Path(p))) for p in found]
            matches_original = lambda p: (Path(p).parent == Path(original).parent and
                                         fnmatch.fnmatchcase(Path(p).name, Path(original).name))
            # A single-file move need not move the parent of a wildcard. Keep
            # checking new matches at the original glob, plus sealed members
            # at their individually authorized destinations.
            found += [p for p in transition.snapshot['files'] if matches_original(p)
                      and transition.location(Path(p)) != Path(p)]
            # A retired alias may remain in systemd's cache until the external
            # operator reloads. Its sealed backing file is still checked below.
            found += [p for p in transition.removed if matches_original(p)]
        if not found and match[2] != 'yes':
            raise RuntimeError('Required unit EnvironmentFiles missing')
        files.extend(found)
    return sorted(set(p for p in files if p))


def unit_reference_bytes(row: dict, checked_files: dict) -> bytes:
    """Read declared unit inputs without evaluating shell or revealing values."""
    raw = canonical(row)
    files = unit_input_paths(row)
    for filename in files:
        if not filename: continue
        if filename not in checked_files:
            try: checked_files[filename] = Path(filename).read_bytes()
            except OSError as error: raise RuntimeError('Cannot read a unit input file') from error
        raw += checked_files[filename]
    return raw


class ServiceInputs:
    """Sealed input identities and hashes; never a substitute for current reads.

    Only completed, non-reverted journal steps authorize a different location
    or content. A stale cached declaration gets this treatment only for its
    original unit. New units are discovered and inspected without that privilege.
    """
    def __init__(self, plan=None, steps=None):
        self.plan = plan
        self.steps = steps if steps is not None else []
        self.snapshot = plan['service_inputs'] if plan else {'units': {}, 'files': {}}

    @property
    def completed(self):
        return [s for s in self.steps if s.get('done') and not s.get('reverted')]

    @property
    def moves(self): return [s for s in self.completed if s['kind'] == 'move']

    @property
    def changed(self):
        return {s['original_path']: s for s in self.completed if s['kind'] != 'move'}

    @property
    def removed(self): return {p for p, s in self.changed.items() if s['kind'] == 'remove_link'}

    @property
    def replaced(self):
        # Atomic replacement and its rollback cannot restore the former inode.
        return {s['original_path'] for s in self.steps if s.get('done') and s['kind'] != 'move'}

    def location(self, path): return mapped(path, self.moves)

    def original(self, path):
        return mapped(path, [{'source': s['target'], 'target': s['source']} for s in self.moves])

    @staticmethod
    def metadata(path):
        s = path.lstat()
        return {'mode': s.st_mode, 'uid': s.st_uid, 'gid': s.st_gid,
                'identity': [s.st_dev, s.st_ino],
                'link': os.readlink(path) if stat.S_ISLNK(s.st_mode) else None}

    @staticmethod
    def stable_read(path):
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()
        fields = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if fields(before) != fields(after): raise RuntimeError('Unit input changed while reading')
        return data

    def read_file(self, name):
        records = self.snapshot['files']
        if not self.plan and name not in records:
            path = Path(name); target = path.resolve(strict=True)
            definition = self.metadata(path); target_meta = self.metadata(target)
            data = self.stable_read(target)
            if self.metadata(path) != definition or path.resolve(strict=True) != target:
                raise RuntimeError('Unit input reference changed while sealing')
            records[name] = {'definition': definition, 'resolved': str(target),
                             'target': target_meta, 'sha256': digest(data)}
        record = records[name]
        path = self.location(Path(name)); target = self.location(Path(record['resolved']))
        changed = self.changed
        operation = changed.get(name)
        expected = dict(record['definition'])
        if operation and operation['kind'] == 'remove_link':
            if path.exists() or path.is_symlink(): raise RuntimeError('Retired unit input alias reappeared')
        else:
            actual = self.metadata(path)
            if name in self.replaced:
                expected.pop('identity'); actual.pop('identity')
            if operation and operation['kind'] == 'link': expected['link'] = operation['after']
            if actual != expected: raise RuntimeError('Unit input identity changed')
            if path.resolve(strict=False) not in {Path(record['resolved']), target}:
                raise RuntimeError('Unit input resolution changed')
        target_operation = changed.get(record['resolved'])
        expected_target = dict(record['target']); actual_target = self.metadata(target)
        expected_hash = record['sha256']
        if record['resolved'] in self.replaced:
            expected_target.pop('identity'); actual_target.pop('identity')
        if target_operation and target_operation['kind'] == 'edit':
            expected_hash = target_operation['after']
        if actual_target != expected_target: raise RuntimeError('Unit input backing identity changed')
        data = self.stable_read(target)
        if digest(data) != expected_hash: raise RuntimeError('Unit input content changed')
        return data

    def verify(self, inventory):
        if not self.plan: return
        for p, sha in self.plan['candidates'].items():
            if digest(Path(p).read_bytes()) != sha: raise RuntimeError('Candidate changed during guarded transition')
        for step in self.moves:
            if Path(step['source']).exists() or Path(step['source']).is_symlink():
                raise RuntimeError('Moved source was recreated')
        keys = {scope+':'+row['Id'] for (scope, _), row in inventory.items()}
        if set(self.snapshot['units']) - keys: raise RuntimeError('Sealed service disappeared from inventory')

    def reference(self, key, row):
        declarations = {k: row.get(k, '') for k in ('FragmentPath', 'DropInPaths', 'EnvironmentFiles')}
        records = self.snapshot['units']
        if not self.plan:
            paths = unit_input_paths(row)
            record = {'declarations': declarations, 'paths': paths}
            if key in records and records[key] != record: raise RuntimeError('Unit inputs changed while sealing')
            records[key] = record
        elif key not in records:
            return unit_reference_bytes(row, {})
        else:
            record = records[key]
            if declarations != record['declarations']: raise RuntimeError('Cached unit input declarations changed')
            if unit_input_paths(row, self) != record['paths']:
                raise RuntimeError('Unit input expansion changed')
        return canonical(row) + b''.join(self.read_file(p) for p in records[key]['paths'])


def service_guard(services: list[dict], roots: list[Path], inputs=None) -> dict:
    declared = service_keys(services)
    inventory = service_inventory(services)
    if inputs: inputs.verify(inventory)
    wanted = set(declared)
    needle = [os.fsencode(str(r)) for r in roots]
    checked_files = {}
    for key, row in inventory.items():
        raw = (inputs.reference(key[0]+':'+row['Id'], row) if inputs
               else unit_reference_bytes(row, checked_files))
        if any(n in raw for n in needle): wanted.add(key)
    pending = list(wanted)
    while pending:
        scope, unit = pending.pop()
        row = inventory.get((scope, unit))
        if row is None or row.get('LoadState') not in {'loaded', 'masked'}:
            raise RuntimeError(f'Unit is not loaded/masked: {scope}:{unit}')
        for trigger in (row.get('Triggers', '')+' '+row.get('TriggeredBy', '')).split():
            key = (scope, trigger)
            if key not in wanted: wanted.add(key); pending.append(key)
    # Names aliases are not distinct consumers; require one declared canonical Id.
    canonical_declared = {(s, inventory.get((s, u), {}).get('Id', u)) for s, u in declared}
    required = {(s, inventory[(s, u)]['Id']) for s, u in wanted}
    missing = sorted(required - canonical_declared)
    active = []
    for s, u in sorted(required):
        row = inventory[(s, u)]
        if (row.get('ActiveState') not in {'inactive', 'failed'} or int(row.get('MainPID', '0'))
                or int(row.get('ControlPID', '0')) or (row.get('Job') or '0').split()[0] != '0'):
            active.append(f'{s}:{u}')
    result = {'required': [dict(scope=s, unit=u) for s, u in sorted(required)],
              'missing': [dict(scope=s, unit=u) for s, u in missing], 'active': active}
    if missing or active:
        raise RuntimeError('Service guard refused: '+json.dumps(result, sort_keys=True))
    return result


def mount_rows(text: str) -> list[dict]:
    def unescape(value):
        return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), value)
    rows = []
    for line in text.splitlines():
        fields = line.split(); split = fields.index('-')
        if split < 6 or len(fields) < split+4: raise ValueError('Malformed mountinfo')
        rows.append({'id': fields[0], 'device': fields[2], 'root': unescape(fields[3]),
                     'point': unescape(fields[4]), 'fs': fields[split+1]})
    if not rows: raise RuntimeError('Empty mount inventory')
    return rows


def mount_conflicts(rows: list[dict], roots: list[Path], baseline: list[dict]) -> list[dict]:
    conflicts = {}
    for root in roots:
        covering = [r for r in baseline if within(root, Path(r['point']))]
        if not covering: raise RuntimeError('No covering mount for protected path')
        base = max(covering, key=lambda r: len(Path(r['point']).parts))
        fs_path = Path(base['root']) / root.relative_to(base['point'])
        for row in rows:
            point, fs_root = Path(row['point']), Path(row['root'])
            nested = within(point, root)
            alias = (row['device'] == base['device'] and
                     (within(fs_root, fs_path) or within(fs_path, fs_root)) and
                     (not within(root, point) or fs_root / root.relative_to(point) != fs_path))
            if nested or alias: conflicts[row['id']] = row
    return list(conflicts.values())


def mount_guard(roots: list[Path]) -> None:
    baseline = mount_rows(Path('/proc/self/mountinfo').read_text())
    own_namespace = os.readlink('/proc/self/ns/mnt')
    conflicts = [r | {'namespace': own_namespace, 'representative_pid': str(os.getpid())}
                 for r in mount_conflicts(baseline, roots, baseline)]
    seen = {own_namespace}
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit(): continue
        try:
            namespace = os.readlink(proc/'ns/mnt')
            if namespace in seen: continue
            rows = mount_rows((proc/'mountinfo').read_text())
            conflicts.extend(r | {'namespace': namespace, 'representative_pid': proc.name}
                             for r in mount_conflicts(rows, roots, baseline)); seen.add(namespace)
        except (FileNotFoundError, ProcessLookupError): continue
        except PermissionError as error:
            raise RuntimeError('Mount namespace visibility incomplete; external maintenance required') from error
    if conflicts:
        raise RuntimeError('Mounted descendant or bind alias must be reconciled before cutover: '+
                           json.dumps(conflicts, sort_keys=True))


def process_guard(roots: list[Path]) -> None:
    # Same-user writers matter even when no service owns them. A privileged
    # external operator is required if /proc visibility is incomplete.
    blocked, unknown = [], []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit(): continue
        try:
            state = (proc / 'stat').read_text().rsplit(')', 1)[1].split()[0]
            if state == 'Z': continue
            command = (proc / 'cmdline').read_bytes()
            if not command: continue  # kernel thread
            values = [command, (proc / 'environ').read_bytes(), (proc / 'maps').read_bytes()]
            for p in [proc / 'cwd', proc / 'exe', *list((proc / 'fd').iterdir())]:
                try: values.append(os.fsencode(os.readlink(p)))
                except FileNotFoundError: pass
            if any(os.fsencode(str(root)) in value for root in roots for value in values): blocked.append(proc.name)
        except (FileNotFoundError, ProcessLookupError): continue
        except PermissionError: unknown.append(proc.name)
    if blocked or unknown:
        raise RuntimeError(f'Process guard refused: referenced_pids={blocked[:20]}, unreadable_count={len(unknown)}')


def quiescent(services: list[dict], roots: list[Path], inputs=None) -> None:
    if sys.platform != 'linux': raise RuntimeError('This cutover guard requires Linux process visibility')
    mount_guard(roots)
    service_guard(services, roots, inputs)
    process_guard(roots)


def roots_of(spec: dict) -> list[Path]:
    original = ([Path(m[k]) for m in spec['moves'] for k in ('source', 'target')] +
                [Path(e['path']) for e in spec['edits'] + spec['links'] + spec['remove_links'] + spec['references']])
    return sorted(set(original + [mapped(p, spec['moves']) for p in original]))


def seal(spec: dict) -> dict:
    validate(spec)
    inputs = ServiceInputs()
    quiescent(spec['services'], roots_of(spec), inputs)
    result = {'schema': 'tfi.deployment-cutover.v3', 'spec': spec, 'before': {}, 'identities': {}, 'candidates': {},
              'service_inputs': inputs.snapshot}
    for p in {m['source'] for m in spec['moves']} | {e['path'] for e in spec['edits'] + spec['links'] + spec['remove_links'] + spec['references']}:
        result['before'][p] = fingerprint(Path(p)); result['identities'][p] = identity(Path(p))
    for e in spec['edits']: result['candidates'][e['candidate']] = digest(Path(e['candidate']).read_bytes())
    quiescent(spec['services'], roots_of(spec), ServiceInputs(result))
    return result


def persist(path: Path, data: dict) -> None:
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.tfi-cutover-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(canonical(data)); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


@contextmanager
def operation_lock(path: Path, roots: list[Path]):
    """All operators must use the same private, independent maintenance lock."""
    if sys.platform != 'linux': raise RuntimeError('Linux cutover lock required')
    import fcntl
    path = absolute(str(path))
    if any(within(path, r) for r in roots): raise ValueError('Lock must be outside deployment roots')
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_uid != os.geteuid() or stat.S_IMODE(s.st_mode) != 0o600:
            raise ValueError('Maintenance lock must be a private, owned regular file')
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error: raise RuntimeError('Another cutover operator holds the lock') from error
        yield
    finally: os.close(fd)  # Keep the inode: unlinking admits a second lock owner.


def replace_bytes(path: Path, content: bytes) -> None:
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.tfi-cutover-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        s = path.stat()
        os.chown(name, s.st_uid, s.st_gid)
        shutil.copystat(path, name)  # preserve mode and extended attributes
        with open(name, 'rb') as stream: os.fsync(stream.fileno())
        os.replace(name, path)
        sync_directory(path.parent)
        if path.read_bytes() != content: raise RuntimeError('Replacement readback mismatch')
    finally: Path(name).unlink(missing_ok=True)


def _replace_link(path: Path, target: str) -> None:
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.tfi-cutover-')
    os.close(fd); os.unlink(name)
    try:
        os.symlink(target, name)
        s = path.lstat(); os.lchown(name, s.st_uid, s.st_gid)
        os.replace(name, path)
        sync_directory(path.parent)
        if os.readlink(path) != target: raise RuntimeError('Link readback mismatch')
    finally: Path(name).unlink(missing_ok=True)


def rename_noreplace(source: Path, target: Path) -> None:
    """Atomic collision refusal; deployment moves are Linux/same-filesystem only."""
    import ctypes
    if sys.platform != 'linux': raise RuntimeError('Linux renameat2 required')
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1):
        error = ctypes.get_errno(); raise OSError(error, os.strerror(error), str(target))
    for directory in {source.parent, target.parent}:
        fd = os.open(directory, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)


def apply(plan: dict, expected: str, backup: Path) -> dict:
    if plan.get('schema') != 'tfi.deployment-cutover.v3' or digest(canonical(plan)) != expected: raise ValueError('Plan digest mismatch')
    spec = validate(plan['spec'])
    backup = absolute(str(backup))
    if any(within(backup, r) or within(r, backup) for r in roots_of(spec)): raise ValueError('Backup must be outside deployment roots')
    steps = []
    inputs = ServiceInputs(plan, steps)
    quiescent(spec['services'], roots_of(spec), inputs)
    for p, before in plan['before'].items():
        if identity(Path(p)) != plan['identities'][p] or fingerprint(Path(p)) != before: raise RuntimeError(f'Precondition changed: {p}')
    for p, expected_hash in plan['candidates'].items():
        if digest(Path(p).read_bytes()) != expected_hash: raise RuntimeError(f'Candidate changed: {p}')
    backup.mkdir(mode=0o700)  # exclusive; never re-use an earlier receipt
    journal = {'schema': 'tfi.deployment-cutover-journal.v1', 'plan_sha256': expected, 'spec': spec, 'steps': steps, 'status': 'applying'}
    persist(backup/'plan.json', plan)
    for i, edit in enumerate(spec['edits']):
        content = Path(edit['path']).read_bytes()
        with (backup/f'{i}.before').open('xb') as out:
            os.chmod(out.name, 0o600); out.write(content); out.flush(); os.fsync(out.fileno())
    persist(backup/'journal.json', journal)
    try:
        quiescent(spec['services'], roots_of(spec), inputs)
        for move in spec['moves']:
            quiescent(spec['services'], roots_of(spec), inputs)
            if identity(Path(move['source'])) != plan['identities'][move['source']] or fingerprint(Path(move['source'])) != plan['before'][move['source']]:
                raise RuntimeError('Source changed during cutover')
            step = {'kind': 'move', **move, 'before': plan['before'][move['source']], 'intent': True}
            journal['steps'].append(step); persist(backup/'journal.json', journal)
            rename_noreplace(Path(move['source']), Path(move['target']))
            if fingerprint(Path(move['target'])) != step['before']: raise RuntimeError('Move readback mismatch')
            step['done'] = True; persist(backup/'journal.json', journal)
        for i, edit in enumerate(spec['edits']):
            quiescent(spec['services'], roots_of(spec), inputs)
            path = mapped(Path(edit['path']), spec['moves'])
            if fingerprint(path) != plan['before'][edit['path']]: raise RuntimeError('Edited file changed during cutover')
            data = Path(edit['candidate']).read_bytes()
            if digest(data) != plan['candidates'][edit['candidate']]: raise RuntimeError('Candidate changed during cutover')
            step = {'kind':'edit', 'original_path':edit['path'], 'path':str(path), 'backup':f'{i}.before', 'before':plan['before'][edit['path']], 'after':digest(data), 'intent':True}
            journal['steps'].append(step); persist(backup/'journal.json', journal)
            replace_bytes(path, data)
            step['done'] = True; persist(backup/'journal.json', journal)
        for link in spec['links']:
            quiescent(spec['services'], roots_of(spec), inputs)
            path = mapped(Path(link['path']), spec['moves'])
            if fingerprint(path) != plan['before'][link['path']]: raise RuntimeError('Link changed during cutover')
            step = {'kind':'link', 'original_path':link['path'], 'path':str(path), 'before':plan['before'][link['path']], 'after':link['target'], 'intent':True}
            journal['steps'].append(step); persist(backup/'journal.json', journal)
            _replace_link(path, link['target'])
            step['done'] = True; persist(backup/'journal.json', journal)
        for link in spec['remove_links']:
            quiescent(spec['services'], roots_of(spec), inputs)
            path = mapped(Path(link['path']), spec['moves'])
            if fingerprint(path) != plan['before'][link['path']]: raise RuntimeError('Retired alias changed during cutover')
            step = {'kind': 'remove_link', 'original_path':link['path'], 'path': str(path), 'before': plan['before'][link['path']], 'intent': True}
            journal['steps'].append(step); persist(backup/'journal.json', journal)
            path.unlink(); sync_directory(path.parent)
            if path.exists() or path.is_symlink(): raise RuntimeError('Retired alias reappeared')
            step['done'] = True; persist(backup/'journal.json', journal)
        quiescent(spec['services'], roots_of(spec), inputs)
        removed = {str(mapped(Path(e['path']), spec['moves'])) for e in spec['remove_links']}
        journal['after'] = {str(mapped(Path(p),spec['moves'])): (
            None if str(mapped(Path(p),spec['moves'])) in removed else fingerprint(mapped(Path(p),spec['moves']))) for p in plan['before']}
        journal['status'] = 'applied'
    except BaseException:
        journal['status'] = 'interrupted'
        persist(backup/'journal.json', journal)
        raise
    persist(backup/'journal.json', journal)
    return journal


def rollback(backup: Path) -> dict:
    journal = json.loads((backup/'journal.json').read_text())
    if journal['status'] == 'rolled_back': return journal
    if journal['status'] != 'applied':
        raise RuntimeError('Interrupted journal requires inspection of recorded intents; no blind replay')
    plan = json.loads((backup/'plan.json').read_text())
    if digest(canonical(plan)) != journal['plan_sha256'] or plan['spec'] != journal['spec']:
        raise RuntimeError('Stored plan changed')
    if plan.get('schema') != 'tfi.deployment-cutover.v3': raise ValueError('Rollback requires a sealed service-input plan')
    spec = journal['spec']; inputs = ServiceInputs(plan, journal['steps'])
    quiescent(spec['services'], roots_of(spec), inputs)
    for p, after in journal['after'].items():
        if after is None:
            if Path(p).exists() or Path(p).is_symlink(): raise RuntimeError('Retired alias was recreated')
        elif fingerprint(Path(p)) != after: raise RuntimeError(f'Post-cutover data changed: {p}')
    for step in journal['steps']:
        if step['kind'] == 'move' and (Path(step['source']).exists() or Path(step['source']).is_symlink()): raise RuntimeError('Old path was recreated')
        if step['kind'] == 'edit' and digest((backup/step['backup']).read_bytes()) != step['before']['sha256']: raise RuntimeError('Backup changed')
    journal['status'] = 'rolling_back'; persist(backup/'journal.json', journal)
    for step in reversed(journal['steps']):
        quiescent(spec['services'], roots_of(spec), inputs)
        if step['kind'] == 'edit': replace_bytes(Path(step['path']), (backup/step['backup']).read_bytes())
        elif step['kind'] == 'link': _replace_link(Path(step['path']), step['before']['target'])
        elif step['kind'] == 'remove_link':
            path = Path(step['path'])
            os.symlink(step['before']['target'], path)
            os.lchown(path, step['before']['uid'], step['before']['gid']); sync_directory(path.parent)
        else: rename_noreplace(Path(step['target']), Path(step['source']))
        step['reverted'] = True; persist(backup/'journal.json', journal)
    quiescent(spec['services'], roots_of(spec), inputs)
    if any(fingerprint(Path(p)) != before for p, before in plan['before'].items()): raise RuntimeError('Rollback readback mismatch')
    journal['status'] = 'rolled_back'; persist(backup/'journal.json', journal)
    return journal


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('inspect'); p.add_argument('manifest', type=Path)
    p = commands.add_parser('seal'); p.add_argument('manifest',type=Path); p.add_argument('output',type=Path)
    p.add_argument('--lock-file',type=Path,required=True)
    p = commands.add_parser('apply'); p.add_argument('plan',type=Path); p.add_argument('--expected-sha256',required=True); p.add_argument('--backup',type=Path,required=True)
    p.add_argument('--lock-file',type=Path,required=True)
    p = commands.add_parser('rollback'); p.add_argument('backup',type=Path)
    p.add_argument('--lock-file',type=Path,required=True)
    args = parser.parse_args(); os.umask(0o077)
    if args.command == 'inspect':
        # Diagnostic only: no lock/file/service mutation and no success seal.
        spec = json.loads(args.manifest.read_text()); roots = roots_of(spec)
        report = {}
        for name, action in [('manifest', lambda: validate(spec)), ('mounts', lambda: mount_guard(roots)),
                             ('services', lambda: service_guard(spec['services'], roots)),
                             ('processes', lambda: process_guard(roots))]:
            try:
                action(); report[name] = {'ok': True}
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                report[name] = {'ok': False, 'error': str(error)}
        print(json.dumps(report, sort_keys=True))
        if not all(v['ok'] for v in report.values()): raise SystemExit(2)
    elif args.command == 'seal':
        spec = json.loads(args.manifest.read_text())
        with operation_lock(args.lock_file, roots_of(spec)):
            result = seal(spec)
            with args.output.open('xb') as stream:
                stream.write(canonical(result)); stream.flush(); os.fsync(stream.fileno())
            sync_directory(args.output.parent)
            print(json.dumps({'plan_sha256':digest(canonical(result))}))
    elif args.command == 'apply':
        plan = json.loads(args.plan.read_text())
        with operation_lock(args.lock_file, roots_of(plan['spec'])):
            print(json.dumps({'status':apply(plan,args.expected_sha256,args.backup)['status']}))
    else:
        spec = json.loads((args.backup/'plan.json').read_text())['spec']
        with operation_lock(args.lock_file, roots_of(spec)):
            print(json.dumps({'status':rollback(args.backup)['status']}))


if __name__ == '__main__': main()
