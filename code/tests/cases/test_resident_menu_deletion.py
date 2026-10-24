"""删除 Resident 时，Resident 菜单与 HATG 面板按已提交的结果释放会话订阅。

:module: resident.session
:aspects: ui, behavior
"""
from pathlib import Path
import subprocess


def test_resident_deletion_releases_menu_and_session():
    """已提交的删除先释放 Session 订阅，取消或失败的删除保留菜单

    较晚打开的菜单不会被旧删除关闭；删除后图读取失败时仍报告删除已提交。
    """
    script = Path(__file__).with_name("javascript") / "resident_menu_deletion.cjs"
    result = subprocess.run(["node", "--test", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
