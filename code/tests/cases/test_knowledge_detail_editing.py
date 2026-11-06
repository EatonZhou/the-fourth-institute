"""Knowledge detail editing lifecycle, using the real document session scripts."""
from pathlib import Path
import subprocess


def test_knowledge_detail_editing_lifecycle():
    root = Path(__file__).resolve().parents[2]
    script = Path(__file__).with_name("javascript") / "knowledge_detail_editing.cjs"
    subprocess.run(["node", "--test", str(script)], cwd=root, check=True, timeout=30)


def test_knowledge_detail_delete_confirmation():
    root = Path(__file__).resolve().parents[2]
    script = Path(__file__).with_name("javascript") / "knowledge_detail_delete_confirmation.cjs"
    subprocess.run(["node", "--test", str(script)], cwd=root, check=True, timeout=30)
