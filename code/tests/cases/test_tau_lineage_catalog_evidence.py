"""Read native navigation without requiring the lazy HATG editor.

:module: lineage.hatg
:design: decision_5b5653c3eaa5
"""
import subprocess
from pathlib import Path


def test_lineage_catalog_surface_is_independent_of_lazy_editor_and_other_views():
    """目录原生状态独立于编辑器和正文加载词，完整候选先于断言保存。

    :design-point:
    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name("javascript") / "tau_lineage_catalog_evidence.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
