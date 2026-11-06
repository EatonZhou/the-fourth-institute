from pathlib import Path
import subprocess


def test_shared_operator_member_keeps_graph_identity_and_references():
    source = Path(__file__).with_name("javascript") / "hatg_shared_operator_member_projection.cjs"
    subprocess.run(["node", str(source)], check=True, capture_output=True, text=True)
