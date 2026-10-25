"""Original Watchboard removal and native workbench tab identity.

:module: resident.conversation
:aspects: ui, behavior
"""
from pathlib import Path
import subprocess


def test_watchboard_original_removal_and_panel_binding():
    """Keep the original receipt and bind Chat DOM to its actual native tab.

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "tau_watchboard_action_contract.cjs")],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_tau_watchboard_install_receipt():
    """Original installation IDs must reconcile with the original create operations.

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "tau_watchboard_install_receipt.cjs")],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
