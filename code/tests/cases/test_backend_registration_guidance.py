"""Native AI connection guidance and recovery reuse the registration protocol."""
from pathlib import Path
import subprocess


def test_backend_registration_guidance():
    """Subscription and API setup, including safe retry after partial success.

    :design: decision_7956d7cb4bde
    """
    root = Path(__file__).resolve().parents[2]
    subprocess.run(["node", str(Path(__file__).with_name("javascript") /
                               "backend_registration_guidance.cjs")], cwd=root, check=True)
