import subprocess
from pathlib import Path


def test_society_mutation_receipt():
    subprocess.run(
        ["node", "--test", str(Path(__file__).with_name("javascript") / "society_mutation_receipt.cjs")],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
