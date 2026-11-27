"""Account-backed named Workbench layouts; no production data side effects."""
from pathlib import Path
import subprocess


def test_named_layout_account_sync():
    result = subprocess.run(
        ["node", "--test", str(Path(__file__).with_name("javascript") / "workbench_saved_layouts.cjs")],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
