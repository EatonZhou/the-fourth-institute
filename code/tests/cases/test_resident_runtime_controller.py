"""The shared Resident runtime-settings controller (portal_resident_runtime_controller.js) under jsdom/vm.

:design: decision_8b2fa224616b
"""
from pathlib import Path
import subprocess


def _node(*args):
    return subprocess.run(["node", *args], capture_output=True, text=True, timeout=60)


def test_runtime_settings_share_cache_permissions_restart_and_migration_progress():
    """多个入口共用同一 Resident 的运行设置缓存，按权限提供保存或仅重启，迁移进度与过期会话被正确处理

    两个入口共享一次读取与增量保存；关闭入口后旧的保存函数失效；只有重启权限时打开重启选择，无权限时拒绝打开；重启进行中、登录身份变化后旧保存被拒绝。
    运行与快照刷新、未变路由的身份通知不影响重启；各阶段的迁移进度在打开前、投影中和选项读取中都按当前状态显示，登录变化或撤权后不能取消，双击只开一个对话框。

    :module: workbench.chat
    """
    result = _node(str(Path(__file__).with_name("javascript") / "resident_runtime_controller.cjs"))
    assert result.returncode == 0, result.stdout + result.stderr


def test_runtime_settings_save_with_unknown_outcome_keeps_its_identity():
    """结果未知的运行设置保存只能以同一身份重试，读回确认后才换新身份，账号变化后不提交旧设置

    保存响应丢失时，换成不同修改的再次保存被拒绝，原修改以同一幂等键和 Resident 重试；已提交但回执丢失的保存经重新读取确认后，下一次保存使用新键；执行账号已变时旧表单不再提交。

    :module: workbench.chat
    """
    result = _node(str(Path(__file__).with_name("javascript") / "resident_runtime_controller.cjs"), "--save-races")
    assert result.returncode == 0, result.stdout + result.stderr


def test_runtime_subscribers_share_loading_and_recover_from_failure():
    """运行状态的订阅者共享加载与结果，失败会通知新订阅者且重试后恢复

    新订阅者立即收到当前状态，命中缓存的加载只回放给调用者；进行中的订阅者共享加载与完成，已销毁的目标互不影响；失败结果传给之后的订阅者，重试成功后共享状态恢复。

    :module: workbench.chat
    """
    result = _node("--test", str(Path(__file__).with_name("javascript") / "resident_runtime_subscription.cjs"))
    assert result.returncode == 0, result.stdout + result.stderr
