"""Task polling stops at page/dialog lifetime boundaries.

:module: workflow.studio
:feature: workbench, paper
:importance: P1
:aspects: functional, behavior
:design: decision_6e230393221c
"""
from pathlib import Path
import subprocess


def run_script(name):
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / name)],
        capture_output=True, text=True, timeout=30,
    )
    print(result.stdout, end="")
    assert result.returncode == 0, result.stdout + result.stderr


def test_paper_task_poll_lifecycle():
    """:module: workflow.paper"""
    run_script("paper_task_poll_lifecycle.cjs")


def test_workbench_task_poll_lifecycle():
    run_script("workbench_task_poll_lifecycle.cjs")
