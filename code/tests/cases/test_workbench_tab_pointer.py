"""Native tab reveal and close with a real pointer below site navigation."""
import os
from pathlib import Path
import subprocess


def test_dynamic_tab_pointer_reveal_and_close(tmp_path):
    script = Path(__file__).with_name('javascript') / 'workbench_tab_pointer.cjs'
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=60,
                            env={**os.environ, 'TAB_POINTER_EVIDENCE': str(tmp_path)})
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
