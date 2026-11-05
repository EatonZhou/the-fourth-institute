"""Native Home grouped-tab folding and pointer/keyboard/touch interaction."""
import os
from pathlib import Path
import subprocess


def test_home_tab_autohide(tmp_path):
    script = Path(__file__).with_name("javascript") / "home_dialogue_layout.cjs"
    env = dict(os.environ, HOME_TAB_FOLD_ONLY="1", HOME_DIALOGUE_LAYOUT_EVIDENCE=str(tmp_path))
    subprocess.run(["node", str(script)], env=env, check=True, capture_output=True, text=True, timeout=60)
