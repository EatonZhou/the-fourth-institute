"""Prepare the core Python environment a code checkout's services start with.

The environment is selected by code_environment from the checkout's dependency
inputs; instances never record a Python path. Feature tools are checked on use.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from management_common.code_environment import dependencies_directory, environment_directory
from management_common.file_lock import lock_stream
from management_common.installation_progress import relay_log_progress, report
from management_common.native_output import run_utf8_capture
from management_common.process_runtime import run_process
from management_common.release_dependencies import STARTUP_PROFILES


def _environment():
    keep = {'PATH', 'SystemRoot', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'TMPDIR', 'LANG',
            'HOME', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'PROCESSOR_ARCHITECTURE',
            'PROCESSOR_ARCHITEW6432'}
    env = {key: value for key, value in os.environ.items() if key in keep or key.startswith('LC_')}
    env.update(PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
    return env


def _doctor(code, python):
    result = run_utf8_capture([str(python), str(code / 'scripts/bootstrap_portal'), 'doctor', *STARTUP_PROFILES,
                               '--venv', str(Path(python).parent.parent), '--tools-only', '--json'],
                              env=_environment(), cwd=code, timeout=540)
    if result.returncode:
        return None
    inventory = json.loads(result.stdout)
    if not inventory or not all(row.get('ok') is True for row in inventory):
        raise ValueError('Dependency doctor returned an invalid inventory')
    return inventory


def prepare(code, *, base_python=None, offline=False, log=None):
    """Verify or create the core environment for ``code``; return its inventory.

    A package that bundles wheels (dependencies/python-wheels) installs from
    them without network access. ``log`` receives the installer's output.
    """
    code = Path(code)
    base = dependencies_directory(code)
    base.mkdir(parents=True, exist_ok=True)
    with (base / '.python-prepare.lock').open('a+b') as stream, lock_stream(stream):
        environment = environment_directory(code)
        python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        inventory = _doctor(code, python) if python.is_file() else None
        if inventory is not None:
            return {'python': str(python), 'source': 'verified-existing', 'inventory': inventory}
        wheelhouse = base / 'python-wheels'
        bundled = (wheelhouse / 'requirements.lock').is_file()
        if offline and not bundled:
            raise RuntimeError('安装包缺少离线 Python 依赖，请重新获取完整安装包。')
        report('正在从安装包准备 Portal 核心 Python 依赖，无需联网' if bundled else
               '正在准备 Portal 核心 Python 依赖；可选浏览器、排版与测试工具不参与首次安装')
        environment.parent.mkdir(parents=True, exist_ok=True)
        log = Path(log) if log else environment.parent / 'install.log'
        command = [str(base_python or sys.executable), str(code / 'scripts/bootstrap_portal'), 'install',
                   *STARTUP_PROFILES, '--venv', str(environment),
                   *(['--wheelhouse', str(wheelhouse)] if bundled else [])]
        with log.open('a', encoding='utf-8') as output, relay_log_progress(log):
            result = run_process(command, env=_environment(), cwd=code, stdout=output, stderr=output,
                                 text=True, timeout=10800)
        if result.returncode:
            raise RuntimeError(f'Dependency preparation failed; inspect {log} and retry')
        inventory = _doctor(code, python)
        if inventory is None:
            raise RuntimeError('Prepared core dependencies failed verification')
        report('运行环境准备完成')
        return {'python': str(python), 'source': 'bundled-offline-wheels' if bundled else 'code-dependency-bootstrap',
                'inventory': inventory}
