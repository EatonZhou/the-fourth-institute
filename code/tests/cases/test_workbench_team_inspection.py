from pathlib import Path
import subprocess


def test_workbench_team_inspection():
    script = Path(__file__).with_name('javascript') / 'workbench_team_inspection.cjs'
    result = subprocess.run(['node', '--test', str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
