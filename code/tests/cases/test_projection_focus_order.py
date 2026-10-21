"""Real host/controller/SDK focus ordering, with local object authorization replies."""
from pathlib import Path
import subprocess


def test_projection_focus_authorization_order_and_failed_replacement():
    source = Path(__file__).with_name('javascript') / 'projection_focus_order.cjs'
    result = subprocess.run(['node', str(source)], capture_output=True, text=True, timeout=30)
    print(result.stdout, end='')
    assert result.returncode == 0, result.stdout + result.stderr
