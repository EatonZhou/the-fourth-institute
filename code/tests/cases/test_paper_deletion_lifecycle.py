"""Paper cascade deletion must release its Chat readers before backend mutation."""
from pathlib import Path
import subprocess


def test_paper_deletion_reader_lifecycle():
    source = Path(__file__).with_name("javascript") / "paper_deletion_lifecycle.cjs"
    result = subprocess.run(["node", "--test", str(source)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
