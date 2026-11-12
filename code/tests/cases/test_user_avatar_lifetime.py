"""Avatar read completion must not override a newer explicit update."""
from pathlib import Path
import subprocess


def test_user_avatar_lifetime():
    result = subprocess.run(['node', '--test', str(Path(__file__).with_name('javascript') / 'user_avatar_lifetime.cjs')],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
