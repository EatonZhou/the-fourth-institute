"""Repair Windows preview login rendering and startup diagnostics in place."""
import argparse
import os
from pathlib import Path

if __package__:
    from .repair_ipc import repair, ORIGINAL_COMMIT
else:
    from repair_ipc import repair, ORIGINAL_COMMIT

VERSIONS = (ORIGINAL_COMMIT, '4976ff96c75f6858a47b83db852774af53e08982')
UPDATES = (
    ('management_portal/page_bootstrap.py',
     'c679ef2a9028c8c8f538187bcd041307076052ccc63bef0cd85535d851335f31',
     '4dc93da83c83f441eb9ce2860f5f57c7986db9d3c97aee9ecaf6c19d6cb24b3b'),
    ('scripts/tfi_release_install.py',
     'a76782efca7aa1db1e1231b9195e270caa70f4b4eb325c8811cd732df82f23cc',
     '4da6d6a683c667ffe31b70f394292b62ff791258cb4910594bfb32a0c57a9651'),
)


def repair_install(platform, payloads):
    payloads = Path(payloads)
    return repair(platform, payloads / 'local_ipc.py', versions=VERSIONS,
                  backup_name='startup-login', extra_payloads=[
                      (relative, payloads / Path(relative).name, old_hash, new_hash)
                      for relative, old_hash, new_hash in UPDATES])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('platform', type=Path)
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('This repair is for Windows.')
    try:
        result = repair_install(args.platform, Path(__file__).with_name('payload'))
    except Exception as exc:
        print(f'Repair failed: {exc}')
        return 1
    print(f'Startup {result}. Open The Fourth Institute desktop shortcut to resume installation.')
    print('Database, project data and the original installation manifest are preserved.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
