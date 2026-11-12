"""Shared Chat search owns listeners and asynchronous results until destroy."""
from pathlib import Path
import subprocess


def test_chat_search_lifetime():
    result = subprocess.run(['node', '--test', str(Path(__file__).with_name('javascript') / 'chat_search_lifetime.cjs')],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
