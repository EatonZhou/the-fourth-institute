"""Original native file scope is readable in the real chat card at narrow width."""
from pathlib import Path
import subprocess


def test_chat_file_approval_scope():
    script = Path(__file__).with_name("javascript") / "chat_file_approval_scope.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
