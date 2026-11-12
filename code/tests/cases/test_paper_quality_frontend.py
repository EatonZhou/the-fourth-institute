"""Paper artifact invalidation, section generations and projection read retries.

:module: workflow.paper
:feature: paper
:importance: P1
:aspects: functional, behavior
:design: decision_528bf9985705, decision_e37f240e186d
"""
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize("scenario", ["artifacts", "sections", "retry", "snapshot", "bounded", "denial", "lifecycle",
                                      "progress", "resume", "readonly"])
def test_paper_quality_frontend(scenario):
    script = Path(__file__).with_name("javascript") / "paper_quality_frontend.cjs"
    result = subprocess.run(
        ["node", "--test-name-pattern", f"^{scenario}:", str(script)],
        capture_output=True, text=True, timeout=30,
    )
    print(result.stdout, end="")
    assert result.returncode == 0, result.stdout + result.stderr
