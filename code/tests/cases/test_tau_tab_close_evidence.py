"""Bound passive native workbench close-control frame sampling.

:module: lineage.hatg
:aspects: ui, behavior
"""
from pathlib import Path
import subprocess


def test_tau_tab_close_evidence():
    """Paused frames preserve original evidence and cancel the pending callback.

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "tau_tab_close_evidence.cjs")],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
