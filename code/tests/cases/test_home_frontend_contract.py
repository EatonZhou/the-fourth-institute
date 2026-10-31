from pathlib import Path
import subprocess



ROOT = Path(__file__).resolve().parents[2]
PORTAL_ROOT = ROOT / "management_portal"


def test_balance_distinguishes_unsupported_queries_from_zero_and_failed_reads():
    """余额未提供、查询失败与真实零余额有不同展示，旧金额保持过期标识。"""
    script = Path(__file__).with_name("javascript") / "home_balance_capability.cjs"
    subprocess.run(["node", str(script)], cwd=ROOT, check=True)


def test_subscription_quota_preserves_stale_and_unknown_values() -> None:
    """订阅额度保留未知值与读取失败时的过期标识。"""
    script = Path(__file__).with_name("javascript") / "home_account_freshness.cjs"
    subprocess.run(["node", str(script)], check=True, cwd=ROOT)


def test_subscription_summary_and_paired_api_rows_fit_in_browser() -> None:
    """订阅额度保持原布局，API 每行两项；覆盖刷新、未知值及宽窄屏。"""
    script = Path(__file__).with_name("javascript") / "home_quota_layout.cjs"
    subprocess.run(["node", str(script)], check=True, cwd=ROOT, timeout=60)


def test_activity_icons_keep_identity_links_with_fallback_avatars() -> None:
    """Home 默认头像、队列图标与真实会话链接

    :design: decision_e30897dd3dc3
    """
    script = Path(__file__).with_name("javascript") / "home_activity_links.cjs"
    subprocess.run(["node", str(script)], check=True, cwd=ROOT)


def test_account_details_only_show_resident_information() -> None:
    """账号详情只保留节点统计和批量接单，移除计费和调度表单。

    :design: decision_4dea0db1f45a
    """
    script = Path(__file__).with_name("javascript") / "home_account_details.cjs"
    subprocess.run(["node", str(script)], cwd=ROOT, check=True)


def test_home_shell_reads_only_the_current_user() -> None:
    """Home 外壳只读取当前用户，各面板自行加载数据，不再请求 summary 或 config

    Home panels read their own data; the shell no longer writes placeholder summaries."""
    controller = (PORTAL_ROOT / "static" / "home.js").read_text(encoding="utf-8")
    parts = "".join(path.read_text(encoding="utf-8") for path in sorted((PORTAL_ROOT / "static").glob("home_*.js")))

    def function_body(name: str) -> str:
        start = controller.index(f"function {name}(")
        return controller[start:controller.index("\nfunction ", start + 1)]

    shell = function_body("connectHomeShell")
    assert "window.PortalUI.loadCurrentUser()" in shell
    assert "homeApi" not in shell
    assert "/api/summary" not in controller + parts
    assert "/api/config" not in controller + parts
    assert "data-summary-label" not in controller + parts
    assert "context.currentUserReady.catch(() => {}).then(" in function_body("connectPulse")


def test_empty_accounts_offer_existing_registration_dialog() -> None:
    """首次接入入口遵守权限，复用账号表单并继续接入流程。"""
    script = Path(__file__).with_name("javascript") / "home_account_registration.cjs"
    subprocess.run(["node", "--experimental-vm-modules", str(script)], cwd=ROOT, check=True)
