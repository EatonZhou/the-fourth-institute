#!/usr/bin/env python3
"""Pinned native TexLab installer for Linux, Windows and macOS.

Asset digests: https://github.com/latex-lsp/texlab/releases/tag/v5.26.0
Only the actual host determines the native asset. Missing TeX is prepared privately.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from management_common.process_runtime import run_process
from management_common.native_output import run_native_capture

VERSION = '5.26.0'
ASSETS = {
    ('linux', 'x86_64'): '8697bd5e479d4584b14b7eed5c320c80ec4e1d91ebefbb6801e6bf38e9971300',
    ('linux', 'aarch64'): 'a85cdfcd22454b8d8550f4b0f0620c45ab51760f302fac7a12bc18a890f70f8c',
    ('macos', 'x86_64'): '6091611f756b28e1a57612b130c196df4b0bb6e22dde5cf5d890578513397daf',
    ('macos', 'aarch64'): 'af7972ffd230711ba04ada9b69cc32ce9111d9196ba69538062872faefdbee56',
    ('windows', 'x86_64'): 'cb028d44c3d2b85d36a2ed52d41a0ff43a341b1f04c500c56c4524c4eb72b316',
    ('windows', 'aarch64'): '99b215e9a44169eb8d786c33484b963055e1c3dd40f68e14fcc47ae1c84e92c1',
}


def release_asset():
    system = {'Linux':'linux', 'Darwin':'macos', 'Windows':'windows'}.get(platform.system())
    machine = platform.machine().lower()
    arch = {'amd64':'x86_64', 'arm64':'aarch64'}.get(machine, machine)
    if (system, arch) not in ASSETS:
        raise RuntimeError(f'No pinned TexLab asset for {system}/{arch}')
    version = os.environ.get('TEXLAB_VERSION', VERSION)
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('TEXLAB_VERSION must be a release version')
    digest = os.environ.get('TEXLAB_SHA256', ASSETS[(system, arch)] if version == VERSION else '')
    if not re.fullmatch(r'[a-fA-F0-9]{64}', digest):
        raise ValueError('An explicit TEXLAB_SHA256 is required for a different version')
    suffix = 'zip' if system == 'windows' else 'tar.gz'
    url = f'https://github.com/latex-lsp/texlab/releases/download/v{version}/texlab-{arch}-{system}.{suffix}'
    return url, digest.lower(), version, 'texlab.exe' if system == 'windows' else 'texlab'


def _extract_binary(archive, destination, name):
    # Copy only the expected regular member, never archive paths or links.
    if name.endswith('.exe'):
        with zipfile.ZipFile(archive) as bundle:
            info = bundle.getinfo(name)
            if info.is_dir() or info.file_size > 128 * 1024 * 1024:
                raise ValueError('Invalid TexLab executable')
            with bundle.open(info) as source, destination.open('wb') as target:
                shutil.copyfileobj(source, target)
    else:
        with tarfile.open(archive, 'r:gz') as bundle:
            info = bundle.getmember(name)
            if not info.isfile() or info.size > 128 * 1024 * 1024:
                raise ValueError('Invalid TexLab executable')
            with bundle.extractfile(info) as source, destination.open('wb') as target:
                shutil.copyfileobj(source, target)
        destination.chmod(0o755)


def _version(binary):
    return run_native_capture([str(binary), '--version'],
                              check=True, timeout=20).stdout.strip()


def install():
    url, digest, version, name = release_asset()
    tool_home = Path(os.environ.get('ARTICLE_TOOL_HOME') or Path.home())
    bin_dir = Path(os.environ.get('ARTICLE_TOOL_BIN_DIR') or tool_home / '.local/bin')
    from scripts.article_dependencies import prepare_tex, smoke_compile
    os.environ.update(prepare_tex(tool_home))
    synctex = shutil.which('synctex')
    if not synctex and os.environ.get('ARTICLE_TINYTEX_BIN'):
        synctex = shutil.which('synctex', path=os.environ['ARTICLE_TINYTEX_BIN'])
    if not synctex:
        for directory in (tool_home / '.TinyTeX/bin').glob('*'):
            synctex = shutil.which('synctex', path=str(directory))
            if synctex:
                break
    tlmgr = shutil.which('tlmgr')
    if not synctex and not tlmgr:
        raise RuntimeError('Private TeX preparation did not provide SyncTeX or tlmgr')
    target = bin_dir / name
    existing = _version(target) if target.is_file() else None
    if existing != f'texlab {version}':
        bin_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.texlab-', dir=bin_dir) as temporary:
            directory = Path(temporary)
            archive = directory / 'archive'
            checksum, size = hashlib.sha256(), 0
            with urllib.request.urlopen(url, timeout=60) as response, archive.open('wb') as stream:
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > 64 * 1024 * 1024:
                        raise ValueError('TexLab download exceeds the size limit')
                    checksum.update(chunk)
                    stream.write(chunk)
            if checksum.hexdigest() != digest:
                raise ValueError('TexLab archive checksum mismatch')
            candidate = directory / name
            _extract_binary(archive, candidate, name)
            if _version(candidate) != f'texlab {version}':
                raise ValueError('TexLab binary version mismatch')
            os.replace(candidate, target)
    if not synctex:
        run_process([tlmgr, 'install', 'synctex'], check=True, timeout=600)
        synctex = shutil.which('synctex', path=str(Path(tlmgr).parent)) or shutil.which('synctex')
        if not synctex:
            raise RuntimeError('SyncTeX is not executable after tlmgr installation')
    run_process([synctex, 'help'], check=True, timeout=20, capture_output=True)
    smoke_compile()
    return target


if __name__ == '__main__':
    print(install())
