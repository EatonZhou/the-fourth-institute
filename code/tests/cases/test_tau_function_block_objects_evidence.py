"""Native component rows in the Function Block Menu.

:module: lineage.hatg
:importance: P1
"""
from pathlib import Path
import subprocess


def test_native_function_block_component_rows():
    """内部组件按原ID取证，Resident与新失败提醒仍独立核对"""
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "tau_function_block_objects_evidence.cjs")],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
