"""One-file IPC repair for the 4ac9224 Windows preview, preserving its instance."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

ORIGINAL_COMMIT = '4ac9224247dacb8065e15648ad1387be1e65d03e'
ORIGINAL_SHA256 = '9dee1e3eed472be431636d45ebbdc942033d35c594aad626b9ed1e145ee53306'
REPAIRED_SHA256 = '20c69ab89aace71fd097e9166981f4d62e0ef35f97a3964324493bf9814a8eb0'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def repair(platform, payload, *, extra_payloads=(), versions=(ORIGINAL_COMMIT,), backup_name='ipc-acl', primary_update=None):
    platform = Path(platform).resolve(strict=True)
    relative, old_hash, new_hash = primary_update or ('management_common/local_ipc.py', ORIGINAL_SHA256, REPAIRED_SHA256)
    updates = [(relative, payload, old_hash, new_hash), *extra_payloads]
    prepared = []
    for relative, source, old_hash, new_hash in updates:
        data = Path(source).read_bytes()
        if digest(data) != new_hash:
            raise ValueError('Repair payload checksum mismatch. Download the repair archive again.')
        prepared.append((relative, data, old_hash, new_hash))
    sys.path.insert(0, str(platform / 'code'))
    from scripts.tfi_windows_launcher import launcher_lock, stop_all

    with launcher_lock(platform):
        receipt = json.loads((platform / 'windows-package.json').read_text(encoding='utf-8'))
        if receipt.get('source_commit') not in versions:
            raise ValueError('This repair does not support the installed preview; no files changed.')
        root = platform / 'instance/example_instance'
        states = list((platform / 'instance').glob('*/installation.json'))
        if states != [root / 'installation.json']:
            raise ValueError('This repair requires exactly the default installed instance.')
        state = json.loads(states[0].read_text(encoding='utf-8'))
        if not {'prepare', 'database'} <= set(state.get('completed', [])):
            raise ValueError('Instance preparation/import is incomplete; no files changed.')
        if (Path(state['target']['shared_code_root']).resolve() != platform / 'code'
                or Path(state['target']['install_root']).resolve() != root):
            raise ValueError('The instance belongs to a different code directory.')
        pending = []
        for relative, data, old_hash, new_hash in prepared:
            target = platform / 'code' / relative
            if any(path.is_symlink() for path in (target, target.parent, target.parent.parent)):
                raise ValueError('Redirected code path; no files changed.')
            original = target.read_bytes()
            if digest(original) == new_hash:
                continue
            if digest(original) != old_hash:
                raise ValueError('Installed code was modified; no files changed: ' + relative)
            pending.append((target, original, data))
        if not pending:
            return 'already repaired'
        # Stop through the installed lifecycle, never kill unowned processes.
        stop_all(platform)
        backup = platform / '.build/windows/repairs' / backup_name
        backup.mkdir(parents=True, exist_ok=True)
        for target, original, _ in pending:
            saved = backup / (target.stem + '.before.py')
            if saved.exists() and saved.read_bytes() != original:
                raise ValueError('Existing repair backup differs; no files changed.')
        staged = []
        try:
            for target, original, data in pending:
                saved = backup / (target.stem + '.before.py')
                if not saved.exists():
                    with saved.open('xb') as stream:
                        stream.write(original)
                fd, temporary = tempfile.mkstemp(prefix='.startup-repair-', dir=target.parent)
                staged.append((temporary, target))
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            # Every file is validated and staged first. Each compatible update
            # is atomic; an interrupted multi-file repair can safely be rerun.
            for temporary, target in staged:
                os.replace(temporary, target)
        finally:
            for temporary, _ in staged:
                Path(temporary).unlink(missing_ok=True)
        return 'repaired'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('platform', type=Path)
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('This repair is for Windows.')
    try:
        result = repair(args.platform, Path(__file__).with_name('local_ipc.py'))
    except Exception as exc:
        print(f'Repair failed: {exc}')
        return 1
    print(f'IPC {result}. Open The Fourth Institute desktop shortcut to resume installation.')
    print('Database, project data and the original installation manifest are preserved.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
