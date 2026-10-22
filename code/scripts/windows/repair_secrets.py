"""Repair private-file attribute access and owner ACLs in Windows previews."""
import argparse
import os
from pathlib import Path

if __package__:
    from .repair_ipc import repair
else:
    from repair_ipc import repair

VERSIONS = (
    '28b94ae89bb7d1e1e966cde8472f9b25f5a41cca',
    '43d7e5544a564dc8625a2187bdfdd7c624797491',
)
OLD_SHA256 = '147b69f8742771bcdf22d14c23f27ebfc3f45467990eb8371c0390f117fb5831'
PREVIOUS_SHA256 = '8925b21bd04ed6c151e615aa984d2f4ea3f2d8baee211e24a7137550d8875beb'
NEW_SHA256 = '153761c1abc4bf89792165e8a23f298ab4595686bc841c9665ac78d29f8a6e2c'


def repair_install(platform, payload):
    return repair(platform, payload, versions=VERSIONS, backup_name='secret-owner-rights',
                  primary_update=('management_common/file_permissions.py',
                                  (OLD_SHA256, PREVIOUS_SHA256), NEW_SHA256))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('platform', type=Path)
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('This repair is for Windows.')
    try:
        result = repair_install(args.platform, Path(__file__).with_name('file_permissions.py'))
    except Exception as exc:
        print(f'Repair failed: {exc}')
        return 1
    print(f'Private-file access {result}. Reopen The Fourth Institute desktop shortcut, then retry API registration.')
    print('This repair does not read or submit any API Key. Existing data and permissions are preserved.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
