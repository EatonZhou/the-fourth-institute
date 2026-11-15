"""Shared typography inside opaque projection frames and built-in source.

:module: workbench.panels
:importance: P1
"""
import os
from pathlib import Path
import re
import subprocess

import pytest
from management_portal.test_framework import slots


@pytest.mark.parametrize('mode', ['package', 'fragment'])
def test_projection_typography_browser(mode):
    script = Path(__file__).with_name('javascript') / 'projection_host_loading.cjs'
    lease = slots.acquire('browser', 1, stop=lambda: False)
    try:
        result = subprocess.run(['node', str(script), '--typography-only'],
                                env=dict(os.environ, PROJECTION_TYPOGRAPHY_MODE=mode),
                                capture_output=True, text=True, timeout=90)
        print(result.stdout, end='')
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        lease.release()


def test_builtin_projection_ui_fonts_consume_shared_token():
    root = Path(__file__).resolve().parents[2] / 'management_portal/workbench_projections'
    violations = []
    for project in ('development_app', 'research_apps'):
        for source in (root / project / 'src').rglob('*.css'):
            for match in re.finditer(r'(?:font-family|font)\s*:[^;}]+', source.read_text()):
                if re.search(r'\b(?:system-ui|Arial|sans-serif)\b', match[0]):
                    violations.append(f'{source.relative_to(root)}: {match[0]}')
    assert not violations, '\n'.join(violations)
