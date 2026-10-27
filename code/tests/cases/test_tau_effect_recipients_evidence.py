"""Native Effect notification status evidence, isolated from live mutations.

:module: lineage.hatg
:design: decision_5b5653c3eaa5
"""
import subprocess
from pathlib import Path


def test_closed_effect_recipients_keep_native_identity_and_explicit_empty_state():
    """按钮装饰文案不混入状态；缺失值、错误身份和未配置不能冒充关闭。

    :design-point:
    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name("javascript") / "tau_effect_recipients_evidence.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
