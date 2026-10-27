"""Original Paper Review transport evidence without business replay.

:module: workflow.paper
:importance: P1
"""
from pathlib import Path
import subprocess


def test_paper_review_original_request_terminal_evidence():
    """原Paper请求失败结束受理观察，保留身份与错误且不重发或补采"""
    script = Path(__file__).with_name("javascript") / "tau_paper_review_evidence.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
