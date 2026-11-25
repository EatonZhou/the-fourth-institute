"""Knowledge primary rename UI with isolated browser transport.

:module: workflow.knowledge
:importance: P1
"""
from pathlib import Path
import subprocess


def test_knowledge_file_rename_browser():
    script = Path(__file__).with_name("javascript") / "knowledge_file_rename.browser.cjs"
    result = subprocess.run(
        ["node", str(script), "--workers=1"], capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_knowledge_file_rename_reload_recovery():
    script = Path(__file__).with_name("javascript") / "knowledge_file_rename.browser.cjs"
    result = subprocess.run(
        ["node", str(script), "--workers=1", "--recovery-only"],
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
