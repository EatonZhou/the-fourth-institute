"""Original editor responses and native navigation, without replay.

:module: workflow.studio
:importance: P1
"""
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize("script", ["tau_workbench_editor_evidence.cjs", "tau_native_navigation_evidence.cjs"])
def test_editor_evidence_uses_original_identity_and_passive_reads(script):
    """编辑器发布与原生导航取证核对原对象、响应及可见状态，不重放请求或用后验补写原证据"""
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / script)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_workbench_job_observation_binds_the_original_request():
    """工作台任务观察绑定原请求，只认同一任务的终态且不发请求

    隔离 Chromium 中核对：登记原请求、顶层 ID 不提前要求消息、同一任务的终态快照、无关任务隔离、身份不符与包装回执被拒绝；原回执不可变，观察被动且关闭后解除监听。

    :module: operations.test-framework
    :design: decision_c953eab654cd
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "workbench_job_observation.cjs")],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
