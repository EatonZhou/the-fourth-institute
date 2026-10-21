"""Native forms and ISA boundary; no live business operations or UI catalog entry."""
from pathlib import Path
import subprocess


def test_explicit_run_as_forms_and_blocked_runtime() -> None:
    """Lineage 运行身份须显式选择或清除，保存被拒时保留草稿，受阻的时间事件源在画布和首页如实显示"""
    root = Path(__file__).resolve().parents[2]
    subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "lineage_run_as.cjs")],
        cwd=root,
        check=True,
    )
