"""头像原图在浏览器内压缩，上传边界只接收小图。

:design: decision_5bc3fdfc7eec
:importance: P1
"""
import os
from pathlib import Path
import subprocess
import sys


def test_avatar_compression_before_upload():
    script = Path(__file__).with_name('javascript') / 'avatar_compression.browser.cjs'
    result = subprocess.run(
        ['node', str(script)], text=True, capture_output=True, timeout=90,
        env={**os.environ, 'PORTAL_TEST_PYTHON': sys.executable},
    )
    assert result.returncode == 0, result.stdout + result.stderr
