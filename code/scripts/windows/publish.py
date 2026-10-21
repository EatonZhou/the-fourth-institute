#!/usr/bin/env python3
"""Publish a verified installer to this repository's GitHub prereleases."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

REPO = Path(__file__).resolve().parents[3]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    args = parser.parse_args()
    release = json.loads(Path(__file__).with_name('release.json').read_text())
    tag = release['tag']
    if not re.fullmatch(r'windows-preview-[0-9]{8}\.[0-9]+', tag):
        raise ValueError('Invalid Windows preview tag')
    build = REPO / '.build/windows'
    receipt = json.loads((build / 'receipt.json').read_text())
    exe = build / 'TheFourthInstitute-Setup-x64.exe'
    with exe.open('rb') as stream:
        actual_sha256 = hashlib.file_digest(stream, 'sha256').hexdigest()
    if (receipt['source_commit'] != args.commit or receipt['source_dirty']
            or receipt['prepared_engineering_validation'] != 'passed'
            or receipt['exe_bytes'] != exe.stat().st_size
            or receipt['exe_sha256'] != actual_sha256):
        raise ValueError('Installer does not match the verified source commit')
    # GitHub uploads assets to a draft and publishes only when all uploads pass.
    # An existing tag fails instead of silently replacing a previous download.
    subprocess.run(['gh', 'release', 'create', tag, '--target', args.commit,
                    '--prerelease', '--latest=false', '--title', release['title'],
                    '--notes-file', str(Path(__file__).with_name('RELEASE.md')),
                    str(exe), str(build / (exe.name + '.sha256')),
                    str(build / 'receipt.json')], cwd=REPO, check=True)


if __name__ == '__main__':
    main()
