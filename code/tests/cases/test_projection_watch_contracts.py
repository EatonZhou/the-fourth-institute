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
