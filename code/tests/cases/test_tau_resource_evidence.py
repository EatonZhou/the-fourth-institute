"""Scoped original resource failures, without content or retrospective matching.

:module: operations.test-framework
:design: decision_c953eab654cd
"""
from pathlib import Path
import subprocess


def test_original_resource_failures_keep_request_identity_and_prior_scope():
    """原资源错误保持请求身份和事前范围，晚登记不补写原证据

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name("javascript") / "tau_resource_evidence.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "resource reference evidence passed"
