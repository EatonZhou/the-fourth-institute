"""Read actual native Fork controls before any accepted mutation.

:module: resident.conversation
:aspects: ui, behavior
"""
from pathlib import Path
import subprocess


def test_tau_hatg_fork_evidence():
    """A visible, hit-tested subpixel control is not a ready Fork target.

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "tau_hatg_fork_evidence.cjs")],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
