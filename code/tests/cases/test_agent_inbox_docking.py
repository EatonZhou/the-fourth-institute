"""Shared Mailbox and personal assistant docking, using isolated browser inputs.

:module: resident.conversation
:aspects: ui, behavior
:design: decision_15f7df737ed5
"""
from pathlib import Path
import subprocess

import pytest


def test_shared_inbox_edge_docking():
    """Both entries dock together without changing click or persistence contracts.

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    code = Path(__file__).resolve().parents[2]
    runtime = code / "management_portal/test_framework/tau_runtime/node_modules/playwright"
    if not runtime.is_dir():
        pytest.skip("Optional browser runtime unavailable")
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "agent_inbox_docking.cjs")],
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
