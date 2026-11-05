from pathlib import Path
import subprocess
import pytest


def test_home_uses_shared_preview_tab_navigation():
    """Home uses the same replacement, lock and discard contract as Preview.

    :design: decision_769a35d6b5c7
    """
    root = Path(__file__).resolve().parents[2]
    script = Path(__file__).with_name('javascript') / 'home_dialogue.browser.cjs'
    subprocess.run(['node', str(script), '--tabs-only'], cwd=root, check=True, timeout=90)


@pytest.mark.parametrize("name", ["home_dialogue.browser.cjs", "home_dialogue_actions.browser.cjs"])
def test_home_preview_existing_host_contracts(name):
    """Preserve live drafts, deferred restores and actual Chat display actions.

    :design: decision_769a35d6b5c7
    """
    root = Path(__file__).resolve().parents[2]
    script = Path(__file__).with_name('javascript') / name
    subprocess.run(['node', str(script)], cwd=root, check=True, timeout=100)
