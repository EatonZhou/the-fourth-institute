from pathlib import Path
import subprocess

def test_personal_default_and_paper_panel_contract():
    script = Path(__file__).parent / 'javascript/paper_personal_layout.cjs'
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
