from pathlib import Path
import subprocess


def test_search_result_preview_context_menu():
    """Search results use the shared Preview menu and resource permissions.

    :design: decision_a144bf700017
    """
    root = Path(__file__).resolve().parents[2]
    subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "command_palette_preview.browser.cjs")],
        cwd=root, check=True, timeout=90,
    )
