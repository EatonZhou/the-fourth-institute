"""The canvas connects Residents to a blackboard from the board's own handle."""
from pathlib import Path
import subprocess


def test_hatg_blackboard_attach_contract():
    """黑板节点自带“连接 Resident”端口，拖到 Resident 上建立 Resident→黑板的 attach 连线；Resident 本身没有黑板端口"""
    source = Path(__file__).with_name('javascript') / 'hatg_blackboard_attach.cjs'
    result = subprocess.run(['node', '--test', str(source)], capture_output=True, text=True, timeout=30)
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
