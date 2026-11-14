"""Mailbox notification detail presentation and dialog interaction.

:module: resident.conversation
:aspects: ui, behavior
:design: decision_15f7df737ed5
"""
from pathlib import Path
import subprocess


def test_notification_detail_layout_and_interaction():
    """Responsive details retain full content, receipt, source action and keyboard access.

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name("javascript") / "agent_notification_detail.browser.cjs"
    subprocess.run(["node", str(script)], check=True, cwd=Path(__file__).resolve().parents[2], timeout=60)
