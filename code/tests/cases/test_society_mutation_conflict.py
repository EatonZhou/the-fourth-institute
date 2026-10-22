import subprocess
from pathlib import Path


def test_society_mutation_conflict():
    subprocess.run(
        ["node", "--test", str(Path(__file__).with_name("javascript") / "society_mutation_conflict.cjs")],
        check=True,
        capture_output=True,
        text=True,
    )
