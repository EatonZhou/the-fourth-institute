"""The Test page of the development projection page, through its real modules in Chromium.

:module: operations.test-workbench
:aspects: ui, behavior
:design: decision_f2a6a5dd4646
"""
from pathlib import Path
import subprocess


def test_test_page_overview_catalog_case_run_and_tau_pages():
    """Test 页面按地址显示总览、用例目录、用例、新运行、运行与 TAU 页，旁边是测试团队的对话

    总览画出每个模块的覆盖格和缺口、最近运行与目录错误；模块格打开按模块筛选的目录，搜索停顿后由服务端筛选并替换地址；
    筛选失败可重试，过期的筛选响应不覆盖新的结果；勾选的用例在新运行页先生成计划，只在“开始运行”时启动，运行页跟到结束并原样保留输出；
    结束而没有输出与仍在进行的运行给出不同提示；用例页的源码定位到入口行，“运行”带上选中的实现；已删除的运行说明不存在；
    TAU 页在准入暂停时不能运行，模块 Resident 可暂时接过对话；运行记录可只看有问题的并在确认后删除。真实页面模块，接口由夹具应答。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name("javascript") / "test_page.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
