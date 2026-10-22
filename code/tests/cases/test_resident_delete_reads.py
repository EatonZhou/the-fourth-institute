"""Resident deletion and browser reads share one lifecycle.

:module: resident.conversation
:design: decision_ce44377c3257
"""
from pathlib import Path
import subprocess


def test_resident_deletion_serializes_browser_reads():
    """删除写入等待在途读取且阻止同目标并发补读，拒绝后恢复。

    :design-point:
    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name("javascript") / "resident_delete_reads.cjs"
    result = subprocess.run(["node", "--test", str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
