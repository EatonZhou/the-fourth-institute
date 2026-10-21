"""The admin page declares a Model API's protocols and runs API-key accounts on a chosen harness."""
from pathlib import Path
import subprocess


def test_api_key_harnesses_ui():
    """Protocol choices filter the harnesses offered and the APIs a harness account can use.

    :design: decision_7956d7cb4bde
    """
    root = Path(__file__).resolve().parents[2]
    subprocess.run(["node", str(Path(__file__).with_name("javascript") /
                               "api_key_harnesses.cjs")], cwd=root, check=True)
