"""Mailbox submission receipt and unavailable Chat input-history regressions.

:module: resident.conversation
:aspects: ui, behavior
"""
from pathlib import Path
import subprocess
import pytest

@pytest.mark.parametrize("script", ["tau_mailbox_send_contract.cjs", "unavailable_history.cjs"])
def test_mailbox_original_send_and_unavailable_chat(script):
    """Native single-send shape and unavailable-binding reads stay scoped to the original target.

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    result = subprocess.run(["node", "--test", str(Path(__file__).with_name("javascript") / script)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
