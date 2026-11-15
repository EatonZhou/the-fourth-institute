"""Shared bilingual typography across Portal page styles."""
import os
from pathlib import Path
import subprocess


def test_portal_pages_share_fonts_and_header_weights(tmp_path):
    result = subprocess.run(['node', str(Path(__file__).with_name('javascript') / 'portal_typography.cjs')],
        env={**os.environ, 'TYPOGRAPHY_EVIDENCE': os.environ.get('TYPOGRAPHY_EVIDENCE', str(tmp_path))},
        capture_output=True, text=True, timeout=120)
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr


def test_authentication_pages_use_shared_font_without_business_requests(tmp_path):
    result = subprocess.run(['node', str(Path(__file__).with_name('javascript') / 'portal_auth_typography.cjs')],
        env={**os.environ, 'TYPOGRAPHY_EVIDENCE': os.environ.get('TYPOGRAPHY_EVIDENCE', str(tmp_path))},
        capture_output=True, text=True, timeout=90)
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
