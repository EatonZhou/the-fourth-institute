"""Question cards keep their browser input across passive message refreshes."""
from pathlib import Path
import subprocess


def test_chat_question_input_focus():
    script = Path(__file__).with_name("javascript") / "chat_question_input_focus.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
