"""Original native Event category evidence.

:module: lineage.hatg
:importance: P1
"""
from pathlib import Path
import subprocess


def test_native_event_category_observation():
    """原生分类隐藏旧选项，分类就绪与具体事件选择及保存分别核验"""
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "tau_event_category_evidence.cjs")],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
