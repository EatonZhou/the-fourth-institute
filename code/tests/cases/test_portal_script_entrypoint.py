from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_portal_entrypoint_uses_package_import() -> None:
    """Portal 入口模块可按 management_portal 包路径导入"""
    result = subprocess.run(
        [sys.executable, "-c", "import management_portal.serve_management_portal"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
