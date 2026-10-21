"""Directed host loading contracts; one local Chromium, no live Portal APIs."""
from pathlib import Path
import subprocess


def test_projection_host_loading_order_readiness_and_lifecycle():
    script = Path(__file__).with_name("javascript") / "projection_host_loading.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    print(result.stdout, end="")
    assert result.returncode == 0, result.stdout + result.stderr
