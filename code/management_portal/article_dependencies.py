"""Explicit installation of an Article's missing TeX files from the configured TeX repository."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile

from management_common.file_lock import try_lock_descriptor, unlock_descriptor
from management_common.process_runtime import run_process
from management_portal import article_build_profile, article_build_results, article_toolchain

_FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]*\.(?:cls|sty|bst)")


def dependency_preflight(article_id: str) -> dict:
    profile = article_build_profile.article_build_preflight(article_id)
    missing = {item['file'] for item in profile.get('dependencies', [])
               if not item.get('resolved') and _FILE.fullmatch(item.get('file', ''))}
    # A class can require packages absent from the manuscript's own declarations.
    build = article_build_results.load_article_build_report(article_id)
    if build.get('status') == 'error':
        for name in re.findall(r"File [`']([^`'\n]+)['`] not found", build.get('log', '')):
            if _FILE.fullmatch(name) and not article_toolchain._kpsewhich_file(name):
                missing.add(name)
    profile['installation'] = {
        'available': bool(article_toolchain._tex_which('tlmgr')),
        'missing_files': sorted(missing),
    }
    return profile


def _packages_for_files(output: str, files: list[str]) -> list[str]:
    matches = {name: set() for name in files}
    package = ''
    for line in output.splitlines():
        header = re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9_.+-]*):', line)
        if header:
            package = header[1]
        elif package and line[:1].isspace():
            path = line.strip()
            # Exclude documentation examples that bundle another package's files.
            if not re.match(r'texmf-dist/(?:tex|bibtex)/', path):
                continue
            name = path.rsplit('/', 1)[-1]
            if name in matches:
                matches[name].add(package)
    unresolved = [name for name, owners in matches.items() if len(owners) != 1]
    if unresolved:
        raise RuntimeError('无法确定这些文件的安装包：' + '、'.join(unresolved))
    return sorted({next(iter(owners)) for owners in matches.values()})


def _run_tlmgr(command: list[str], *, timeout: int) -> str:
    # Never run in the manuscript directory or interpret manuscript text as shell commands.
    with tempfile.TemporaryDirectory(prefix='portal-tex-install-') as directory:
        try:
            result = run_process(command, cwd=directory,
                                 env={**os.environ, 'PATH': article_toolchain._tex_search_path()},
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, errors='replace', timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError('安装依赖超时，请稍后重试。') from exc
        if result.returncode:
            raise RuntimeError('TeX 依赖安装失败\n' + result.stdout[-4000:])
        return result.stdout


def install_article_dependencies(article_id: str) -> dict:
    manager = article_toolchain._tex_which('tlmgr')
    if not manager:
        raise RuntimeError('当前 TeX 环境没有 tlmgr，无法从页面安装依赖。')
    identity = hashlib.sha256(str(Path(manager).resolve()).encode()).hexdigest()[:20]
    lock_path = Path(tempfile.gettempdir()) / ('portal-tex-install-' + identity + '.lock')
    with lock_path.open('a+b') as lock:
        if not try_lock_descriptor(lock.fileno()):
            raise RuntimeError('其他文稿正在安装依赖，请稍后重试。')
        try:
            profile = dependency_preflight(article_id)
            files = profile['installation']['missing_files']
            if not files:
                return {'installed_packages': [], 'preflight': profile}
            pattern = '/(' + '|'.join(re.escape(name) for name in files) + ')$'
            output = _run_tlmgr([manager, 'search', '--global', '--file', pattern], timeout=60)
            packages = _packages_for_files(output, files)
            _run_tlmgr([manager, 'install', *packages], timeout=240)
            return {'installed_packages': packages, 'preflight': dependency_preflight(article_id)}
        finally:
            unlock_descriptor(lock.fileno())
