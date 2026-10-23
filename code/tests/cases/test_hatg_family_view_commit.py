"""Family creation retains its camera across the immediate next reload."""
from pathlib import Path
import subprocess


def test_family_creation_waits_for_its_personal_view():
    """创建 Family 完成时视图保存已结束，偏好失败不重投业务变更。

    :module: lineage.hatg
    """
    script = Path(__file__).with_name("javascript") / "hatg_family_view_commit.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
