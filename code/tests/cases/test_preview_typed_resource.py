"""Typed addresses share the resource-reference resolver (:design: decision_769a35d6b5c7)."""
from pathlib import Path
import subprocess


def test_typed_resource_id_opens_native_preview():
    root=Path(__file__).resolve().parents[2]
    script=Path(__file__).with_name('javascript')/'home_dialogue_actions.browser.cjs'
    subprocess.run(['node',str(script),'--address-only'],cwd=root,check=True,timeout=90)
