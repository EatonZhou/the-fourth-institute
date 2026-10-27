"""Release bootstrap must select the shared owner before importing contracts.

:design: decision_08bb2d9141f9
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


SOURCE = Path(__file__).resolve().parents[2]


@pytest.fixture
def installation(tmp_path):
    release = tmp_path / 'release checkout'
    shared = tmp_path / 'shared source'
    for root, marker in ((release, 'old release'), (shared, 'shared')):
        for package in ('scripts', 'management_common', 'management_portal/execution'):
            (root / package).mkdir(parents=True, exist_ok=True)
            (root / package / '__init__.py').write_text('')
        for name in ('portal_paths.py', 'product_environment.py', 'site_config.py', 'site_compatibility.py'):
            (root / 'management_common' / name).write_bytes((SOURCE / 'management_common' / name).read_bytes())
        (root / 'management_common/service_endpoint.py').write_text(
            'class LocalServiceSocketMixin: pass\n' if root == shared else '# Earlier Release has no mixin.\n')
        (root / 'management_portal/execution/service.py').write_text(
            'from management_common.service_endpoint import LocalServiceSocketMixin\n'
            f'owner = {marker!r}\n')
    (release / 'scripts/portal_source_layout.py').write_bytes((SOURCE / 'scripts/portal_source_layout.py').read_bytes())
    (release / 'management_portal/__init__.py').write_bytes((SOURCE / 'management_portal/__init__.py').read_bytes())
    (release / 'management_portal/web_only.py').write_text("owner = 'release'\n")
    environment = dict(os.environ, PORTAL_SITE_CONFIG=str(tmp_path / 'absent-site.json'),
                       PORTAL_PATHS_CONFIG=str(tmp_path / 'absent-paths.json'),
                       PORTAL_SHARED_SOURCE_ROOT=str(shared))
    environment.pop('PYTHONPATH', None)
    return release, shared, environment


def probe(installation, code):
    release, _, environment = installation
    return subprocess.run([sys.executable, '-B', '-c', code], cwd=release,
                          env=environment, text=True, capture_output=True, timeout=15)


@pytest.mark.parametrize('entry', ['management_portal', 'scripts.portal_source_layout'])
def test_shared_contracts_are_selected_before_bootstrap_dependencies(installation, entry):
    release, shared, _ = installation
    result = probe(installation, f'''
import {entry}
import management_portal.web_only as web
import management_portal.execution.service as service
import management_common.service_endpoint as endpoint
import management_common.site_config as site
from scripts import portal_source_layout as layout
import json
layout.activate_shared_imports(__import__('pathlib').Path.cwd())
print(json.dumps([web.owner, service.owner, endpoint.__file__, site.__file__, str(layout.SHARED_ROOT),
    sum(isinstance(f, layout.SharedSourceFinder) for f in __import__('sys').meta_path)]))
''')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ['release', 'shared',
        str(shared / 'management_common/service_endpoint.py'),
        str(shared / 'management_common/site_config.py'), str(shared), 1]


def test_missing_shared_owner_does_not_fall_back_to_release(installation):
    release, shared, _ = installation
    (shared / 'management_common').rename(shared / 'unavailable')
    result = probe(installation, 'import management_portal')
    assert result.returncode != 0
    assert 'Shared source package is unavailable' in result.stderr


def test_standalone_install_without_shared_source_uses_local_configuration(installation):
    release, _, environment = installation
    environment.pop('PORTAL_SHARED_SOURCE_ROOT')
    result = probe(installation, '''
import management_portal
import management_common.site_config as site
from scripts import portal_source_layout as layout
import json
print(json.dumps([site.__file__, str(layout.SHARED_ROOT)]))
''')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [str(release / 'management_common/site_config.py'), str(release)]
