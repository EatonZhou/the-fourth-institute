"""Shared question cards close on acceptance and recover from real send failures."""
from pathlib import Path
import subprocess


def test_question_delivery_receipt_and_retry():
    script = Path(__file__).with_name("javascript") / "chat_question_delivery.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
