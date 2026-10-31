"""Native forms and ISA boundary; no live business operations or UI catalog entry."""
from pathlib import Path
import subprocess


def test_lineage_forms_save_leader_and_show_blocked_runtime() -> None:
    """Lineage 表单不带运行身份并以 Leader 保存，被拒时保留草稿，受阻的时间事件源在画布和首页如实显示

    创建和编辑都不出现运行身份控件、不读取 User 目录，也不提交 run_as_principal；编辑按打开时的修订号保存 Leader，权限拒绝与并发冲突保留草稿和错误且不重放。
    Event 源以 Leader 名义监听，受阻、暂停或耗尽的状态在画布与首页如实显示，没有计划时不显示过期的下一次时间。
    """
    root = Path(__file__).resolve().parents[2]
    subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "lineage_run_as.cjs")],
        cwd=root,
        check=True,
    )


def test_chromium_lineage_dialogs_use_the_leader_identity() -> None:
    """Chromium 中的 Lineage 创建与编辑表单不选运行身份，编辑时说明 Leader 承担身份和权限

    真实 HATG 面板样式与对话框在 1280px 中文和 390px 英文下运行，传输隔离：创建不显示 Leader 字段且不提交 run_as_principal；
    编辑显示 Leader 说明，按打开时的修订号保存 Leader，不读取 User 目录也不显示旧的运行身份。

    :module: lineage.team
    :design: decision_6fc653d55c79
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "lineage_leader_identity_browser.cjs")],
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
