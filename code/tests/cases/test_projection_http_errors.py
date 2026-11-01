"""Projection HTTP errors retain transport and domain failure semantics."""
from pathlib import Path
import subprocess
import pytest


@pytest.mark.parametrize("name", ["projection_http_errors.cjs", "projection_http_bridge.cjs"])
def test_projection_http_errors(name):
    script = Path(__file__).with_name("javascript") / name
    result = subprocess.run(
        ["node", "--test", str(script)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
