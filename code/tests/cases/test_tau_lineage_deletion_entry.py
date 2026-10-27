"""Original Lineage deletion-settings readiness follows the native form.

:module: lineage.hatg
:importance: P1
"""
from pathlib import Path
import subprocess


def test_native_lineage_deletion_entry():
    """删除入口核原Lineage编辑表单，不把说明标题当按钮名称"""
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "tau_lineage_deletion_entry.cjs")],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
