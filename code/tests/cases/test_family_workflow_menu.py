"""Native Family menu provides explicit ordinary workflow ownership controls.

:module: lineage.hatg
:design: decision_44cf587c1721
"""
import subprocess
from pathlib import Path


def test_family_workflow_menu_ownership_and_save_boundaries():
    """原生菜单保留组件身份，收纳与移出只提交明确归属变更。

    :design-point:
    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name("javascript") / "family_workflow_menu.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    print(result.stdout)
