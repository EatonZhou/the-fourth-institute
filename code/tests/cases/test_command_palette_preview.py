from pathlib import Path
import os
import subprocess


def test_search_failure_clears_loading_and_allows_retry():
    """搜索服务暂不可用时结束加载并提示错误，恢复服务后可重新搜索。

    :module: resource.index-search
    :importance: P1
    """
    root = Path(__file__).resolve().parents[2]
    subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'command_palette_preview.browser.cjs')],
        cwd=root, env={**os.environ, 'SEARCH_RECOVERY_ONLY': '1'}, check=True, timeout=60,
    )


def test_search_result_preview_context_menu():
    """Search results use the shared Preview menu and resource permissions.

    :design: decision_a144bf700017
    """
    root = Path(__file__).resolve().parents[2]
    subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "command_palette_preview.browser.cjs")],
        cwd=root, check=True, timeout=90,
    )
