"""Repair Windows preview process-record I/O without resetting the instance."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

VERSIONS = (
    'c7f6319d2f5f2377d4a582d40e4e3b49c0e6afab',
    '9f638e7f2915cf77f2eca4394e0de6cfe87912e3',
    '1e62f188f8ef0f14bf7c42eb06938c8698360f11',
)
OLD_SHA = '10fa999172f321def82ac1d70aa08504aa1d85889e4394719411aaa75a5dee51'
NEW_SHA = 'e6f2f4a68dede7967ece856bdbf398f401d534f790788045c06e8a002598c740'


def repair(platform, payload):
    platform = Path(platform).resolve(strict=True)
    data = Path(payload).read_bytes()
    if hashlib.sha256(data).hexdigest() != NEW_SHA:
        raise ValueError('Invalid repair payload; no files changed.')
    sys.path.insert(0, str(platform / 'code'))
    from scripts.tfi_windows_launcher import launcher_lock
    from management_common.instance_config import read_installation
    with launcher_lock(platform):
        receipt = json.loads((platform / 'windows-package.json').read_text(encoding='utf-8'))
        if receipt.get('source_commit') not in VERSIONS:
            raise ValueError('This repair supports Windows preview 20261021.1/.2/.3 only.')
        root = platform / 'instance/example_instance'
        state = read_installation(root)
        if (Path(state['target']['shared_code_root']).resolve() != platform / 'code'
                or Path(state['target']['install_root']).resolve() != root):
            raise ValueError('Instance ownership mismatch; no files changed.')
        target = platform / 'code/scripts/tfi_windows_user_services.py'
        if any(p.is_symlink() for p in (target, target.parent, target.parent.parent)):
            raise ValueError('Redirected code path; no files changed.')
        original = target.read_bytes()
        digest = hashlib.sha256(original).hexdigest()
        if digest == NEW_SHA:
            return 'already repaired'
        if digest != OLD_SHA:
            raise ValueError('Installed service code was modified; no files changed.')
        # The record format is unchanged. Existing supervisors can keep running;
        # the next launcher starts with the fixed reader. Do not stop services
        # through the old, failing reader or alter their PID records/ACLs.
        backup = platform / '.build/windows/repairs/process-records/services.before.py'
        backup.parent.mkdir(parents=True, exist_ok=True)
        if backup.exists():
            if backup.read_bytes() != original:
                raise ValueError('Existing backup differs; no files changed.')
        else:
            with backup.open('xb') as stream:
                stream.write(original)
        fd, temporary = tempfile.mkstemp(prefix='.services-repair-', dir=target.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return 'repaired'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('platform', type=Path)
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('This repair is for Windows.')
    try:
        result = repair(args.platform, Path(__file__).with_name('tfi_windows_user_services.py'))
    except Exception as exc:
        print(f'Repair failed: {exc}')
        return 1
    print(f'Services {result}. Reopen The Fourth Institute shortcut to resume installation.')
    print('Database, project files, process records and file permissions were preserved.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
