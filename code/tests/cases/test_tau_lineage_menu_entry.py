"""Original Lineage cleanup entry uses the native Scope Menu dialog.

:module: lineage.hatg
:importance: P1
"""
from pathlib import Path
import subprocess


def test_native_lineage_menu_entry():
    """Lineage Menu按dialog与原ID就绪，清理和删除仍是独立动作"""
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "tau_lineage_menu_entry.cjs")],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
