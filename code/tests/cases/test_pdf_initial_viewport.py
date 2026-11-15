"""Shared PDF renderer default and viewport priority in an isolated browser."""
from pathlib import Path
import subprocess
from management_portal.test_framework.slots import acquire


def test_pdf_initial_viewport():
    lease = acquire("browser", 1, stop=lambda: False)
    try:
        script = Path(__file__).with_name("javascript") / "pdf_initial_viewport.browser.cjs"
        result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        lease.release()
