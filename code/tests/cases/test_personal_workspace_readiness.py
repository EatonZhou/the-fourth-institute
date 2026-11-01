"""Workspace read state covers all visible sources, not only an explicit resource.

:module: resident.conversation
:design: decision_8838269736d3
"""
from pathlib import Path
import subprocess


def test_workspace_readiness_includes_resident_bindings_and_directory_reads():
    """显式目录已显示时，Resident 绑定和展开目录的未完成读取仍标记 busy。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name("javascript") / "personal_workspace_readiness.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
