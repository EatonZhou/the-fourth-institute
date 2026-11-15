"""English UI keeps simplified Chinese glyphs in shared and Chat text."""
import os
from pathlib import Path
import subprocess


def test_portal_chinese_font_is_independent_of_ui_language(tmp_path):
    result = subprocess.run(['node', str(Path(__file__).with_name('javascript') / 'portal_cjk_font.cjs')],
        env={**os.environ, 'CJK_FONT_EVIDENCE': os.environ.get('CJK_FONT_EVIDENCE', str(tmp_path))},
        capture_output=True, text=True, timeout=60)
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
