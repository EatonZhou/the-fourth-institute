"""TAU regressions for retiring a visible Resident and the last Preview document."""
from pathlib import Path
import subprocess


def test_deleted_resident_and_closed_preview_keep_correct_state():
    script = Path(__file__).with_name("javascript") / "tau_state_lifecycle.cjs"
    subprocess.run(["node", str(script)], cwd=Path(__file__).resolve().parents[2], check=True)
