import os
from pathlib import Path
import subprocess


def test_home_aligned_layout_removes_agent_access(tmp_path):
    script = Path(__file__).with_name("javascript") / "home_aligned_layout.cjs"
    subprocess.run(
        ["node", str(script)],
        env={**os.environ, "HOME_ALIGNED_EVIDENCE": str(tmp_path)},
        check=True,
        timeout=60,
    )
