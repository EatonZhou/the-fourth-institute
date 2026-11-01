"""The standalone daemon uses the same manifest without source import hooks.

:module: execution.providers
"""
import os
from pathlib import Path
import subprocess
import sys

from scripts.claude_app_server_package import build_package
from tests.cases.instance_test_support import write_instance


def test_standalone_package_resolves_installed_paths_without_source_tree(tmp_path):
    instance = write_instance(tmp_path / 'instance')
    package = tmp_path / 'package'
    build_package(Path(__file__).resolve().parents[2], package)
    result = subprocess.run(
        [sys.executable, '-I', '-B', '-c',
         'import sys; sys.path.insert(0, sys.argv[1]); '
         'from management_common.portal_paths import path; print(path("runtime"))', str(package)],
        cwd=tmp_path, env={**os.environ, 'PORTAL_INSTANCE_ROOT': str(instance)},
        text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(instance / 'runtime')
    assert not (package / 'systemd').exists()
