"""The Resident Menu's Duties tab.

:importance: P2
:design: decision_13725a7900f6
"""
import subprocess
from pathlib import Path


def test_resident_menu_duties_tab():
    """Menu 的职责页按顺序列出职责，调序和撤下走对应指令"""
    result = subprocess.run(
        ['node', '--test', str(Path(__file__).with_name('javascript') / 'resident_menu_duties.cjs')],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
