import subprocess
from pathlib import Path


def test_portal_mobile_shell_is_compact_and_mailbox_panel_fits_viewport():
    script = Path(__file__).with_name("javascript") / "portal_mobile_shell.cjs"
    result = subprocess.run(["node", str(script)], check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"status":"passed"' in result.stdout
