import subprocess
from pathlib import Path


def test_home_credits_settings_and_refresh():
    script = Path(__file__).with_name('javascript') / 'home_credits.cjs'
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
