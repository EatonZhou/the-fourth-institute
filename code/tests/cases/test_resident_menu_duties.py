"""The Resident Menu's Duties and Memory tabs.

:importance: P2
:design: decision_13725a7900f6
"""
import subprocess
from pathlib import Path


def test_resident_menu_duties_tab():
    """职责页只读展示顺序、结构与提示词，详情跳到 HATG 对象页，保留调序撤下；Memory 按四级查看并定位职责"""
    result = subprocess.run(
        ['node', '--test', str(Path(__file__).with_name('javascript') / 'resident_menu_duties.cjs')],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
