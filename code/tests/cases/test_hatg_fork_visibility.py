"""Native canvas Fork completion and measured viewport visibility."""
from pathlib import Path
import subprocess


def test_canvas_fork_reveals_created_node():
    """画布 Fork 和空白会话创建完成时显示新卡片，不修改持久位置。

    :module: lineage.hatg
    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name('javascript') / 'hatg_fork_visibility.cjs'
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_hatg_quick_fork_layout():
    """Committed quick Fork cards are separated by actual rendered bounds.

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "hatg_quick_fork_layout.cjs")],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
