"""Usage Details retain the anonymous groups already present in the chart.

:module: execution.service
:design: decision_fc26f883773d
"""
from pathlib import Path
import subprocess


def test_usage_details_include_anonymous_categories_for_each_selected_view():
    """前20个具名归属与匿名分类分别保留，分组、周期、缓存和账号切换使用同一快照。"""
    script = Path(__file__).with_name('javascript') / 'home_usage_details.cjs'
    subprocess.run(['node', str(script)], cwd=Path(__file__).resolve().parents[2], check=True, timeout=30)
