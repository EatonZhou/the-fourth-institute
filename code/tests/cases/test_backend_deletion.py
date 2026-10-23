"""Account deletion uses explicit confirmation and preserves references."""
from pathlib import Path
import subprocess


def test_account_deletion_confirmation_and_recovery():
    """Cancelled, referenced and pending deletions do not claim success or cascade.

    :design: decision_7956d7cb4bde
    """
    root = Path(__file__).resolve().parents[2]
    subprocess.run(["node", str(Path(__file__).with_name("javascript") / "backend_deletion.cjs")],
                   cwd=root, check=True)
