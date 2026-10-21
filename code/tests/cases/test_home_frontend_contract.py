from pathlib import Path
import subprocess



ROOT = Path(__file__).resolve().parents[2]
PORTAL_ROOT = ROOT / "management_portal"


def test_activity_icons_keep_identity_links_with_fallback_avatars() -> None:
    """Home 默认头像、队列图标与真实会话链接

    :design: decision_e30897dd3dc3
    """
    script = Path(__file__).with_name("javascript") / "home_activity_links.cjs"
    subprocess.run(["node", str(script)], check=True, cwd=ROOT)


def test_account_details_own_scheduler_form_and_async_save() -> None:
    """账号详情保留草稿、权限和异步保存目标。

    :design: decision_4dea0db1f45a
    """
    script = Path(__file__).with_name("javascript") / "home_account_settings.cjs"
    subprocess.run(["node", str(script)], cwd=ROOT, check=True)


def test_home_shell_reads_only_the_current_user() -> None:
    """Home 外壳只读取当前用户，各面板自行加载数据，不再请求 summary 或 config

    Home panels read their own data; the shell no longer writes placeholder summaries."""
    controller = (PORTAL_ROOT / "static" / "home.js").read_text(encoding="utf-8")

    def function_body(name: str) -> str:
        start = controller.index(f"function {name}(")
        return controller[start:controller.index("\nfunction ", start + 1)]

    shell = function_body("connectHomeShell")
    assert "window.PortalUI.loadCurrentUser()" in shell
    assert "homeApi" not in shell
    assert "/api/summary" not in controller
    assert "/api/config" not in controller
    assert "data-summary-label" not in controller
    assert "context.currentUserReady.catch(() => {}).then(" in function_body("connectPulse")
