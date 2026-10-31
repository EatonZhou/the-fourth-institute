"""Independently selectable Projection watch contracts, formerly browser cases."""
from pathlib import Path
import subprocess


def _run_contract(name):
    source = Path(__file__).with_name("javascript") / "projection_watch_contracts.cjs"
    result = subprocess.run(
        ["node", str(source), name], capture_output=True, text=True, timeout=30,
    )
    print(result.stdout, end="")
    assert result.returncode == 0, result.stdout + result.stderr


def test_complete_584_reference_group_and_later_batch_revocation():
    """584 条引用分批完整授权且撤权时不交付部分结果"""
    _run_contract("batches")


def test_replacement_atomic_failure_supersession_and_working_stop():
    """订阅替换失败保留旧订阅且被取代请求停止继续分批"""
    _run_contract("replacement")


def test_hidden_stopped_destroyed_inflight_refreshes():
    """投影隐藏、停止或销毁后丢弃在途刷新结果"""
    _run_contract("inflight")


def test_shared_disposal_overlapping_stop_and_real_failures():
    """并发清理复用停止请求并报告可重试的真实失败"""
    _run_contract("disposal")


def _run_fault_probe(name):
    """One in-memory production fault must be rejected by its positive contract."""
    source = Path(__file__).with_name("javascript") / "projection_watch_negatives.cjs"
    result = subprocess.run(
        ["node", str(source), name], capture_output=True, text=True, timeout=30,
    )
    print(result.stdout, end="")
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"result":"EXPECTED_FAILURE"' in result.stdout, result.stdout + result.stderr
    assert f'"probe":"{name}"' in result.stdout, result.stdout


def test_fault_probe_undeduplicated_references_fail_the_batch_contract():
    """去掉引用去重的投影宿主被分批授权契约拒绝

    在内存中把 projection_host.js 的引用去重换成原样传递，batches 契约必须以断言失败报告该故障；生产文件不改动。

    :module: workbench.panels
    """
    _run_fault_probe("deduplication")


def test_fault_probe_stale_disposer_fails_the_replacement_contract():
    """旧订阅的清理函数仍发请求时被订阅替换契约拒绝

    在内存中删去 projection_sdk.js 的修订号检查，replacement 契约必须报告旧清理函数发出了请求。

    :module: workbench.panels
    """
    _run_fault_probe("stale_disposer")


def test_fault_probe_late_error_fails_the_inflight_contract():
    """隐藏面板收到在途错误结果时被在途刷新契约拒绝

    在内存中去掉 projection_host.js 投递错误前的当前性检查，inflight 契约必须报告隐藏面板收到了在途结果。

    :module: workbench.panels
    """
    _run_fault_probe("late_error")


def test_fault_probe_swallowed_cancellation_fails_the_disposal_contract():
    """吞掉所有取消错误的订阅清理被并发清理契约拒绝

    在内存中让 projection_sdk.js 无条件忽略 projection.cancelled，disposal 契约必须报告缺少应有的拒绝。

    :module: workbench.panels
    """
    _run_fault_probe("cancellation")


def test_fault_probe_swallowed_cleanup_failure_fails_the_disposal_contract():
    """吞掉清理失败的开发投影被并发清理契约拒绝

    在内存中让 development-lifecycle.bundle.js 的清理 Promise 吞掉错误，disposal 契约必须报告缺少应有的拒绝。

    :module: workbench.panels
    """
    _run_fault_probe("cleanup_failure")
