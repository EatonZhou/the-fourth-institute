"""Mailbox original receipts, scoped notifications and concurrent read identity.

:module: resident.conversation
:aspects: ui, behavior
"""
from pathlib import Path
import subprocess


def test_mailbox_original_receipts_and_passive_samples():
    """Preserve original identities without issuing helper business requests.

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "tau_mailbox_original_contract.cjs")],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
