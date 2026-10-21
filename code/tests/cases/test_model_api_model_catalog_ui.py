"""The admin page edits a Model API's model catalog, including reasoning efforts."""
from pathlib import Path
import subprocess


def test_model_api_model_catalog_ui():
    """Model lists are validated before saving, prefilled on edit and only sent when changed.

    :design: decision_7956d7cb4bde
    """
    root = Path(__file__).resolve().parents[2]
    subprocess.run(["node", str(Path(__file__).with_name("javascript") /
                               "model_api_model_catalog.cjs")], cwd=root, check=True)
