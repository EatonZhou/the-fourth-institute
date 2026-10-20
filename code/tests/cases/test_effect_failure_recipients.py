"""Effect recipient UI states and change-only saves, without a browser.

:module: lineage.hatg
:profiles: local.fixture
"""
from pathlib import Path
import subprocess


def test_effect_recipient_states_and_explicit_close():
    source = Path(__file__).with_name("javascript") / "effect_failure_recipients.cjs"
    result = subprocess.run(["node", "--test", str(source)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
