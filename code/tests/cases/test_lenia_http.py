"""公开示例中的 Lenia HTTP 兼容与发布资源完整性。

:module: operations.deployment
:design: decision_d88306d0bd17
:profiles: local.fixture
:risk: no-external-side-effects
"""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


def test_lenia_http_hashing_and_published_artifacts():
    """无 WebCrypto 时校验有效与损坏帧，并核对已发布工作台的全部资源。"""
    node = shutil.which("node")
    if node is None:
        pytest.skip("此测试需要 Node.js")
    root = Path(__file__).resolve().parents[3]
    seed = Path(os.environ.get("TFI_EXAMPLE_SEED", root / "instance/example_instance/seed"))
    source = Path(__file__).with_name("javascript") / "lenia_http.mjs"
    result = subprocess.run(
        [node, str(source), str(seed)], capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
