"""Shared quote author layout in narrow conversations.

:module: workbench.chat
:design: decision_f560803947c6
:importance: P1
"""
from pathlib import Path
import subprocess


def test_long_quote_authors_fit_without_changing_reference_or_excerpt():
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'chat_quote_author_geometry.cjs')],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
